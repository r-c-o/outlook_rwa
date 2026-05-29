"""Core computation functions for Outlook RWA pipeline.

Provides RWA calculation (SA, AA, ERBA methods), waterfall key computation,
data transformation, pivot operations, and column formatting utilities used
by both convergence and outlook RWA stages.
"""
import re
import warnings
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from dateutil.relativedelta import relativedelta
import yaml
from .constants import (
    AA_ACCOUNT_NUM,
    AA_RWA,
    AA_RWF,
    ADV_CBNA_TOTAL_RWA_AMT,
    ADV_CG_TOTAL_RWA_AMT,
    DISCONTINUED_OPS_L2,
    ERBA_RWA,
    FINANCE_PMF_LEVEL_5_DESC,
    GAAP_AMOUNT,
    LATIN_AMERICA,
    LEGACY_FRANCHISES_L3,
    LEGACY_HOLDINGS_ASSETS_L4,
    MANAGED_GEO_L3_DESC,
    MANAGED_GEO_L4_DESC,
    MANAGED_GEOGRAPHY_L3_DESCR,
    MANAGED_SEGMENT_L2_DESCR,
    MANAGED_SEGMENT_L3_DESCR,
    MANAGED_SEGMENT_L4_DESCR,
    MANAGED_SGMNT_L2_DESC,
    MANAGED_SGMNT_L2_ID,
    MANAGED_SGMNT_L3_DESC,
    MANAGED_SGMNT_L3_ID,
    MANAGED_SGMNT_L4_DESC,
    MANAGED_SGMNT_L4_ID,
    MARKETS_FILTER,
    MARKETS_L2,
    MNGD_SGMT_L2_DESC,
    NON_CREDIT_RISK_PMF,
    PMF_ACCOUNT_L5_DESCR,
    PMF_ACCT_L5_DESC,
    QRTR_ID,
    QUARTER_ID,
    REPORTABLE_ENTITY_IS_CBNA,
    REPORTABLE_ENTITY_IS_CG,
    REPORTING_LAYER,
    RWA_CALC,
    RWA_EXPOSURE_TYPE,
    SA_ACCOUNT_NUM,
    SA_RWA,
    SA_RWA_AMT,
    SA_RWF,
    UPLOAD_TEMPLATE_COL_ORDER,
    UPLOAD_TEMPLATE_MONTH_STUBS,
)
from .transforms import (
    BALANCE_SHEET_MONTH_COLS,
    DEFAULT_AA_ACCOUNT,
    DEFAULT_SA_ACCOUNT,
    MONTH_COL_ORDER,
    UPLOAD_STUB_DEFAULTS,
)


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
    """Load config.yaml and merge an optional git-ignored config.local.yaml.

    Machine-specific values (paths, Q0) belong in config.local.yaml so they stay
    out of version control and never conflict on `git pull`. Falls back to
    config.yaml alone when no local override exists.

    Args:
        config_dir: Path to the directory containing `config.yaml` (and
            optionally `config.local.yaml`).

    Returns:
        Merged configuration dict with local overrides applied.

    Raises:
        FileNotFoundError: If `config.yaml` does not exist in `config_dir`.
    """
    config_dir = Path(config_dir)
    with open(config_dir / "config.yaml", "r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    local_path = config_dir / "config.local.yaml"
    if local_path.exists():
        with open(local_path, "r", encoding="utf-8") as fh:
            local = yaml.safe_load(fh) or {}
        config = _deep_merge(config, local)
    return config


def _int_str(series: pd.Series) -> pd.Series:
    """Convert float-typed integer columns to clean int strings (e.g. 4.0 → '4')."""
    return pd.to_numeric(series, errors="coerce").apply(
        lambda x: str(int(x)) if pd.notna(x) else str(x)
    )


def assign_quarter_id(outlook_df, quarter_id_mapping):
    """Assign Quarter Id to the outlook DataFrame based on YEAR and Month columns.

    Uses the provided mapping. If no match is found, assigns 'Unknown'.
    Modifies the DataFrame in place.

    Args:
        outlook_df: DataFrame with YEAR and Month columns to map onto quarter IDs.
        quarter_id_mapping: Dict mapping (year, month_abbr) tuples to quarter ID strings.
    """
    outlook_df[QRTR_ID] = outlook_df[["YEAR", "Month"]].apply(
        lambda row: quarter_id_mapping.get((row["YEAR"], row["Month"]), "Unknown"), axis=1
    )


def assign_year_month_from_quarter(
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna,
        quarter_map):
    """Derive YEAR and Month from Quarter Id — the inverse of assign_quarter_id.

    Used after the add-on pivot, where the descriptor rows survive via the pivot
    index (which carries Quarter Id) but YEAR / Month were dropped. Rows whose
    Quarter Id is not a known quarter (e.g. 'Unknown') get YEAR / Month = NA,
    matching the pre-pivot behaviour for unparseable Projected Quarters.

    Args:
        cg_addon_markets_credit_risk: CG Markets credit-risk add-on DataFrame;
            modified in place.
        cbna_addon_markets_credit_risk: CBNA Markets credit-risk add-on DataFrame;
            modified in place.
        non_credit_risk_non_waterfall_cg: CG non-waterfall non-credit-risk DataFrame;
            modified in place.
        non_credit_risk_non_waterfall_cbna: CBNA non-waterfall non-credit-risk DataFrame;
            modified in place.
        quarter_map: Dict mapping quarter number to (year, month_abbr) tuple, as
            returned by build_quarter_mappings.

    Raises:
        Exception: Re-raises any exception after emitting a warning, so callers
            see the original error.
    """
    try:
        year_map = {int(k): v[0] for k, v in quarter_map.items()}
        month_map = {int(k): v[1] for k, v in quarter_map.items()}
        for name, df in {
            'cg_addon_markets_credit_risk': cg_addon_markets_credit_risk,
            'cbna_addon_markets_credit_risk': cbna_addon_markets_credit_risk,
            'non_credit_risk_non_waterfall_cg': non_credit_risk_non_waterfall_cg,
            'non_credit_risk_non_waterfall_cbna': non_credit_risk_non_waterfall_cbna
        }.items():
            q = pd.to_numeric(df[QRTR_ID], errors="coerce").astype("Int64")

            bad_vals = df.loc[q.isna() & df[QRTR_ID].notna(), QRTR_ID].drop_duplicates().to_list()
            if bad_vals:
                warnings.warn(
                    f"{name}: Unrecognized Quarter Id values "
                    f"(set to NA for YEAR/Month): {bad_vals}"
                )

            df['YEAR'] = q.map(year_map).astype("Int64")
            df['Month'] = q.map(month_map)
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
    """Compute SA RWA via the waterfall RWF and store results in-place.

    Applies the first valid SA RWF from the waterfall key columns (Key1 through
    KeyN) to the Balances column. Non-credit-risk PMF accounts are zeroed out.
    Modifies df in place.

    Args:
        df: Outlook DataFrame containing Balances, SA RWF, any SA RWF_keyN columns,
            and PMF_ACCT_L5_DESC.
    """
    key_cols = sorted([c for c in df.columns if c.startswith("SA RWF_key")])
    rwf_columns = [SA_RWF] + key_cols
    df["FINAL_SA_RWF"] = _first_valid_rwf(df, rwf_columns)
    df[SA_RWA] = np.where(
        df[PMF_ACCT_L5_DESC].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_SA_RWF"],
    )


def calculate_aa_rwa(df):
    """Compute AA RWA via the waterfall RWF and store results in-place.

    Applies the first valid AA RWF from the waterfall key columns (Key1 through
    KeyN) to the Balances column. Non-credit-risk PMF accounts are zeroed out.
    Modifies df in place.

    Args:
        df: Outlook DataFrame containing Balances, AA RWF, any AA RWF_keyN columns,
            and PMF_ACCT_L5_DESC.
    """
    key_cols = sorted([c for c in df.columns if c.startswith("AA RWF_key")])
    rwf_columns = [AA_RWF] + key_cols
    df["FINAL_AA_RWF"] = _first_valid_rwf(df, rwf_columns)
    df[AA_RWA] = np.where(
        df[PMF_ACCT_L5_DESC].isin(NON_CREDIT_RISK_PMF),
        0,
        pd.to_numeric(df["Balances"], errors="coerce") * df["FINAL_AA_RWF"],
    )


def assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook):
    """Assign ERBA RWA and Comment columns to CG and CBNA outlook DataFrames.

    ERBA RWA is set to SA RWA where QRTR_ID is 5 or 6 (int or string),
    else NaN. Comment is set to empty string. Modifies DataFrames in place.

    Args:
        cg_outlook: CG outlook DataFrame; modified in place.
        cbna_outlook: CBNA outlook DataFrame; modified in place.
    """
    cg_outlook[ERBA_RWA] = cg_outlook[SA_RWA].where(
        cg_outlook[QRTR_ID].isin([5, 6]) |
        cg_outlook[QRTR_ID].isin(['5', '6']))
    cbna_outlook[ERBA_RWA] = cbna_outlook[SA_RWA].where(
        cbna_outlook[QRTR_ID].isin([5, 6]) |
        cbna_outlook[QRTR_ID].isin(['5', '6']))
    cg_outlook["Comment"] = ""

    cbna_outlook["Comment"] = ""



def split_convergence(convergence, pmf_accounts, markets_l2):
    """Split convergence into mutually exclusive credit-risk, non-waterfall, and Markets add-on
    buckets.

    Args:
        convergence: Raw convergence DataFrame containing entity flags, PMF L5
            descriptions, and managed segment L2 descriptions.
        pmf_accounts: Collection of PMF L5 description values that qualify as
            credit-risk accounts.
        markets_l2: Managed Segment L2 description string identifying the Markets
            segment (used for the add-on bucket).

    Returns:
        Tuple of six DataFrames:
        (credit_risk_convergence_cg, credit_risk_convergence_cbna,
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna,
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk).
    """
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


def build_markets_addon_pivot(
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
        addon_pivot_index):
    """Pivot (sum) the Markets credit-risk add-on for CG and CBNA.

    Collapses the raw convergence rows to one row per `addon_pivot_index`
    combination, summing the additive RWA amounts. Without this aggregation the
    add-on export carries one row per raw record (surplus rows).

    Args:
        cg_addon_markets_credit_risk: CG Markets add-on DataFrame filtered from
            convergence.
        cbna_addon_markets_credit_risk: CBNA Markets add-on DataFrame filtered from
            convergence.
        addon_pivot_index: List of column names to use as the pivot index.

    Returns:
        Tuple of (pivoted_cg, pivoted_cbna) DataFrames with SA/AA RWA amounts
        summed per index group.
    """
    pivoted_cg = cg_addon_markets_credit_risk.pivot_table(
        values=[SA_RWA_AMT, ADV_CG_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()
    pivoted_cbna = cbna_addon_markets_credit_risk.pivot_table(
        values=[SA_RWA_AMT, ADV_CBNA_TOTAL_RWA_AMT], index=addon_pivot_index, aggfunc="sum"
    ).reset_index()
    return pivoted_cg, pivoted_cbna


def build_addon_pivot(
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna,
        addon_pivot_index):
    """Pivot (sum) the non-waterfall non-credit-risk add-on for CG and CBNA.

    Fills null PMF L5 keys so those rows survive the pivot, sums the additive
    RWA amounts to one row per `addon_pivot_index` combination, then derives
    ERBA RWA (= SA RWA Amount in quarters 5/6) and a blank Comment.

    Args:
        non_credit_risk_non_waterfall_cg: CG non-waterfall non-credit-risk DataFrame
            filtered from convergence.
        non_credit_risk_non_waterfall_cbna: CBNA non-waterfall non-credit-risk DataFrame
            filtered from convergence.
        addon_pivot_index: List of column names to use as the pivot index.

    Returns:
        Tuple of (pivoted_cg, pivoted_cbna) DataFrames with ERBA RWA and Comment
        columns set.
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
        pivoted[ERBA_RWA] = pivoted[SA_RWA_AMT].where(
            pivoted[QRTR_ID].isin([5, 6]) |
            pivoted[QRTR_ID].isin(['5', '6']))
        pivoted["Comment"] = ""
    return pivoted_cg, pivoted_cbna


def create_key_pivots(crd_df, adv_rwa_col, key_defs):
    """Create pivot tables for a given entity's credit-risk data, one per key_def.

    Args:
        crd_df: Credit-risk convergence DataFrame for one entity (CG or CBNA).
        adv_rwa_col: Name of the Adv. RWA column specific to this entity
            (e.g. ADV_CG_TOTAL_RWA_AMT or ADV_CBNA_TOTAL_RWA_AMT).
        key_defs: List of waterfall key definition dicts from config, each
            specifying the convergence columns that form the pivot index.

    Returns:
        List of pivot DataFrames (one per key_def), indexed by the key columns
        and summing GAAP Amount, SA RWA Amount, and the entity Adv. RWA column.
    """
    pivots = []
    for key_def in key_defs:
        seen = set()
        index = []
        for col in [QRTR_ID] + [f["convergence_col"] for f in key_def["fields"]]:
            if col not in seen:
                seen.add(col)
                index.append(col)
        pivots.append(crd_df.pivot_table(
            values=[GAAP_AMOUNT, SA_RWA_AMT, adv_rwa_col],
            index=index,
            aggfunc="sum",
        ))
    return pivots


def compute_rwf(key_df, adv_rwa_col):
    """Compute SA RWF and AA RWF, cap at abs(12.5), set out-of-range to 1.

    Args:
        key_df: Pivot DataFrame (from create_key_pivots) containing GAAP Amount,
            SA RWA Amount, and the entity Adv. RWA column.
        adv_rwa_col: Name of the Adv. RWA column to use for AA RWF computation.

    Returns:
        key_df with SA_RWF and AA_RWF columns added/updated in place.
    """
    key_df[SA_RWF] = (
        pd.to_numeric(key_df[SA_RWA_AMT], errors="coerce")
        / pd.to_numeric(key_df[GAAP_AMOUNT], errors="coerce")
    )
    key_df.loc[key_df[SA_RWF].abs() > 12.5, SA_RWF] = 1
    key_df[AA_RWF] = (
        pd.to_numeric(key_df[adv_rwa_col], errors="coerce")
        / pd.to_numeric(key_df[GAAP_AMOUNT], errors="coerce")
    )
    key_df.loc[key_df[AA_RWF].abs() > 12.5, AA_RWF] = 1
    return key_df


def set_markets_rwf(key_df):
    """Null out RWFs for Markets rows (they get add-on treatment instead).

    Args:
        key_df: Pivot DataFrame with a MNGD_SGMT_L2_DESC index level and SA_RWF /
            AA_RWF columns.

    Returns:
        key_df with SA_RWF and AA_RWF set to NaN for Markets-segment rows.
    """
    is_markets = key_df.index.get_level_values(MNGD_SGMT_L2_DESC).isin([MARKETS_L2])
    key_df[SA_RWF] = key_df[SA_RWF].where(~is_markets)
    key_df[AA_RWF] = key_df[AA_RWF].where(~is_markets)
    return key_df


def build_outlook_key_strings(outlook_df, key_defs):
    """Build composite key strings for the waterfall on an outlook DataFrame.

    Writes Key1…KeyN columns by concatenating the outlook columns specified in
    each key_def plus the Quarter Id. Modifies the DataFrame in place.

    Args:
        outlook_df: Long-format outlook DataFrame to annotate with key columns.
        key_defs: List of waterfall key definition dicts from config, each
            specifying outlook_col, int_str, and pivot_only fields.
    """
    for i, key_def in enumerate(key_defs):
        parts = []
        for f in key_def["fields"]:
            if f.get("pivot_only"):
                continue
            col = outlook_df[f["outlook_col"]]
            parts.append(_int_str(col) if f.get("int_str") else col.astype(str))
        parts.append(outlook_df[QRTR_ID].astype(str))
        outlook_df[f"Key{i + 1}"] = parts[0]
        for part in parts[1:]:
            outlook_df[f"Key{i + 1}"] = outlook_df[f"Key{i + 1}"] + part


def rename_month_columns(df):
    """Rename M*_USDOLLAR columns to quarter month names (Mar, Jun, Sep, Dec).

    Modifies df in place.

    Args:
        df: Balance sheet DataFrame containing M3_USDOLLAR, M6_USDOLLAR,
            M9_USDOLLAR, and M12_USDOLLAR columns.
    """
    for src, label in BALANCE_SHEET_MONTH_COLS.items():
        df[label] = df[src]


def create_quarterly_pivot(df):
    """Pivot the balance sheet DataFrame to sum quarterly balances by key dimensions.

    Args:
        df: Balance sheet DataFrame with Mar/Jun/Sep/Dec balance columns and the
            standard segment/geography/PMF index columns.

    Returns:
        Pivoted DataFrame with one row per unique index combination and summed
        quarterly balance columns.
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
        values=MONTH_COL_ORDER,
        aggfunc="sum",
    ).reset_index()


def melt_quarterly_pivot(pivot_df):
    """Melt a quarterly pivot DataFrame to long format with Month and Balances columns.

    Args:
        pivot_df: Wide-format DataFrame from create_quarterly_pivot with Mar/Jun/Sep/Dec
            value columns.

    Returns:
        Long-format DataFrame with Month (Mar/Jun/Sep/Dec) and Balances columns,
        one row per index-combination/month pair.
    """
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
        value_vars=MONTH_COL_ORDER,
        var_name="Month",
        value_name="Balances",
    )


def check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook):
    """Check that quarter counts match across all inputs and return the maximum.

    Emits a warning if the number of unique quarters differs between convergence
    and either outlook. Prints a confirmation message when all three agree.

    Args:
        convergence: Convergence DataFrame with a 'Quarter Id' column.
        cg_outlook: CG long-format outlook DataFrame with YEAR and Month columns.
        cbna_outlook: CBNA long-format outlook DataFrame with YEAR and Month columns.

    Returns:
        Maximum number of quarters found across all three inputs.
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

    if num_convergence_quarters != num_cg_quarters or num_cg_quarters != num_cbna_quarters:
        warnings.warn(
            f"Quarter count mismatch: "
            f"convergence={num_convergence_quarters}, "
            f"CG outlook={num_cg_quarters}, "
            f"CBNA outlook={num_cbna_quarters}"
        )
    else:
        print(
            f"✅ Quarter counts match across convergence and both outlooks: "
            f"{num_convergence_quarters}"
        )

    max_quarters = max(num_convergence_quarters, num_cg_quarters, num_cbna_quarters)
    print(f"Max quarters found: {max_quarters}")
    return max_quarters


def build_quarter_mappings(Q0, max_quarters):
    """Build quarter_map and quarter_id_mapping based on Q0 and max_quarters.

    Args:
        Q0: Base quarter date string in 'Mon YYYY' format (e.g. 'Mar 2024'),
            representing the first outlook quarter.
        max_quarters: Total number of quarters to map; each quarter spans 3 months.

    Returns:
        Tuple of (quarter_map, quarter_id_mapping) where quarter_map is a dict
        of quarter_number -> (year, month_abbr) and quarter_id_mapping is a dict
        of (year, month_abbr) -> quarter_number_str.
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

def _apply_waterfall_lookups(outlook_df, lookups, key_defs):
    """Merge the convergence pivot RWF tables onto an outlook DataFrame."""
    for i, (lk_df, key_def) in enumerate(zip(lookups, key_defs)):
        lk = lk_df.reset_index()
        parts = []
        for f in key_def["fields"]:
            if f.get("pivot_only"):
                continue
            col = lk[f["convergence_col"]]
            parts.append(_int_str(col) if f.get("int_str") else col.astype(str))
        parts.append(_int_str(lk[QRTR_ID]))
        lk["_key"] = parts[0]
        for part in parts[1:]:
            lk["_key"] = lk["_key"] + part
        if i == 0:
            rename_map = {SA_RWF: SA_RWF, AA_RWF: AA_RWF}
        else:
            rename_map = {SA_RWF: f"SA RWF_key{i + 1}", AA_RWF: f"AA RWF_key{i + 1}"}
        outlook_df = outlook_df.merge(
            lk[["_key", SA_RWF, AA_RWF]].rename(columns=rename_map),
            left_on=f"Key{i + 1}", right_on="_key", how="left",
        ).drop(columns=["_key"])
    return outlook_df


# =============================================================================
# Outlook RWA stage: adjustments, addon, pivots, upload template, controls
# =============================================================================

def format_adjustments(input_df):
    """Coerce RWF/Balances columns to numeric, then fill NaN (0 numeric, 'N/A' text).

    Args:
        input_df: Adjustments DataFrame with Balances, SA RWF, AA RWF, and any
            SA/AA RWF_keyN columns.

    Returns:
        input_df with numeric columns coerced and NaN filled (0 for numeric,
        'N/A' for string columns).
    """
    key_rwf_cols = sorted([c for c in input_df.columns if re.match(r"(SA|AA) RWF_key\d+$", c)])
    cols_to_num = ["Balances", "SA RWF", "AA RWF"] + key_rwf_cols
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

    Args:
        input_df: Add-on DataFrame with convergence-style long column names.
        entity: Entity identifier string, either 'CG' or 'CBNA', used to select
            the correct Adv. RWA column.

    Returns:
        New DataFrame with convergence column names replaced by outlook-style
        short names and collision columns dropped.
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

    legacy_non_holdings = legacy[
        legacy[MANAGED_SEGMENT_L4_DESCR] != LEGACY_HOLDINGS_ASSETS_L4
    ].copy()

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
    for col in [
        MANAGED_SEGMENT_L4_DESCR, MANAGED_SEGMENT_L3_DESCR, MANAGED_SEGMENT_L2_DESCR,
        PMF_ACCOUNT_L5_DESCR, 'Entity', REPORTING_LAYER,
        SA_ACCOUNT_NUM, AA_ACCOUNT_NUM, 'PUG',
    ]:
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
    input_df[QUARTER_ID] = (
        pd.to_numeric(input_df[QUARTER_ID], errors="coerce").fillna(0).astype(int)
    )

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

    Args:
        input_df: Concatenated pivot DataFrame from create_upload_template_pivots
            containing ERBA, AA, and SA rows with RWA_CALC, SA_ACCOUNT_NUM,
            and AA_ACCOUNT_NUM columns.

    Returns:
        Formatted DataFrame in the production upload column order, with stub
        columns filled and numeric columns zeroed where NaN.
    """
    input_df = input_df.copy()

    numeric_cols = input_df.select_dtypes(include=['number']).columns
    input_df[numeric_cols] = input_df[numeric_cols].fillna(0)

    # Fixed upload stub columns (values defined centrally in transforms.py)
    for col, val in UPLOAD_STUB_DEFAULTS.items():
        input_df[col] = val

    # Account: AA -> AA account #, SA -> SA account #, otherwise N/A
    input_df["Account"] = np.where(
        input_df[RWA_CALC] == "AA",
        input_df[AA_ACCOUNT_NUM],
        np.where(input_df[RWA_CALC] == "SA", input_df[SA_ACCOUNT_NUM], "N/A"),
    )
    # Default account numbers where the PMF mapping was missing ('None')
    input_df["Account"] = np.where(
        (input_df[RWA_CALC] == "AA") & (input_df["Account"] == "None"),
        DEFAULT_AA_ACCOUNT, input_df["Account"],
    )
    input_df["Account"] = np.where(
        (input_df[RWA_CALC] == "SA") & (input_df["Account"] == "None"),
        DEFAULT_SA_ACCOUNT, input_df["Account"],
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

    Args:
        convergence_df: Raw convergence DataFrame with entity flags, segment
            descriptions, and RWA amount columns.
        entity_filter_col: Column name whose value 'Y' identifies rows belonging
            to the target entity (REPORTABLE_ENTITY_IS_CG or REPORTABLE_ENTITY_IS_CBNA).
        adv_rwa_col: Name of the Adv. RWA column to rename to AA RWA for this entity.

    Returns:
        Wide-format control DataFrame indexed by L2 segment and RWA Calc type,
        with one column per Quarter Id.
    """
    mnged = "Managed Segment Level 2 Description"
    ctrl = convergence_df[convergence_df[entity_filter_col] == "Y"].copy()
    ctrl = ctrl[ctrl[mnged] != DISCONTINUED_OPS_L2]
    ctrl = ctrl.rename(columns={adv_rwa_col: AA_RWA, "SA RWA Amount": SA_RWA,
                                mnged: MANAGED_SEGMENT_L2_DESCR})
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

    Args:
        frm_output_df: Formatted upload template DataFrame from format_upload_template
            with RWA Actuals and integer quarter columns 1-7.

    Returns:
        Summary DataFrame grouped by L2 segment and RWA Calc, with ERBA rows
        excluded and RWA Calc values mapped to canonical AA/SA RWA names.
    """
    ctrl = frm_output_df.groupby([MANAGED_SEGMENT_L2_DESCR, RWA_CALC]).agg(
        {"RWA Actuals": "sum", 1: "sum", 2: "sum", 3: "sum", 4: "sum",
         5: "sum", 6: "sum", 7: "sum"}).reset_index()
    ctrl = ctrl.rename(columns={"RWA Actuals": 0})
    ctrl[RWA_CALC] = ctrl[RWA_CALC].map({"AA": AA_RWA, "SA": SA_RWA})
    ctrl = ctrl[ctrl[RWA_CALC].isin([AA_RWA, SA_RWA])]
    return ctrl


def build_raw_data_control(raw_data_df):
    """Summarise raw data SA/AA RWA by L2 segment x quarter for the control file.

    Args:
        raw_data_df: Raw data DataFrame (pre-legacy-breakout) with MANAGED_SEGMENT_L2_DESCR,
            QUARTER_ID, SA_RWA, and AA_RWA columns.

    Returns:
        Wide-format control DataFrame indexed by L2 segment and RWA Calc type,
        with one column per Quarter Id.
    """
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



def concat_addon_all(
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna):
    """Concatenate Markets and non-waterfall add-on frames for each entity.

    Args:
        cg_addon_markets_credit_risk: Pivoted CG Markets credit-risk add-on DataFrame.
        cbna_addon_markets_credit_risk: Pivoted CBNA Markets credit-risk add-on DataFrame.
        non_credit_risk_non_waterfall_cg: Pivoted CG non-waterfall non-credit-risk DataFrame.
        non_credit_risk_non_waterfall_cbna: Pivoted CBNA non-waterfall non-credit-risk DataFrame.

    Returns:
        Tuple of (cg_addon_non_waterfall_rwa, cbna_addon_non_waterfall_rwa) DataFrames,
        each combining Markets and non-waterfall rows for the respective entity.
    """
    cg_addon_non_waterfall_rwa = pd.concat(
        [cg_addon_markets_credit_risk, non_credit_risk_non_waterfall_cg],
        ignore_index=True,
    )
    cbna_addon_non_waterfall_rwa = pd.concat(
        [cbna_addon_markets_credit_risk, non_credit_risk_non_waterfall_cbna],
        ignore_index=True,
    )
    return cg_addon_non_waterfall_rwa, cbna_addon_non_waterfall_rwa
