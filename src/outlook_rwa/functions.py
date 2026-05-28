import os
import warnings
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta
import polars as pl
import toml
from .constants import *


# =============================================================================
# Model Convergence Functions
# =============================================================================

# =============================================================================
# Business Logic Functions
# =============================================================================

def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge `override` into a copy of `base` (override wins)."""
    result = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(config_dir):
    """Load config.toml and merge an optional git-ignored config.local.toml over it.

    Machine-specific values (paths, Q0) belong in config.local.toml so they stay
    out of version control and never conflict on `git pull`. Falls back to
    config.toml alone when no local override exists.
    """
    config_dir = Path(config_dir)
    config = toml.load(config_dir / "config.toml")
    local_path = config_dir / "config.local.toml"
    if local_path.exists():
        config = _deep_merge(config, toml.load(local_path))
    return config


def _int_str(series: pd.Series) -> pd.Series:
    """Convert float-typed integer columns to clean int strings (e.g. 4.0 → '4')."""
    return pd.to_numeric(series, errors="coerce").apply(
        lambda x: str(int(x)) if pd.notna(x) else str(x)
    )


def assign_quarter_id(outlook_df, quarter_id_mapping):
    """
    Assigns Quarter Id to the outlook DataFrame based on YEAR and Month columns
    using the provided mapping. If no match is found, assigns 'Unknown'.
    Modifies the DataFrame in place.
    """
    outlook_df[QRTR_ID] = outlook_df[["YEAR", "Month"]].apply(
        lambda row: quarter_id_mapping.get((row["YEAR"], row["Month"]), "Unknown"), axis=1
    )


def assign_year_month_from_quarter(*dfs, quarter_map):
    """Inverse of assign_quarter_id: derive YEAR / Month from Quarter Id.

    Used after the add-on pivot, where the descriptor rows survive via the pivot
    index (which carries Quarter Id) but YEAR / Month were dropped. Rows whose
    Quarter Id is not a known quarter (e.g. 'Unknown') get YEAR / Month = NA,
    matching the pre-pivot behaviour for unparseable Projected Quarters.
    """
    try:
        for df in dfs:
            df['YEAR'] = df[QRTR_ID].map(lambda x: quarter_map[x][0])
            df['Month'] = df[QRTR_ID].map(lambda x: quarter_map[x][1])
            # q = pd.to_numeric(df[QRTR_ID], errors="coerce")
            # df["YEAR"] = q.map(lambda x: quarter_map[int(x)][0] if pd.notna(x) and int(x) in quarter_map else pd.NA).astype("Int64")
            # df["Month"] = q.map(lambda x: quarter_map[int(x)][1] if pd.notna(x) and int(x) in quarter_map else None)
    except Exception as e:
        warnings.warn(f"Error assigning YEAR/Month from Quarter Id: {e}")
        raise e

def _first_valid_rwf(df, cols):
    """Return the first present (non-null) RWF across cols; a present 0 is valid.

    Only null/None/empty/non-numeric values (coerced to NaN) are skipped — a key
    whose RWF is genuinely 0 is used as-is, matching production's waterfall.
    """
    return (
        df[cols]
        .apply(pd.to_numeric, errors="coerce")
        .bfill(axis=1)
        .iloc[:, 0]
    )


def calculate_sa_rwa(df):
    rwf_columns = [
        SA_RWF,
        "SA RWF_key2",
        "SA RWF_key3",
        "SA RWF_key4",
        "SA RWF_key5",
    ]
    # first present multiplier (a present 0 is used as-is; only null/empty skipped)
    df["FINAL_SA_RWF"] = _first_valid_rwf(df, rwf_columns)
    df[SA_RWA] = np.where(
        df[PMF_ACCT_L5_DESC].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_SA_RWF"],
    )


def calculate_aa_rwa(df):
    rwf_columns = [
        AA_RWF,
        "AA RWF_key2",
        "AA RWF_key3",
        "AA RWF_key4",
        "AA RWF_key5",
    ]
    # first present multiplier (a present 0 is used as-is; only null/empty skipped)
    df["FINAL_AA_RWF"] = _first_valid_rwf(df, rwf_columns)
    df[AA_RWA] = np.where(
        df[PMF_ACCT_L5_DESC].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_AA_RWF"],
    )


def assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook):
    """
    Assign ERBA RWA, Comment, and RWA Exposure Type columns to CG and CBNA
    outlook DataFrames. ERBA RWA is set to SA RWA where QRTR_ID is '5' or '6',
    else NaN. Comment is set to empty string, RWA Exposure Type to 'Banking Book'.
    Modifies DataFrames in place.
    """
    cg_outlook[ERBA_RWA] = cg_outlook[SA_RWA].where(cg_outlook[QRTR_ID].isin([5, 6]))
    cbna_outlook[ERBA_RWA] = cbna_outlook[SA_RWA].where(cbna_outlook[QRTR_ID].isin([5, 6]))
    cg_outlook["Comment"] = ""

    cbna_outlook["Comment"] = ""



def split_convergence(convergence, pmf_accounts, markets_l2):
    """Split convergence into mutually exclusive credit-risk, non-waterfall, and Markets add-on buckets."""
    credit_risk_convergence_cg = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts))
    ].copy()

    credit_risk_convergence_cbna = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts))
    ].copy()

    cg_addon_markets_credit_risk = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (convergence[MNGD_SGMT_L2_DESC].isin([markets_l2]))
    ].copy()

    cbna_addon_markets_credit_risk = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (convergence[MNGD_SGMT_L2_DESC].isin([markets_l2]))
    ].copy()

    non_credit_risk_non_waterfall_cg = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CG] == "Y") &
        (~convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts)) &
        (convergence[MNGD_SGMT_L2_DESC] != markets_l2)
    ].copy()

    non_credit_risk_non_waterfall_cbna = convergence[
        (convergence[REPORTABLE_ENTITY_IS_CBNA] == "Y") &
        (~convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts)) &
        (convergence[MNGD_SGMT_L2_DESC] != markets_l2)
    ].copy()

    return (
        credit_risk_convergence_cg,
        credit_risk_convergence_cbna,
        non_credit_risk_non_waterfall_cg,
        non_credit_risk_non_waterfall_cbna,
        cg_addon_markets_credit_risk,
        cbna_addon_markets_credit_risk,
    )


def build_markets_addon_pivot(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk, addon_pivot_index):
    """Pivot (sum) the Markets credit-risk add-on for CG and CBNA.

    Collapses the raw convergence rows to one row per `addon_pivot_index`
    combination, summing the additive RWA amounts. Without this aggregation the
    add-on export carries one row per raw record (surplus rows). Returns
    (pivoted_cg, pivoted_cbna).
    """
    pivoted_cg = cg_addon_markets_credit_risk.pivot_table(
        values=[SA_RWA_AMT, ADV_CG_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()
    pivoted_cbna = cbna_addon_markets_credit_risk.pivot_table(
        values=[SA_RWA_AMT, ADV_CBNA_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()
    return pivoted_cg, pivoted_cbna


def build_addon_pivot(non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna, addon_pivot_index):
    """Pivot (sum) the non-waterfall non-credit-risk add-on for CG and CBNA.

    Fills null PMF L5 keys so those rows survive the pivot, sums the additive
    RWA amounts to one row per `addon_pivot_index` combination, then derives
    ERBA RWA (= SA RWA Amount in quarters 5/6) and a blank Comment. Returns
    (pivoted_cg, pivoted_cbna).
    """
    non_credit_risk_non_waterfall_cg = non_credit_risk_non_waterfall_cg.copy()
    non_credit_risk_non_waterfall_cbna = non_credit_risk_non_waterfall_cbna.copy()
    non_credit_risk_non_waterfall_cg[FINANCE_PMF_LEVEL_5_DESC] = (
        non_credit_risk_non_waterfall_cg[FINANCE_PMF_LEVEL_5_DESC].fillna(0)
    )
    non_credit_risk_non_waterfall_cbna[FINANCE_PMF_LEVEL_5_DESC] = (
        non_credit_risk_non_waterfall_cbna[FINANCE_PMF_LEVEL_5_DESC].fillna(0)
    )

    pivoted_cg = non_credit_risk_non_waterfall_cg.pivot_table(
        values=[SA_RWA_AMT, ADV_CG_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()
    pivoted_cbna = non_credit_risk_non_waterfall_cbna.pivot_table(
        values=[SA_RWA_AMT, ADV_CBNA_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()

    for pivoted in (pivoted_cg, pivoted_cbna):
        # Quarter Id is a string here (assign_quarter_id), so the quarter 5/6 test
        # uses strings rather than production's int literals.
        pivoted[ERBA_RWA] = pivoted[SA_RWA_AMT].where(pivoted[QRTR_ID].isin([5, 6]))
        pivoted["Comment"] = ""
    return pivoted_cg, pivoted_cbna


def create_key_pivots(crd_df, adv_rwa_col):
    """Create the 5 key pivot tables for a given entity's credit-risk data."""
    key1 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L4_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key2 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key3 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L2_CDE, MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key4 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE, MNGD_GEO_L3_DESC, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    key5 = crd_df.pivot_table(
        values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
        index=[QRTR_ID, MNGD_SGMT_L3_CDE, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
        aggfunc="sum",
    )
    return key1, key2, key3, key4, key5


def compute_rwf(key_df, adv_rwa_col):
    """Compute SA RWF and AA RWF, cap at abs(12.5), set out-of-range to 1."""
    key_df[SA_RWF] = pd.to_numeric(key_df[SA_RWA_AMT], errors="coerce") / pd.to_numeric(key_df[GAAP_AMOUNT], errors="coerce")
    key_df.loc[key_df[SA_RWF].abs() > 12.5, SA_RWF] = 1
    key_df[AA_RWF] = pd.to_numeric(key_df[adv_rwa_col], errors="coerce") / pd.to_numeric(key_df[GAAP_AMOUNT], errors="coerce")
    key_df.loc[key_df[AA_RWF].abs() > 12.5, AA_RWF] = 1
    return key_df


def set_markets_rwf(key_df):
    """Null out RWFs for Markets rows (they get add-on treatment instead)."""
    is_markets = key_df.index.get_level_values(MNGD_SGMT_L2_DESC).isin([MARKETS_L2])
    key_df[SA_RWF] = key_df[SA_RWF].where(~is_markets)
    key_df[AA_RWF] = key_df[AA_RWF].where(~is_markets)
    return key_df


def build_outlook_key_strings(outlook_df):
    """Build composite key strings for the 5-key waterfall on an outlook DataFrame.
    Modifies the DataFrame in place.
    """
    outlook_df["Key1"] = (
        _int_str(outlook_df[MANAGED_SGMNT_L4_ID])
        + outlook_df[MANAGED_GEO_L4_DESC].astype(str)
        + outlook_df[PMF_ACCT_L5_DESC].astype(str)
        + outlook_df[QRTR_ID].astype(str)
    )
    outlook_df["Key2"] = (
        _int_str(outlook_df[MANAGED_SGMNT_L3_ID])
        + outlook_df[MANAGED_GEO_L4_DESC].astype(str)
        + outlook_df[PMF_ACCT_L5_DESC].astype(str)
        + outlook_df[QRTR_ID].astype(str)
    )
    outlook_df["Key3"] = (
        _int_str(outlook_df[MANAGED_SGMNT_L2_ID])
        + outlook_df[MANAGED_GEO_L4_DESC].astype(str)
        + outlook_df[PMF_ACCT_L5_DESC].astype(str)
        + outlook_df[QRTR_ID].astype(str)
    )
    outlook_df["Key4"] = (
        _int_str(outlook_df[MANAGED_SGMNT_L3_ID])
        + outlook_df[MANAGED_GEO_L3_DESC].astype(str)
        + outlook_df[PMF_ACCT_L5_DESC].astype(str)
        + outlook_df[QRTR_ID].astype(str)
    )
    outlook_df["Key5"] = (
        _int_str(outlook_df[MANAGED_SGMNT_L3_ID])
        + outlook_df[PMF_ACCT_L5_DESC].astype(str)
        + outlook_df[QRTR_ID].astype(str)
    )


def rename_month_columns(df):
    """Rename M*_USDOLLAR columns to quarter month names (Mar, Jun, Sep, Dec).
    Modifies df in place.
    """
    df["Mar"] = df["M3_USDOLLAR"]
    df["Jun"] = df["M6_USDOLLAR"]
    df["Sep"] = df["M9_USDOLLAR"]
    df["Dec"] = df["M12_USDOLLAR"]


def create_quarterly_pivot(df):
    """Pivot the balance sheet DataFrame to sum quarterly balances by key dimensions.
    Returns the pivoted DataFrame.
    """
    pivot_index = [
        "YEAR",
        MANAGED_SGMNT_L4_DESC,
        MANAGED_SGMNT_L3_DESC,
        MANAGED_SGMNT_L2_DESC,
        MANAGED_GEO_L4_DESC,
        MANAGED_GEO_L3_DESC,
        PMF_ACCT_L5_DESC,
        MANAGED_SGMNT_L4_ID,
        MANAGED_SGMNT_L3_ID,
        MANAGED_SGMNT_L2_ID,
    ]
    return df.pivot_table(
        index=pivot_index,
        values=["Mar", "Jun", "Sep", "Dec"],
        aggfunc="sum",
    ).reset_index()


def melt_quarterly_pivot(pivot_df):
    """Melt a quarterly pivot DataFrame to long format with Month and Balances columns."""
    melt_id_vars = [
        "YEAR",
        MANAGED_SGMNT_L4_DESC,
        MANAGED_SGMNT_L3_DESC,
        MANAGED_SGMNT_L2_DESC,
        MANAGED_GEO_L4_DESC,
        MANAGED_GEO_L3_DESC,
        PMF_ACCT_L5_DESC,
        MANAGED_SGMNT_L4_ID,
        MANAGED_SGMNT_L3_ID,
        MANAGED_SGMNT_L2_ID,
    ]
    return pd.melt(
        pivot_df,
        id_vars=melt_id_vars,
        value_vars=["Mar", "Jun", "Sep", "Dec"],
        var_name="Month",
        value_name="Balances",
    )


def check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook):
    """
    Checks that the number of unique quarters in convergence and both outlooks match.
    Returns the maximum number of quarters found. Warns if there is a mismatch.
    """
    cg_unique_year_months = (
        cg_outlook[["YEAR", "Month"]].drop_duplicates().sort_values(["YEAR", "Month"])
    )
    cbna_unique_year_months = (
        cbna_outlook[["YEAR", "Month"]].drop_duplicates().sort_values(["YEAR", "Month"])
    )

    num_convergence_quarters = len(convergence["Quarter Id"].unique())
    num_cg_quarters = cg_unique_year_months.shape[0]
    num_cbna_quarters = cbna_unique_year_months.shape[0]

    if not (num_convergence_quarters == num_cg_quarters == num_cbna_quarters):
        warnings.warn(
            f"Quarter count mismatch: "
            f"convergence={num_convergence_quarters}, "
            f"CG outlook={num_cg_quarters}, "
            f"CBNA outlook={num_cbna_quarters}"
        )
    else:
        print(f"✅ Quarter counts match across convergence and both outlooks: {num_convergence_quarters}")

    max_quarters = max(num_convergence_quarters, num_cg_quarters, num_cbna_quarters)
    print(f"Max quarters found: {max_quarters}")
    return max_quarters


def build_quarter_mappings(Q0, max_quarters):
    """
    Build quarter_map and quarter_id_mapping based on Q0 and max_quarters.

    Returns:
        quarter_map: dict of quarter_number -> (year, month_abbr)
        quarter_id_mapping: dict of (year, month_abbr) -> quarter_number_str
    """
    quarter_map = {}
    quarter_id_mapping = {}
    q0_date = datetime.strptime(Q0, "%b %Y")
    for i in range(0, max_quarters * 3, 3):
        quarter = i // 3
        temp_date = q0_date + relativedelta(months=i)
        quarter_map[quarter] = (temp_date.year, temp_date.strftime("%b"))
        quarter_id_mapping[(temp_date.year, temp_date.strftime("%b"))] = str(quarter)

    print("Quarter mapping:")
    for k, v in quarter_map.items():
        print(f"  Q{k}: {v[1]} {v[0]}")

    first_qtr = quarter_map.get(0)
    if first_qtr != (q0_date.year, q0_date.strftime("%b")):
        warnings.warn(
            f"⚠️ First quarter in mapping ({first_qtr[1]} {first_qtr[0]}) "
            f"does not match Q0 ({q0_date.strftime('%b')} {q0_date.year})"
        )
    else:
        print(f"✅ First quarter mapping matches Q0: {q0_date.strftime('%b')} {q0_date.year}")

    return quarter_map, quarter_id_mapping


# =============================================================================
# Waterfall RWF Lookups (model convergence stage)
# =============================================================================

def _apply_waterfall_lookups(outlook_df, lookup1, lookup2, lookup3, lookup4, lookup5):
    """Merge the 5 convergence pivot RWF tables onto an outlook DataFrame."""
    # Key1: MNGD_SGMT_L4_CDE + MNGD_GEO_L4_DESC + FINANCE_PMF_LEVEL_5_DESC + QRTR_ID
    lk1 = lookup1.reset_index()
    lk1["_key"] = (
        _int_str(lk1[MNGD_SGMT_L4_CDE])
        + lk1[MNGD_GEO_L4_DESC].astype(str)
        + lk1[FINANCE_PMF_LEVEL_5_DESC].astype(str)
        + _int_str(lk1[QRTR_ID])
    )
    outlook_df = outlook_df.merge(
        lk1[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: SA_RWF, AA_RWF: AA_RWF}),
        left_on="Key1", right_on="_key", how="left",
    ).drop(columns=["_key"])

    # Key2: MNGD_SGMT_L3_CDE + MNGD_GEO_L4_DESC + FINANCE_PMF_LEVEL_5_DESC + QRTR_ID
    lk2 = lookup2.reset_index()
    lk2["_key"] = (
        _int_str(lk2[MNGD_SGMT_L3_CDE])
        + lk2[MNGD_GEO_L4_DESC].astype(str)
        + lk2[FINANCE_PMF_LEVEL_5_DESC].astype(str)
        + _int_str(lk2[QRTR_ID])
    )
    outlook_df = outlook_df.merge(
        lk2[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: "SA RWF_key2", AA_RWF: "AA RWF_key2"}),
        left_on="Key2", right_on="_key", how="left",
    ).drop(columns=["_key"])

    # Key3: MNGD_SGMT_L2_CDE + MNGD_GEO_L4_DESC + FINANCE_PMF_LEVEL_5_DESC + QRTR_ID
    lk3 = lookup3.reset_index()
    lk3["_key"] = (
        _int_str(lk3[MNGD_SGMT_L2_CDE])
        + lk3[MNGD_GEO_L4_DESC].astype(str)
        + lk3[FINANCE_PMF_LEVEL_5_DESC].astype(str)
        + _int_str(lk3[QRTR_ID])
    )
    outlook_df = outlook_df.merge(
        lk3[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: "SA RWF_key3", AA_RWF: "AA RWF_key3"}),
        left_on="Key3", right_on="_key", how="left",
    ).drop(columns=["_key"])

    # Key4: MNGD_SGMT_L3_CDE + MNGD_GEO_L3_DESC + FINANCE_PMF_LEVEL_5_DESC + QRTR_ID
    lk4 = lookup4.reset_index()
    lk4["_key"] = (
        _int_str(lk4[MNGD_SGMT_L3_CDE])
        + lk4[MNGD_GEO_L3_DESC].astype(str)
        + lk4[FINANCE_PMF_LEVEL_5_DESC].astype(str)
        + _int_str(lk4[QRTR_ID])
    )
    outlook_df = outlook_df.merge(
        lk4[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: "SA RWF_key4", AA_RWF: "AA RWF_key4"}),
        left_on="Key4", right_on="_key", how="left",
    ).drop(columns=["_key"])

    # Key5: MNGD_SGMT_L3_CDE + FINANCE_PMF_LEVEL_5_DESC + QRTR_ID
    lk5 = lookup5.reset_index()
    lk5["_key"] = (
        _int_str(lk5[MNGD_SGMT_L3_CDE])
        + lk5[FINANCE_PMF_LEVEL_5_DESC].astype(str)
        + _int_str(lk5[QRTR_ID])
    )
    outlook_df = outlook_df.merge(
        lk5[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: "SA RWF_key5", AA_RWF: "AA RWF_key5"}),
        left_on="Key5", right_on="_key", how="left",
    ).drop(columns=["_key"])

    return outlook_df


# =============================================================================
# Outlook RWA stage: adjustments, addon, pivots, upload template, controls
# =============================================================================

def format_adjustments(input_df):
    """Coerce RWF/Balances columns to numeric, then fill NaN (0 numeric, 'N/A' text)."""
    cols_to_num = ['Balances', 'SA RWF', 'AA RWF', 'SA RWF_key2', 'AA RWF_key2',
                   'SA RWF_key3', 'AA RWF_key3', 'SA RWF_key4', 'AA RWF_key4',
                   'SA RWF_key5', 'AA RWF_key5']
    for c in cols_to_num:
        if c in input_df.columns:
            input_df[c] = pd.to_numeric(input_df[c], errors='coerce')

    numeric_cols = input_df.select_dtypes(include=['number']).columns
    input_df[numeric_cols] = input_df[numeric_cols].fillna(0)

    string_cols = input_df.select_dtypes(include=['object']).columns
    input_df[string_cols] = input_df[string_cols].fillna('N/A')

    return input_df


def rename_addon_columns(input_df, entity):
    """Rename convergence-style addon columns to outlook-style short names.

    `entity` ('CG'/'CBNA') selects which Adv. RWA column maps to AA RWA, so the
    CBNA addon's AA RWA is sourced from its own column rather than CG's.

    step1 pre-creates partial short columns (SA RWA / RWA Exposure Type) on the
    addon frame; those collide with the long->short rename, so the partial
    copies are dropped first and the fully-populated convergence columns take
    their place. Quarter Id is intentionally not renamed (it already matches),
    so it survives into the downstream concat.
    """
    adv_col = f'Adv. {entity.upper()} Total RWA Amount with 1.06 Multiplier'
    rename_dict = {
        adv_col: AA_RWA,
        'Managed Segment Level 4 Description': MANAGED_SEGMENT_L4_DESCR,
        'Managed Segment Level 3 Description': MANAGED_SEGMENT_L3_DESCR,
        'Managed Segment Level 2 Description': MANAGED_SEGMENT_L2_DESCR,
        'Managed Geography Level 4 Description': 'Managed Geography L4 Descr',
        'Managed Geography Level 3 Description': MANAGED_GEOGRAPHY_L3_DESCR,
        'Finance PMF Level 5 Description': PMF_ACCOUNT_L5_DESCR,
        'SA RWA Amount': SA_RWA,
        'Managed Segment Level 2 Code': 'Managed Segment L2 Id',
        'Managed Segment Level 4 Code': 'Managed Segment L4 Id',
        'Managed Segment Level 3 Code': 'Managed Segment L3 Id',
        'RWA Exposure Type Description': RWA_EXPOSURE_TYPE,
    }
    rename_dict = {k: v for k, v in rename_dict.items() if k in input_df.columns}
    collisions = [v for v in rename_dict.values() if v in input_df.columns]
    return input_df.drop(columns=collisions).rename(columns=rename_dict)


def legacy_franchises_breakout(input_df):
    """Split data by Reporting Layer into legacy and non-legacy sub-groups.

    Splits into sub-groups based on REPORTING_LAYER and MANAGED_SEGMENT_L3_DESCR /
    MANAGED_SEGMENT_L4_DESCR values, assigns appropriate REPORTING_LAYER label,
    then recombines.

    Args:
        input_df: DataFrame with REPORTING_LAYER, MANAGED_SEGMENT_L3_DESCR,
                  MANAGED_SEGMENT_L4_DESCR, and MANAGED_GEOGRAPHY_L3_DESCR.

    Returns:
        DataFrame with REPORTING_LAYER values set per sub-group.
    """
    input_df = input_df.copy()

    legacy          = input_df[input_df[MANAGED_SEGMENT_L3_DESCR] == LEGACY_FRANCHISES_L3].copy()
    legacy_holdings = legacy[legacy[MANAGED_SEGMENT_L4_DESCR] == LEGACY_HOLDINGS_ASSETS_L4].copy()

    legacy_non_holdings = legacy[legacy[MANAGED_SEGMENT_L4_DESCR] != LEGACY_HOLDINGS_ASSETS_L4].copy()

    non_legacy      = input_df[input_df[MANAGED_SEGMENT_L3_DESCR] != LEGACY_FRANCHISES_L3].copy()
    non_latin       = non_legacy[non_legacy[MANAGED_GEOGRAPHY_L3_DESCR] != LATIN_AMERICA].copy()
    non_legacy_latin = non_legacy[non_legacy[MANAGED_GEOGRAPHY_L3_DESCR] == LATIN_AMERICA].copy()

    legacy_holdings[REPORTING_LAYER]     = "Legacy Holdings"
    legacy_non_holdings[REPORTING_LAYER] = "Legacy Holdings Other"
    non_latin[REPORTING_LAYER]           = "Non Legacy"
    non_legacy_latin[REPORTING_LAYER]    = "Legacy - Latin America"

    return pd.concat([legacy_holdings, legacy_non_holdings, non_latin, non_legacy_latin])


def format_columns_before_pivots(input_df):
    """Ensure numeric/string/RWA column types and fill NaN before pivots.

    Coerces SA_RWA, AA_RWA, ERBA_RWA to numeric with errors='coerce'.

    Args:
        input_df: DataFrame prior to pivot operations.

    Returns:
        input_df with numeric RWA columns coerced.
    """
    input_df[SA_RWA]   = pd.to_numeric(input_df[SA_RWA],   errors='coerce')
    input_df[AA_RWA]   = pd.to_numeric(input_df[AA_RWA],   errors='coerce')
    input_df[ERBA_RWA] = pd.to_numeric(input_df[ERBA_RWA], errors='coerce')

    # Fill NaN pivot-key strings with 'None' so group-by/pivot does not drop
    # NaN-keyed rows (which would empty the upload template).
    for col in [MANAGED_SEGMENT_L4_DESCR, MANAGED_SEGMENT_L3_DESCR, MANAGED_SEGMENT_L2_DESCR,
                PMF_ACCOUNT_L5_DESCR, 'Entity', REPORTING_LAYER,
                SA_ACCOUNT_NUM, AA_ACCOUNT_NUM, 'PUG']:
        if col in input_df.columns:
            input_df[col] = input_df[col].fillna('None')
    return input_df


def create_markets_filter(input_df):
    """Mark rows Keep/Remove based on Markets L2 + RWA Exposure Type.

    A row is "Remove" only when it IS Markets [L2] and has a non-zero RWA
    exposure type; every other row (including the entire non-Markets universe)
    is "Keep". Matches the nested np.where in production.

    Args:
        input_df: DataFrame with MANAGED_SEGMENT_L2_DESCR and RWA_EXPOSURE_TYPE.

    Returns:
        input_df with MARKETS_FILTER column added.
    """
    input_df[MARKETS_FILTER] = np.where(
        (input_df[MANAGED_SEGMENT_L2_DESCR] == MARKETS_L2)
        & (input_df[RWA_EXPOSURE_TYPE] == 0),
        "Keep",
        np.where(
            input_df[MANAGED_SEGMENT_L2_DESCR] != MARKETS_L2,
            "Keep",
            "Remove",
        ),
    )
    return input_df


def create_upload_template_pivots(input_df):
    """Create ERBA, AA, SA upload template pivots and concatenate.

    Creates three pivots — ERBA, AA, SA — each summed over QUARTER_ID as
    columns. Sets RWA_CALC column value per pivot type.

    Args:
        input_df: DataFrame with all required columns for pivoting.

    Returns:
        Concatenated DataFrame of ERBA, AA, SA pivots with RWA_CALC set.
    """
    input_df = input_df.copy()
    input_df = input_df.fillna(0)
    # Integer quarter labels so the downstream integer-label reorder/rename/agg
    # match regardless of any float coercion upstream.
    input_df[QUARTER_ID] = pd.to_numeric(input_df[QUARTER_ID], errors="coerce").fillna(0).astype(int)

    pivot_index = [
        MANAGED_SEGMENT_L4_DESCR,
        MANAGED_SEGMENT_L3_DESCR,
        MANAGED_SEGMENT_L2_DESCR,
        PMF_ACCOUNT_L5_DESCR,
        "Comment",
        RWA_EXPOSURE_TYPE,
        "Entity",
        REPORTING_LAYER,
        SA_ACCOUNT_NUM,
        AA_ACCOUNT_NUM,
        "PUG",
    ]

    # Filter pivot_index to columns that actually exist
    pivot_index = [c for c in pivot_index if c in input_df.columns]

    def make_pivot(values_col, rwa_label):
        """Build a single pivot table for one RWA calc type."""
        pivot = input_df.pivot_table(
            values=values_col,
            index=pivot_index,
            columns=[QUARTER_ID],
            aggfunc="sum",
            fill_value=0,
        ).reset_index()
        for i in range(8):
            if i not in pivot.columns:
                pivot[i] = 0
        pivot = pivot[pivot_index + [1, 2, 3, 4, 5, 6, 7, 0]]
        pivot[RWA_CALC] = rwa_label
        return pivot

    erba_pivot = make_pivot(ERBA_RWA, "ERBA")
    aa_pivot   = make_pivot(AA_RWA,   "AA")
    sa_pivot   = make_pivot(SA_RWA,   "SA")

    pivots = pd.concat([erba_pivot, aa_pivot, sa_pivot])
    pivots.columns.name = None
    return pivots


def format_upload_template(input_df):
    """Add upload stub columns, derive the Account number, and reorder for upload.

    Adds the fixed upload stub columns, derives a single Account number from the
    SA/AA account numbers per RWA Calc type (defaulting missing ones), adds the
    month placeholder columns, drops the now-redundant SA/AA account columns and
    reorders to the production upload layout.
    """
    input_df = input_df.copy()

    numeric_cols = input_df.select_dtypes(include=['number']).columns
    input_df[numeric_cols] = input_df[numeric_cols].fillna(0)

    # Fixed upload stub columns
    input_df["FileType"]        = "R"
    input_df["ManagedGeo"]      = ""
    input_df["FrsBu"]           = ""
    input_df["CustomerSegment"] = ""
    input_df["Product"]         = ""
    input_df["Affiliate"]       = "00000"
    input_df["Project"]         = ""
    input_df["TransactionId"]   = ""
    input_df["BalanceType"]     = "EOP"
    input_df["Currency"]        = "USD"
    input_df["Layer"]           = ""
    input_df["ModelId"]         = ""
    input_df["MDRM"]            = ""
    input_df["ReasonCode"]      = ""
    input_df["Comments"]        = ""

    # Account: AA -> AA account #, SA -> SA account #, otherwise N/A
    input_df["Account"] = np.where(
        input_df[RWA_CALC] == "AA",
        input_df[AA_ACCOUNT_NUM],
        np.where(input_df[RWA_CALC] == "SA", input_df[SA_ACCOUNT_NUM], "N/A"),
    )
    # Default account numbers where the PMF mapping was missing ('None')
    input_df["Account"] = np.where(
        (input_df[RWA_CALC] == "AA") & (input_df["Account"] == "None"),
        "664062", input_df["Account"],
    )
    input_df["Account"] = np.where(
        (input_df[RWA_CALC] == "SA") & (input_df["Account"] == "None"),
        "663722", input_df["Account"],
    )

    # Month placeholder columns (quarter-end values live in the integer columns)
    for m in UPLOAD_TEMPLATE_MONTH_STUBS:
        input_df[m] = 0

    input_df = input_df.drop(columns=[SA_ACCOUNT_NUM, AA_ACCOUNT_NUM])
    input_df = input_df.rename(columns={0: "RWA Actuals"})

    input_df = input_df[[c for c in UPLOAD_TEMPLATE_COL_ORDER if c in input_df.columns]]
    input_df = input_df.sort_values([MANAGED_SEGMENT_L2_DESCR, MANAGED_SEGMENT_L3_DESCR])
    return input_df


def build_convergence_control(convergence_df, entity_filter_col, adv_rwa_col):
    """Summarise convergence SA/AA RWA by L2 segment x quarter for the control file.

    Filters to the entity (CG/CBNA), excludes Discontinued Ops, then melts SA/AA
    into an RWA Calc dimension and pivots quarters across the columns.
    """
    MNGED = "Managed Segment Level 2 Description"
    ctrl = convergence_df[convergence_df[entity_filter_col] == "Y"].copy()
    ctrl = ctrl[ctrl[MNGED] != DISCONTINUED_OPS_L2]
    ctrl = ctrl.rename(columns={adv_rwa_col: AA_RWA, "SA RWA Amount": SA_RWA,
                                MNGED: MANAGED_SEGMENT_L2_DESCR})
    ctrl = ctrl.groupby([MANAGED_SEGMENT_L2_DESCR, QUARTER_ID]).agg(
        {SA_RWA: "sum", AA_RWA: "sum"}).reset_index()
    ctrl = ctrl.melt(id_vars=[MANAGED_SEGMENT_L2_DESCR, QUARTER_ID],
                     value_name="Month", var_name=RWA_CALC)
    ctrl = ctrl.pivot_table(index=[MANAGED_SEGMENT_L2_DESCR, RWA_CALC],
                            columns=QUARTER_ID, values="Month", aggfunc="sum").reset_index()
    ctrl.columns.name = None
    return ctrl


def build_frm_control(frm_output_df):
    """Summarise the formatted upload template by L2 segment x RWA calc type.

    Sums the quarter columns (1-7) and the actuals column, mapping the AA/SA
    pivot labels to the canonical RWA names and dropping ERBA.
    """
    ctrl = frm_output_df.groupby([MANAGED_SEGMENT_L2_DESCR, RWA_CALC]).agg(
        {"RWA Actuals": "sum", 1: "sum", 2: "sum", 3: "sum", 4: "sum",
         5: "sum", 6: "sum", 7: "sum"}).reset_index()
    ctrl = ctrl.rename(columns={"RWA Actuals": 0})
    ctrl[RWA_CALC] = ctrl[RWA_CALC].map({"AA": AA_RWA, "SA": SA_RWA})
    ctrl = ctrl[ctrl[RWA_CALC].isin([AA_RWA, SA_RWA])]
    return ctrl


def build_raw_data_control(raw_data_df):
    """Summarise raw data SA/AA RWA by L2 segment x quarter for the control file."""
    ctrl = raw_data_df.copy()
    ctrl[QUARTER_ID] = pd.to_numeric(ctrl[QUARTER_ID], errors="coerce")
    ctrl = ctrl.groupby([MANAGED_SEGMENT_L2_DESCR, QUARTER_ID]).agg(
        {SA_RWA: "sum", AA_RWA: "sum"}).reset_index()
    ctrl = ctrl.melt(id_vars=[MANAGED_SEGMENT_L2_DESCR, QUARTER_ID],
                     value_name="Month", var_name=RWA_CALC)
    ctrl = ctrl.pivot_table(index=[MANAGED_SEGMENT_L2_DESCR, RWA_CALC],
                            columns=QUARTER_ID, values="Month", aggfunc="sum").reset_index()
    ctrl.columns.name = None
    return ctrl



def concat_addon_all(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk, non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna):
    cg_addon_non_waterfall_rwa = pd.concat([cg_addon_markets_credit_risk, non_credit_risk_non_waterfall_cg], ignore_index=True)
    cbna_addon_non_waterfall_rwa = pd.concat([cbna_addon_markets_credit_risk, non_credit_risk_non_waterfall_cbna], ignore_index=True)
    return cg_addon_non_waterfall_rwa, cbna_addon_non_waterfall_rwa
