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
)
from parallel_excel_to_parquet import load_schema_registry_from_csv
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
output_cg_upload_full_filename    = f"CG_Upload_Template_Full.{run_datetime}.xlsx"
output_cbna_upload_full_filename  = f"CBNA_Upload_Template_Full.{run_datetime}.xlsx"
output_cg_raw_data_filename       = f"CG_RAW_DATA.{run_datetime}.xlsx"
output_cbna_raw_data_filename     = f"CBNA_RAW_DATA.{run_datetime}.xlsx"
output_control_filename           = f"control_file.{run_datetime}.xlsx"

output_dir = Path(config["paths"]["data_dir"]).parent / "output" / run_datetime
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

# --- Data Quality Check: verify files exist ---
all_input_files = [
    adjustments_file,
    pug_file,
    pmf_mapping_file,
    cg_outlook_file,
    cbna_outlook_file,
    cg_addon_file,
    cbna_addon_file,
]

for f in all_input_files:
    if not f.exists():
        raise FileNotFoundError(f"INPUT FILE NOT FOUND: {f}")
    else:
        print(f"Found: {f.name}")

src_cg_adjustments   = pd.read_excel(adjustments_file, sheet_name="Adjustments - CG")
src_cbna_adjustments = pd.read_excel(adjustments_file, sheet_name="Adjustments - CBNA")
src_pug              = pd.read_excel(pug_file)
src_rwa_pmf_mapping  = pd.read_excel(pmf_mapping_file, sheet_name="Sheet1")
src_cg_outlook       = pd.read_excel(cg_outlook_file)
src_cbna_outlook     = pd.read_excel(cbna_outlook_file)
src_addon_all_cg     = pd.read_excel(cg_addon_file)
src_addon_all_cbna   = pd.read_excel(cbna_addon_file)

cg_adjustments   = src_cg_adjustments.copy(deep=True)
cbna_adjustments = src_cbna_adjustments.copy(deep=True)
pug_df           = src_pug.copy(deep=True)
rwa_pmf_mapping  = src_rwa_pmf_mapping.copy(deep=True)
cg_outlook       = src_cg_outlook.copy(deep=True)
cbna_outlook     = src_cbna_outlook.copy(deep=True)
addon_all_cg     = src_addon_all_cg.copy(deep=True)
addon_all_cbna   = src_addon_all_cbna.copy(deep=True)

print(f"CG Adjustments rows:   {len(cg_adjustments):,}")
print(f"CBNA Adjustments rows: {len(cbna_adjustments):,}")
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
    """Coerce numeric columns and format adjustment data.

    Args:
        input_df: Raw adjustments DataFrame.

    Returns:
        Formatted DataFrame with numeric columns coerced.
    """
    numeric_cols = input_df.select_dtypes(include='number').columns
    for c in numeric_cols:
        if c in input_df.columns:
            input_df[c] = pd.to_numeric(input_df[c], errors='coerce')

    string_cols = input_df.select_dtypes(include='object').columns  # noqa: F841

    return input_df


cg_adjustments_formatted  = format_adjustments(cg_adjustments.copy())
cbna_adjustments_formatted = format_adjustments(cbna_adjustments.copy())

print(f"CG adjustments formatted:   {len(cg_adjustments_formatted):,} rows")
print(f"CBNA adjustments formatted: {len(cbna_adjustments_formatted):,} rows")

# %% [markdown]
# ## 3. Rename Addon Columns to Match Outlook Schema

# %%
# Run Cell | Run Above | Debug Cell

def rename_addon_columns(input_df):
    """Rename convergence-schema column names to balance-sheet-style short names.

    Addon data uses long convergence column names; outlook data uses short
    balance-sheet-style names. Renames so they can be concatenated.

    When the addon frame contains both the long-form source column and the
    short-form target column (because step1 concatenates credit-risk and
    non-waterfall frames that each carry different column sets), the existing
    short-form column is dropped first so the rename does not create duplicates.

    Args:
        input_df: Addon DataFrame with convergence-style column names.

    Returns:
        DataFrame with renamed columns matching outlook schema, no duplicates.
    """
    rename_dict = {
        "Managed Segment Level 4 Description": MANAGED_SEGMENT_L4_DESCR,
        "Managed Segment Level 3 Description": MANAGED_SEGMENT_L3_DESCR,
        "Managed Segment Level 2 Description": MANAGED_SEGMENT_L2_DESCR,
        "Managed Geography Level 3 Description": MANAGED_GEOGRAPHY_L3_DESCR,
        "Finance PMF Level 5 Description": PMF_ACCOUNT_L5_DESCR,
        "SA RWA Amount": SA_RWA,
        "Adv. CG Total RWA Amount with 1.06 Multiplier": AA_RWA,
        "Quarter Id": QUARTER_ID,
        "RWA Exposure Type Description": RWA_EXPOSURE_TYPE,
        "Comments": "Comment",
    }
    # Only rename columns that actually exist in the frame
    rename_dict = {k: v for k, v in rename_dict.items() if k in input_df.columns}
    # Drop any pre-existing target columns that would collide with the rename
    cols_to_drop = [v for v in rename_dict.values() if v in input_df.columns]
    df = input_df.drop(columns=cols_to_drop)
    return df.rename(columns=rename_dict)


addon_all_cg   = rename_addon_columns(addon_all_cg)
addon_all_cbna = rename_addon_columns(addon_all_cbna)

print(f"CG addon columns renamed")
print(f"CBNA addon columns renamed")

# %% [markdown]
# ## 4. Filter out Discontinued Ops

# %%
# Run Cell | Run Above | Debug Cell

def filter_out_discontinued_ops(input_df):
    """Filter out rows where Managed Segment L2 == Discontinued Ops.

    Args:
        input_df: DataFrame with MANAGED_SEGMENT_L2_DESCR column.

    Returns:
        Filtered DataFrame with discontinued ops rows removed.
    """
    return input_df[input_df[MANAGED_SEGMENT_L2_DESCR] != DISCONTINUED_OPS_L2].copy()


# Filter discontinued ops
cg_outlook   = filter_out_discontinued_ops(cg_outlook)
cbna_outlook = filter_out_discontinued_ops(cbna_outlook)

addon_all_cg   = filter_out_discontinued_ops(addon_all_cg)
addon_all_cbna = filter_out_discontinued_ops(addon_all_cbna)

print(f"CG outlook after filtering discontinued ops:   {len(cg_outlook):,} rows")
print(f"CBNA outlook after filtering discontinued ops: {len(cbna_outlook):,} rows")

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

# Data Quality Check: PMF RWA mapping duplicates on PMF L5
pmf_dupes = src_rwa_pmf_mapping.duplicated(subset=[PMF_ACCOUNT_L5_DESCR], keep=False)
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


cg_frm_output   = create_markets_filter(frm_output_cg)
cbna_frm_output = create_markets_filter(frm_output_cbna)

print(f"Markets filter applied!")

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
        pivot[RWA_CALC] = rwa_label
        return pivot

    # ERBA pivot (commented out — kept for reference)
    erba_pivot = make_pivot(ERBA_RWA, "ERBA")
    aa_pivot   = make_pivot(AA_RWA,   "AA")
    sa_pivot   = make_pivot(SA_RWA,   "SA")

    pivots = pd.concat([erba_pivot, aa_pivot, sa_pivot])
    pivots.columns.name = None
    return pivots


cg_frm_output   = create_upload_template_pivots(cg_frm_output)
cbna_frm_output = create_upload_template_pivots(cbna_frm_output)

print(f"CG pivot rows:   {len(cg_frm_output):,}")
print(f"CBNA pivot rows: {len(cbna_frm_output):,}")

# %% [markdown]
# ## 14. Format Upload Template (Add Upload Columns)

# %%
# Run Cell | Run Above | Debug Cell

def format_upload_template(input_df):
    """Add required upload stub columns and reorder for upload.

    Adds FileType='R', ManagedGeo='', FrsBu='', Product='', Affiliate='',
    ProjectAccount='000000', and monthly placeholder columns (Month1-Month8).
    Fills default SA/AA account numbers where missing and reorders columns.

    Args:
        input_df: DataFrame with all RWA and segment columns populated.

    Returns:
        input_df with required upload columns added and reordered.
    """
    input_df = input_df.copy()

    # Add required upload columns
    input_df["FileType"]       = "R"
    input_df["ManagedGeo"]     = ""
    input_df["FrsBu"]          = ""
    input_df["Product"]        = ""
    input_df["Affiliate"]      = ""
    input_df["ProjectAccount"] = "000000"

    # Monthly placeholder columns (Month1-Month8)  # TODO: verify exact count from source
    MONTHLY = ["Month1", "Month2", "Month3", "Month4", "Month5", "Month6", "Month7", "Month8"]
    for m in MONTHLY:
        input_df[m] = ""

    # Fill default Account numbers where missing
    input_df[SA_ACCOUNT_NUM] = np.where(
        (input_df[RWA_CALC] == "AA") & (input_df[AA_ACCOUNT_NUM].isna()),
        input_df[AA_ACCOUNT_NUM],
        input_df[SA_ACCOUNT_NUM],
    )

    # Fill SA/AA account numbers where missing with default values
    input_df[SA_ACCOUNT_NUM] = input_df[SA_ACCOUNT_NUM].fillna("563722")  # TODO: verify from source
    input_df[AA_ACCOUNT_NUM] = input_df[AA_ACCOUNT_NUM].fillna("563722")  # TODO: verify from source

    # Drop SA/AA Account columns  # TODO: verify exact drop logic from source
    # Column ordering
    col_order = [
        "FileType",
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
        RWA_CALC,
        "ManagedGeo",
        "FrsBu",
        "Product",
        "Affiliate",
        "ProjectAccount",
    ] + MONTHLY

    input_df = input_df[[c for c in col_order if c in input_df.columns]]
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

def build_frm_control(frm_output):
    """Build FRM convergence control totals by groupby aggregation.

    Aggregates RWA amounts by segment, quarter, and RWA calc type.
    Filters out Discontinued Ops rows from the control summary.

    Args:
        frm_output: DataFrame with RWA amount columns and segment descriptors.

    Returns:
        Summary DataFrame of control totals.
    """
    ctrl = frm_output.groupby(
        [MANAGED_SEGMENT_L2_DESCR, MANAGED_SEGMENT_L3_DESCR, QUARTER_ID],
        dropna=False,
    ).agg(
        **{
            SA_RWA:   (SA_RWA,   "sum"),
            AA_RWA:   (AA_RWA,   "sum"),
            ERBA_RWA: (ERBA_RWA, "sum"),
        }
    ).reset_index()

    ctrl = ctrl[ctrl[MANAGED_SEGMENT_L2_DESCR] != DISCONTINUED_OPS_L2]

    add_rwa_calc = ctrl.copy()
    add_rwa_calc[RWA_CALC] = "AA"
    ctrl[RWA_CALC] = "SA"
    ctrl = pd.concat([ctrl, add_rwa_calc])

    ctrl.columns.name = None
    return ctrl


def build_raw_data_control(raw_data):
    """Build raw data control totals by groupby aggregation.

    Args:
        raw_data: Raw DataFrame before pivot transformations.

    Returns:
        Summary DataFrame of raw data control totals.
    """
    ctrl = raw_data.groupby(
        [MANAGED_SEGMENT_L2_DESCR, MANAGED_SEGMENT_L3_DESCR, QUARTER_ID],
        dropna=False,
    ).agg(
        **{
            SA_RWA: (SA_RWA, "sum"),
            AA_RWA: (AA_RWA, "sum"),
        }
    ).reset_index()

    ctrl = ctrl[ctrl[MANAGED_SEGMENT_L2_DESCR] != DISCONTINUED_OPS_L2]

    add_rwa_calc = ctrl.copy()
    add_rwa_calc[RWA_CALC] = "AA"
    ctrl[RWA_CALC] = "SA"
    ctrl = pd.concat([ctrl, add_rwa_calc])

    ctrl.columns.name = None
    return ctrl


# %% [markdown]
# ## CG Controls

# %%
# Run Cell | Run Above | Debug Cell

cg_frm_control        = build_frm_control(frm_output_cg)
cbna_frm_control      = build_frm_control(frm_output_cbna)
cg_raw_data_control   = build_raw_data_control(cg_raw_data)
cbna_raw_data_control = build_raw_data_control(cbna_raw_data)

# Convergence control — Total RWA Amount with 1.06 Multiplier
cg_convergence_control   = build_frm_control(frm_output_cg)
cbna_convergence_control = build_frm_control(frm_output_cbna)

cg_frm_data_control   = build_raw_data_control(cg_raw_data)
cbna_frm_data_control = build_raw_data_control(cbna_raw_data)

cons_frm_control = build_frm_control(pd.concat([frm_output_cg, frm_output_cbna]))

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
    start_row = 0
    cg_frm_data_control.to_excel(writer, sheet_name="CG Raw Data Control", startrow=start_row)

    # --- CBNA Raw Data Control sheet ---
    cbna_frm_data_control.to_excel(writer, sheet_name="CBNA Raw Data Control", startrow=0)

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
