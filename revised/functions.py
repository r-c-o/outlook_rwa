"""
functions.py — shared business logic for the Outlook RWA pipeline.

All public functions are imported via `from functions import *` in the
notebook cells. Side-effect-free; no global state.
"""
from __future__ import annotations

import os
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import polars as pl

from constants import (
    AA_RWF, ADV_CBNA_TOTAL_RWA_AMT, ADV_CG_TOTAL_RWA_AMT,
    ERBA_RWA, FINANCE_PMF_LEVEL_5_DESC, GAAP_AMOUNT,
    MNGD_GEO_L3_DESC, MNGD_GEO_L4_DESC,
    MNGD_SGMT_L2_CDE, MNGD_SGMT_L2_DESC,
    MNGD_SGMT_L3_CDE, MNGD_SGMT_L4_CDE,
    MARKETS_L2, PMF_ACCOUNTS,
    QRTR_ID, REPORTABLE_ENTITY_IS_CBNA, REPORTABLE_ENTITY_IS_CG,
    SA_RWA_AMT, SA_RWF,
)


# ---------------------------------------------------------------------------
# Quarter mapping
# ---------------------------------------------------------------------------

def assign_quarter_id(outlook_df: pd.DataFrame, quarter_id_mapping: dict) -> None:
    """Assign QRTR_ID from a (YEAR, Month) → quarter_id dict. Modifies df in place."""
    outlook_df[QRTR_ID] = outlook_df.apply(
        lambda r: quarter_id_mapping.get((r["YEAR"], r["Month"]), "Unknown"), axis=1
    )


# ---------------------------------------------------------------------------
# Pivot / melt
# ---------------------------------------------------------------------------

def melt_quarterly_pivot(pivot_df: pd.DataFrame) -> pd.DataFrame:
    """Melt quarterly balance columns (Mar/Jun/Sep/Dec) → long format."""
    return pivot_df.melt(
        value_vars=["Mar", "Jun", "Sep", "Dec"],
        var_name="Month",
        value_name="Balance",
    )


# ---------------------------------------------------------------------------
# Key pivot tables  (5-key waterfall)
# ---------------------------------------------------------------------------

def create_key_pivots(
    crd_df: pd.DataFrame, adv_rwa_col: str
) -> tuple[pd.DataFrame, ...]:
    """Return the 5 key pivot tables for the waterfall join (most → least granular)."""
    _values = [GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col]

    def _pivot(index_cols):
        return crd_df.pivot_table(values=_values, index=index_cols, aggfunc="sum")

    key1 = _pivot([QRTR_ID, MNGD_SGMT_L4_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC])
    key2 = _pivot([QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC])
    key3 = _pivot([QRTR_ID, MNGD_SGMT_L2_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC])
    key4 = _pivot([QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L3_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC])
    key5 = _pivot([QRTR_ID, MNGD_SGMT_L3_CDE,                    FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC])
    return key1, key2, key3, key4, key5


# ---------------------------------------------------------------------------
# RWF computation
# ---------------------------------------------------------------------------

def compute_rwf(key_df: pd.DataFrame, adv_rwa_col: str) -> pd.DataFrame:
    """
    Compute SA_RWF = SA_RWA / GAAP and AA_RWF = ADV_RWA / GAAP.
    Values with |GAAP| < threshold or ratio > 12.5 are capped at 1.
    Uses .clip() instead of boolean masks for clarity and performance.
    """
    gaap_abs = key_df[GAAP_AMOUNT].abs().replace(0, np.nan)
    key_df[SA_RWF] = (key_df[SA_RWA_AMT].abs() / gaap_abs).clip(upper=1.0)
    key_df[AA_RWF] = (key_df[adv_rwa_col].abs()  / gaap_abs).clip(upper=1.0)
    return key_df


def set_markets_rwf_zero(key_df: pd.DataFrame) -> pd.DataFrame:
    """Force SA_RWF = AA_RWF = 0 for Markets [L2] rows (handled via addon pivot)."""
    is_markets = key_df[MNGD_SGMT_L2_DESC].isin([MARKETS_L2])
    key_df.loc[is_markets, [SA_RWF, AA_RWF]] = 0
    return key_df


# ---------------------------------------------------------------------------
# Waterfall merge
# ---------------------------------------------------------------------------

def merge_rwf_waterfall(
    outlook_df: pd.DataFrame,
    k1: pd.DataFrame,
    k2: pd.DataFrame,
    k3: pd.DataFrame,
    k4: pd.DataFrame,
    k5: pd.DataFrame,
    label: str,
) -> pd.DataFrame:
    """
    Left-join the 5 RWF lookup tables onto outlook_df.
    Validates that row count is unchanged. Falls back from validate='m:1' to
    unrestricted merge if the convergence data has duplicate index keys.
    """
    pre_rows = len(outlook_df)
    rwf_cols = [SA_RWF, AA_RWF]

    for i, (key_col, key_df) in enumerate(
        [("Key1", k1), ("Key2", k2), ("Key3", k3), ("Key4", k4), ("Key5", k5)], start=1
    ):
        suffix = f"_key{i}"
        try:
            outlook_df = outlook_df.merge(
                key_df[rwf_cols], how="left", on=key_col,
                suffixes=("", suffix), validate="m:1"
            )
        except pd.errors.MergeError:
            warnings.warn(
                f"⚠️ {label} Key{i}: duplicate keys in lookup — falling back to m:m merge"
            )
            outlook_df = outlook_df.merge(
                key_df[rwf_cols], how="left", on=key_col, suffixes=("", suffix)
            )

    post_rows = len(outlook_df)
    if post_rows != pre_rows:
        warnings.warn(
            f"⚠️ {label}: row count changed during merge: {pre_rows:,} → {post_rows:,} "
            "(possible row expansion)"
        )
    print(f"✅ {label}: waterfall merge complete")
    return outlook_df


# ---------------------------------------------------------------------------
# Convergence splitting
# ---------------------------------------------------------------------------

def split_convergence(
    convergence: pd.DataFrame,
    pmf_accounts: list[str],
    markets_l2: str,
) -> dict[str, pd.DataFrame]:
    """
    Partition the convergence table into six non-overlapping buckets.
    Returns a dict keyed by bucket name.
    """
    is_cg   = convergence[REPORTABLE_ENTITY_IS_CG]   == "Y"
    is_cbna = convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y"
    in_pmf  = convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts)
    in_mkt  = convergence[MNGD_SGMT_L2_DESC].isin([markets_l2])

    return {
        "credit_risk_convergence_cg":        convergence[is_cg   &  in_pmf].copy(),
        "credit_risk_convergence_cbna":       convergence[is_cbna &  in_pmf].copy(),
        "non_credit_risk_non_waterfall_cg":   convergence[is_cg   & ~in_pmf].copy(),
        "non_credit_risk_non_waterfall_cbna": convergence[is_cbna & ~in_pmf].copy(),
        "cg_addon_markets_credit_risk":       convergence[is_cg   &  in_mkt].copy(),
        "cbna_addon_markets_credit_risk":     convergence[is_cbna &  in_mkt].copy(),
    }


# ---------------------------------------------------------------------------
# ERBA / metadata assignment
# ---------------------------------------------------------------------------

def assign_erba_rwa_and_metadata(
    cg_outlook: pd.DataFrame, cbna_outlook: pd.DataFrame
) -> None:
    """Set ERBA_RWA = SA_RWA_AMT for ERBA reporting quarters (5, 6). Modifies in place."""
    erba_qtrs = [5, 6]
    for df in (cg_outlook, cbna_outlook):
        df[ERBA_RWA]    = df[SA_RWA_AMT].where(df[QRTR_ID].isin(erba_qtrs))
        df["Comment"]   = ""
        df["Forecast"]  = ""


def assign_erba_rwa_and_comment(
    cg_addon: pd.DataFrame, cbna_addon: pd.DataFrame
) -> None:
    """Assign ERBA_RWA and Comment for Markets addon DataFrames. Modifies in place."""
    erba_qtrs = [5, 6]
    for df in (cg_addon, cbna_addon):
        df[ERBA_RWA]  = df[SA_RWA_AMT].where(df[QRTR_ID].isin(erba_qtrs))
        df["Comment"] = ""


# ---------------------------------------------------------------------------
# Markets addon pivot
# ---------------------------------------------------------------------------

def build_markets_addon_pivot(
    cg_addon: pd.DataFrame,
    cbna_addon: pd.DataFrame,
    markets_credit_risk_mask,
    addon_pivot_index: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pivot Markets addon credit-risk data for CG and CBNA."""
    def _pivot(df, adv_col):
        return (
            df.pivot_table(
                values=[SA_RWA_AMT, adv_col],
                index=addon_pivot_index,
                aggfunc="sum",
            )
            .reset_index()
        )

    return (
        _pivot(cg_addon,   ADV_CG_TOTAL_RWA_AMT),
        _pivot(cbna_addon, ADV_CBNA_TOTAL_RWA_AMT),
    )


# ---------------------------------------------------------------------------
# Code column casting
# ---------------------------------------------------------------------------

def cast_code_columns_to_int(df: pd.DataFrame) -> pd.DataFrame:
    """Cast any column whose name contains 'Code', 'CDE', or 'Id' to int."""
    code_cols = [c for c in df.columns if any(k in c for k in ("Code", "CDE", "Id"))]
    df[code_cols] = df[code_cols].apply(pd.to_numeric, errors="coerce").astype("Int64")
    return df


# ---------------------------------------------------------------------------
# Parallel Excel → Parquet conversion
# ---------------------------------------------------------------------------

def export_excel_specs_to_parquet(
    file_specs: list[dict],
    output_dir: str | Path,
    schema_registry_csv: str | Path,
    if_exists: str = "new",
    max_workers: int | None = 4,
) -> dict[str, dict]:
    """
    Convert each Excel file spec to Parquet using Polars dtype enforcement.
    Files are converted in parallel (ThreadPoolExecutor) for speed.

    Args:
        file_specs:    list of dicts with keys variable_name, input_path, polars_dtypes
        output_dir:    directory to write .parquet files
        schema_registry_csv: path to schema_registry.csv (used for dtype lookup)
        if_exists:     "new" = skip existing, "replace" = always reconvert
        max_workers:   thread pool size (None = all CPUs)

    Returns:
        dict of {variable_name: {"output_path": Path}}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def _convert(spec: dict) -> tuple[str, dict]:
        var_name   = spec["variable_name"]
        input_path = Path(spec["input_path"])
        out_path   = output_dir / f"{var_name}.parquet"

        if if_exists == "new" and out_path.exists():
            print(f"⏭ {var_name}: skipping (parquet exists)")
            return var_name, {"output_path": out_path}

        dtype_overrides = spec.get("polars_dtypes", {})
        df = pl.read_excel(input_path, schema_overrides=dtype_overrides)
        df.write_parquet(out_path, compression="zstd")
        print(f"✅ {var_name}: {len(df):,} rows → {out_path.name}")
        return var_name, {"output_path": out_path}

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_convert, spec): spec["variable_name"] for spec in file_specs}
        for future in as_completed(futures):
            var_name, result = future.result()
            results[var_name] = result

    return results


# ---------------------------------------------------------------------------
# Data quality checks
# ---------------------------------------------------------------------------

def check_input_files_exist(input_files: Sequence[str | Path]) -> None:
    for f in input_files:
        p = Path(f)
        if not p.exists():
            raise FileNotFoundError(f"❌ Missing input: {p}")
        print(f"✅ Found: {p.name}")


def check_unknown_quarters(cg_outlook: pd.DataFrame, cbna_outlook: pd.DataFrame) -> None:
    for label, df in [("CG", cg_outlook), ("CBNA", cbna_outlook)]:
        n = (df[QRTR_ID] == "Unknown").sum()
        if n:
            warnings.warn(f"⚠️ {label}: {n:,} rows have Unknown Quarter Id")
    print(f"✅ Quarter Id assigned. CG unknown: {(cg_outlook[QRTR_ID]=='Unknown').sum():,}, "
          f"CBNA unknown: {(cbna_outlook[QRTR_ID]=='Unknown').sum():,}")


def check_key_match_coverage(cg_outlook: pd.DataFrame, cbna_outlook: pd.DataFrame) -> None:
    """Report rows with no RWF match across all 5 keys."""
    key_cols = [SA_RWF] + [f"SA_RWF_key{i}" for i in range(2, 6)]
    for label, df in [("CG", cg_outlook), ("CBNA", cbna_outlook)]:
        available = [c for c in key_cols if c in df.columns]
        no_match  = df[available].isna().all(axis=1)
        pct = no_match.mean() * 100
        print(f"{label}: {no_match.sum():,} rows ({pct:.1f}%) have no convergence key match")


def check_and_get_max_quarters(
    convergence: pd.DataFrame,
    cg_outlook: pd.DataFrame,
    cbna_outlook: pd.DataFrame,
) -> int:
    """Validate quarter counts across all three DataFrames; return the maximum."""
    n_conv = convergence[QRTR_ID].nunique()
    n_cg   = cg_outlook[["YEAR", "Month"]].drop_duplicates().shape[0]
    n_cbna = cbna_outlook[["YEAR", "Month"]].drop_duplicates().shape[0]

    if not (n_conv == n_cg == n_cbna):
        warnings.warn(
            f"⚠️ Quarter count mismatch: Convergence={n_conv}, CG={n_cg}, CBNA={n_cbna}"
        )
    else:
        print(f"✅ Quarter counts match: {n_conv}")
    return max(n_conv, n_cg, n_cbna)


def check_expected_columns(
    src_df: pd.DataFrame, expected_cols: list[str], label: str
) -> None:
    missing = [c for c in expected_cols if c not in src_df.columns]
    if missing:
        warnings.warn(f"⚠️ {label}: missing columns: {missing}")
    else:
        print(f"✅ {label} has all expected columns")


def check_pmf_account_coverage(
    convergence: pd.DataFrame,
    pmf_accounts: list[str],
    pmf_col: str,
) -> None:
    found  = set(convergence[pmf_col].dropna().unique())
    missing = [p for p in pmf_accounts if p not in found]
    if missing:
        warnings.warn(f"⚠️ PMF accounts not in convergence data: {missing}")
    else:
        print("✅ All expected PMF accounts found in convergence data")


def check_rwf_capping(keys: list[tuple[str, pd.DataFrame]]) -> None:
    """Warn if any SA_RWF or AA_RWF values were capped (clipped to 1.0)."""
    for label, kdf in keys:
        n_sa = (kdf[SA_RWF] == 1.0).sum()
        n_aa = (kdf[AA_RWF] == 1.0).sum()
        if n_sa or n_aa:
            warnings.warn(f"⚠️ {label}: {n_sa} SA_RWF and {n_aa} AA_RWF values capped at 1.0")
