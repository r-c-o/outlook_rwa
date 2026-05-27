import os
import warnings
import time
from dataclasses import dataclass
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
    outlook_df[QUARTER_ID] = outlook_df[["YEAR", "Month"]].apply(
        lambda row: quarter_id_mapping.get((row["YEAR"], row["Month"]), "Unknown"), axis=1
    )


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
        df[PMF_ACCOUNT_L5_DESCR].isin(NON_CREDIT_RISK_PMF),
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
        df[PMF_ACCOUNT_L5_DESCR].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_AA_RWF"],
    )


def assign_erba_rwa_and_metadata(outlook_df):
    """
    Assign ERBA RWA, Comment, and RWA Exposure Type columns to an outlook
    DataFrame. ERBA RWA is set to SA RWA where Quarter Id is '5' or '6', else
    NaN. Comment is set to empty string, RWA Exposure Type to 'Banking Book'.
    Modifies the DataFrame in place.
    """
    outlook_df[ERBA_RWA] = outlook_df[SA_RWA].where(outlook_df[QUARTER_ID].isin(["5", "6"]))
    outlook_df["Comment"] = ""
    outlook_df["RWA Exposure Type"] = "Banking Book"


@dataclass(frozen=True)
class ConvergenceBuckets:
    """An entity's convergence rows split into the three downstream buckets."""
    credit_risk: pd.DataFrame    # PMF accounts -> 5-key RWF waterfall
    non_waterfall: pd.DataFrame  # non-PMF, non-Markets -> add-on (RWA carried as-is)
    markets: pd.DataFrame        # Markets [L2] -> add-on


def split_convergence(convergence, entities=ENTITIES, pmf_accounts=PMF_ACCOUNTS, markets_l2=MARKETS_L2):
    """Split convergence into per-entity credit-risk, non-waterfall, and Markets buckets.

    Returns a dict keyed by entity name (e.g. {"CG": ConvergenceBuckets, ...}).
    The three buckets are mutually exclusive within an entity.
    """
    is_pmf = convergence[FINANCE_PMF_LEVEL_5_DESC].isin(pmf_accounts)
    is_markets = convergence[MNGD_SGMT_L2_DESC].isin([markets_l2])

    result = {}
    for entity in entities:
        is_entity = convergence[entity.reportable_col] == "Y"
        result[entity.name] = ConvergenceBuckets(
            credit_risk=convergence[is_entity & is_pmf].copy(),
            non_waterfall=convergence[
                is_entity & ~is_pmf & (convergence[MNGD_SGMT_L2_DESC] != markets_l2)
            ].copy(),
            markets=convergence[is_entity & is_markets].copy(),
        )
    return result


def create_key_pivots(crd_df, adv_rwa_col):
    """Build the 5 waterfall pivot tables for an entity's credit-risk data.

    Returns a dict keyed by waterfall key name ("Key1".."Key5"). The pivot index
    for each key is driven by WATERFALL_KEYS (segment code, optional geography),
    always summed by Quarter Id + PMF L5 + Segment L2.
    """
    pivots = {}
    for key in WATERFALL_KEYS:
        index = [QUARTER_ID, key.conv_segment_code]
        if key.conv_geo is not None:
            index.append(key.conv_geo)
        index += [FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC]
        pivots[key.name] = crd_df.pivot_table(
            values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
            index=index,
            aggfunc="sum",
        )
    return pivots


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
    """Build the 5 composite waterfall key strings on an outlook DataFrame.

    Driven by WATERFALL_KEYS: each key is <segment id> [+ <geography>] + <PMF L5>
    + <Quarter Id>. Modifies the DataFrame in place.
    """
    pmf = outlook_df[PMF_ACCOUNT_L5_DESCR].astype(str)
    quarter = outlook_df[QUARTER_ID].astype(str)
    for key in WATERFALL_KEYS:
        composite = _int_str(outlook_df[key.outlook_segment_id])
        if key.outlook_geo is not None:
            composite = composite + outlook_df[key.outlook_geo].astype(str)
        outlook_df[key.name] = composite + pmf + quarter


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
        MANAGED_SEGMENT_L4_DESCR,
        MANAGED_SEGMENT_L3_DESCR,
        MANAGED_SEGMENT_L2_DESCR,
        MANAGED_GEOGRAPHY_L4_DESCR,
        MANAGED_GEOGRAPHY_L3_DESCR,
        PMF_ACCOUNT_L5_DESCR,
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
        MANAGED_SEGMENT_L4_DESCR,
        MANAGED_SEGMENT_L3_DESCR,
        MANAGED_SEGMENT_L2_DESCR,
        MANAGED_GEOGRAPHY_L4_DESCR,
        MANAGED_GEOGRAPHY_L3_DESCR,
        PMF_ACCOUNT_L5_DESCR,
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

def _apply_waterfall_lookups(outlook_df, lookups):
    """Merge the 5 convergence RWF pivot tables onto an outlook DataFrame.

    `lookups` is the dict returned by create_key_pivots ({"Key1": pivot, ...}).
    For each waterfall key, the convergence-side composite (segment code [+ geo]
    + PMF L5 + Quarter Id) is matched against the outlook-side key string, and the
    pivot's SA/AA RWF land on that key's output columns (Key1 -> base, Key2-5 ->
    suffixed).
    """
    for key in WATERFALL_KEYS:
        lk = lookups[key.name].reset_index()
        composite = _int_str(lk[key.conv_segment_code])
        if key.conv_geo is not None:
            composite = composite + lk[key.conv_geo].astype(str)
        lk["_key"] = composite + lk[FINANCE_PMF_LEVEL_5_DESC].astype(str) + _int_str(lk[QUARTER_ID])
        outlook_df = outlook_df.merge(
            lk[["_key", SA_RWF, AA_RWF]].rename(columns={SA_RWF: key.sa_rwf_col, AA_RWF: key.aa_rwf_col}),
            left_on=key.name, right_on="_key", how="left",
        ).drop(columns=["_key"])
    return outlook_df


def apply_adjustments(outlook_df, adjustments_df):
    """Left-merge the adjustments frame onto an outlook frame on Key1.

    Builds the adjustments Key1 composite (segment L4 id + geography L4 + PMF L5 +
    Quarter Id) to match build_outlook_key_strings, then pulls ADJUSTMENT_MERGE_COLS
    across. Returns a new frame; the adjustments frame is not mutated.
    """
    adjustments_df = adjustments_df.copy()
    adjustments_df["Key1"] = (
        _int_str(adjustments_df[MANAGED_SGMNT_L4_ID])
        + adjustments_df[MANAGED_GEOGRAPHY_L4_DESCR].astype(str)
        + adjustments_df[PMF_ACCOUNT_L5_DESCR].astype(str)
        + adjustments_df[QUARTER_ID].astype(str)
    )
    return outlook_df.merge(
        adjustments_df[ADJUSTMENT_MERGE_COLS],
        on="Key1",
        how="left",
        suffixes=("", "_adj"),
    )


def prepare_addon_quarter_fields(addon_df, quarter_id_mapping):
    """Derive YEAR / Month / Quarter Id on an add-on frame from Projected Quarter.

    'Projected Quarter' looks like '4Q25': digit 0 is the quarter number, chars
    from index 2 are the 2-digit year. Modifies the DataFrame in place.
    """
    q_num = pd.to_numeric(addon_df["Projected Quarter"].str[0], errors="coerce").astype("Int64")
    addon_df["YEAR"] = pd.to_numeric(
        addon_df["Projected Quarter"].str[2:].apply(lambda x: "20" + x if pd.notna(x) else x),
        errors="coerce",
    ).astype("Int64")
    addon_df["Month"] = q_num.map(PROJECTED_QUARTER_TO_MONTH)
    assign_quarter_id(addon_df, quarter_id_mapping)


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

    `entity` is an EntityConfig; its adv_rwa_col selects which Adv. RWA column
    maps to AA RWA, so the CBNA addon's AA RWA is sourced from its own column
    rather than CG's.

    step1 pre-creates partial short columns (SA RWA / RWA Exposure Type) on the
    addon frame; those collide with the long->short rename, so the partial
    copies are dropped first and the fully-populated convergence columns take
    their place. Quarter Id is intentionally not renamed (it already matches),
    so it survives into the downstream concat.
    """
    rename_dict = {
        entity.adv_rwa_col: AA_RWA,
        MNGD_SGMT_L4_DESC: MANAGED_SEGMENT_L4_DESCR,
        MNGD_SGMT_L3_DESC: MANAGED_SEGMENT_L3_DESCR,
        MNGD_SGMT_L2_DESC: MANAGED_SEGMENT_L2_DESCR,
        MNGD_GEO_L4_DESC: MANAGED_GEOGRAPHY_L4_DESCR,
        MNGD_GEO_L3_DESC: MANAGED_GEOGRAPHY_L3_DESCR,
        FINANCE_PMF_LEVEL_5_DESC: PMF_ACCOUNT_L5_DESCR,
        SA_RWA_AMT: SA_RWA,
        MNGD_SGMT_L2_CDE: MANAGED_SGMNT_L2_ID,
        MNGD_SGMT_L4_CDE: MANAGED_SGMNT_L4_ID,
        MNGD_SGMT_L3_CDE: MANAGED_SGMNT_L3_ID,
        "RWA Exposure Type Description": RWA_EXPOSURE_TYPE,
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

    Args:
        input_df: DataFrame with MANAGED_SEGMENT_L2_DESCR and RWA_EXPOSURE_TYPE.

    Returns:
        input_df with MARKETS_FILTER column added.
    """
    input_df[MARKETS_FILTER] = np.where(
        (input_df[MANAGED_SEGMENT_L2_DESCR] == MARKETS_L2)
        & (input_df[RWA_EXPOSURE_TYPE] == 0),
        "Keep",
        "Remove",
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
