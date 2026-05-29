"""Run the rendered SQL pipeline on DuckDB and (optionally) export final tables.

DuckDB is the primary engine for Track D: in-process, file-based, zero-config,
and it executes the same SQL the Oracle loader runs. This module:

  1. Loads the base/mapping tables (convergence, balance_sheet_cg/_cbna,
     pug_mapping, pmf_rwa_mapping, quarter_map) into a DuckDB connection.
  2. Executes sql/*.sql in dependency order (each file creates one table).
  3. Returns the connection so callers can SELECT any intermediate or final
     table into a pandas DataFrame for verification or export.

It is import-safe (no side effects at import) so pytest can drive it.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
from dateutil.relativedelta import relativedelta

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SQL_DIR = REPO_ROOT / "sql"
for _p in (str(SRC), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Order matters: each statement depends only on tables created before it.
PIPELINE_ORDER = [
    "conv_credit_risk.sql",
    "conv_markets_addon.sql",
    "conv_non_waterfall.sql",
    "waterfall_rwf.sql",
    "outlook_long.sql",
    "outlook_with_keys.sql",
    "outlook_with_rwf.sql",
    "outlook_rwa.sql",
    "addon_pivot.sql",
    "frm_base.sql",
    "upload_template_pivot.sql",
    "control_summary.sql",
]


def build_quarter_map(q0: str, n_quarters: int) -> pd.DataFrame:
    """Build the quarter_map base table (quarter_id, year, month_abbr) from Q0.

    Mirrors functions.build_quarter_mappings: quarter k is Q0 + 3*k months, and
    the label is that quarter-end month's abbreviation (e.g. 'Mar'). This is a
    *configuration*-derived mapping (Q0 comes from config.yaml, not transforms.py),
    so it is loaded as a base table rather than injected into SQL.
    """
    q0_date = datetime.strptime(q0, "%b %Y")
    rows = []
    for k in range(n_quarters):
        d = q0_date + relativedelta(months=3 * k)
        rows.append({"quarter_id": k, "year": d.year, "month_abbr": d.strftime("%b")})
    return pd.DataFrame(rows)


def load_base_tables(con: duckdb.DuckDBPyConnection, tables: dict[str, pd.DataFrame]) -> None:
    """Register each DataFrame as a DuckDB table by name.

    `tables` maps table_name -> DataFrame for the base + mapping tables the SQL
    pipeline reads (convergence, balance_sheet_cg, balance_sheet_cbna,
    pug_mapping, pmf_rwa_mapping, quarter_map).
    """
    for name, df in tables.items():
        con.register(f"_df_{name}", df)
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _df_{name}')
        con.unregister(f"_df_{name}")


def run_pipeline(con: duckdb.DuckDBPyConnection, sql_dir: Path = SQL_DIR) -> None:
    """Execute the rendered SQL files in dependency order against `con`."""
    for fname in PIPELINE_ORDER:
        path = sql_dir / fname
        if not path.exists():
            raise FileNotFoundError(
                f"Missing generated SQL {path}. Run scripts/generate_sql.py first."
            )
        con.execute(path.read_text(encoding="utf-8"))


def build_connection_from_fixture(seed: int = 42) -> duckdb.DuckDBPyConnection:
    """End-to-end helper: fixture -> base tables -> run pipeline -> return con."""
    import sql_fixture as sf  # noqa: PLC0415

    inputs = sf.build_fixture_inputs(seed=seed)
    cfg = inputs.pop("_config")
    q0 = cfg["parameters"]["Q0"]
    # The fixture spans 4 quarters of convergence data; allow headroom so the
    # quarter_map covers every (year, month) present in the balance sheets.
    n_quarters = max(8, int(inputs["convergence"]["Quarter Id"].max()) + 1)
    base = {
        "convergence": inputs["convergence"],
        "balance_sheet_cg": inputs["balance_sheet_cg"],
        "balance_sheet_cbna": inputs["balance_sheet_cbna"],
        "pug_mapping": inputs["pug_mapping"],
        "pmf_rwa_mapping": inputs["pmf_rwa_mapping"],
        "quarter_map": build_quarter_map(q0, n_quarters),
    }
    con = duckdb.connect()
    load_base_tables(con, base)
    run_pipeline(con)
    return con


def table_to_df(con: duckdb.DuckDBPyConnection, table: str) -> pd.DataFrame:
    """Return a DuckDB table as a pandas DataFrame."""
    return con.execute(f'SELECT * FROM "{table}"').df()


def main() -> int:
    """Run the DuckDB pipeline on the fixture and print final-table row counts."""
    con = build_connection_from_fixture()
    for tbl in ("control_summary", "outlook_rwa", "upload_template_pivot", "waterfall_rwf"):
        n = con.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0]
        print(f"  {tbl}: {n} rows")
    print("DuckDB pipeline ran successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
