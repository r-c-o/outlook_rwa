# %% [markdown]
# # Step 1: Model Convergence
#
# Merges outlook balance sheet data with convergence data using a waterfall join
# (Key1→Key5), computes SA/AA Risk Weight Factors (RWF), and produces:
#   > cg_outlook.xlsx, cbna_outlook.xlsx (waterfall RWA)
#   > cbna_addon_non_waterfall_rwa.xlsx
#
# **How to use:** Update the PARAMETERS cell below, then run all cells top-to-bottom.

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

from functions import *
pd.set_option("display.max_columns", 500)

# %%
# Start timing the script
start_time = time.time()

# ==============================================================================
# PARAMETERS — Update these before each run
# ==============================================================================

config_path = Path(
    r"C:\Users\rl09895\project_home\outlook-rwa-release\outlook-rwa-app\src\main\tools\config.toml"
)
config        = toml.load(config_path)
global_params = config["global_params"]

# Paths and filenames from config
QO                    = global_params["QO"]
period                = global_params["period"]
data_dir              = Path(global_params["data_dir"])
input_dir             = Path(global_params["input_dir"])
output_dir            = Path(global_params["output_dir"])
model_convergence_dir = Path(global_params["model_convergence_dir"])
parquet_dir           = input_dir / "parquet_cache"
schema_csv            = Path(global_params["schema_registry_path"])

# Build input file map from config manifest
step1_inputs = {
    spec["variable_name"]: input_dir / spec["filename"]
    for spec in config["inputs"]["step1"]
}
check_input_files_exist(list(step1_inputs.values()))

# %%
# Excel dtype specs for parallel parquet conversion
excel_dtype_specs = {
    var: {
        "variable_name": var,
        "input_path":    str(path),
        "schema_key":    next(
            s["schema_key"] for s in config["inputs"]["step1"]
            if s["variable_name"] == var
        ),
    }
    for var, path in step1_inputs.items()
}

# %%
# Export Excel → Parquet (parallel; skips already-converted files)
results = export_excel_specs_to_parquet(
    file_specs=[excel_dtype_specs[k] for k in excel_dtype_specs],
    output_dir=parquet_dir,
    schema_registry_csv=schema_csv,
    if_exists="new",
)

for var_name, export_results in results.items():
    excel_dtype_specs[var_name]["parquet_export_results"] = export_results

excel_dtype_specs

# %%
# Load parquet into pandas (no Excel reads anymore)
cg_balancesheet   = pd.read_parquet(excel_dtype_specs["cg_balancesheet"]["parquet_export_results"]["output_path"])
cbna_balancesheet = pd.read_parquet(excel_dtype_specs["cbna_balancesheet"]["parquet_export_results"]["output_path"])
convergence       = pd.read_parquet(excel_dtype_specs["convergence"]["parquet_export_results"]["output_path"])

print(f"CG rows:          {len(cg_balancesheet):,}")
print(f"CBNA rows:        {len(cbna_balancesheet):,}")
print(f"Convergence rows: {len(convergence):,}")

print(f"⏱ Time to load input files: {time.time() - start_time:.2f} seconds")

# ==============================================================================
# 1. Data Quality: Check expected columns
# ==============================================================================
# %%
EXPECTED_BALANCESHEET_COLS = [
    "YEAR", "Month", GAAP_AMOUNT, SA_RWA_AMT,
    MNGD_SGMT_L4_CDE, MNGD_SGMT_L3_CDE, MNGD_SGMT_L2_CDE,
    MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC,
]
check_expected_columns(cg_balancesheet,   EXPECTED_BALANCESHEET_COLS, "CG balancesheet")
check_expected_columns(cbna_balancesheet, EXPECTED_BALANCESHEET_COLS, "CBNA balancesheet")
check_pmf_account_coverage(convergence, PMF_ACCOUNTS, FINANCE_PMF_LEVEL_5_DESC)

# ==============================================================================
# 2. Cast code columns to int
# ==============================================================================
# %%
cg_balancesheet   = cast_code_columns_to_int(cg_balancesheet)
cbna_balancesheet = cast_code_columns_to_int(cbna_balancesheet)

# ==============================================================================
# 3. Melt quarterly pivots → long format
# ==============================================================================
# %%
cg_outlook   = melt_quarterly_pivot(cg_balancesheet)
cbna_outlook = melt_quarterly_pivot(cbna_balancesheet)

print(f"CG outlook rows:   {len(cg_outlook):,}")
print(f"CBNA outlook rows: {len(cbna_outlook):,}")

# ==============================================================================
# 4. Build quarter-id mapping from convergence quarters
# ==============================================================================
# %%
max_quarters = check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook)

# %%
# Build YEAR/Month → Quarter Id mapping from convergence quarters
quarter_map, quarter_id_mapping = build_quarter_mappings(QO, max_quarters)

# %%
# Map YEAR/Month in outlook data to Quarter Id using the mapping built from convergence quarters,
# assign 'Unknown' if no match found
# %%
assign_quarter_id(cg_outlook,   quarter_id_mapping)
assign_quarter_id(cbna_outlook, quarter_id_mapping)

check_unknown_quarters(cg_outlook, cbna_outlook)

# %% [markdown]
# ## 9. Build Outlook Key Strings & Merge RWF via Waterfall

# %%
# Build composite key strings on outlook data
for outlook_df in [cg_outlook, cbna_outlook]:
    pass  # populated by build_waterfall_lookup_keys below

print("✅ Key strings built on outlook data")

# ==============================================================================
# 5. Split convergence into credit-risk buckets
# ==============================================================================
# %%
buckets = split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2)
credit_risk_convergence_cg          = buckets["credit_risk_convergence_cg"]
credit_risk_convergence_cbna        = buckets["credit_risk_convergence_cbna"]
non_credit_risk_non_waterfall_cg    = buckets["non_credit_risk_non_waterfall_cg"]
non_credit_risk_non_waterfall_cbna  = buckets["non_credit_risk_non_waterfall_cbna"]
cg_addon_markets_credit_risk        = buckets["cg_addon_markets_credit_risk"]
cbna_addon_markets_credit_risk      = buckets["cbna_addon_markets_credit_risk"]

# ==============================================================================
# 4. Create key pivot tables (Key1–Key5) for CG and CBNA
# ==============================================================================
# %%
(cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
 cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5) = create_key_pivots(
    credit_risk_convergence_cg, ADV_CG_TOTAL_RWA_AMT
)
(cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
 cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5) = create_key_pivots(
    credit_risk_convergence_cbna, ADV_CBNA_TOTAL_RWA_AMT
)

print(f"Key1 CG rows:   {len(cg_waterfall_rwf_lookup_1):,} | Key5 CBNA rows: {len(cbna_waterfall_rwf_lookup_5):,}")
print(f"Key5 CG rows:   {len(cg_waterfall_rwf_lookup_5):,} | Key5 CBNA rows: {len(cbna_waterfall_rwf_lookup_5):,}")

# %% [markdown]
# ## 5. Compute RWF (Risk Weight Factors) for Each Key

# %%
for k in [cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
          cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5]:
    compute_rwf(k, ADV_CG_TOTAL_RWA_AMT)

for k in [cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
          cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5]:
    compute_rwf(k, ADV_CBNA_TOTAL_RWA_AMT)

# --- Data Quality: RWF capping ---
# %%
check_rwf_capping([
    ("Key1 CG",   cg_waterfall_rwf_lookup_1),   ("Key1 CBNA", cbna_waterfall_rwf_lookup_1),
    ("Key2 CG",   cg_waterfall_rwf_lookup_2),   ("Key2 CBNA", cbna_waterfall_rwf_lookup_2),
    ("Key3 CG",   cg_waterfall_rwf_lookup_3),   ("Key3 CBNA", cbna_waterfall_rwf_lookup_3),
])

print("✅ RWF computed for all keys")

# %% [markdown]
# ## 6. Reset Indexes, Set Markets RWF = 0, Build Key Strings

# %%
# Reset indexes
for k in [cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
          cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5,
          cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
          cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5]:
    k.reset_index(inplace=True)

# %%
# Markets [L2] → RWF = 0
for k in [cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
          cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5,
          cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
          cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5]:
    set_markets_rwf_zero(k)

# ==============================================================================
# 7. Merge RWF waterfall onto outlook data
# ==============================================================================
# %%
cg_outlook   = merge_rwf_waterfall(
    cg_outlook,
    cg_waterfall_rwf_lookup_1, cg_waterfall_rwf_lookup_2, cg_waterfall_rwf_lookup_3,
    cg_waterfall_rwf_lookup_4, cg_waterfall_rwf_lookup_5,
    label="CG",
)
cbna_outlook = merge_rwf_waterfall(
    cbna_outlook,
    cbna_waterfall_rwf_lookup_1, cbna_waterfall_rwf_lookup_2, cbna_waterfall_rwf_lookup_3,
    cbna_waterfall_rwf_lookup_4, cbna_waterfall_rwf_lookup_5,
    label="CBNA",
)

check_key_match_coverage(cg_outlook, cbna_outlook)

# ==============================================================================
# 8. Assign ERBA RWA and metadata
# ==============================================================================
# %%
assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook)

# ==============================================================================
# 9. Build quarter → year/month mapping for non-waterfall RWA
# ==============================================================================
# %%
assign_year_month_from_quarter_non_waterfall_rwa_cbna_pivot(
    non_credit_risk_non_waterfall_cbna, quarter_map
)

# %%
# Markets addon — assign ERBA RWA and comment
cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk = build_markets_addon_pivot(
    cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
    markets_credit_risk=MARKETS_L2,
    addon_pivot_index=[QRTR_ID, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC],
)

# %%
assign_erba_rwa_and_comment(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk)

# %%
assign_year_month_from_quarter_markets_addon(
    cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk, quarter_map
)

print(f"\n⏱ Total elapsed: {time.time() - start_time:.2f} seconds")
