import os
import warnings
import time
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from dateutil.relativedelta import relativedelta
import polars as pl
from constants import *


# =============================================================================
# Model Convergence Functions
# =============================================================================

# =============================================================================
# Business Logic Functions
# =============================================================================

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


def calculate_sa_rwa(df):
    multipliers = [
        df[SA_RWF],
        df["SA RWF_key2"],
        df["SA RWF_key3"],
        df["SA RWF_key4"],
        df["SA RWF_key5"],
    ]
    # first non-null multiplier
    df["FINAL_SA_RWF"] = (
        df[SA_RWF]
        .combine_first(df["SA RWF_key2"])
        .combine_first(df["SA RWF_key3"])
        .combine_first(df["SA RWF_key4"])
        .combine_first(df["SA RWF_key5"])
    )
    df[SA_RWA] = np.where(
        df[PMF_ACCT_L5_DESC].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_SA_RWF"],
    )


def calculate_aa_rwa(df):
    multipliers = [
        df[AA_RWF],
        df["AA RWF_key2"],
        df["AA RWF_key3"],
        df["AA RWF_key4"],
        df["AA RWF_key5"],
    ]
    # first non-null multiplier
    df["FINAL_AA_RWF"] = (
        df[AA_RWF]
        .combine_first(df["AA RWF_key2"])
        .combine_first(df["AA RWF_key3"])
        .combine_first(df["AA RWF_key4"])
        .combine_first(df["AA RWF_key5"])
    )
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
    cg_outlook[ERBA_RWA] = cg_outlook[SA_RWA].where(cg_outlook[QRTR_ID].isin(["5", "6"]))
    cbna_outlook[ERBA_RWA] = cbna_outlook[SA_RWA].where(cbna_outlook[QRTR_ID].isin(["5", "6"]))
    cg_outlook["Comment"] = ""
    cg_outlook["RWA Exposure Type"] = "Banking Book"
    cbna_outlook["Comment"] = ""
    cbna_outlook["RWA Exposure Type"] = "Banking Book"


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
