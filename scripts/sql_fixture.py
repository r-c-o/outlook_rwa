"""Shared fixture + golden-extraction helpers for the SQL/Oracle pipeline.

This module is the single place that materializes the integration-test fixture
into a set of base tables (as pandas DataFrames) and extracts the golden numeric
intermediates from the *Python* pipeline (functions.py). Both the DuckDB SQL
pipeline and the pytest verification consume these helpers, guaranteeing the SQL
engine is fed the exact same inputs the Python equality-oracle uses.

Nothing here re-expresses business logic — it reuses functions.py directly for
the golden values and reuses test.test_integration's fixture writers so the
numbers line up with the committed integration test.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def build_fixture_inputs(seed: int = 42) -> dict[str, pd.DataFrame]:
    """Materialize the integration-test fixture and return the raw input frames.

    Reuses test.test_integration's writers (same RNG seed/order) so the produced
    DataFrames are identical to what pipeline.main() consumes in the integration
    test. Returns a dict of {logical_name: DataFrame} plus a "_config" entry.
    """
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import test.test_integration as ti  # noqa: PLC0415

    tmp = Path(tempfile.mkdtemp(prefix="sql_fixture_"))
    input_dir = tmp / "data" / "input"
    input_dir.mkdir(parents=True)
    rng = np.random.default_rng(seed)
    # Order must match the fixture_dataset fixture exactly (RNG is shared).
    ti._write_pug_mapping(input_dir)
    ti._write_adjustment_master_file(input_dir, rng)
    ti._write_balancesheets(input_dir, rng)
    ti._write_convergence(input_dir, rng)
    ti._write_pmf_rwa_mapping(input_dir)

    convergence = pd.read_excel(input_dir / "aggregator_for_convergence.xlsx")
    balancesheet_cg = pd.read_excel(input_dir / "outlook_balancesheet_cg.xlsx")
    balancesheet_cbna = pd.read_excel(input_dir / "outlook_balancesheet_cbna.xlsx")
    pug = pd.read_excel(input_dir / "pug_mapping.xlsx")
    pmf_rwa = pd.read_excel(input_dir / "pmf_rwa_mapping.xlsx", sheet_name="Sheet1")
    cfg = ti._fixture_config(tmp / "data")

    return {
        "convergence": convergence,
        "balance_sheet_cg": balancesheet_cg,
        "balance_sheet_cbna": balancesheet_cbna,
        "pug_mapping": pug,
        "pmf_rwa_mapping": pmf_rwa,
        "_config": cfg,
    }


def golden_convergence_control(convergence: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Golden long-format convergence control totals from the Python pipeline.

    Runs functions.build_convergence_control for both entities and stacks the
    result into a tidy long frame keyed by (entity, segment_l2, rwa_calc,
    quarter_id) -> rwa_amount. This is the SQL pipeline's primary numeric oracle.
    """
    from outlook_rwa import functions as fns  # noqa: PLC0415
    from outlook_rwa.constants import (  # noqa: PLC0415
        ADV_CBNA_TOTAL_RWA_AMT,
        ADV_CG_TOTAL_RWA_AMT,
        MANAGED_SEGMENT_L2_DESCR,
        RWA_CALC,
        REPORTABLE_ENTITY_IS_CBNA,
        REPORTABLE_ENTITY_IS_CG,
    )

    out = []
    for entity, flag_col, adv_col in (
        ("CG", REPORTABLE_ENTITY_IS_CG, ADV_CG_TOTAL_RWA_AMT),
        ("CBNA", REPORTABLE_ENTITY_IS_CBNA, ADV_CBNA_TOTAL_RWA_AMT),
    ):
        wide = fns.build_convergence_control(convergence, flag_col, adv_col)
        qcols = [c for c in wide.columns if c not in (MANAGED_SEGMENT_L2_DESCR, RWA_CALC)]
        long = wide.melt(
            id_vars=[MANAGED_SEGMENT_L2_DESCR, RWA_CALC],
            value_vars=qcols,
            var_name="quarter_id",
            value_name="rwa_amount",
        )
        long.insert(0, "entity", entity)
        long = long.rename(columns={
            MANAGED_SEGMENT_L2_DESCR: "segment_l2",
            RWA_CALC: "rwa_calc",
        })
        out.append(long)
    res = pd.concat(out, ignore_index=True)
    res["quarter_id"] = pd.to_numeric(res["quarter_id"], errors="coerce").astype("Int64")
    res = res.dropna(subset=["rwa_amount"])
    return res.reset_index(drop=True)


def golden_waterfall_rwf_key1(convergence: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Golden Key1 SA/AA RWF table for both entities (post compute_rwf + markets).

    Mirrors create_key_pivots(key1) -> compute_rwf -> set_markets_rwf for CG and
    CBNA, returned long with an `entity` column. Used to verify the SQL
    waterfall_rwf intermediate.
    """
    from outlook_rwa import functions as fns  # noqa: PLC0415
    from outlook_rwa.constants import (  # noqa: PLC0415
        ADV_CBNA_TOTAL_RWA_AMT,
        ADV_CG_TOTAL_RWA_AMT,
        AA_RWF,
        MARKETS_L2,
        PMF_ACCOUNTS,
        SA_RWF,
    )

    key_defs = cfg["parameters"]["waterfall_keys"]
    (crg, crc, *_rest) = fns.split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2)
    out = []
    for entity, crd, adv in (("CG", crg, ADV_CG_TOTAL_RWA_AMT),
                             ("CBNA", crc, ADV_CBNA_TOTAL_RWA_AMT)):
        pivots = fns.create_key_pivots(crd, adv, key_defs)
        k1 = pivots[0]
        fns.compute_rwf(k1, adv)
        fns.set_markets_rwf(k1)
        df = k1.reset_index()[[*k1.index.names, SA_RWF, AA_RWF]].copy()
        df.insert(0, "entity", entity)
        out.append(df)
    return pd.concat(out, ignore_index=True)
