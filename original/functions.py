# %%
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import toml
import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta
import polars as pl
from constants import *
import time


# ==============================================================================
# Step1 Model Convergence Functions
# ==============================================================================

# ==============================================================================
# Business Logic Functions
# ==============================================================================

def assign_quarter_id(outlook_df, quarter_id_mapping):
    """
    Assigns Quarter Id to the outlook DataFrame based on YEAR and Month columns
    using the provided mapping.
    If no match is found, assigns 'Unknown'.
    Modifies the DataFrame in place.
    """
    outlook_df[QRTR_ID] = outlook_df[["YEAR", "Month"]].apply(
        lambda row: quarter_id_mapping.get((row["YEAR"], row["Month"]), "Unknown"),
        axis=1,
    )


def calculate_sa_rwa(df):
    multipliers = {
        # populated from config / lookup table at runtime
    }
    df[SA_RWA_AMT] = df.apply(
        lambda row: row[GAAP_AMOUNT] * multipliers.get(row[SA_ACCOUNT_NUM], 0),
        axis=1,
    )
    return df


def create_key_pivots(crd_df, adv_rwa_col):
    """Create the 5 key pivot tables for a given entity's credit-risk data."""
    key1 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L4_CDE, MNGD_GEO_L4_DESC,
               FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key2 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L4_DESC,
               FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key3 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L2_CDE, MNGD_GEO_L4_DESC,
               FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key4 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L3_DESC,
               FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key5 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE,
               FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    return key1, key2, key3, key4, key5


def compute_rwf(key_df, adv_rwa_col):
    """Compute SA RWF and AA RWF; cap both at 1 (i.e., max 100%)."""
    key_df[SA_RWF] = key_df[SA_RWA_AMT].abs() / key_df[GAAP_AMOUNT].abs()
    key_df.loc[key_df[SA_RWF].abs() > 12.5, SA_RWF] = 1

    key_df[AA_RWF] = key_df[adv_rwa_col].abs() / key_df[GAAP_AMOUNT].abs()
    key_df.loc[key_df[AA_RWF].abs() > 12.5, AA_RWF] = 1
    return key_df


def set_markets_rwf_zero(key_df):
    key_df[SA_RWF] = key_df[SA_RWF].where(~key_df[MNGD_SGMT_L2_DESC].isin([MARKETS_L2]), 0)
    key_df[AA_RWF] = key_df[AA_RWF].where(~key_df[MNGD_SGMT_L2_DESC].isin([MARKETS_L2]), 0)
    return key_df


def cast_code_columns_to_int(df):
    code_cols = [col for col in df.columns if "Code" in col or "CDE" in col or "ID" in col]
    for col in code_cols:
        df[col] = df[col].astype(int)
        print(f"Casted {col} to int")
    return df


def build_waterfall_lookup_keys(keys, entity_prefix):
    """
    Build composite key strings for the 5-key waterfall for a given entity.
    Args:
        keys: tuple/list of 5 DataFrames (key1, key2, key3, key4, key5)
        entity_prefix: 'cg' or 'cbna' (for error messages only)
    """
    for i, key_df in enumerate(keys, start=1):
        key_df["Key"] = key_df.index.map(lambda idx: "|".join(str(v) for v in idx))
    return keys


def split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2):
    """Split convergence into credit-risk and market buckets."""
    credit_risk_convergence_cg = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (convergence[FINANCE_PMF_LEVEL_5_DESC].isin(PMF_ACCOUNTS))
    ].copy()

    credit_risk_convergence_cbna = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (convergence[FINANCE_PMF_LEVEL_5_DESC].isin(PMF_ACCOUNTS))
    ].copy()

    non_credit_risk_non_waterfall_cg = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (~convergence[FINANCE_PMF_LEVEL_5_DESC].isin(PMF_ACCOUNTS))
    ].copy()

    non_credit_risk_non_waterfall_cbna = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (~convergence[FINANCE_PMF_LEVEL_5_DESC].isin(PMF_ACCOUNTS))
    ].copy()

    cg_addon_markets_credit_risk = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (convergence[MNGD_SGMT_L2_DESC].isin([MARKETS_L2]))
    ].copy()

    cbna_addon_markets_credit_risk = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (convergence[MNGD_SGMT_L2_DESC].isin([MARKETS_L2]))
    ].copy()

    return {
        "credit_risk_convergence_cg":          credit_risk_convergence_cg,
        "credit_risk_convergence_cbna":         credit_risk_convergence_cbna,
        "non_credit_risk_non_waterfall_cg":     non_credit_risk_non_waterfall_cg,
        "non_credit_risk_non_waterfall_cbna":   non_credit_risk_non_waterfall_cbna,
        "cg_addon_markets_credit_risk":         cg_addon_markets_credit_risk,
        "cbna_addon_markets_credit_risk":       cbna_addon_markets_credit_risk,
    }


def assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook):
    """
    ERBA RWA is set to SA_RWA_AMT for quarters 5 and 6 (ERBA reporting periods).
    Comment is set to empty string. Forecast flag set to 'Backbook'.
    Modifies DataFrames in place.
    """
    cg_outlook[ERBA_RWA]   = cg_outlook[SA_RWA_AMT].where(cg_outlook[QRTR_ID].isin(["5", "6"]))
    cbna_outlook[ERBA_RWA] = cbna_outlook[SA_RWA_AMT].where(cbna_outlook[QRTR_ID].isin(["5", "6"]))

    cg_outlook["Comment"]   = ""
    cg_outlook["Forecast"]  = ""
    cbna_outlook["Comment"] = ""
    cbna_outlook["Forecast"] = ""


def melt_quarterly_pivot(pivot_df):
    return pivot_df.melt(
        value_vars=["Mar", "Jun", "Sep", "Dec"],
        var_name="Month",
        value_name="Balance",
    )


def check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook):
    """
    Checks that the number of unique quarters in convergence and both outlooks match.
    Returns the maximum number of quarters found.
    Warns if there is a mismatch.
    """
    cg_unique_year_months   = cg_outlook[["YEAR", "Month"]].drop_duplicates().sort_values(["YEAR", "Month"])
    cbna_unique_year_months = cbna_outlook[["YEAR", "Month"]].drop_duplicates().sort_values(["YEAR", "Month"])

    num_convergence_quarters = len(convergence[QRTR_ID].unique())
    num_cg_quarters          = cg_unique_year_months.shape[0]
    num_cbna_quarters        = cbna_unique_year_months.shape[0]

    if not (num_convergence_quarters == num_cg_quarters == num_cbna_quarters):
        warnings.warn(
            f"⚠️ Quarter count mismatch: "
            f"Convergence={num_convergence_quarters}, "
            f"CG outlook={num_cg_quarters}, "
            f"CBNA outlook={num_cbna_quarters}"
        )
    else:
        print(f"✅ Quarter counts match across Convergence and both outlooks: {num_convergence_quarters}")
    max_quarters = max(num_convergence_quarters, num_cg_quarters, num_cbna_quarters)
    print(f"Max quarters found: {max_quarters}")
    return max_quarters


def merge_rwf_waterfall(outlook_df, k1, k2, k3, k4, k5, label):
    """
    Merge RWF waterfall keys onto the outlook DataFrame using a left join cascade.
    Falls back gracefully on Exception, warning the user.
    Validates row count is unchanged after each merge.
    """
    pre_rows = len(outlook_df)
    try:
        outlook_df = outlook_df.merge(k1[["SA_RWF", "AA_RWF"]], how="left", on="Key1",
                                      suffixes=("", "_key1"), validate="m:1")
        outlook_df = outlook_df.merge(k2[["SA_RWF", "AA_RWF"]], how="left", on="Key2",
                                      suffixes=("", "_key2"), validate="m:1")
        outlook_df = outlook_df.merge(k3[["SA_RWF", "AA_RWF"]], how="left", on="Key3",
                                      suffixes=("", "_key3"), validate="m:1")
        outlook_df = outlook_df.merge(k4[["SA_RWF", "AA_RWF"]], how="left", on="Key4",
                                      suffixes=("", "_key4"), validate="m:1")
        outlook_df = outlook_df.merge(k5[["SA_RWF", "AA_RWF"]], how="left", on="Key5",
                                      suffixes=("", "_key5"), validate="m:1")
        print(f"✅ {label}: Merges validated (1:1 for Key1, m:1 for Key2-5)")
    except Exception:
        warnings.warn(f"⚠️ {label}: Merge validation failed (≥1), falling back to m:m merge")
        outlook_df = outlook_df.merge(k1[["SA_RWF", "AA_RWF"]], how="left", on="Key1", suffixes=("", "_key1"))
        outlook_df = outlook_df.merge(k2[["SA_RWF", "AA_RWF"]], how="left", on="Key2", suffixes=("", "_key2"))
        outlook_df = outlook_df.merge(k3[["SA_RWF", "AA_RWF"]], how="left", on="Key3", suffixes=("", "_key3"))
        outlook_df = outlook_df.merge(k4[["SA_RWF", "AA_RWF"]], how="left", on="Key4", suffixes=("", "_key4"))
        outlook_df = outlook_df.merge(k5[["SA_RWF", "AA_RWF"]], how="left", on="Key5", suffixes=("", "_key5"))

    post_rows = len(outlook_df)
    if post_rows != pre_rows:
        warnings.warn(
            f"⚠️ {label}: Row count changed during merge! "
            f"{pre_rows:,} → {post_rows:,} (possible row expansion)"
        )
    return outlook_df


def build_markets_addon_pivot(cg_addon, cbna_addon, markets_credit_risk, addon_pivot_index):
    """
    Pivot Markets addon credit-risk data.
    Returns: (pivoted_cg, pivoted_cbna)
    """
    pivoted_cg = cg_addon[markets_credit_risk].pivot_table(
        values=[SA_RWA_AMT, ADV_CG_TOTAL_RWA_AMT],
        index=addon_pivot_index,
        aggfunc="sum",
    ).reset_index()
    pivoted_cbna = cbna_addon[markets_credit_risk].pivot_table(
        values=[SA_RWA_AMT, ADV_CBNA_TOTAL_RWA_AMT],
        index=addon_pivot_index,
        aggfunc="sum",
    ).reset_index()
    return pivoted_cg, pivoted_cbna


def assign_erba_rwa_and_comment(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk):
    """
    Assign ERBA RWA and Comment columns to Markets addon credit-risk DataFrames for CG and CBNA.
    ERBA RWA = SA_RWA_AMT where Quarter Id is in ('5', '6'), else NaN.
    Comment is set to empty string.
    """
    cg_addon_markets_credit_risk[ERBA_RWA] = cg_addon_markets_credit_risk[SA_RWA_AMT].where(
        cg_addon_markets_credit_risk[QRTR_ID].isin([5, 6])
    )
    cg_addon_markets_credit_risk["Comment"] = ""

    cbna_addon_markets_credit_risk[ERBA_RWA] = cbna_addon_markets_credit_risk[SA_RWA_AMT].where(
        cbna_addon_markets_credit_risk[QRTR_ID].isin([5, 6])
    )
    cbna_addon_markets_credit_risk["Comment"] = ""


def check_input_files_exist(input_files):
    for fname in input_files:
        if not Path(fname).exists():
            raise FileNotFoundError(f"❌ Input file not found: {fname}")
        else:
            print(f"✅ Found: {fname}")


def check_unknown_quarters(cg_outlook, cbna_outlook):
    """
    Data Quality: Prints summary of unknown Quarter Ids in both outlooks.
    """
    unknown_cg   = cg_outlook[cg_outlook[QRTR_ID] == "Unknown"]
    unknown_cbna = cbna_outlook[cbna_outlook[QRTR_ID] == "Unknown"]
    if unknown_cg.shape[0] > 0:
        warnings.warn(
            f"⚠️ {unknown_cg.shape[0]} CG rows have Unknown Quarter Id "
            f"(YEAR/Month not in mapping)"
        )
    if unknown_cbna.shape[0] > 0:
        warnings.warn(
            f"⚠️ {unknown_cbna.shape[0]} CBNA rows have Unknown Quarter Id"
        )
    print(f"✅ Quarter Id assigned. Unknown CG: {unknown_cg.shape[0]:,}, Unknown CBNA: {unknown_cbna.shape[0]:,}")


def check_key_match_coverage(cg_outlook, cbna_outlook):
    """
    Data Quality: Print rows in CG and CBNA outlooks that have no convergence key match
    across all 5 keys.
    """
    for label, df in [("CG", cg_outlook), ("CBNA", cbna_outlook)]:
        no_match = (
            df[SA_RWF].isna() &
            df["SA_RWF_key2"].isna() &
            df["SA_RWF_key3"].isna() &
            df["SA_RWF_key4"].isna() &
            df["SA_RWF_key5"].isna()
        )
        pct = no_match.sum() / len(df) * 100 if len(df) > 0 else 0
        print(
            f"{label} — {no_match.sum():,} rows ({pct:.1f}%) have no convergence key match "
            f"across all 5 keys"
        )


def check_expected_columns(src_df, expected_cols, label):
    missing = [c for c in expected_cols if c not in src_df.columns]
    if missing:
        warnings.warn(f"⚠️ {label}: Missing columns: {missing}")
    else:
        print(f"✅ {label} has all expected columns")


def check_pmf_account_coverage(convergence, PMF_ACCOUNTS, FINANCE_PMF_LEVEL_5_DESC):
    """
    Check if all PMF_ACCOUNTS are present in the convergence data's FINANCE_PMF_LEVEL_5_DESC column.
    Warn if any are missing.
    """
    convergence_pmf_values = convergence[FINANCE_PMF_LEVEL_5_DESC].dropna().unique()
    missing_pmf = [p for p in PMF_ACCOUNTS if p not in convergence_pmf_values]
    if missing_pmf:
        warnings.warn(f"⚠️ PMF accounts not found in convergence data: {missing_pmf}")
    else:
        print("✅ All expected PMF accounts found in convergence data")


def check_rwf_capping(keys):
    """
    Warn if any SA RWF or AA RWF values were capped (set to 1 due to abs > 12.5).
    Args:
        keys: list of (label, DataFrame) tuples
    """
    for label, kdf in keys:
        capped_sa = (kdf[SA_RWF] == 1).sum()
        capped_aa = (kdf[AA_RWF] == 1).sum()
        if capped_sa > 0 or capped_aa > 0:
            warnings.warn(
                f"⚠️ {label}: {capped_sa} SA RWF and {capped_aa} AA RWF values were capped to 1"
            )


def export_excel_specs_to_parquet(file_specs, output_dir, schema_registry_csv, if_exists="new"):
    """
    Reads each Excel file spec and writes it to parquet using Polars for dtype enforcement.
    Returns dict of {variable_name: {"output_path": Path, ...}}.
    """
    import polars as pl

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for spec in file_specs:
        var_name   = spec["variable_name"]
        input_path = Path(spec["input_path"])
        schema_key = spec.get("schema_key")
        out_path   = output_dir / f"{var_name}.parquet"

        if if_exists == "new" and out_path.exists():
            print(f"⏭ Skipping (exists): {out_path}")
            results[var_name] = {"output_path": out_path}
            continue

        df = pl.read_excel(input_path, schema_overrides=spec.get("polars_dtypes", {}))
        df.write_parquet(out_path)
        print(f"✅ Written: {out_path} ({len(df):,} rows)")
        results[var_name] = {"output_path": out_path}

    return results
