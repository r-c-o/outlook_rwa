"""
Step 1: Model Convergence
Reads balance sheet and convergence data, builds 5-key RWF waterfall lookups,
applies them to outlook DataFrames, and exports parquet outputs for Step 2.

Prerequisite: schema_registry.csv must exist (run create_schema_csv.py first).
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import warnings
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

import toml
from functions import (
    _int_str,
    assign_quarter_id,
    calculate_sa_rwa,
    calculate_aa_rwa,
    assign_erba_rwa_and_metadata,
    split_convergence,
    create_key_pivots,
    compute_rwf,
    set_markets_rwf,
    build_outlook_key_strings,
    rename_month_columns,
    create_quarterly_pivot,
    melt_quarterly_pivot,
    check_and_get_max_quarters,
    build_quarter_mappings,
)
from parallel_excel_to_parquet import (
    load_schema_registry_from_csv,
    convert_files_to_parquet,
    ExcelInputSpec,
)
from constants import (
    ADV_CG_TOTAL_RWA_AMT,
    ADV_CBNA_TOTAL_RWA_AMT,
    PMF_ACCOUNTS,
    MARKETS_L2,
    QRTR_ID,
    SA_RWF,
    AA_RWF,
    SA_RWA,
    AA_RWA,
    ERBA_RWA,
    REPORTABLE_ENTITY_IS_CG,
    REPORTABLE_ENTITY_IS_CBNA,
    FINANCE_PMF_LEVEL_5_DESC,
    GAAP_AMOUNT,
    SA_RWA_AMT,
    MNGD_SGMT_L4_CDE,
    MNGD_SGMT_L3_CDE,
    MNGD_SGMT_L2_CDE,
    MNGD_GEO_L4_DESC,
    MNGD_GEO_L3_DESC,
    MNGD_SGMT_L2_DESC,
    MANAGED_SGMNT_L4_DESC,
    MANAGED_SGMNT_L3_DESC,
    MANAGED_SGMNT_L2_DESC,
    MANAGED_GEO_L4_DESC,
    MANAGED_GEO_L3_DESC,
    PMF_ACCT_L5_DESC,
    MANAGED_SGMNT_L4_ID,
    MANAGED_SGMNT_L3_ID,
    MANAGED_SGMNT_L2_ID,
)

pd.set_option("display.max_columns", 500)

# =============================================================================
# PARAMETERS — update before running
# =============================================================================

# Load config
config_path = Path(__file__).parent.parent.parent.parent / "config.toml"
config = toml.load(config_path)

schema_csv = Path(config["paths"]["schema_registry_csv"])
if not schema_csv.exists() and "schema_registry_csv_backup" in config["paths"]:
    schema_csv = Path(config["paths"]["schema_registry_csv_backup"])

# Starting quarter (first quarter of the projection horizon, e.g. "Mar 2025")
Q0 = config["parameters"]["Q0"]

# =============================================================================
# 1. Read Input Files
# =============================================================================

data_dir = Path(config["paths"]["data_dir"])
if not data_dir.exists() and "data_dir_backup" in config["paths"]:
    data_dir = Path(config["paths"]["data_dir_backup"])
input_dir = data_dir / "input"
output_dir = Path(config["outputs"]["step1_dir"])
if not output_dir.exists() and "step1_dir_backup" in config["outputs"]:
    output_dir = Path(config["outputs"]["step1_dir_backup"])
output_dir.mkdir(parents=True, exist_ok=True)
schema_registry = load_schema_registry_from_csv(schema_csv)

_dtype_compat = {
    "int8": "float64", "int16": "float64", "int32": "float64", "int64": "float64",
    "uint8": "float64", "uint16": "float64", "uint32": "float64", "uint64": "float64",
    "boolean": "object", "bool": "object",
    "string": "object", "utf8": "object", "large_string": "object", "large_utf8": "object",
    "categorical": "object", "date": "object", "duration": "object",
    "datetime": "datetime64[ns]",
}
_flat_schema = {col: np.dtype(_dtype_compat.get(str(dtype).lower(), str(dtype).lower()))
                for d in schema_registry.values()
                for col, dtype in d.items()}

# %%
_input_specs = [
    ExcelInputSpec("cg", input_dir / "outlook_balancesheet_cg.xlsx", "balancesheet", "outlook_balancesheet_cg.parquet"),
    ExcelInputSpec("cbna", input_dir / "outlook_balancesheet_cbna.xlsx", "balancesheet", "outlook_balancesheet_cbna.parquet"),
    ExcelInputSpec("convergence", input_dir / "aggregator_for_convergence.xlsx", "convergence", "aggregator_for_convergence.parquet"),
    ExcelInputSpec("cg_adjustments", input_dir / "adjustment_master_file.xlsx", "adjustments", "adjustments_cg.parquet", "Adjustments - CG"),
    ExcelInputSpec("cbna_adjustments", input_dir / "adjustment_master_file.xlsx", "adjustments", "adjustments_cbna.parquet", "Adjustments - CBNA"),
]


def _read_parquets():
    def _load(name):
        df = pd.read_parquet(output_dir / name)
        return df.astype({c: _flat_schema[c] for c in df.columns if c in _flat_schema}, errors="ignore")
    return (
        _load("outlook_balancesheet_cg.parquet"),
        _load("outlook_balancesheet_cbna.parquet"),
        _load("aggregator_for_convergence.parquet"),
        _load("adjustments_cg.parquet"),
        _load("adjustments_cbna.parquet"),
    )


try:
    src_cg, src_cbna, src_convergence, src_cg_adjustments, src_cbna_adjustments = _read_parquets()
except Exception as e:
    print(f"Error reading parquet files: {e}")
    print("Parquet files missing — building them from Excel via the parallel loader...")
    convert_files_to_parquet(_input_specs, output_dir, schema_csv)
    src_cg, src_cbna, src_convergence, src_cg_adjustments, src_cbna_adjustments = _read_parquets()

cg = src_cg.copy(deep=True)
cbna = src_cbna.copy(deep=True)
convergence = src_convergence.copy(deep=True)
cg_adjustments = src_cg_adjustments.copy(deep=True)
cbna_adjustments = src_cbna_adjustments.copy(deep=True)

print(f"✅ CG rows:          {len(cg):,}")
print(f"✅ CBNA rows:        {len(cbna):,}")
print(f"✅ Convergence rows: {len(convergence):,}")

# =============================================================================
# 2. Merge Geography Level 3 into Convergence
# =============================================================================

dummy_df = cg[["Managed Geography L3 Descr", "Managed Geography L4 Descr"]].drop_duplicates()
dummy_df = dummy_df.rename(columns={
    "Managed Geography L3 Descr": "Managed Geography Level 3 Description",
    "Managed Geography L4 Descr": "Managed Geography Level 4 Description",
})
dummy_df = dummy_df.drop_duplicates(subset="Managed Geography Level 4 Description", keep="first")

if "Managed Geography Level 3 Description" not in convergence.columns:
    convergence = convergence.merge(
        dummy_df[["Managed Geography Level 3 Description", "Managed Geography Level 4 Description"]],
        on="Managed Geography Level 4 Description",
        how="left",
    )

# =============================================================================
# 3. Normalise PMF Account / Finance PMF column types
# =============================================================================

cg["PMF Account L5 Descr"] = cg["PMF Account L5 Descr"].astype(str)
cbna["PMF Account L5 Descr"] = cbna["PMF Account L5 Descr"].astype(str)
convergence["Finance PMF Level 5 Description"] = convergence["Finance PMF Level 5 Description"].astype(str)

# =============================================================================
# 4. Split Convergence into Credit-Risk Buckets
# =============================================================================

(
    credit_risk_convergence_cg,
    credit_risk_convergence_cbna,
    non_credit_risk_non_waterfall_cg,
    non_credit_risk_non_waterfall_cbna,
    cg_addon_markets_credit_risk,
    cbna_addon_markets_credit_risk,
) = split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2)

print(f"CG credit-risk rows:   {len(credit_risk_convergence_cg):,}")
print(f"CBNA credit-risk rows: {len(credit_risk_convergence_cbna):,}")

# =============================================================================
# 5. Build 5-Key Pivot Tables and Compute RWFs
# =============================================================================

(
    cg_waterfall_rwf_lookup_1,
    cg_waterfall_rwf_lookup_2,
    cg_waterfall_rwf_lookup_3,
    cg_waterfall_rwf_lookup_4,
    cg_waterfall_rwf_lookup_5,
) = create_key_pivots(credit_risk_convergence_cg, ADV_CG_TOTAL_RWA_AMT)

(
    cbna_waterfall_rwf_lookup_1,
    cbna_waterfall_rwf_lookup_2,
    cbna_waterfall_rwf_lookup_3,
    cbna_waterfall_rwf_lookup_4,
    cbna_waterfall_rwf_lookup_5,
) = create_key_pivots(credit_risk_convergence_cbna, ADV_CBNA_TOTAL_RWA_AMT)

for key_df in [
    cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2,
    cg_waterfall_rwf_lookup_3, cg_waterfall_rwf_lookup_4,
    cg_waterfall_rwf_lookup_5,
]:
    compute_rwf(key_df, ADV_CG_TOTAL_RWA_AMT)
    set_markets_rwf(key_df)

for key_df in [
    cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2,
    cbna_waterfall_rwf_lookup_3, cbna_waterfall_rwf_lookup_4,
    cbna_waterfall_rwf_lookup_5,
]:
    compute_rwf(key_df, ADV_CBNA_TOTAL_RWA_AMT)
    set_markets_rwf(key_df)

# =============================================================================
# 6. Reshape Balance Sheet — Pivot then Melt to Long Format
# =============================================================================

rename_month_columns(cg)
rename_month_columns(cbna)

cg_pivot = create_quarterly_pivot(cg)
cbna_pivot = create_quarterly_pivot(cbna)

cg_outlook = melt_quarterly_pivot(cg_pivot)
cbna_outlook = melt_quarterly_pivot(cbna_pivot)

print(f"CG outlook long rows:   {len(cg_outlook):,}")
print(f"CBNA outlook long rows: {len(cbna_outlook):,}")

# =============================================================================
# 7. Build Quarter Mapping
# =============================================================================

max_quarters = check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook)
quarter_map, quarter_id_mapping = build_quarter_mappings(Q0, max_quarters)

# =============================================================================
# 8. Assign Quarter IDs
# =============================================================================

assign_quarter_id(cg_outlook, quarter_id_mapping)
assign_quarter_id(cbna_outlook, quarter_id_mapping)

# =============================================================================
# 9. Build Key Strings for Waterfall Lookups
# =============================================================================

build_outlook_key_strings(cg_outlook)
build_outlook_key_strings(cbna_outlook)

# =============================================================================
# 9b. Waterfall RWF Lookups — merge pivot RWFs onto outlook DataFrames
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


cg_outlook = _apply_waterfall_lookups(
    cg_outlook,
    cg_waterfall_rwf_lookup_1,
    cg_waterfall_rwf_lookup_2,
    cg_waterfall_rwf_lookup_3,
    cg_waterfall_rwf_lookup_4,
    cg_waterfall_rwf_lookup_5,
)

cbna_outlook = _apply_waterfall_lookups(
    cbna_outlook,
    cbna_waterfall_rwf_lookup_1,
    cbna_waterfall_rwf_lookup_2,
    cbna_waterfall_rwf_lookup_3,
    cbna_waterfall_rwf_lookup_4,
    cbna_waterfall_rwf_lookup_5,
)

# =============================================================================
# 10. Apply Adjustments
# =============================================================================

cg_adjustments["Key1"] = (
    _int_str(cg_adjustments["Managed Segment L4 Id"])
    + cg_adjustments["Managed Geography L4 Descr"].astype(str)
    + cg_adjustments["PMF Account L5 Descr"].astype(str)
    + cg_adjustments["Quarter Id"].astype(str)
)

cg_outlook = pd.merge(
    cg_outlook,
    cg_adjustments[[
        "Key1", "Key2", "Key3", "Key4", "Key5",
        "SA RWA", "AA RWA", "ERBA RWA",
        "SA RWF", "AA RWF",
        "SA RWF_key2", "AA RWF_key2",
        "SA RWF_key3", "AA RWF_key3",
        "SA RWF_key4", "AA RWF_key4",
        "SA RWF_key5", "AA RWF_key5",
        "Comment", "RWA Exposure Type",
    ]],
    on="Key1",
    how="left",
    suffixes=("", "_adj"),
)

# same for cbna
cbna_adjustments["Key1"] = (
    _int_str(cbna_adjustments["Managed Segment L4 Id"])
    + cbna_adjustments["Managed Geography L4 Descr"].astype(str)
    + cbna_adjustments["PMF Account L5 Descr"].astype(str)
    + cbna_adjustments["Quarter Id"].astype(str)
)

cbna_outlook = pd.merge(
    cbna_outlook,
    cbna_adjustments[[
        "Key1", "Key2", "Key3", "Key4", "Key5",
        "SA RWA", "AA RWA", "ERBA RWA",
        "SA RWF", "AA RWF",
        "SA RWF_key2", "AA RWF_key2",
        "SA RWF_key3", "AA RWF_key3",
        "SA RWF_key4", "AA RWF_key4",
        "SA RWF_key5", "AA RWF_key5",
        "Comment", "RWA Exposure Type",
    ]],
    on="Key1",
    how="left",
    suffixes=("", "_adj"),
)

# =============================================================================
# 11. Calculate RWA
# =============================================================================

calculate_sa_rwa(cg_outlook)
calculate_aa_rwa(cg_outlook)
calculate_sa_rwa(cbna_outlook)
calculate_aa_rwa(cbna_outlook)

assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook)

# =============================================================================
# 11b. Addon: Markets / Non-Waterfall
# =============================================================================

cg_addon_markets_credit_risk[SA_RWA] = cg_addon_markets_credit_risk[SA_RWA_AMT]
cbna_addon_markets_credit_risk[SA_RWA] = cbna_addon_markets_credit_risk[SA_RWA_AMT]
assign_erba_rwa_and_metadata(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk)

_pq_to_month = {1: "Mar", 2: "Jun", 3: "Sep", 4: "Dec"}
for addon_df in [cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk]:
    q_num = pd.to_numeric(addon_df["Projected Quarter"].str[0], errors="coerce").astype("Int64")
    addon_df["YEAR"] = pd.to_numeric(
        addon_df["Projected Quarter"].str[2:].apply(lambda x: "20" + x if pd.notna(x) else x),
        errors="coerce",
    ).astype("Int64")
    addon_df["Month"] = q_num.map(_pq_to_month)

assign_quarter_id(cg_addon_markets_credit_risk, quarter_id_mapping)
assign_quarter_id(cbna_addon_markets_credit_risk, quarter_id_mapping)

cg_addon_non_waterfall_rwa, cbna_addon_non_waterfall_rwa = (
    pd.concat([non_credit_risk_non_waterfall_cg, cg_addon_markets_credit_risk], ignore_index=True),
    pd.concat([non_credit_risk_non_waterfall_cbna, cbna_addon_markets_credit_risk], ignore_index=True),
)

print(f"CG addon non-waterfall rows:   {len(cg_addon_non_waterfall_rwa):,}")
print(f"CBNA addon non-waterfall rows: {len(cbna_addon_non_waterfall_rwa):,}")

# =============================================================================
# 12. Export Outputs
# =============================================================================

output_files = {
    config["outputs"]["step1"][0]["cg_outlook"]: cg_outlook,
    config["outputs"]["step1"][0]["cbna_outlook"]: cbna_outlook,
    config["outputs"]["step1"][0]["cg_addon_non_waterfall_rwa"]: cg_addon_non_waterfall_rwa,
    config["outputs"]["step1"][0]["cbna_addon_non_waterfall_rwa"]: cbna_addon_non_waterfall_rwa,
}

for fname, df in output_files.items():
    schema = {
        col: str(dtype).lower()
        for dataset, dtype_dict in schema_registry.items()
        for col, dtype in dtype_dict.items()
        if col in df.columns
    }
    out_path = output_dir / fname
    df.to_excel(out_path, index=False)
    print(f"✅ Written: {fname}  ({len(df):,} rows)")
