"""
Outlook RWA — combined pipeline (model convergence + outlook RWA).

Single end-to-end run that (1) builds the 5-key RWF waterfall and computes
SA/AA/ERBA RWA, then (2) joins PUG/PMF mappings + adjustments + convergence and
writes the CG/CBNA upload templates, raw-data files and control file.

The two stages share memory: the model-convergence outputs (cg_outlook,
cbna_outlook and the two addon frames) are passed in-process to the outlook-RWA
stage instead of being re-read from disk. Their parquet artifacts are still
written for inspection; the bulky xlsx copies are written only when
EXPORT_INTERMEDIATE_XLSX is True.

Prerequisite: schema_registry.csv must exist (run create_schema_csv.py first).
"""
import sys
import warnings
import pandas as pd
from pathlib import Path

# Allow running as a stand-alone script (python src/outlook_rwa/pipeline.py) in
# addition to module / installed-entry-point execution. When run as a script
# __package__ is empty and relative imports fail, so put src/ on the path and
# use absolute imports (which resolve in both modes).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from outlook_rwa.functions import (
    _int_str,
    assign_quarter_id,
    assign_year_month_from_quarter,
    calculate_sa_rwa,
    calculate_aa_rwa,
    assign_erba_rwa_and_metadata,
    split_convergence,
    build_markets_addon_pivot,
    build_addon_pivot,
    create_key_pivots,
    compute_rwf,
    set_markets_rwf,
    build_outlook_key_strings,
    rename_month_columns,
    create_quarterly_pivot,
    melt_quarterly_pivot,
    check_and_get_max_quarters,
    build_quarter_mappings,
    load_config,
    _apply_waterfall_lookups,
    format_adjustments,
    rename_addon_columns,
    legacy_franchises_breakout,
    format_columns_before_pivots,
    create_markets_filter,
    create_upload_template_pivots,
    format_upload_template,
    build_convergence_control,
    build_frm_control,
    build_raw_data_control,
)
from outlook_rwa.parallel_excel_to_parquet import (
    load_schema_registry_from_csv,
    load_spec_with_fallback,
    load_specs_with_schema_cast,
    export_outputs,
    make_input_specs,
    normalize_nulls,
    ExcelInputSpec,
)
from outlook_rwa.constants import (
    ADV_CG_TOTAL_RWA_AMT,
    ADV_CBNA_TOTAL_RWA_AMT,
    ADDON_PIVOT_INDEX,
    PMF_ACCOUNTS,
    MARKETS_L2,
    SA_RWA,
    SA_RWA_AMT,
    PROJECTED_QUARTER_TO_MONTH,
    MANAGED_SEGMENT_L4_DESCR,
    PMF_ACCOUNT_L5_DESCR,
    SA_ACCOUNT_NUM,
    AA_ACCOUNT_NUM,
    QUARTER_ID,
    REPORTABLE_ENTITY_IS_CG,
    REPORTABLE_ENTITY_IS_CBNA,
)

pd.set_option("display.max_columns", 500)

# When True, also write the intermediate model-convergence frames as xlsx (the
# parquet copies are always written). The outlook-RWA stage uses the in-memory
# frames regardless, so this flag is purely for human inspection/debugging.
EXPORT_INTERMEDIATE_XLSX = False


def _resolve_output_dir(outputs_cfg, key):
    """Resolve an output dir (with optional *_backup fallback) and create only its
    trailing subfolders. The dir's root (parent.parent, e.g. the data dir) must
    already exist, so a placeholder/mis-set path fails loudly instead of
    materializing a whole bogus tree via mkdir(parents=True)."""
    output_dir = Path(outputs_cfg[key])
    backup_key = f"{key}_backup"
    if not output_dir.exists() and backup_key in outputs_cfg:
        output_dir = Path(outputs_cfg[backup_key])
    root = output_dir.parent.parent
    if not root.exists():
        raise FileNotFoundError(
            f"Cannot create output dir '{output_dir}': its root '{root}' does not "
            f"exist. Check '{key}' in config."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def main():
    # =============================================================================
    # Setup — config, paths, schema registry
    # =============================================================================

    config = load_config(Path(__file__).resolve().parents[2])

    Q0 = config["parameters"]["Q0"]

    schema_csv = Path(config["paths"]["schema_registry_csv"])
    if not schema_csv.exists() and "schema_registry_csv_backup" in config["paths"]:
        schema_csv = Path(config["paths"]["schema_registry_csv_backup"])
    registry = load_schema_registry_from_csv(schema_csv)

    data_dir = Path(config["paths"]["data_dir"])
    if not data_dir.exists() and "data_dir_backup" in config["paths"]:
        data_dir = Path(config["paths"]["data_dir_backup"])
    input_dir = data_dir / "input"

    # Intermediate model-convergence artifacts go here; final deliverables go to the
    # step2 dir. Both keys keep their existing names/semantics.
    model_convergence_dir = _resolve_output_dir(config["outputs"], "step1_dir")
    output_dir = _resolve_output_dir(config["outputs"], "step2_dir")

    print(f"Q0:                    {Q0}")
    print(f"Input dir:             {input_dir}")
    print(f"Model convergence dir: {model_convergence_dir}")
    print(f"Output dir:            {output_dir}")

    # =============================================================================
    # STAGE 1 — Model Convergence
    # =============================================================================

    # --- 1.1 Read input files (parquet-first, schema-cast) ----------------------
    step1_specs = list(make_input_specs(input_dir).values())
    src_cg, src_cbna, src_convergence, src_cg_adjustments, src_cbna_adjustments = (
        load_specs_with_schema_cast(step1_specs, model_convergence_dir, schema_csv)
    )

    cg = src_cg.copy(deep=True)
    cbna = src_cbna.copy(deep=True)
    convergence = src_convergence.copy(deep=True)
    cg_adjustments = src_cg_adjustments.copy(deep=True)
    cbna_adjustments = src_cbna_adjustments.copy(deep=True)

    print(f"✅ CG rows:          {len(cg):,}")
    print(f"✅ CBNA rows:        {len(cbna):,}")
    print(f"✅ Convergence rows: {len(convergence):,}")

    # --- 1.2 Merge Geography Level 3 into convergence ---------------------------
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

    # --- 1.3 Normalise PMF Account / Finance PMF column types -------------------
    cg["PMF Account L5 Descr"] = cg["PMF Account L5 Descr"].astype(str)
    cbna["PMF Account L5 Descr"] = cbna["PMF Account L5 Descr"].astype(str)
    convergence["Finance PMF Level 5 Description"] = convergence["Finance PMF Level 5 Description"].astype(str)

    # --- 1.4 Split convergence into credit-risk buckets -------------------------
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

    # --- 1.5 Build 5-key pivot tables and compute RWFs --------------------------
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

    # --- 1.6 Reshape balance sheet: pivot then melt to long ---------------------
    rename_month_columns(cg)
    rename_month_columns(cbna)

    cg_pivot = create_quarterly_pivot(cg)
    cbna_pivot = create_quarterly_pivot(cbna)

    cg_outlook = melt_quarterly_pivot(cg_pivot)
    cbna_outlook = melt_quarterly_pivot(cbna_pivot)

    print(f"CG outlook long rows:   {len(cg_outlook):,}")
    print(f"CBNA outlook long rows: {len(cbna_outlook):,}")

    # --- 1.7 Quarter mapping + quarter IDs --------------------------------------
    max_quarters = check_and_get_max_quarters(convergence, cg_outlook, cbna_outlook)
    quarter_map, quarter_id_mapping = build_quarter_mappings(Q0, max_quarters)

    assign_quarter_id(cg_outlook, quarter_id_mapping)
    assign_quarter_id(cbna_outlook, quarter_id_mapping)

    # --- 1.8 Build waterfall key strings and apply RWF lookups ------------------
    build_outlook_key_strings(cg_outlook)
    build_outlook_key_strings(cbna_outlook)

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

    # --- 1.9 Apply adjustments --------------------------------------------------
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

    # --- 1.10 Calculate RWA -----------------------------------------------------
    calculate_sa_rwa(cg_outlook)
    calculate_aa_rwa(cg_outlook)
    calculate_sa_rwa(cbna_outlook)
    calculate_aa_rwa(cbna_outlook)

    assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook)

    # --- 1.11 Addon: markets / non-waterfall ------------------------------------
    # Derive YEAR / Month / Quarter Id on the raw rows first (Quarter Id is a
    # pivot key), then aggregate both add-on buckets to the addon-pivot grain so
    # the export carries one summarized row per index group instead of one row
    # per raw convergence record.
    for addon_df in [
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna,
    ]:
        q_num = pd.to_numeric(addon_df["Projected Quarter"].str[0], errors="coerce").astype("Int64")
        addon_df["YEAR"] = pd.to_numeric(
            addon_df["Projected Quarter"].str[2:].apply(lambda x: "20" + x if pd.notna(x) else x),
            errors="coerce",
        ).astype("Int64")
        addon_df["Month"] = q_num.map(PROJECTED_QUARTER_TO_MONTH)
        assign_quarter_id(addon_df, quarter_id_mapping)
        # pivot_table silently drops rows whose index keys are NaN; fill first.
        addon_df[ADDON_PIVOT_INDEX] = addon_df[ADDON_PIVOT_INDEX].fillna("None")

    cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk = build_markets_addon_pivot(
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk, ADDON_PIVOT_INDEX
    )
    non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna = build_addon_pivot(
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna, ADDON_PIVOT_INDEX
    )

    # Re-derive YEAR / Month (dropped by the pivot) from the surviving Quarter Id,
    # then derive the RWA metadata from the aggregated totals. ERBA RWA / Comment
    # / RWA Exposure Type mirror the waterfall path and apply to the Markets
    # credit-risk add-on only (matching the pre-pivot behaviour).
    assign_year_month_from_quarter(
        cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk,
        non_credit_risk_non_waterfall_cg, non_credit_risk_non_waterfall_cbna,
        quarter_map=quarter_map,
    )
    cg_addon_markets_credit_risk[SA_RWA] = cg_addon_markets_credit_risk[SA_RWA_AMT]
    cbna_addon_markets_credit_risk[SA_RWA] = cbna_addon_markets_credit_risk[SA_RWA_AMT]
    assign_erba_rwa_and_metadata(cg_addon_markets_credit_risk, cbna_addon_markets_credit_risk)

    cg_addon_non_waterfall_rwa, cbna_addon_non_waterfall_rwa = (
        pd.concat([non_credit_risk_non_waterfall_cg, cg_addon_markets_credit_risk], ignore_index=True),
        pd.concat([non_credit_risk_non_waterfall_cbna, cbna_addon_markets_credit_risk], ignore_index=True),
    )

    print(f"CG addon non-waterfall rows:   {len(cg_addon_non_waterfall_rwa):,}")
    print(f"CBNA addon non-waterfall rows: {len(cbna_addon_non_waterfall_rwa):,}")

    # --- 1.12 Export intermediate artifacts (parquet always; xlsx if debug) -----
    intermediate_files = {
        config["outputs"]["step1"][0]["cg_outlook"]: cg_outlook,
        config["outputs"]["step1"][0]["cbna_outlook"]: cbna_outlook,
        config["outputs"]["step1"][0]["cg_addon_non_waterfall_rwa"]: cg_addon_non_waterfall_rwa,
        config["outputs"]["step1"][0]["cbna_addon_non_waterfall_rwa"]: cbna_addon_non_waterfall_rwa,
    }
    intermediate_formats = ("xlsx", "parquet") if EXPORT_INTERMEDIATE_XLSX else ("parquet",)
    export_outputs(intermediate_files, model_convergence_dir, formats=intermediate_formats)

    # --- 1.13 Hand off to stage 2 in memory -------------------------------------
    # Reproduce the null semantics the old parquet round-trip applied (empty string
    # -> NaN), so the in-memory handoff is identical to re-reading from disk.
    src_cg_outlook = normalize_nulls(cg_outlook)
    src_cbna_outlook = normalize_nulls(cbna_outlook)
    src_addon_all_cg = normalize_nulls(cg_addon_non_waterfall_rwa)
    src_addon_all_cbna = normalize_nulls(cbna_addon_non_waterfall_rwa)

    # =============================================================================
    # STAGE 2 — Outlook RWA
    # =============================================================================

    input_adjustments_filename    = "adjustment_master_file.xlsx"
    input_pug_filename             = "pug_mapping.xlsx"
    input_pmf_rwa_mapping_filename = "pmf_rwa_mapping.xlsx"

    input_cg_outlook_filename   = config["outputs"]["step1"][0]["cg_outlook"]
    input_cbna_outlook_filename = config["outputs"]["step1"][0]["cbna_outlook"]
    input_cg_addon_filename     = config["outputs"]["step1"][0]["cg_addon_non_waterfall_rwa"]
    input_cbna_addon_filename   = config["outputs"]["step1"][0]["cbna_addon_non_waterfall_rwa"]

    output_cg_upload_full_filename   = "CG_Upload_Template_Full.xlsx"
    output_cbna_upload_full_filename = "CBNA_Upload_Template_Full.xlsx"
    output_cg_raw_data_filename      = "CG_RAW_DATA.xlsx"
    output_cbna_raw_data_filename    = "CBNA_RAW_DATA.xlsx"
    output_control_filename          = "control_file.xlsx"

    pug_file         = input_dir / input_pug_filename
    pmf_mapping_file = input_dir / input_pmf_rwa_mapping_filename

    output_cg_upload_full_filename_path   = output_dir / output_cg_upload_full_filename
    output_cbna_upload_full_filename_path = output_dir / output_cbna_upload_full_filename
    output_cg_raw_data_filename_path      = output_dir / output_cg_raw_data_filename
    output_cbna_raw_data_filename_path    = output_dir / output_cbna_raw_data_filename
    output_control_file_path              = output_dir / output_control_filename

    # --- 2.1 Read stage-2 inputs ------------------------------------------------
    # Adjustments / PUG / PMF / convergence stay on disk (parquet-first w/ Excel
    # fallback); the outlook + addon frames come from stage 1 in memory.
    shared = make_input_specs(input_dir)
    disk_specs = [
        (shared["cg_adjustments"],   input_dir),
        (shared["cbna_adjustments"], input_dir),
        (ExcelInputSpec("pug",             pug_file,         "pug", "pug_mapping.parquet"),                input_dir),
        (ExcelInputSpec("pmf_rwa_mapping", pmf_mapping_file, "pmf", "pmf_rwa_mapping.parquet", "Sheet1"),  input_dir),
        (shared["convergence"],      model_convergence_dir),
    ]

    for spec, parquet_dir in disk_specs:
        if not (parquet_dir / spec.output_name).exists() and not spec.path.exists():
            raise FileNotFoundError(
                f"INPUT NOT FOUND for '{spec.label}': neither "
                f"{parquet_dir / spec.output_name} nor {spec.path}"
            )

    (
        src_cg_adjustments,
        src_cbna_adjustments,
        src_pug,
        src_rwa_pmf_mapping,
        src_convergence,
    ) = [load_spec_with_fallback(spec, parquet_dir, registry) for spec, parquet_dir in disk_specs]

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
    print(f"PUG Mapping rows:      {len(pug_df):,}")

    # --- 2.2 Format adjustments -------------------------------------------------
    cg_adjustments_formatted  = format_adjustments(cg_adjustments.copy())
    cbna_adjustments_formatted = format_adjustments(cbna_adjustments.copy())

    # --- 2.3 Rename addon columns to outlook schema -----------------------------
    addon_all_cg   = rename_addon_columns(addon_all_cg, 'CG')
    addon_all_cbna = rename_addon_columns(addon_all_cbna, 'CBNA')

    # --- 2.4 PUG / PMF mapping data-quality checks ------------------------------
    pug_dupes = src_pug.duplicated(subset=[MANAGED_SEGMENT_L4_DESCR], keep=False)
    if pug_dupes.sum() > 0:
        warnings.warn(f"PUG mapping has {pug_dupes.sum()} duplicates on Managed Segment L4 Descr")

    rwa_pmf_mapping = rwa_pmf_mapping.rename(columns={"PMF L5": PMF_ACCOUNT_L5_DESCR})

    pmf_dupes = rwa_pmf_mapping.duplicated(subset=[PMF_ACCOUNT_L5_DESCR], keep=False)
    if pmf_dupes.sum() > 0:
        warnings.warn(f"PMF RWA mapping has {pmf_dupes.sum()} duplicates on PMF L5")

    # --- 2.5 Concatenate adjustments + outlook + addon --------------------------
    cg_concat = pd.concat([
        cg_adjustments_formatted,
        cg_outlook,
        addon_all_cg,
    ], ignore_index=True).copy()

    cbna_concat = pd.concat([
        cbna_adjustments_formatted,
        cbna_outlook,
        addon_all_cbna,
    ], ignore_index=True).copy()

    cg_concat[QUARTER_ID]   = pd.to_numeric(cg_concat[QUARTER_ID], errors='coerce')
    cbna_concat[QUARTER_ID] = pd.to_numeric(cbna_concat[QUARTER_ID], errors='coerce')

    cg_concat   = cg_concat[cg_concat[QUARTER_ID] != 'Unknown']
    cbna_concat = cbna_concat[cbna_concat[QUARTER_ID] != 'Unknown']

    cg_concat   = cg_concat[cg_concat[QUARTER_ID].notna()]
    cbna_concat = cbna_concat[cbna_concat[QUARTER_ID].notna()]

    print(f"CG after filtering unknowns:   {len(cg_concat)}")
    print(f"CBNA after filtering unknowns: {len(cbna_concat)}")

    # --- 2.6 Save raw data (before further transforms) --------------------------
    cg_concat["Entity"]   = "BA"
    cbna_concat["Entity"] = "BB"

    cg_raw_data   = cg_concat.copy()
    cbna_raw_data = cbna_concat.copy()

    # --- 2.7 Legacy franchises breakout -----------------------------------------
    cg_concat   = legacy_franchises_breakout(cg_concat)
    cbna_concat = legacy_franchises_breakout(cbna_concat)

    # --- 2.8 FRM output ---------------------------------------------------------
    frm_output_cg   = cg_concat.copy()
    frm_output_cbna = cbna_concat.copy()

    # --- 2.9 Join PUG mapping ---------------------------------------------------
    pre_cg   = len(frm_output_cg)
    pre_cbna = len(frm_output_cbna)

    frm_output_cg = frm_output_cg.merge(
        pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]], how="left", on=MANAGED_SEGMENT_L4_DESCR,
    )
    frm_output_cbna = frm_output_cbna.merge(
        pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]], how="left", on=MANAGED_SEGMENT_L4_DESCR,
    )

    if len(frm_output_cg) != pre_cg:
        warnings.warn(f"CG: PUG join caused row expansion: {pre_cg} -> {len(frm_output_cg)}")
    if len(frm_output_cbna) != pre_cbna:
        warnings.warn(f"CBNA: PUG join caused row expansion: {pre_cbna} -> {len(frm_output_cbna)}")

    for label, df in [("CG", frm_output_cg), ("CBNA", frm_output_cbna)]:
        unmatched = df[df["PUG"].isna()]
        pct = len(unmatched) / (len(df) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(f"{label}: (unmatched: {pct:.1f}%) rows ({len(unmatched):,}) have no PUG mapping match!")

    # --- 2.10 Join PMF mapping --------------------------------------------------
    pre_cg   = len(frm_output_cg)
    pre_cbna = len(frm_output_cbna)

    frm_output_cg = frm_output_cg.merge(
        rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
        how="left", on=PMF_ACCOUNT_L5_DESCR,
    )
    frm_output_cbna = frm_output_cbna.merge(
        rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
        how="left", on=PMF_ACCOUNT_L5_DESCR,
    )

    if len(frm_output_cg) != pre_cg:
        warnings.warn(f"CG: PMF join caused row expansion: {pre_cg} -> {len(frm_output_cg)}")
    if len(frm_output_cbna) != pre_cbna:
        warnings.warn(f"CBNA: PMF join caused row expansion: {pre_cbna} -> {len(frm_output_cbna)}")

    for label, df in [("CG", frm_output_cg), ("CBNA", frm_output_cbna)]:
        unmatched = df[df[SA_ACCOUNT_NUM].isna() & df[PMF_ACCOUNT_L5_DESCR].notna()]
        pct = len(unmatched) / (len(df) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(
                f"{label}: PMF RWA mapping has {len(unmatched):,} ({pct:.1f}%) rows with no match!"
            )

    # --- 2.11 Format columns before pivots --------------------------------------
    frm_output_cg   = format_columns_before_pivots(frm_output_cg.copy())
    frm_output_cbna = format_columns_before_pivots(frm_output_cbna.copy())

    # --- 2.12 Upload template pivots (ERBA / AA / SA) + markets filter -----------
    cg_frm_output   = create_upload_template_pivots(frm_output_cg)
    cbna_frm_output = create_upload_template_pivots(frm_output_cbna)

    # Markets filter (inert) — applied after pivots to mirror production ordering.
    cg_frm_output   = create_markets_filter(cg_frm_output)
    cbna_frm_output = create_markets_filter(cbna_frm_output)

    print(f"CG pivot rows:   {len(cg_frm_output):,}")
    print(f"CBNA pivot rows: {len(cbna_frm_output):,}")

    # --- 2.13 Format upload template + export upload/raw-data --------------------
    cg_frm_output_full   = format_upload_template(cg_frm_output)
    cbna_frm_output_full = format_upload_template(cbna_frm_output)

    print(f"CG Upload template rows:   {len(cg_frm_output_full):,}")
    print(f"CBNA Upload template rows: {len(cbna_frm_output_full):,}")

    cg_frm_output_full.to_excel(output_cg_upload_full_filename_path, index=False)
    cbna_frm_output_full.to_excel(output_cbna_upload_full_filename_path, index=False)
    cg_raw_data.to_excel(output_cg_raw_data_filename_path, index=False)
    cbna_raw_data.to_excel(output_cbna_raw_data_filename_path, index=False)

    print(f"Exported: {output_cg_upload_full_filename_path}")
    print(f"Exported: {output_cbna_upload_full_filename_path}")
    print(f"Exported: {output_cg_raw_data_filename_path}")
    print(f"Exported: {output_cbna_raw_data_filename_path}")

    # --- 2.14 Build controls ----------------------------------------------------
    cg_convergence_control   = build_convergence_control(convergence, REPORTABLE_ENTITY_IS_CG,   ADV_CG_TOTAL_RWA_AMT)
    cbna_convergence_control = build_convergence_control(convergence, REPORTABLE_ENTITY_IS_CBNA, ADV_CBNA_TOTAL_RWA_AMT)

    cg_frm_control   = build_frm_control(cg_frm_output_full)
    cbna_frm_control = build_frm_control(cbna_frm_output_full)

    cg_raw_data_control   = build_raw_data_control(cg_raw_data)
    cbna_raw_data_control = build_raw_data_control(cbna_raw_data)

    # --- 2.15 Parameters summary ------------------------------------------------
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

    # --- 2.16 Export control file -----------------------------------------------
    with pd.ExcelWriter(output_control_file_path, engine="openpyxl") as writer:
        start_row = 0
        cg_frm_control.to_excel(writer, sheet_name="CG", startrow=start_row)
        start_row += len(cg_frm_control) + 2
        cg_convergence_control.to_excel(writer, sheet_name="CG", startrow=start_row)

        start_row = 0
        cbna_frm_control.to_excel(writer, sheet_name="CBNA", startrow=start_row)
        start_row += len(cbna_frm_control) + 2
        cbna_convergence_control.to_excel(writer, sheet_name="CBNA", startrow=start_row)

        cg_raw_data_control.to_excel(writer, sheet_name="CG Raw Data Control", startrow=0)
        cbna_raw_data_control.to_excel(writer, sheet_name="CBNA Raw Data Control", startrow=0)
        param_df.to_excel(writer, sheet_name="Parameters")

    print(f"Exported: {output_control_file_path}")

    # =============================================================================
    # Summary
    # =============================================================================
    print("=" * 60)
    print("Outlook RWA pipeline complete")
    print("=" * 60)
    print(f"Output directory: {output_dir}")
    print("\nFiles produced:")
    for f in [
        output_cg_upload_full_filename,
        output_cbna_upload_full_filename,
        output_cg_raw_data_filename,
        output_cbna_raw_data_filename,
        output_control_filename,
    ]:
        print(f"  {f}")


if __name__ == "__main__":
    main()
