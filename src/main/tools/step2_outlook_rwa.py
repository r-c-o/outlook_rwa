"""
Step 2: Outlook RWA (Simplified Walkthrough)
Consumes Model Convergence outputs + adjustment files + PUG/PMF mappings,
applies adjustments, maps PUG codes and PMF account numbers, creates
pivot-based upload templates for CG and CBNA entities.

How to use: Update the PARAMETERS cell below, then run all cells top-to-bottom.
Prerequisite: Run step1_model_convergence.py first.
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import warnings
import numpy as np
import pandas as pd
import toml
from pathlib import Path
from datetime import datetime
from constants import (
    MANAGED_SEGMENT_L4_DESCR,
    MANAGED_SEGMENT_L3_DESCR,
    MANAGED_SEGMENT_L2_DESCR,
    MANAGED_GEOGRAPHY_L3_DESCR,
    PMF_ACCOUNT_L5_DESCR,
    SA_RWA,
    AA_RWA,
    ERBA_RWA,
    QUARTER_ID,
    REPORTING_LAYER,
    SA_ACCOUNT_NUM,
    AA_ACCOUNT_NUM,
    RWA_EXPOSURE_TYPE,
    RWA_CALC,
    MARKETS_FILTER,
    DISCONTINUED_OPS_L2,
    LEGACY_FRANCHISES_L3,
    LEGACY_HOLDINGS_ASSETS_L4,
    LATIN_AMERICA,
    MARKETS_L2,
    BANKING_L2,
    WEALTH_L2,
    SERVICES_L2,
)
from parallel_excel_to_parquet import load_schema_registry_from_csv

pd.set_option("display.max_columns", 500)

# =============================================================================
# PARAMETERS — update before running
# =============================================================================

config_path = Path(__file__).parent.parent.parent.parent / "config.toml"
config = toml.load(config_path)

schema_csv = Path(config["schema_registry_csv"])

# Starting quarter (e.g. "Mar 2025") — the actuals quarter
Q0 = config["parameters"]["Q0"]

run_datetime = datetime.now().strftime("%d%b%Y").lower().replace(" ", "")
run_datetime = run_datetime + "_run_datetime"
print(f"✅ Run datetime: {run_datetime}")

# Base data dir — contains input/ and output/ subfolders
data_dir = Path(config["data_dir"])
input_dir = data_dir / "input"
output_dir = data_dir / "output" / run_datetime

output_dir.mkdir(parents=True, exist_ok=True)

# =============================================================================
# Input filenames
# =============================================================================

input_adjustments_filename = "adjustment_master_file.xlsx"
input_pug_filename = "pug_mapping.xlsx"
input_pmf_rwa_mapping_filename = "pmf_rwa_mapping.xlsx"
input_convergence_filename = "aggregator_for_convergence.xlsx"

# Map Model Convergence outputs to input filenames for Outlook
output_cg_outlook_filename = f"cg_outlook.{run_datetime}.xlsx"
output_cbna_outlook_filename = f"cbna_outlook.{run_datetime}.xlsx"
output_cg_addon_filename = f"addon_all_cg.{run_datetime}.xlsx"
output_cbna_addon_filename = f"addon_all_cbna.{run_datetime}.xlsx"

input_cg_outlook_filename = output_cg_outlook_filename
input_cbna_outlook_filename = output_cbna_outlook_filename
input_cg_addon_filename = output_cg_addon_filename
input_cbna_addon_filename = output_cbna_addon_filename

# Output filenames
output_cg_upload_full_filename = f"CG_Upload_Template_Full.{run_datetime}.xlsx"
output_cbna_upload_full_filename = f"CBNA_Upload_Template_Full.{run_datetime}.xlsx"
output_cg_raw_data_filename = f"CG_RAW_DATA.{run_datetime}.xlsx"
output_cbna_raw_data_filename = f"CBNA_RAW_DATA.{run_datetime}.xlsx"
output_control_filename = f"control_file.{run_datetime}.xlsx"

# Model Convergence output directory (from step1)
model_convergence_dir = output_dir

print(f"Q0:                   {Q0}")
print(f"Input dir:            {input_dir}")
print(f"Model Convergence dir:{model_convergence_dir}")
print(f"Output dir:           {output_dir}")

# =============================================================================
# Mapping & adjustment files
# =============================================================================

adjustments_file = input_dir / input_adjustments_filename
pug_file = input_dir / input_pug_filename
pmf_mapping_file = input_dir / input_pmf_rwa_mapping_filename
convergence_file = input_dir / input_convergence_filename

# Model convergence outputs
cg_outlook_file = model_convergence_dir / input_cg_outlook_filename
cbna_outlook_file = model_convergence_dir / input_cbna_outlook_filename
cg_addon_file = model_convergence_dir / input_cg_addon_filename
cbna_addon_file = model_convergence_dir / input_cbna_addon_filename

# --- Data Quality Check: verify files exist ---
all_input_files = [
    adjustments_file, pug_file, pmf_mapping_file, convergence_file,
    cg_outlook_file, cbna_outlook_file, cg_addon_file, cbna_addon_file,
]

# =============================================================================
# 1. Read Input Files
# =============================================================================

for f in all_input_files:
    if not f.exists():
        raise FileNotFoundError(f"⚠️  INPUT FILE NOT FOUND: {f}")
    else:
        print(f"✅ Found: {f.name}")

src_cg_adjustments = pd.read_excel(adjustments_file, sheet_name="Adjustments - CG")
src_cbna_adjustments = pd.read_excel(adjustments_file, sheet_name="Adjustments - CBNA")
src_pug = pd.read_excel(pug_file)
src_rwa_pmf_mapping = pd.read_excel(pmf_mapping_file, sheet_name="Sheet1")
src_cg_outlook = pd.read_excel(cg_outlook_file)
src_cbna_outlook = pd.read_excel(cbna_outlook_file)
src_addon_all_cg = pd.read_excel(cg_addon_file)
src_addon_all_cbna = pd.read_excel(cbna_addon_file)

cg_adjustments = src_cg_adjustments.copy(deep=True)
cbna_adjustments = src_cbna_adjustments.copy(deep=True)
pug_df = src_pug.copy(deep=True)
rwa_pmf_mapping = src_rwa_pmf_mapping.copy(deep=True)
cg_outlook = src_cg_outlook.copy(deep=True)
cbna_outlook = src_cbna_outlook.copy(deep=True)
addon_all_cg = src_addon_all_cg.copy(deep=True)
addon_all_cbna = src_addon_all_cbna.copy(deep=True)

print(f"✅ CG outlook rows:   {len(cg_outlook):,}")
print(f"✅ CBNA outlook rows: {len(cbna_outlook):,}")

# =============================================================================
# 2. Apply PUG Mapping
# =============================================================================

cg_outlook = cg_outlook.merge(
    pug_df[[
        "Managed Segment L2 Descr",
        "Managed Segment L3 Id",
        "Managed Segment L3 Descr",
        "Managed Segment L4 Id",
        "Managed Segment L4 Descr",
    ]],
    on=["Managed Segment L2 Descr", "Managed Segment L3 Descr", "Managed Segment L4 Descr"],
    how="left",
    suffixes=("", "_pug"),
)

cbna_outlook = cbna_outlook.merge(
    pug_df[[
        "Managed Segment L2 Descr",
        "Managed Segment L3 Id",
        "Managed Segment L3 Descr",
        "Managed Segment L4 Id",
        "Managed Segment L4 Descr",
    ]],
    on=["Managed Segment L2 Descr", "Managed Segment L3 Descr", "Managed Segment L4 Descr"],
    how="left",
    suffixes=("", "_pug"),
)

# =============================================================================
# 3. Apply PMF RWA Mapping
# =============================================================================

cg_outlook = cg_outlook.merge(
    rwa_pmf_mapping,
    on=PMF_ACCOUNT_L5_DESCR,
    how="left",
)
cbna_outlook = cbna_outlook.merge(
    rwa_pmf_mapping,
    on=PMF_ACCOUNT_L5_DESCR,
    how="left",
)

# =============================================================================
# 4. Categorise Segments
# =============================================================================

def assign_reporting_flags(df):
    """Tag each row with Reporting Layer, Markets Filter, etc."""
    df[REPORTING_LAYER] = np.where(
        df[MANAGED_SEGMENT_L2_DESCR] == MARKETS_L2, "Markets", "Non-Markets"
    )
    df[MARKETS_FILTER] = df[MANAGED_SEGMENT_L2_DESCR] == MARKETS_L2
    df["Is_Banking"] = df[MANAGED_SEGMENT_L2_DESCR] == BANKING_L2
    df["Is_Wealth"] = df[MANAGED_SEGMENT_L2_DESCR] == WEALTH_L2
    df["Is_Services"] = df[MANAGED_SEGMENT_L2_DESCR] == SERVICES_L2
    df["Is_Discontinued"] = df[MANAGED_SEGMENT_L2_DESCR] == DISCONTINUED_OPS_L2
    df["Is_Legacy_Franchises"] = df[MANAGED_SEGMENT_L3_DESCR] == LEGACY_FRANCHISES_L3
    df["Is_Legacy_Holdings"] = df[MANAGED_SEGMENT_L4_DESCR] == LEGACY_HOLDINGS_ASSETS_L4
    df["Is_Latin_America"] = df[MANAGED_GEOGRAPHY_L3_DESCR] == LATIN_AMERICA


assign_reporting_flags(cg_outlook)
assign_reporting_flags(cbna_outlook)

# =============================================================================
# 5. Build Upload Templates
# =============================================================================

upload_cols = [
    MANAGED_SEGMENT_L4_DESCR,
    MANAGED_SEGMENT_L3_DESCR,
    MANAGED_SEGMENT_L2_DESCR,
    MANAGED_GEOGRAPHY_L3_DESCR,
    PMF_ACCOUNT_L5_DESCR,
    QUARTER_ID,
    "Balances",
    SA_RWA,
    AA_RWA,
    ERBA_RWA,
    "Comment",
    RWA_EXPOSURE_TYPE,
    RWA_CALC,
    SA_ACCOUNT_NUM,
    AA_ACCOUNT_NUM,
    REPORTING_LAYER,
    MARKETS_FILTER,
]

cg_upload = cg_outlook[[c for c in upload_cols if c in cg_outlook.columns]].copy()
cbna_upload = cbna_outlook[[c for c in upload_cols if c in cbna_outlook.columns]].copy()

# =============================================================================
# 6. Export Outputs
# =============================================================================

exports = {
    output_cg_raw_data_filename: cg_outlook,
    output_cbna_raw_data_filename: cbna_outlook,
    output_cg_upload_full_filename: cg_upload,
    output_cbna_upload_full_filename: cbna_upload,
}

for fname, df in exports.items():
    out_path = output_dir / fname
    df.to_excel(out_path, index=False)
    print(f"✅ Written: {fname}  ({len(df):,} rows)")

print("\n🎉 Step 2 complete.")
