"""
Step 2: Outlook RWA (Simplified Walkthrough)
Consumes Model Convergence outputs + adjustment files + PUG/PMF mappings,
applies adjustments, maps PUG codes and PMF account numbers, creates
pivot-based upload templates for CG and CBNA entities.

How to use: Update the PARAMETERS cell below, then run all cells top-to-bottom.
Prerequisite: Run `step1_model_convergence.py` first.
"""
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import warnings
import numpy as np
import pandas as pd
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
    REPORTABLE_ENTITY_IS_CG,
    REPORTABLE_ENTITY_IS_CBNA,
    ADV_CG_TOTAL_RWA_AMT,
    ADV_CBNA_TOTAL_RWA_AMT,
)
from parallel_excel_to_parquet import (
    load_schema_registry_from_csv,
    load_spec_with_fallback,
    make_input_specs,
    ExcelInputSpec,
)
from functions import load_config

pd.set_option("display.max_columns", 500)

# %% [markdown]
# # Step 2: Outlook RWA (Simplified Walkthrough)

# %% [markdown]
# ## Parameters & Constants

# %%
# Run Cell | Run Above | Debug Cell

run_datetime = datetime.now().strftime("%d%b%Y").lower().replace(" ", "")
print(f"Run date: {run_datetime}")

# =============================================================================
# PARAMETERS — Update these before running
# =============================================================================

config = load_config(Path(__file__).parent.parent.parent.parent)

# Q0: Quarter-0 date (format: Mon_YYYY) — the actuals quarter
Q0 = config["parameters"]["Q0"]
print(f"Q0: {Q0}")

# =============================================================================
# Input Filenames — mapping/adjustment files (in data_dir/input)
# =============================================================================

data_dir  = Path(config["paths"]["data_dir"])
input_dir = data_dir / "input"

input_adjustments_filename    = "adjustment_master_file.xlsx"
input_pug_filename             = "pug_mapping.xlsx"
input_pmf_rwa_mapping_filename = "pmf_rwa_mapping.xlsx"

# Schema registry (drives dtype overrides when building parquet from Excel)
schema_csv = Path(config["paths"]["schema_registry_csv"])
if not schema_csv.exists() and "schema_registry_csv_backup" in config["paths"]:
    schema_csv = Path(config["paths"]["schema_registry_csv_backup"])
registry = load_schema_registry_from_csv(schema_csv)

# =============================================================================
# Map Model Convergence outputs to Input Filenames for Outlook
# step1 writes fixed filenames there (not run_datetime-named files)
# =============================================================================

# Model Convergence output directory (from step1)
model_convergence_dir = Path(config["outputs"]["step1_dir"])

input_cg_outlook_filename    = config["outputs"]["step1"][0]["cg_outlook"]
input_cbna_outlook_filename  = config["outputs"]["step1"][0]["cbna_outlook"]
input_cg_addon_filename      = config["outputs"]["step1"][0]["cg_addon_non_waterfall_rwa"]
input_cbna_addon_filename    = config["outputs"]["step1"][0]["cbna_addon_non_waterfall_rwa"]

# Output Filenames
output_cg_upload_full_filename    = "CG_Upload_Template_Full.xlsx"
output_cbna_upload_full_filename  = "CBNA_Upload_Template_Full.xlsx"
output_cg_raw_data_filename       = "CG_RAW_DATA.xlsx"
output_cbna_raw_data_filename     = "CBNA_RAW_DATA.xlsx"
output_control_filename           = "control_file.xlsx"

output_dir = Path(config["outputs"]["step2_dir"])
if not output_dir.exists() and "step2_dir_backup" in config["outputs"]:
    output_dir = Path(config["outputs"]["step2_dir_backup"])
output_dir.mkdir(parents=True, exist_ok=True)

print(f"Q0:                    {Q0}")
print(f"Input dir:             {input_dir}")
print(f"Model Convergence dir: {model_convergence_dir}")
print(f"Output dir:            {output_dir}")

# =============================================================================
# Mapping & adjustment files (full paths)
# =============================================================================

adjustments_file = input_dir / input_adjustments_filename
pug_file         = input_dir / input_pug_filename
pmf_mapping_file = input_dir / input_pmf_rwa_mapping_filename

# Model convergence outputs (full paths — from step1)
cg_outlook_file   = model_convergence_dir / input_cg_outlook_filename
cbna_outlook_file = model_convergence_dir / input_cbna_outlook_filename
cg_addon_file     = model_convergence_dir / input_cg_addon_filename
cbna_addon_file   = model_convergence_dir / input_cbna_addon_filename

# Output Filenames (full paths)
output_cg_upload_full_filename_path   = output_dir / output_cg_upload_full_filename
output_cbna_upload_full_filename_path = output_dir / output_cbna_upload_full_filename
output_cg_raw_data_filename_path      = output_dir / output_cg_raw_data_filename
output_cbna_raw_data_filename_path    = output_dir / output_cbna_raw_data_filename
output_control_file_path              = output_dir / output_control_filename

print(f"[Q0]:            {Q0}")
print(f"[0b]:            {model_convergence_dir}")
print(f"[0c]:            {output_dir}")

# %% [markdown]
# # ## 1. Read Input Files

# %%
# Run Cell | Run Above | Debug Cell

# --- Input dataset specs: parquet-first load with Excel fallback ---
# Raw mapping/adjustment inputs cache their parquet in input_dir; step1 outputs
# (incl. the convergence parquet) already carry parquet alongside their xlsx in
# model_convergence_dir. The adjustments + convergence specs are reused from the
# shared factory so step1 and step2 reference one definition of each source file.
shared = make_input_specs(input_dir)
input_specs = [
    (shared["cg_adjustments"],   input_dir),
    (shared["cbna_adjustments"], input_dir),
    (ExcelInputSpec("pug",             pug_file,         "pug", "pug_mapping.parquet"),                input_dir),
    (ExcelInputSpec("pmf_rwa_mapping", pmf_mapping_file, "pmf", "pmf_rwa_mapping.parquet", "Sheet1"),  input_dir),
    (shared["convergence"],      model_convergence_dir),
    (ExcelInputSpec("cg_outlook",     cg_outlook_file,   "outlook", "cg_outlook.parquet"),     model_convergence_dir),
    (ExcelInputSpec("cbna_outlook",   cbna_outlook_file, "outlook", "cbna_outlook.parquet"),   model_convergence_dir),
    (ExcelInputSpec("addon_all_cg",   cg_addon_file,     "addon",   "addon_all_cg.parquet"),   model_convergence_dir),
    (ExcelInputSpec("addon_all_cbna", cbna_addon_file,   "addon",   "addon_all_cbna.parquet"), model_convergence_dir),
]

# --- Data Quality Check: each dataset needs its parquet OR its source Excel ---
for spec, parquet_dir in input_specs:
    if not (parquet_dir / spec.output_name).exists() and not spec.path.exists():
        raise FileNotFoundError(
            f"INPUT NOT FOUND for '{spec.label}': neither "
            f"{parquet_dir / spec.output_name} nor {spec.path}"
        )
    print(f"Found: {spec.label}")

# Load order matches input_specs above
loaded = [load_spec_with_fallback(spec, parquet_dir, registry) for spec, parquet_dir in input_specs]
(
    src_cg_adjustments,
    src_cbna_adjustments,
    src_pug,
    src_rwa_pmf_mapping,
    src_convergence,
    src_cg_outlook,
    src_cbna_outlook,
    src_addon_all_cg,
    src_addon_all_cbna,
) = loaded

cg_adjustments   = src_cg_adjustments.copy(deep=True)
cbna_adjustments = src_cbna_adjustments.copy(deep=True)
pug_df           = src_pug.copy(deep=True)
rwa_pmf_mapping  = src_rwa_pmf_mapping.copy(deep=True)
convergence      = src_convergence.copy(deep=True)
cg_outlook       = src_cg_outlook.copy(deep=True)
cbna_outlook     = src_cbna_outlook.copy(deep=True)
addon_all_cg     = src_addon_all_cg.copy(deep=True)
addon_all_cbna   = src_addon_all_cbna.copy(deep=True)

print(f"CG Adjustments rows:   {len(cg_adjustments):,}")
print(f"CBNA Adjustments rows: {len(cbna_adjustments):,}")
print(f"Convergence rows:      {len(convergence):,}")
print(f"PUG/PMF Mapping rows:  {len(pug_df):,}")
print(f"CG outlook rows:       {len(cg_outlook):,}")
print(f"CBNA outlook rows:     {len(cbna_outlook):,}")
print(f"CG addon rows:         {len(addon_all_cg):,}")
print(f"CBNA addon rows:       {len(addon_all_cbna):,}")

# %% [markdown]
# ## 2. Format Adjustments

# %%
# Run Cell | Run Above | Debug Cell

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


cg_adjustments_formatted  = format_adjustments(cg_adjustments.copy())
cbna_adjustments_formatted = format_adjustments(cbna_adjustments.copy())

print(f"CG adjustments formatted:   {len(cg_adjustments_formatted):,} rows")
print(f"CBNA adjustments formatted: {len(cbna_adjustments_formatted):,} rows")

# %% [markdown]
# ## 3. Rename Addon Columns to Match Outlook Schema

# %%
# Run Cell | Run Above | Debug Cell

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


addon_all_cg   = rename_addon_columns(addon_all_cg, 'CG')
addon_all_cbna = rename_addon_columns(addon_all_cbna, 'CBNA')

print(f"CG addon columns renamed")
print(f"CBNA addon columns renamed")

# %% [markdown]
# ## 4. Filter out Discontinued Ops

# %%
# Run Cell | Run Above | Debug Cell

# Production does NOT drop discontinued-ops rows here; they flow through to the
# upload template and are only excluded inside the convergence control summary.
# def filter_out_discontinued_ops(input_df):
#     """Filter out rows where Managed Segment L2 == Discontinued Ops."""
#     return input_df[input_df[MANAGED_SEGMENT_L2_DESCR] != DISCONTINUED_OPS_L2].copy()
#
# cg_outlook   = filter_out_discontinued_ops(cg_outlook)
# cbna_outlook = filter_out_discontinued_ops(cbna_outlook)
# addon_all_cg   = filter_out_discontinued_ops(addon_all_cg)
# addon_all_cbna = filter_out_discontinued_ops(addon_all_cbna)

print(f"CG outlook rows:   {len(cg_outlook):,}")
print(f"CBNA outlook rows: {len(cbna_outlook):,}")

# %% [markdown]
# ## 5. Rename PMF Mapping Columns & Data Quality Checks

# %%
# Run Cell | Run Above | Debug Cell

# Data Quality Check: PUG mapping duplicates on Managed Segment L4 Descr
pug_dupes = src_pug.duplicated(subset=[MANAGED_SEGMENT_L4_DESCR], keep=False)
if pug_dupes.sum() > 0:
    warnings.warn(f"PUG mapping has {pug_dupes.sum()} duplicates on Managed Segment L4 Descr")
else:
    print(f"PUG mapping is 1:1 on Managed Segment L4 Descr")

# Rename PMF RWA mapping columns to canonical schema (source file uses "PMF L5")
rwa_pmf_mapping = rwa_pmf_mapping.rename(columns={"PMF L5": PMF_ACCOUNT_L5_DESCR})

# Data Quality Check: PMF RWA mapping duplicates on PMF L5
pmf_dupes = rwa_pmf_mapping.duplicated(subset=[PMF_ACCOUNT_L5_DESCR], keep=False)
if pmf_dupes.sum() > 0:
    warnings.warn(f"PMF RWA mapping has {pmf_dupes.sum()} duplicates on PMF L5")
else:
    print(f"PMF RWA mapping is 1:1 on PMF L5")

# %% [markdown]
# ## 6. Concatenate Adjustments + Outlook + Addon

# %%
# Run Cell | Run Above | Debug Cell

# Concatenate CG: Adjustments + Outlook + Addon
cg_concat = pd.concat([
    cg_adjustments_formatted,
    cg_outlook,
    addon_all_cg,
], ignore_index=True).copy()

# Concatenate CBNA: Adjustments + Outlook + Addon
cbna_concat = pd.concat([
    cbna_adjustments_formatted,
    cbna_outlook,
    addon_all_cbna,
], ignore_index=True).copy()

# Convert Quarter ID to numeric; filter out 'Unknown' quarter IDs
cg_concat[QUARTER_ID]   = pd.to_numeric(cg_concat[QUARTER_ID], errors='coerce')
cbna_concat[QUARTER_ID] = pd.to_numeric(cbna_concat[QUARTER_ID], errors='coerce')

# CG after filtering unknowns
cg_concat   = cg_concat[cg_concat[QUARTER_ID] != 'Unknown']
cbna_concat = cbna_concat[cbna_concat[QUARTER_ID] != 'Unknown']

# Drop NaT/NaN quarter IDs
cg_concat   = cg_concat[cg_concat[QUARTER_ID].notna()]
cbna_concat = cbna_concat[cbna_concat[QUARTER_ID].notna()]

print(f"CG after filtering unknowns:   {len(cg_concat)}")
print(f"CBNA after filtering unknowns: {len(cbna_concat)}")

# Check unique quarters
unique_quarters_cg   = sorted(cg_concat[QUARTER_ID].dropna().unique())
unique_quarters_cbna = sorted(cbna_concat[QUARTER_ID].dropna().unique())

if not unique_quarters_cg:
    warnings.warn(f"CG: No valid Quarter Ids found after filtering.")
else:
    print(f"Unique Quarter Ids (CG):   {unique_quarters_cg}")

if not unique_quarters_cbna:
    warnings.warn(f"CBNA: No valid Quarter Ids found after filtering.")
else:
    print(f"Unique Quarter Ids (CBNA): {unique_quarters_cbna}")

# %% [markdown]
# ## 7. Save Raw Data (before further transformations)

# %%
# Run Cell | Run Above | Debug Cell

# Add Entity column
cg_concat["Entity"]   = "BA"
cbna_concat["Entity"] = "BB"

cg_raw_data   = cg_concat.copy()
cbna_raw_data = cbna_concat.copy()

# %% [markdown]
# ## 8. Legacy Franchises Breakout

# %%
# Run Cell | Run Above | Debug Cell

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


cg_concat   = legacy_franchises_breakout(cg_concat)
cbna_concat = legacy_franchises_breakout(cbna_concat)

print(f"CG rows:   {len(cg_concat):,}")
print(f"CBNA rows: {len(cbna_concat):,}")

# %% [markdown]
# ## 9. FRM Output

# %%
# Run Cell | Run Above | Debug Cell

frm_output_cg   = cg_concat.copy()
frm_output_cbna = cbna_concat.copy()

print(f"FRM output CG rows:   {len(frm_output_cg):,}")
print(f"FRM output CBNA rows: {len(frm_output_cbna):,}")

# %% [markdown]
# ## 10. Join PUG Mapping

# %%
# Run Cell | Run Above | Debug Cell
# %% [markdown]

pre_cg   = len(frm_output_cg)
pre_cbna = len(frm_output_cbna)

# PUG join — merge on [MANAGED_SEGMENT_L4_DESCR] to get PUG codes
frm_output_cg = frm_output_cg.merge(
    pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]],
    how="left",
    on=MANAGED_SEGMENT_L4_DESCR,
)

frm_output_cbna = frm_output_cbna.merge(
    pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]],
    how="left",
    on=MANAGED_SEGMENT_L4_DESCR,
)

# Data Quality Check: PUG join should not expand rows (1:1 on L4)
if len(frm_output_cg) != pre_cg:
    warnings.warn(
        f"CG: PUG join caused row expansion: {pre_cg} -> {len(frm_output_cg)}"
        f" — may cause row expansion!"
    )

if len(frm_output_cbna) != pre_cbna:
    warnings.warn(
        f"CBNA: PUG join caused row expansion: {pre_cbna} -> {len(frm_output_cbna)}"
        f" — may cause row expansion!"
    )

# Data Quality Check: unmatched Managed Segment L4 Descr entries
for label, df in [("CG", frm_output_cg), ("CBNA", frm_output_cbna)]:
    pug_dupes_df = src_pug.duplicated(subset=[MANAGED_SEGMENT_L4_DESCR], keep=False)
    unmatched    = df[df["PUG"].isna()]
    pct          = len(unmatched) / (len(df) + 1) * 100
    if len(unmatched) > 0:
        warnings.warn(
            f"{label}: (unmatched: {pct:.1f}%) rows ({len(unmatched):,}) have no PUG mapping match!"
        )

print(f"PUG mapping joined!")

# %% [markdown]
# ## 11. Join PMF Mapping

# %%
# Run Cell | Run Above | Debug Cell

pre_cg   = len(frm_output_cg)
pre_cbna = len(frm_output_cbna)

# PMF join — merge on PMF_ACCOUNT_L5_DESCR to get SA_ACCOUNT_NUM, AA_ACCOUNT_NUM
frm_output_cg = frm_output_cg.merge(
    rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
    how="left",
    on=PMF_ACCOUNT_L5_DESCR,
)

frm_output_cbna = frm_output_cbna.merge(
    rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
    how="left",
    on=PMF_ACCOUNT_L5_DESCR,
)

# Data Quality Check: PMF join should not expand rows
if len(frm_output_cg) != pre_cg:
    warnings.warn(
        f"CG: PMF join caused row expansion: {pre_cg} -> {len(frm_output_cg)}"
        f" — may cause row expansion!"
    )

if len(frm_output_cbna) != pre_cbna:
    warnings.warn(
        f"CBNA: PMF join caused row expansion: {pre_cbna} -> {len(frm_output_cbna)}"
        f" — may cause row expansion!"
    )

# Data Quality Check: unmatched PMF L5 entries
for label, df in [("CG", frm_output_cg), ("CBNA", frm_output_cbna)]:
    unmatched = df[df[SA_ACCOUNT_NUM].isna() & df[PMF_ACCOUNT_L5_DESCR].notna()]
    pct = len(unmatched) / (len(df) + 1) * 100
    if len(unmatched) > 0:
        warnings.warn(
            f"{label}: PMF RWA mapping has {len(unmatched):,} ({pct:.1f}%) rows"
            f" have no PMF RWA mapping match!"
        )
        print(
            f"  {label}: unmatched PMF L5 entries: "
            f"{sorted(unmatched[PMF_ACCOUNT_L5_DESCR].dropna().unique())}"
        )

print(f"PMF mapping joined!")

# %% [markdown]
# ## 12. Format Columns Before Pivots

# %%
# Run Cell | Run Above | Debug Cell

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


frm_output_cg   = format_columns_before_pivots(frm_output_cg.copy())
frm_output_cbna = format_columns_before_pivots(frm_output_cbna.copy())

print(f"CG formatted:   {len(frm_output_cg):,} rows")
print(f"CBNA formatted: {len(frm_output_cbna):,} rows")

# %% [markdown]
# ## 13. Markets Filter

# %%
# Run Cell | Run Above | Debug Cell

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


# Markets filter is applied after the pivots (see below) to mirror production's
# ordering. It only adds a column (never drops rows) and that column is dropped
# at template formatting, so the ordering has no effect on the output numbers.

# %% [markdown]
# ## Create Upload Template Pivots (ERBA, AA, SA)

# %%
# Run Cell | Run Above | Debug Cell

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

    # ERBA pivot (commented out — kept for reference)
    erba_pivot = make_pivot(ERBA_RWA, "ERBA")
    aa_pivot   = make_pivot(AA_RWA,   "AA")
    sa_pivot   = make_pivot(SA_RWA,   "SA")

    pivots = pd.concat([erba_pivot, aa_pivot, sa_pivot])
    pivots.columns.name = None
    return pivots


cg_frm_output   = create_upload_template_pivots(frm_output_cg)
cbna_frm_output = create_upload_template_pivots(frm_output_cbna)

# Markets filter (inert) — applied after pivots to mirror production ordering.
cg_frm_output   = create_markets_filter(cg_frm_output)
cbna_frm_output = create_markets_filter(cbna_frm_output)

print(f"CG pivot rows:   {len(cg_frm_output):,}")
print(f"CBNA pivot rows: {len(cbna_frm_output):,}")

# %% [markdown]
# ## 14. Format Upload Template (Add Upload Columns)

# %%
# Run Cell | Run Above | Debug Cell

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
    MONTHLY = ["Month1", "Month2", "Month4", "Month5", "Month7", "Month8",
               "Month10", "Month11", "Month13", "Month14"]
    for m in MONTHLY:
        input_df[m] = 0

    input_df = input_df.drop(columns=[SA_ACCOUNT_NUM, AA_ACCOUNT_NUM])
    input_df = input_df.rename(columns={0: "RWA Actuals"})

    # Column order transcribed from the production upload template: RWA Actuals
    # sits near the front; the quarter value columns (1-7) are interleaved with
    # the Month placeholders; Comment / RWA Exposure Type / Markets Filter trail
    # at the end.
    col_order = [
        REPORTING_LAYER,
        MANAGED_SEGMENT_L2_DESCR,
        MANAGED_SEGMENT_L3_DESCR,
        RWA_CALC,
        PMF_ACCOUNT_L5_DESCR,
        "RWA Actuals",
        "FileType",
        MANAGED_SEGMENT_L4_DESCR,
        "ManagedGeo",
        "PUG",
        "FrsBu",
        "CustomerSegment",
        "Product",
        "Entity",
        "Affiliate",
        "Project",
        "TransactionId",
        "Account",
        "BalanceType",
        "Currency",
        "Layer",
        "ModelId",
        "MDRM",
        "ReasonCode",
        "Comments",
        1, "Month1", "Month2",
        2, "Month4", "Month5",
        3, "Month7", "Month8",
        4, "Month10", "Month11",
        5, "Month13", "Month14",
        6,
        7,
        "Comment",
        RWA_EXPOSURE_TYPE,
        MARKETS_FILTER,
    ]

    input_df = input_df[[c for c in col_order if c in input_df.columns]]
    input_df = input_df.sort_values([MANAGED_SEGMENT_L2_DESCR, MANAGED_SEGMENT_L3_DESCR])
    return input_df


# %% [markdown]
# ## 15. Export Upload Templates & Raw Data

# %%
# Run Cell | Run Above | Debug Cell

cg_frm_output_full   = format_upload_template(cg_frm_output)
cbna_frm_output_full = format_upload_template(cbna_frm_output)

print(f"CG Upload template rows:   {len(cg_frm_output_full):,}")
print(f"CBNA Upload template rows: {len(cbna_frm_output_full):,}")
print(f"CG raw data rows:          {len(cg_raw_data):,}")
print(f"CBNA raw data rows:        {len(cbna_raw_data):,}")

# Export CG/CBNA upload full templates
cg_frm_output_full.to_excel(output_cg_upload_full_filename_path, index=False)
cbna_frm_output_full.to_excel(output_cbna_upload_full_filename_path, index=False)

# Export raw data
cg_raw_data.to_excel(output_cg_raw_data_filename_path, index=False)
cbna_raw_data.to_excel(output_cbna_raw_data_filename_path, index=False)

print(f"Exported: {output_cg_upload_full_filename_path}")
print(f"Exported: {output_cbna_upload_full_filename_path}")
print(f"Exported: {output_cg_raw_data_filename_path}")
print(f"Exported: {output_cbna_raw_data_filename_path}")

# %% [markdown]
# ## Build FRM Control

# %%
# Run Cell | Run Above | Debug Cell

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


# %% [markdown]
# ## CG Controls

# %%
# Run Cell | Run Above | Debug Cell

cg_convergence_control   = build_convergence_control(convergence, REPORTABLE_ENTITY_IS_CG,   ADV_CG_TOTAL_RWA_AMT)
cbna_convergence_control = build_convergence_control(convergence, REPORTABLE_ENTITY_IS_CBNA, ADV_CBNA_TOTAL_RWA_AMT)

cg_frm_control   = build_frm_control(cg_frm_output_full)
cbna_frm_control = build_frm_control(cbna_frm_output_full)

cg_raw_data_control   = build_raw_data_control(cg_raw_data)
cbna_raw_data_control = build_raw_data_control(cbna_raw_data)

print(f"CG FRM control rows:   {len(cg_frm_control):,}")
print(f"CBNA FRM control rows: {len(cbna_frm_control):,}")

# %% [markdown]
# ## Parameters summary

# %%
# Run Cell | Run Above | Debug Cell

param_data = [
    ("input_dir",                    str(input_dir)),
    ("model_convergence_dir",        str(model_convergence_dir)),
    ("input_cg_outlook_filename",    input_cg_outlook_filename),
    ("input_cbna_outlook_filename",  input_cbna_outlook_filename),
    ("input_cg_addon_filename",      input_cg_addon_filename),
    ("input_cbna_addon_filename",    input_cbna_addon_filename),
    ("input_pug_filename",           input_pug_filename),
    ("input_pmf_rwa_mapping_filename", input_pmf_rwa_mapping_filename),
    ("input_adjustments_filename",   input_adjustments_filename),
    ("output_cg_upload_full_filename",   output_cg_upload_full_filename),
    ("output_cbna_upload_full_filename", output_cbna_upload_full_filename),
    ("output_cg_raw_data_filename",      output_cg_raw_data_filename),
    ("output_cbna_raw_data_filename",    output_cbna_raw_data_filename),
    ("output_control_filename",          output_control_filename),
    ("output_dir",                       str(output_dir)),
    ("Q0",                               Q0),
]

param_df = pd.DataFrame(param_data, columns=["Parameter", "Value"])

# %% [markdown]
# ## Export Control File (Convergence Control + Raw Data Control + Parameters)

# %%
# Run Cell | Run Above | Debug Cell

cg_convergence_control_file_path = output_dir / output_control_filename

with pd.ExcelWriter(output_control_file_path, engine="openpyxl") as writer:
    # --- CG sheet ---
    start_row = 0
    cg_frm_control.to_excel(writer, sheet_name="CG", startrow=start_row)
    start_row += len(cg_frm_control) + 2

    cg_convergence_control.to_excel(writer, sheet_name="CG", startrow=start_row)

    # --- CBNA sheet ---
    start_row = 0
    cbna_frm_control.to_excel(writer, sheet_name="CBNA", startrow=start_row)
    start_row += len(cbna_frm_control) + 2

    cbna_convergence_control.to_excel(writer, sheet_name="CBNA", startrow=start_row)

    # --- CG Raw Data Control sheet ---
    cg_raw_data_control.to_excel(writer, sheet_name="CG Raw Data Control", startrow=0)

    # --- CBNA Raw Data Control sheet ---
    cbna_raw_data_control.to_excel(writer, sheet_name="CBNA Raw Data Control", startrow=0)

    # --- Parameters sheet ---
    param_df.to_excel(writer, sheet_name="Parameters")

print(f"Exported: {output_control_file_path}")

# %% [markdown]
# ## 17. Summary

# %%
# Run Cell | Run Above | Debug Cell

print("=" * 60)
print("Step 2 complete — Outlook RWA")
print("=" * 60)
print(f"Output directory: {output_dir}")
print(f"\nFiles produced:")
for f in [
    output_cg_upload_full_filename,
    output_cbna_upload_full_filename,
    output_cg_raw_data_filename,
    output_cbna_raw_data_filename,
    output_control_filename,
]:
    print(f"  {f}")
