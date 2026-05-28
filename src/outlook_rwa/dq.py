"""Data-quality checks for the Outlook RWA pipeline.

Each check function returns a DQResult. run_all_checks bundles them into a
single DataFrame exported by export_dq_results as both Parquet and Excel.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .constants import (
    AA_RWF,
    FINANCE_PMF_LEVEL_5_DESC,
    MANAGED_SEGMENT_L4_DESCR,
    MNGD_SGMT_L2_DESC,
    PMF_ACCOUNT_L5_DESCR,
    QUARTER_ID,
    REPORTABLE_ENTITY_IS_CBNA,
    REPORTABLE_ENTITY_IS_CG,
    SA_ACCOUNT_NUM,
    SA_RWF,
    convergence_polars_dtypes,
)


@dataclass
class DQResult:
    check_name: str
    dataset: str
    status: str   # "PASS" | "WARN" | "FAIL"
    detail: str
    n_failing: int = 0
    n_total: int = 0

    @property
    def pct_failing(self) -> float:
        return round(self.n_failing / self.n_total * 100, 2) if self.n_total else 0.0


def _status(n_failing: int, n_total: int, warn_pct: float = 0.0, fail_pct: float = 5.0) -> str:
    if n_total == 0:
        return "WARN"
    pct = n_failing / n_total * 100
    if pct > fail_pct:
        return "FAIL"
    if pct > warn_pct:
        return "WARN"
    return "PASS"


def _check_row_count(df: pd.DataFrame, dataset: str) -> DQResult:
    n = len(df)
    return DQResult(
        check_name="row_count_nonzero", dataset=dataset,
        status="FAIL" if n == 0 else "PASS",
        detail=f"{n:,} rows",
        n_failing=1 if n == 0 else 0, n_total=1,
    )


def _check_required_cols(df: pd.DataFrame, required: list, dataset: str) -> DQResult:
    missing = [c for c in required if c not in df.columns]
    if missing:
        shown = missing[:5]
        suffix = f" … and {len(missing) - 5} more" if len(missing) > 5 else ""
        return DQResult(
            check_name="required_columns", dataset=dataset, status="FAIL",
            detail=f"Missing {len(missing)}/{len(required)} columns: {shown}{suffix}",
            n_failing=len(missing), n_total=len(required),
        )
    return DQResult(
        check_name="required_columns", dataset=dataset, status="PASS",
        detail=f"All {len(required)} required columns present",
        n_failing=0, n_total=len(required),
    )


def _check_null_rate(
    df: pd.DataFrame, col: str, dataset: str, check_name: str,
    warn_pct: float = 0.0, fail_pct: float = 5.0,
) -> DQResult:
    if col not in df.columns:
        return DQResult(check_name=check_name, dataset=dataset, status="WARN",
                        detail=f"Column '{col}' not found — skipped", n_failing=0, n_total=0)
    n = len(df)
    n_null = int(df[col].isna().sum())
    pct = n_null / n * 100 if n else 0.0
    return DQResult(
        check_name=check_name, dataset=dataset,
        status=_status(n_null, n, warn_pct, fail_pct),
        detail=f"{n_null:,}/{n:,} ({pct:.1f}%) null values in '{col}'",
        n_failing=n_null, n_total=n,
    )


def _check_allowed_values(
    df: pd.DataFrame, col: str, allowed: set, dataset: str, check_name: str,
) -> DQResult:
    if col not in df.columns:
        return DQResult(check_name=check_name, dataset=dataset, status="WARN",
                        detail=f"Column '{col}' not found — skipped", n_failing=0, n_total=0)
    n = len(df)
    bad_mask = df[col].notna() & ~df[col].isin(allowed)
    n_bad = int(bad_mask.sum())
    bad_vals = df.loc[bad_mask, col].unique()[:5].tolist()
    return DQResult(
        check_name=check_name, dataset=dataset,
        status=_status(n_bad, n),
        detail=(
            f"{n_bad:,}/{n:,} rows have unexpected values (sample: {bad_vals})"
            if n_bad else f"All values in allowed set {sorted(allowed)}"
        ),
        n_failing=n_bad, n_total=n,
    )


def _check_waterfall_keys(df: pd.DataFrame, dataset: str, n_keys: int) -> list:
    """Check match rate at each key level and overall fallthrough rate."""
    results = []
    n = len(df)
    if n == 0:
        return results

    for rwf_col, label in [(SA_RWF, "SA"), (AA_RWF, "AA")]:
        if rwf_col in df.columns:
            matched = int(pd.to_numeric(df[rwf_col], errors="coerce").notna().sum())
            results.append(DQResult(
                check_name=f"waterfall_key1_{label.lower()}_match_rate", dataset=dataset,
                status="PASS",
                detail=f"Key1 {label} RWF: {matched:,}/{n:,} ({matched/n*100:.1f}%) matched",
                n_failing=n - matched, n_total=n,
            ))

        for i in range(2, n_keys + 1):
            col = f"{rwf_col}_key{i}"
            if col not in df.columns:
                continue
            matched = int(pd.to_numeric(df[col], errors="coerce").notna().sum())
            results.append(DQResult(
                check_name=f"waterfall_key{i}_{label.lower()}_match_rate", dataset=dataset,
                status="PASS",
                detail=f"Key{i} {label} RWF: {matched:,}/{n:,} ({matched/n*100:.1f}%) matched",
                n_failing=n - matched, n_total=n,
            ))

        final_col = f"FINAL_{label}_RWF"
        if final_col in df.columns:
            n_null = int(pd.to_numeric(df[final_col], errors="coerce").isna().sum())
            pct = n_null / n * 100
            results.append(DQResult(
                check_name=f"waterfall_{label.lower()}_fallthrough_rate", dataset=dataset,
                status=_status(n_null, n, warn_pct=0.0, fail_pct=5.0),
                detail=f"{n_null:,}/{n:,} ({pct:.1f}%) rows fell through all {n_keys} {label} RWF keys",
                n_failing=n_null, n_total=n,
            ))

    return results


def _check_join_match_rate(
    df: pd.DataFrame, left_key_col: str, joined_col: str,
    dataset: str, check_name: str,
    warn_pct: float = 0.0, fail_pct: float = 5.0,
) -> DQResult:
    """Among rows with a non-null left key, check what fraction have a non-null joined column."""
    if joined_col not in df.columns:
        return DQResult(check_name=check_name, dataset=dataset, status="WARN",
                        detail=f"Joined column '{joined_col}' not found — join may not have run",
                        n_failing=0, n_total=0)
    has_key = (
        df[left_key_col].notna()
        if left_key_col in df.columns
        else pd.Series([True] * len(df), index=df.index)
    )
    n = int(has_key.sum())
    n_unmatched = int((has_key & df[joined_col].isna()).sum())
    pct = n_unmatched / n * 100 if n else 0.0
    return DQResult(
        check_name=check_name, dataset=dataset,
        status=_status(n_unmatched, n, warn_pct, fail_pct),
        detail=f"{n_unmatched:,}/{n:,} ({pct:.1f}%) rows with a key have no join match",
        n_failing=n_unmatched, n_total=n,
    )


def _check_quarter_coverage(df: pd.DataFrame, dataset: str) -> DQResult:
    expected = {1, 2, 3, 4, 5, 6, 7}
    if QUARTER_ID not in df.columns:
        return DQResult(
            check_name="quarter_coverage", dataset=dataset, status="WARN",
            detail=f"'{QUARTER_ID}' column not found", n_failing=0, n_total=0,
        )
    present = set(
        pd.to_numeric(df[QUARTER_ID], errors="coerce").dropna().astype(int).unique()
    )
    missing = expected - present
    return DQResult(
        check_name="quarter_coverage", dataset=dataset,
        status="FAIL" if missing else "PASS",
        detail=f"Quarters present: {sorted(present)}; missing: {sorted(missing) if missing else 'none'}",
        n_failing=len(missing), n_total=len(expected),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_all_checks(
    *,
    convergence: pd.DataFrame,
    cg_outlook: pd.DataFrame,
    cbna_outlook: pd.DataFrame,
    cg_adjustments: pd.DataFrame,
    cbna_adjustments: pd.DataFrame,
    frm_output_cg: pd.DataFrame,
    frm_output_cbna: pd.DataFrame,
    n_keys: int,
) -> pd.DataFrame:
    """Run all DQ checks and return a DataFrame with one row per check."""
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[DQResult] = []

    # Row counts — basic sanity that no input is empty
    for label, df in [
        ("convergence",      convergence),
        ("cg_outlook",       cg_outlook),
        ("cbna_outlook",     cbna_outlook),
        ("cg_adjustments",   cg_adjustments),
        ("cbna_adjustments", cbna_adjustments),
        ("cg_frm_output",    frm_output_cg),
        ("cbna_frm_output",  frm_output_cbna),
    ]:
        results.append(_check_row_count(df, label))

    # Convergence schema
    results.append(_check_required_cols(convergence, list(convergence_polars_dtypes), "convergence"))

    # Convergence entity flags: must be Y or N
    for col in (REPORTABLE_ENTITY_IS_CG, REPORTABLE_ENTITY_IS_CBNA):
        results.append(_check_allowed_values(convergence, col, {"Y", "N"}, "convergence", "entity_flag_values"))

    # Convergence key column null rates
    for col, name in [
        ("Projected Quarter",      "projected_quarter_nulls"),
        (FINANCE_PMF_LEVEL_5_DESC, "finance_pmf_l5_nulls"),
        (MNGD_SGMT_L2_DESC,        "mngd_sgmt_l2_nulls"),
    ]:
        results.append(_check_null_rate(convergence, col, "convergence", name))

    # Waterfall key match rates + fallthrough — most important diagnostic
    results.extend(_check_waterfall_keys(cg_outlook,   "cg_outlook",   n_keys))
    results.extend(_check_waterfall_keys(cbna_outlook, "cbna_outlook", n_keys))

    # Adjustment raw data: null rates on columns that feed Key1 build
    for label, adj_df in [("cg_adjustments", cg_adjustments), ("cbna_adjustments", cbna_adjustments)]:
        results.append(_check_null_rate(adj_df, QUARTER_ID,         label, "quarter_id_nulls"))
        results.append(_check_null_rate(adj_df, PMF_ACCOUNT_L5_DESCR, label, "pmf_l5_nulls", fail_pct=30.0))

    # PUG / PMF join match rates (checked before format_columns_before_pivots
    # fills NaN with 'None', so nulls here are genuine misses)
    for label, df in [("cg_frm_output", frm_output_cg), ("cbna_frm_output", frm_output_cbna)]:
        results.append(_check_join_match_rate(df, MANAGED_SEGMENT_L4_DESCR, "PUG",       label, "pug_join_match_rate"))
        results.append(_check_join_match_rate(df, PMF_ACCOUNT_L5_DESCR,     SA_ACCOUNT_NUM, label, "pmf_join_match_rate"))

    # Quarter coverage in final concatenated output
    for label, df in [("cg_frm_output", frm_output_cg), ("cbna_frm_output", frm_output_cbna)]:
        results.append(_check_quarter_coverage(df, label))

    return pd.DataFrame([
        {
            "check_name":    r.check_name,
            "dataset":       r.dataset,
            "status":        r.status,
            "detail":        r.detail,
            "n_failing":     r.n_failing,
            "n_total":       r.n_total,
            "pct_failing":   r.pct_failing,
            "run_timestamp": ts,
        }
        for r in results
    ])


def export_dq_results(results_df: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    """Write DQ results as Parquet and Excel. Returns (parquet_path, xlsx_path)."""
    parquet_path = output_dir / "dq_results.parquet"
    xlsx_path    = output_dir / "dq_results.xlsx"
    results_df.to_parquet(parquet_path, index=False)
    results_df.to_excel(xlsx_path, index=False, sheet_name="DQ Results")
    return parquet_path, xlsx_path
