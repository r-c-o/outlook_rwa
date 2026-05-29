"""Load the Outlook RWA base tables into Oracle, run the SQL pipeline, export xlsx.

Layered on top of the DuckDB SQL pipeline (Approach D): the *same* rendered SQL
in sql/*.sql runs against Oracle; only the loader/DDL differ. python-oracledb is
used in thin mode (no Oracle client install needed) and is imported lazily so
this module — and the offline DDL/mapping-log generation — works in environments
without Oracle reachable.

Usage:
    # Offline: generate DDL + column-mapping logs only (no DB connection):
    python scripts/load_oracle.py --offline

    # Live: connect, create tables, bulk-load, run pipeline, export xlsx:
    python scripts/load_oracle.py

Connection config is read from environment variables (see .env.example). Copy
.env.example to .env and fill in your values, or export them in your shell.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
for _p in (str(SRC), str(REPO_ROOT / "scripts"), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import oracle_ddl as odl  # noqa: E402
import run_duckdb_pipeline as rdp  # noqa: E402

# =============================================================================
# CONFIG — edit here or (preferred) set these as environment variables / .env.
# These are read at runtime so secrets never live in the committed source.
# =============================================================================
DB_USER = os.environ.get("DB_USER", "rwa_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_DSN = os.environ.get("DB_DSN", "ora-host:1521/ORCLPDB")
DB_SCHEMA = os.environ.get("DB_SCHEMA", "RWA")
BATCH_SIZE = 1000
# =============================================================================

LOGS_DIR = REPO_ROOT / "logs"
SQL_DIR = REPO_ROOT / "sql"

# Base + mapping tables that get loaded into Oracle. Each is a CREATE TABLE +
# bulk insert; the SQL pipeline then creates the intermediate/final tables.
BASE_TABLE_NAMES = [
    "convergence",
    "balance_sheet_cg",
    "balance_sheet_cbna",
    "pug_mapping",
    "pmf_rwa_mapping",
    "quarter_map",
]

# Final tables exported to xlsx after the pipeline runs.
EXPORT_TABLES = [
    "control_summary",
    "outlook_rwa",
    "upload_template_pivot",
    "waterfall_rwf",
]


def _load_env_file() -> None:
    """Minimal .env reader (KEY=VALUE lines) so users need no extra dependency.

    Does not override variables already set in the environment.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def build_base_tables() -> dict[str, pd.DataFrame]:
    """Build the base/mapping DataFrames from the integration-test fixture.

    In production these come from the Excel inputs; here we reuse the same
    fixture the DuckDB pipeline and the integration test use so the loader is
    runnable end-to-end without external data.
    """
    import sql_fixture as sf  # noqa: PLC0415

    inputs = sf.build_fixture_inputs()
    cfg = inputs.pop("_config")
    q0 = cfg["parameters"]["Q0"]
    n_quarters = max(8, int(inputs["convergence"]["Quarter Id"].max()) + 1)
    return {
        "convergence": inputs["convergence"],
        "balance_sheet_cg": inputs["balance_sheet_cg"],
        "balance_sheet_cbna": inputs["balance_sheet_cbna"],
        "pug_mapping": inputs["pug_mapping"],
        "pmf_rwa_mapping": inputs["pmf_rwa_mapping"],
        "quarter_map": rdp.build_quarter_map(q0, n_quarters),
    }


def generate_ddl_and_logs(tables: dict[str, pd.DataFrame],
                          schema: str = DB_SCHEMA) -> dict[str, dict]:
    """Generate DDL files (sql/oracle/) + mapping logs (logs/) for each base table.

    Pure offline operation — no DB connection required. Returns
    {table: {"ddl": str, "mapping": dict, "ddl_path": Path, "log_path": Path}}.
    """
    ddl_dir = SQL_DIR / "oracle"
    ddl_dir.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}
    for name, df in tables.items():
        mapping = odl.build_mapping(df)
        ddl = odl.generate_ddl(df, name.upper(), schema=schema, mapping=mapping)
        ddl_path = ddl_dir / f"ddl_{name}.sql"
        ddl_path.write_text(ddl + ";\n", encoding="utf-8")
        log_path = LOGS_DIR / f"column_mapping_{name}.csv"
        odl.write_mapping_log_with_dtypes(df, mapping, log_path)
        out[name] = {
            "ddl": ddl, "mapping": mapping,
            "ddl_path": ddl_path, "log_path": log_path,
        }
        def _rel(p: Path) -> str:
            try:
                return str(p.relative_to(REPO_ROOT))
            except ValueError:
                return str(p)

        print(f"  {name}: DDL -> {_rel(ddl_path)}, log -> {_rel(log_path)}")
    return out


def run_offline() -> int:
    """Generate DDL + mapping logs without any Oracle connection."""
    print("== Offline mode: DDL + column-mapping logs only ==")
    tables = build_base_tables()
    generate_ddl_and_logs(tables)
    print("\nOffline artifacts written. No Oracle connection was made.")
    return 0


def run_live() -> int:
    """Connect to Oracle, create tables, bulk-load, run the SQL pipeline, export."""
    _load_env_file()
    user = os.environ.get("DB_USER", DB_USER)
    password = os.environ.get("DB_PASSWORD", DB_PASSWORD)
    dsn = os.environ.get("DB_DSN", DB_DSN)
    schema = os.environ.get("DB_SCHEMA", DB_SCHEMA)

    import oracledb  # noqa: PLC0415  (lazy: keep module importable without Oracle)

    print(f"== Live mode: connecting to {dsn} as {user} (schema {schema}) ==")
    tables = build_base_tables()
    artifacts = generate_ddl_and_logs(tables, schema=schema)

    con = oracledb.connect(user=user, password=password, dsn=dsn)  # thin mode
    cur = con.cursor()
    try:
        for name in BASE_TABLE_NAMES:
            df = tables[name]
            info = artifacts[name]
            created = odl.create_table_if_not_exists(
                cur, schema, name.upper(), info["ddl"])
            print(f"  {name}: {'created' if created else 'exists'}")
            odl.bulk_insert(cur, df, f"{schema}.{name.upper()}",
                            info["mapping"], batch_size=BATCH_SIZE)
            print(f"  {name}: loaded {len(df)} rows")

        print("Running SQL pipeline on Oracle ...")
        for fname in rdp.PIPELINE_ORDER:
            cur.execute((SQL_DIR / fname).read_text(encoding="utf-8"))
        con.commit()

        out_dir = REPO_ROOT / "output" / "oracle"
        out_dir.mkdir(parents=True, exist_ok=True)
        for tbl in EXPORT_TABLES:
            df = pd.read_sql(f"SELECT * FROM {schema}.{tbl.upper()}", con)
            df.to_excel(out_dir / f"{tbl}.xlsx", index=False)
            print(f"  exported {tbl} -> output/oracle/{tbl}.xlsx")
    finally:
        cur.close()
        con.close()
    print("Live Oracle load complete.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point: --offline generates DDL/logs only; default attempts live load."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true",
                        help="Generate DDL + mapping logs only (no DB connection)")
    args = parser.parse_args(argv)
    return run_offline() if args.offline else run_live()


if __name__ == "__main__":
    raise SystemExit(main())
