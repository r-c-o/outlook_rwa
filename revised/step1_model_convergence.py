# %% [markdown]
# # Step 1: Model Convergence (revised)
#
# Merges outlook balance sheet with convergence data via a 5-key waterfall join,
# computes SA/AA Risk Weight Factors, and produces CG and CBNA outlook files.
#
# **How to use:** Set OUTLOOK_RWA_DATA_DIR and OUTLOOK_RWA_APP_DIR env vars
# (or edit config.toml paths), then run all cells top-to-bottom.

# %%
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import time
import toml
import warnings
import pandas as pd
from pathlib import Path

from constants import *
from functions import (
    assign_quarter_id, build_markets_addon_pivot, cast_code_columns_to_int,
    check_and_get_max_quarters, check_expected_columns, check_input_files_exist,
    check_key_match_coverage, check_pmf_account_coverage, check_rwf_capping,
    check_unknown_quarters, compute_rwf, create_key_pivots,
    assign_erba_rwa_and_comment, assign_erba_rwa_and_metadata,
    export_excel_specs_to_parquet, melt_quarterly_pivot, merge_rwf_waterfall,
    set_markets_rwf_zero, split_convergence,
)

pd.set_option("display.max_columns", 500)

# %%
start_time = time.time()

# ==============================================================================
# PARAMETERS — driven entirely by config.toml + env vars; no edits needed here
# ==============================================================================
config_path = Path(os.environ.get(
    "OUTLOOK_RWA_CONFIG",
    r"C:\Users\rl09895\project_home\outlook-rwa-release\outlook-rwa-app\src\main\tools\config.toml",
))
config        = toml.load(config_path)
gp            = config["global_params"]
parallel_cfg  = config.get("parallel", {})

input_dir             = Path(gp["input_dir"])
output_dir            = Path(gp["output_dir"])
model_convergence_dir = Path(gp["model_convergence_dir"])
parquet_dir           = input_dir / parallel_cfg.get("parquet_subdir", "parquet_cache")
schema_csv            = Path(gp["schema_registry_path"])
QO                    = gp["QO"]

# Build file-spec list from config manifest — single source of truth
step1_specs = [
    {
        "variable_name": s["variable_name"],
        "input_path":    str(input_dir / s["filename"]),
        "schema_key":    s["schema_key"],
    }
    for s in config["inputs"]["step1"]
]

check_input_files_exist([s["input_path"] for s in step1_specs])

# %%
# ---------------------------------------------------------------------------
# Parallel Excel → Parquet (skips existing files by default)
# ---------------------------------------------------------------------------
parquet_results = export_excel_specs_to_parquet(
    file_specs=step1_specs,
    output_dir=parquet_dir,
    schema_registry_csv=schema_csv,
    if_exists=parallel_cfg.get("if_exists", "new"),
    max_workers=parallel_cfg.get("max_workers", 4),
)

# %%
# Load parquets into pandas — fast, typed, no Excel overhead
cg_balancesheet   = pd.read_parquet(parquet_results["cg_balancesheet"]["output_path"])
cbna_balancesheet = pd.read_parquet(parquet_results["cbna_balancesheet"]["output_path"])
convergence       = pd.read_parquet(parquet_results["convergence"]["output_path"])

print(f"CG rows:          {len(cg_balancesheet):,}")
print(f"CBNA rows:        {len(cbna_balancesheet):,}")
print(f"Convergence rows: {len(convergence):,}")
print(f"⏱ Load time: {time.time() - start_time:.1f}s")

# ==============================================================================
# Data Quality: columns + PMF account coverage
# ==============================================================================
# %%
EXPECTED_BS_COLS = [
    "YEAR", "Month", GAAP_AMOUNT, SA_RWA_AMT,
    MNGD_SGMT_L4_CDE, MNGD_SGMT_L3_CDE, MNGD_SGMT_L2_CDE,
    MNGD_GEO_L4_DESC, FINANCE_PMF_LEVEL_5_DESC,
]
check_expected_columns(cg_balancesheet,   EXPECTED_BS_COLS, "CG balancesheet")
check_expected_columns(cbna_balancesheet, EXPECTED_BS_COLS, "CBNA balancesheet")
check_pmf_account_coverage(convergence, PMF_ACCOUNTS, FINANCE_PMF_LEVEL_5_DESC)

# %%
cg_balancesheet   = cast_code_columns_to_int(cg_balancesheet)
cbna_balancesheet = cast_code_columns_to_int(cbna_balancesheet)

# ==============================================================================
# Melt quarterly pivots → long format
# ==============================================================================
# %%
cg_outlook   = melt_quarterly_pivot(cg_balancesheet)
cbna_outlook = melt_quarterly_pivot(cbna_balancesheet)

print(f"CG outlook rows:   {len(cg_outlook):,}")
print(f"CBNA outlook rows: {len(cbna_outlook):,}")

# ==============================================================================
# Build quarter-id mapping and assign Quarter Id
# ==============================================================================
# %%
max_quarters = check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook)

# Build YEAR/Month → Quarter Id mapping from convergence (implementation in functions.py)
quarter_map, quarter_id_mapping = build_quarter_mappings(QO, max_quarters)

assign_quarter_id(cg_outlook,   quarter_id_mapping)
assign_quarter_id(cbna_outlook, quarter_id_mapping)
check_unknown_quarters(cg_outlook, cbna_outlook)

# ==============================================================================
# Split convergence into credit-risk buckets
# ==============================================================================
# %%
buckets = split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2)
credit_risk_cg         = buckets["credit_risk_convergence_cg"]
credit_risk_cbna       = buckets["credit_risk_convergence_cbna"]
non_wf_cg              = buckets["non_credit_risk_non_waterfall_cg"]
non_wf_cbna            = buckets["non_credit_risk_non_waterfall_cbna"]
mkt_credit_risk_cg     = buckets["cg_addon_markets_credit_risk"]
mkt_credit_risk_cbna   = buckets["cbna_addon_markets_credit_risk"]

# ==============================================================================
# Build RWF lookup tables from config-driven key list
# ==============================================================================
# %%
rwf_key_configs = config["rwf_keys"]["key"]  # list of {label, index} dicts

cg_lookups   = create_key_pivots(credit_risk_cg,   ADV_CG_TOTAL_RWA_AMT)
cbna_lookups = create_key_pivots(credit_risk_cbna, ADV_CBNA_TOTAL_RWA_AMT)

for k in (*cg_lookups, *cbna_lookups):
    k.reset_index(inplace=True)

# %%
for k in cg_lookups:
    compute_rwf(k, ADV_CG_TOTAL_RWA_AMT)
    set_markets_rwf_zero(k)

for k in cbna_lookups:
    compute_rwf(k, ADV_CBNA_TOTAL_RWA_AMT)
    set_markets_rwf_zero(k)

# %%
check_rwf_capping(
    [(f"CG Key{i}",   k) for i, k in enumerate(cg_lookups,   1)] +
    [(f"CBNA Key{i}", k) for i, k in enumerate(cbna_lookups, 1)]
)
print("✅ RWF computed for all keys")

# ==============================================================================
# Waterfall merge
# ==============================================================================
# %%
cg_outlook   = merge_rwf_waterfall(cg_outlook,   *cg_lookups,   label="CG")
cbna_outlook = merge_rwf_waterfall(cbna_outlook, *cbna_lookups, label="CBNA")
check_key_match_coverage(cg_outlook, cbna_outlook)

# ==============================================================================
# ERBA RWA and metadata
# ==============================================================================
# %%
assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook)

# ==============================================================================
# Markets addon pivot + ERBA
# ==============================================================================
# %%
ADDON_PIVOT_INDEX = [QRTR_ID, FINANCE_PMF_LEVEL_5_DESC, MNGD_SGMT_L2_DESC]
cg_mkt_pivot, cbna_mkt_pivot = build_markets_addon_pivot(
    mkt_credit_risk_cg, mkt_credit_risk_cbna,
    markets_credit_risk_mask=None,
    addon_pivot_index=ADDON_PIVOT_INDEX,
)
assign_erba_rwa_and_comment(cg_mkt_pivot, cbna_mkt_pivot)

print(f"\n⏱ Total elapsed: {time.time() - start_time:.1f}s")
