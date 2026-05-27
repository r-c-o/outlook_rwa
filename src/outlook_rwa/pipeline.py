"""
Outlook RWA — combined pipeline (model convergence + outlook RWA).

Single end-to-end run that (1) builds the 5-key RWF waterfall and computes
SA/AA/ERBA RWA, then (2) joins PUG/PMF mappings + adjustments + convergence and
writes the CG/CBNA upload templates, raw-data files and control file.

The two stages share memory: the model-convergence outputs (the per-entity
outlook and addon frames) are passed in-process to the outlook-RWA stage instead
of being re-read from disk. Their parquet artifacts are still written for
inspection; the bulky xlsx copies are written only when EXPORT_INTERMEDIATE_XLSX
is True.

CG and CBNA run through one shared code path, parameterised by EntityConfig
(constants.ENTITIES); per-entity frames are kept in dicts keyed by entity name.

Prerequisite: schema_registry.csv must exist (run create_schema_csv.py first).
"""
import warnings
import pandas as pd
from pathlib import Path

from .functions import (
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
    load_config,
    _apply_waterfall_lookups,
    apply_adjustments,
    prepare_addon_quarter_fields,
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
from .parallel_excel_to_parquet import (
    load_schema_registry_from_csv,
    load_spec_with_fallback,
    load_specs_with_schema_cast,
    export_outputs,
    make_input_specs,
    normalize_nulls,
    ExcelInputSpec,
)
from .constants import (
    ENTITIES,
    CG_ENTITY,
    CBNA_ENTITY,
    SA_RWA,
    SA_RWA_AMT,
    MANAGED_SEGMENT_L4_DESCR,
    PMF_ACCOUNT_L5_DESCR,
    SA_ACCOUNT_NUM,
    AA_ACCOUNT_NUM,
    QUARTER_ID,
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


# =============================================================================
# STAGE 1 — Model Convergence
# =============================================================================

def run_model_convergence(config, schema_csv, input_dir, model_convergence_dir, Q0):
    """Build the 5-key RWF waterfall, compute SA/AA/ERBA RWA, and return the
    per-entity outlook + addon frames (already null-normalised for stage 2)."""

    # --- 1.1 Read input files (parquet-first, schema-cast) ------------------
    step1_specs = list(make_input_specs(input_dir).values())
    src_cg, src_cbna, src_convergence, src_cg_adjustments, src_cbna_adjustments = (
        load_specs_with_schema_cast(step1_specs, model_convergence_dir, schema_csv)
    )

    balance_sheet = {CG_ENTITY.name: src_cg.copy(deep=True), CBNA_ENTITY.name: src_cbna.copy(deep=True)}
    adjustments = {CG_ENTITY.name: src_cg_adjustments.copy(deep=True), CBNA_ENTITY.name: src_cbna_adjustments.copy(deep=True)}
    convergence = src_convergence.copy(deep=True)

    for entity in ENTITIES:
        print(f"✅ {entity.name} rows: {len(balance_sheet[entity.name]):,}")
    print(f"✅ Convergence rows: {len(convergence):,}")

    # --- 1.2 Merge Geography Level 3 into convergence -----------------------
    dummy_df = balance_sheet[CG_ENTITY.name][
        ["Managed Geography L3 Descr", "Managed Geography L4 Descr"]
    ].drop_duplicates()
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

    # --- 1.3 Normalise PMF Account / Finance PMF column types ---------------
    for entity in ENTITIES:
        balance_sheet[entity.name]["PMF Account L5 Descr"] = balance_sheet[entity.name]["PMF Account L5 Descr"].astype(str)
    convergence["Finance PMF Level 5 Description"] = convergence["Finance PMF Level 5 Description"].astype(str)

    # --- 1.4 Split convergence into per-entity buckets ----------------------
    buckets = split_convergence(convergence)
    for entity in ENTITIES:
        print(f"{entity.name} credit-risk rows: {len(buckets[entity.name].credit_risk):,}")

    # --- 1.5 Build 5-key pivot tables and compute RWFs ----------------------
    lookups = {}
    for entity in ENTITIES:
        pivots = create_key_pivots(buckets[entity.name].credit_risk, entity.adv_rwa_col)
        for pivot in pivots.values():
            compute_rwf(pivot, entity.adv_rwa_col)
            set_markets_rwf(pivot)
        lookups[entity.name] = pivots

    # --- 1.6 Reshape balance sheet: pivot then melt to long -----------------
    outlook = {}
    for entity in ENTITIES:
        bs_df = balance_sheet[entity.name]
        rename_month_columns(bs_df)
        outlook[entity.name] = melt_quarterly_pivot(create_quarterly_pivot(bs_df))
        print(f"{entity.name} outlook long rows: {len(outlook[entity.name]):,}")

    # --- 1.7 Quarter mapping + quarter IDs ----------------------------------
    max_quarters = check_and_get_max_quarters(convergence, outlook[CG_ENTITY.name], outlook[CBNA_ENTITY.name])
    quarter_map, quarter_id_mapping = build_quarter_mappings(Q0, max_quarters)

    for entity in ENTITIES:
        assign_quarter_id(outlook[entity.name], quarter_id_mapping)

    # --- 1.8 Build waterfall key strings and apply RWF lookups --------------
    for entity in ENTITIES:
        build_outlook_key_strings(outlook[entity.name])
        outlook[entity.name] = _apply_waterfall_lookups(outlook[entity.name], lookups[entity.name])

    # --- 1.9 Apply adjustments ----------------------------------------------
    for entity in ENTITIES:
        outlook[entity.name] = apply_adjustments(outlook[entity.name], adjustments[entity.name])

    # --- 1.10 Calculate RWA + metadata --------------------------------------
    for entity in ENTITIES:
        calculate_sa_rwa(outlook[entity.name])
        calculate_aa_rwa(outlook[entity.name])
        assign_erba_rwa_and_metadata(outlook[entity.name])

    # --- 1.11 Addon: markets / non-waterfall --------------------------------
    addon = {}
    for entity in ENTITIES:
        b = buckets[entity.name]
        b.markets[SA_RWA] = b.markets[SA_RWA_AMT]
        assign_erba_rwa_and_metadata(b.markets)
        prepare_addon_quarter_fields(b.markets, quarter_id_mapping)
        prepare_addon_quarter_fields(b.non_waterfall, quarter_id_mapping)
        addon[entity.name] = pd.concat([b.non_waterfall, b.markets], ignore_index=True)
        print(f"{entity.name} addon non-waterfall rows: {len(addon[entity.name]):,}")

    # --- 1.12 Export intermediate artifacts (parquet always; xlsx if debug) -
    step1_cfg = config["outputs"]["step1"][0]
    intermediate_files = {
        step1_cfg["cg_outlook"]: outlook[CG_ENTITY.name],
        step1_cfg["cbna_outlook"]: outlook[CBNA_ENTITY.name],
        step1_cfg["cg_addon_non_waterfall_rwa"]: addon[CG_ENTITY.name],
        step1_cfg["cbna_addon_non_waterfall_rwa"]: addon[CBNA_ENTITY.name],
    }
    intermediate_formats = ("xlsx", "parquet") if EXPORT_INTERMEDIATE_XLSX else ("parquet",)
    export_outputs(intermediate_files, model_convergence_dir, formats=intermediate_formats)

    # --- 1.13 Hand off to stage 2 in memory ---------------------------------
    # Reproduce the null semantics the old parquet round-trip applied (empty
    # string -> NaN), so the in-memory handoff is identical to re-reading parquet.
    return {
        "outlook": {name: normalize_nulls(df) for name, df in outlook.items()},
        "addon": {name: normalize_nulls(df) for name, df in addon.items()},
    }


# =============================================================================
# STAGE 2 — Outlook RWA
# =============================================================================

def run_outlook_rwa_stage(config, frames, registry, input_dir, model_convergence_dir, output_dir, Q0):
    """Join PUG/PMF mappings + adjustments + convergence onto the stage-1 frames
    and write the CG/CBNA upload templates, raw-data files and control file."""

    input_adjustments_filename     = "adjustment_master_file.xlsx"
    input_pug_filename             = "pug_mapping.xlsx"
    input_pmf_rwa_mapping_filename = "pmf_rwa_mapping.xlsx"

    step1_cfg = config["outputs"]["step1"][0]
    input_cg_outlook_filename   = step1_cfg["cg_outlook"]
    input_cbna_outlook_filename = step1_cfg["cbna_outlook"]
    input_cg_addon_filename     = step1_cfg["cg_addon_non_waterfall_rwa"]
    input_cbna_addon_filename   = step1_cfg["cbna_addon_non_waterfall_rwa"]

    output_cg_upload_full_filename   = "CG_Upload_Template_Full.xlsx"
    output_cbna_upload_full_filename = "CBNA_Upload_Template_Full.xlsx"
    output_cg_raw_data_filename      = "CG_RAW_DATA.xlsx"
    output_cbna_raw_data_filename    = "CBNA_RAW_DATA.xlsx"
    output_control_filename          = "control_file.xlsx"

    pug_file         = input_dir / input_pug_filename
    pmf_mapping_file = input_dir / input_pmf_rwa_mapping_filename

    upload_paths = {
        CG_ENTITY.name:   output_dir / output_cg_upload_full_filename,
        CBNA_ENTITY.name: output_dir / output_cbna_upload_full_filename,
    }
    raw_data_paths = {
        CG_ENTITY.name:   output_dir / output_cg_raw_data_filename,
        CBNA_ENTITY.name: output_dir / output_cbna_raw_data_filename,
    }
    output_control_file_path = output_dir / output_control_filename

    # --- 2.1 Read stage-2 inputs --------------------------------------------
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

    adjustments = {CG_ENTITY.name: src_cg_adjustments.copy(deep=True), CBNA_ENTITY.name: src_cbna_adjustments.copy(deep=True)}
    pug_df          = src_pug.copy(deep=True)
    rwa_pmf_mapping = src_rwa_pmf_mapping.copy(deep=True)
    convergence     = src_convergence.copy(deep=True)
    outlook = {name: df.copy(deep=True) for name, df in frames["outlook"].items()}
    addon   = {name: df.copy(deep=True) for name, df in frames["addon"].items()}

    for entity in ENTITIES:
        print(f"{entity.name} Adjustments rows: {len(adjustments[entity.name]):,}")
    print(f"Convergence rows: {len(convergence):,}")
    print(f"PUG Mapping rows: {len(pug_df):,}")

    # --- 2.2 PUG / PMF mapping data-quality checks --------------------------
    pug_dupes = src_pug.duplicated(subset=[MANAGED_SEGMENT_L4_DESCR], keep=False)
    if pug_dupes.sum() > 0:
        warnings.warn(f"PUG mapping has {pug_dupes.sum()} duplicates on Managed Segment L4 Descr")

    rwa_pmf_mapping = rwa_pmf_mapping.rename(columns={"PMF L5": PMF_ACCOUNT_L5_DESCR})

    pmf_dupes = rwa_pmf_mapping.duplicated(subset=[PMF_ACCOUNT_L5_DESCR], keep=False)
    if pmf_dupes.sum() > 0:
        warnings.warn(f"PMF RWA mapping has {pmf_dupes.sum()} duplicates on PMF L5")

    # --- 2.3 Per-entity: format, concat, raw data, joins, pivots ------------
    raw_data = {}
    frm_output = {}
    upload_full = {}

    for entity in ENTITIES:
        name = entity.name

        # Format adjustments + rename addon columns onto the outlook schema.
        adjustments_formatted = format_adjustments(adjustments[name].copy())
        addon_renamed = rename_addon_columns(addon[name], entity)

        # Concatenate adjustments + outlook + addon, drop unknown/NaN quarters.
        concat = pd.concat([adjustments_formatted, outlook[name], addon_renamed], ignore_index=True).copy()
        concat[QUARTER_ID] = pd.to_numeric(concat[QUARTER_ID], errors="coerce")
        concat = concat[concat[QUARTER_ID] != "Unknown"]
        concat = concat[concat[QUARTER_ID].notna()]
        print(f"{name} after filtering unknowns:   {len(concat)}")

        # Raw data (before further transforms), then legacy-franchises breakout.
        concat["Entity"] = entity.code
        raw_data[name] = concat.copy()
        df = legacy_franchises_breakout(concat)

        # Join PUG mapping.
        pre = len(df)
        df = df.merge(pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]], how="left", on=MANAGED_SEGMENT_L4_DESCR)
        if len(df) != pre:
            warnings.warn(f"{name}: PUG join caused row expansion: {pre} -> {len(df)}")
        unmatched = df[df["PUG"].isna()]
        pct = len(unmatched) / (len(df) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(f"{name}: (unmatched: {pct:.1f}%) rows ({len(unmatched):,}) have no PUG mapping match!")

        # Join PMF mapping.
        pre = len(df)
        df = df.merge(
            rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
            how="left", on=PMF_ACCOUNT_L5_DESCR,
        )
        if len(df) != pre:
            warnings.warn(f"{name}: PMF join caused row expansion: {pre} -> {len(df)}")
        unmatched = df[df[SA_ACCOUNT_NUM].isna() & df[PMF_ACCOUNT_L5_DESCR].notna()]
        pct = len(unmatched) / (len(df) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(f"{name}: PMF RWA mapping has {len(unmatched):,} ({pct:.1f}%) rows with no match!")

        # Format + upload-template pivots (ERBA / AA / SA) + inert markets filter.
        df = format_columns_before_pivots(df.copy())
        df = create_upload_template_pivots(df)
        df = create_markets_filter(df)
        frm_output[name] = df
        print(f"{name} pivot rows: {len(df):,}")

        upload_full[name] = format_upload_template(df)
        print(f"{name} Upload template rows: {len(upload_full[name]):,}")

    # --- 2.4 Export upload templates + raw data -----------------------------
    for entity in ENTITIES:
        upload_full[entity.name].to_excel(upload_paths[entity.name], index=False)
        raw_data[entity.name].to_excel(raw_data_paths[entity.name], index=False)
        print(f"Exported: {upload_paths[entity.name]}")
        print(f"Exported: {raw_data_paths[entity.name]}")

    # --- 2.5 Build controls -------------------------------------------------
    convergence_control = {}
    frm_control = {}
    raw_data_control = {}
    for entity in ENTITIES:
        convergence_control[entity.name] = build_convergence_control(convergence, entity.reportable_col, entity.adv_rwa_col)
        frm_control[entity.name] = build_frm_control(upload_full[entity.name])
        raw_data_control[entity.name] = build_raw_data_control(raw_data[entity.name])

    # --- 2.6 Parameters summary ---------------------------------------------
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

    # --- 2.7 Export control file --------------------------------------------
    with pd.ExcelWriter(output_control_file_path, engine="openpyxl") as writer:
        start_row = 0
        frm_control[CG_ENTITY.name].to_excel(writer, sheet_name="CG", startrow=start_row)
        start_row += len(frm_control[CG_ENTITY.name]) + 2
        convergence_control[CG_ENTITY.name].to_excel(writer, sheet_name="CG", startrow=start_row)

        start_row = 0
        frm_control[CBNA_ENTITY.name].to_excel(writer, sheet_name="CBNA", startrow=start_row)
        start_row += len(frm_control[CBNA_ENTITY.name]) + 2
        convergence_control[CBNA_ENTITY.name].to_excel(writer, sheet_name="CBNA", startrow=start_row)

        raw_data_control[CG_ENTITY.name].to_excel(writer, sheet_name="CG Raw Data Control", startrow=0)
        raw_data_control[CBNA_ENTITY.name].to_excel(writer, sheet_name="CBNA Raw Data Control", startrow=0)
        param_df.to_excel(writer, sheet_name="Parameters")

    print(f"Exported: {output_control_file_path}")

    # --- Summary ------------------------------------------------------------
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


# =============================================================================
# Entry point
# =============================================================================

def main():
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

    # Intermediate model-convergence artifacts go to step1; final deliverables
    # to step2. Both keys keep their existing names/semantics.
    model_convergence_dir = _resolve_output_dir(config["outputs"], "step1_dir")
    output_dir = _resolve_output_dir(config["outputs"], "step2_dir")

    print(f"Q0:                    {Q0}")
    print(f"Input dir:             {input_dir}")
    print(f"Model convergence dir: {model_convergence_dir}")
    print(f"Output dir:            {output_dir}")

    frames = run_model_convergence(config, schema_csv, input_dir, model_convergence_dir, Q0)
    run_outlook_rwa_stage(config, frames, registry, input_dir, model_convergence_dir, output_dir, Q0)


if __name__ == "__main__":
    main()
