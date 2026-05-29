"""Track D coverage: SQLGlot generation, DuckDB numeric equality, Oracle DDL.

These tests exercise the SQL/Oracle pipeline end-to-end against the SAME fixture
the integration test uses, and assert the DuckDB output reproduces the Python
pipeline's numeric output (the equality oracle). Float summation order differs
between pandas and DuckDB, so numeric comparisons use a documented tolerance.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
SRC = REPO_ROOT / "src"
for _p in (str(SCRIPTS), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

duckdb = pytest.importorskip("duckdb")
sqlglot = pytest.importorskip("sqlglot")

# Documented numeric tolerances. The DuckDB and pandas engines sum the same
# values in different orders, so exact float equality is not expected; the
# observed relative error is ~1e-16 (machine epsilon) on values up to ~1e8.
REL_TOL = 1e-9
ABS_TOL = 1e-3


def _generate():
    import generate_sql  # noqa: PLC0415
    return generate_sql


def test_generate_sql_runs_clean_and_injects_transforms(tmp_path, monkeypatch):
    """generate_sql renders all templates and injects values from transforms/constants."""
    gen = _generate()
    rc = gen.main()
    assert rc == 0

    # Every template produced a .sql file.
    templates = sorted((REPO_ROOT / "sql" / "templates").glob("*.sql.j2"))
    for tpl in templates:
        out = REPO_ROOT / "sql" / tpl.name[: -len(".j2")]
        assert out.exists(), f"{out} not generated"

    # PMF credit-risk list (constants.PMF_ACCOUNTS) is injected into the IN clause.
    from outlook_rwa.constants import PMF_ACCOUNTS  # noqa: PLC0415
    ccr = (REPO_ROOT / "sql" / "conv_credit_risk.sql").read_text()
    for acct in PMF_ACCOUNTS:
        assert f"'{acct}'" in ccr, f"{acct} missing from generated conv_credit_risk.sql"

    # UNPIVOT columns come from transforms.QUARTERLY_PERIODS source columns.
    from outlook_rwa.transforms import QUARTERLY_PERIODS  # noqa: PLC0415
    ol = (REPO_ROOT / "sql" / "outlook_long.sql").read_text()
    for period in QUARTERLY_PERIODS:
        src = period.get("source_col") or period["source_cols"][-1]
        assert f"SUM({src})" in ol, f"{src} missing from outlook_long.sql"
        assert f"'{period['label']}'" in ol


def test_generated_sql_parses_for_duckdb():
    """Every generated statement parses under the DuckDB dialect (SQLGlot)."""
    _generate().main()
    for path in sorted((REPO_ROOT / "sql").glob("*.sql")):
        text = path.read_text()
        # Should not raise.
        list(sqlglot.parse(text, dialect="duckdb"))


def test_duckdb_pipeline_matches_python_control_summary():
    """control_summary (convergence control totals) matches the Python golden."""
    import run_duckdb_pipeline as rdp  # noqa: PLC0415
    import sql_fixture as sf  # noqa: PLC0415

    _generate().main()
    inputs = sf.build_fixture_inputs()
    golden = sf.golden_convergence_control(inputs["convergence"], inputs["_config"])
    con = rdp.build_connection_from_fixture()
    actual = rdp.table_to_df(con, "control_summary")

    def keyed(df):
        df = df.copy()
        df["quarter_id"] = df["quarter_id"].astype(int)
        df["k"] = (df["entity"] + "|" + df["segment_l2"] + "|"
                   + df["rwa_calc"] + "|" + df["quarter_id"].astype(str))
        return df.set_index("k")["rwa_amount"]

    g = keyed(golden)
    a = keyed(actual)
    assert set(g.index) == set(a.index), "control_summary keys differ from Python"
    merged = pd.concat([g.rename("g"), a.rename("a")], axis=1)
    diff = (merged["g"] - merged["a"]).abs()
    rel = diff / merged["g"].abs().clip(lower=1.0)
    assert (diff <= ABS_TOL).all() or (rel <= REL_TOL).all(), (
        f"control_summary numeric mismatch: max abs {diff.max()}, max rel {rel.max()}"
    )


def test_duckdb_pipeline_matches_python_waterfall_rwf():
    """waterfall_rwf Key1 SA/AA RWF matches the Python golden (exact)."""
    import run_duckdb_pipeline as rdp  # noqa: PLC0415
    import sql_fixture as sf  # noqa: PLC0415

    _generate().main()
    inputs = sf.build_fixture_inputs()
    golden = sf.golden_waterfall_rwf_key1(inputs["convergence"], inputs["_config"])
    golden = golden.rename(columns={
        "Quarter Id": "quarter_id",
        "Managed Segment Level 4 Code": "seg_l4_code",
        "Managed Geography Level 4 Description": "geo_l4_desc",
        "Finance PMF Level 5 Description": "pmf_l5",
        "Managed Segment Level 2 Description": "seg_l2_desc",
        "SA RWF": "sa_rwf", "AA RWF": "aa_rwf",
    })
    con = rdp.build_connection_from_fixture()
    actual = rdp.table_to_df(con, "waterfall_rwf")

    keys = ["entity", "quarter_id", "seg_l4_code", "geo_l4_desc", "pmf_l5", "seg_l2_desc"]

    def kf(df):
        df = df.copy()
        df["k"] = df[keys].astype(str).agg("|".join, axis=1)
        return df.set_index("k")

    g = kf(golden)
    a = kf(actual)
    assert set(g.index) == set(a.index)
    for col in ("sa_rwf", "aa_rwf"):
        merged = pd.concat([g[col].rename("g"), a[col].rename("a")], axis=1)
        both = merged.dropna()
        diff = (both["g"] - both["a"]).abs()
        assert (diff <= ABS_TOL).all(), f"{col} mismatch: max abs {diff.max()}"
        # NULLs (Markets rows) must align on both sides.
        assert (merged["g"].isna() == merged["a"].isna()).all(), f"{col} null mask differs"


def test_oracle_ddl_offline_generation(tmp_path, monkeypatch):
    """Oracle DDL + column-mapping logs generate offline without a connection."""
    import load_oracle  # noqa: PLC0415

    tables = load_oracle.build_base_tables()
    monkeypatch.setattr(load_oracle, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(load_oracle, "SQL_DIR", tmp_path / "sql")
    artifacts = load_oracle.generate_ddl_and_logs(tables, schema="RWA")

    assert set(artifacts) == set(load_oracle.BASE_TABLE_NAMES)
    for name, info in artifacts.items():
        assert info["ddl_path"].exists()
        assert info["log_path"].exists()
        assert info["ddl"].startswith("CREATE TABLE RWA.")
        log = pd.read_csv(info["log_path"])
        assert {"original", "oracle_name", "oracle_type"} <= set(log.columns)
        # All Oracle identifiers respect the 30-char limit.
        assert (log["oracle_name"].str.len() <= 30).all()
        # VARCHAR2 widths cover the observed max length.
        v = log[log["oracle_type"].str.startswith("VARCHAR2", na=False)]
        for _, r in v.iterrows():
            width = int(r["oracle_type"].split("(")[1].rstrip(")"))
            if pd.notna(r["max_observed_length"]) and str(r["max_observed_length"]) != "":
                assert width >= int(r["max_observed_length"])


def test_oracle_type_inference_rules():
    """infer_oracle_type maps pandas dtypes to the documented Oracle types."""
    import oracle_ddl as odl  # noqa: PLC0415

    assert odl.infer_oracle_type(pd.Series([1, 2, 3], dtype="int64"))[0] == "NUMBER(18,0)"
    assert odl.infer_oracle_type(pd.Series([1.0, 2.0], dtype="float64"))[0] == "FLOAT"
    assert odl.infer_oracle_type(pd.Series([True, False]))[0] == "CHAR(1)"
    assert odl.infer_oracle_type(
        pd.to_datetime(pd.Series(["2024-01-01"])))[0] == "TIMESTAMP(6)"
    vtype, meta = odl.infer_oracle_type(pd.Series(["abcde", "fg"]))
    assert vtype == "VARCHAR2(15)"  # max observed 5 + 10 padding
    assert meta["max_observed_length"] == 5
