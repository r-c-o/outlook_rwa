"""
Outlook RWA — combined pipeline (model convergence + outlook RWA).

Single end-to-end run that (1) builds the 5-key RWF waterfall and computes
SA/AA/ERBA RWA, then (2) joins PUG/PMF mappings + adjustments + convergence and
writes the CG/CBNA upload templates, raw-data files and control file.

The two stages share memory: the model-convergence outputs (cg_outlook,
cbna_outlook and the two addon frames) are passed in-process to the outlook-RWA
stage instead of being re-read from disk. Their parquet artifacts are still
written for inspection; the bulky xlsx copies are written only when
export_intermediate_xlsx is True.

Prerequisite: schema_registry.csv must exist (run create_schema_csv.py first).
"""
import sys
import warnings
from pathlib import Path

import pandas as pd

# Allow running as a stand-alone script (python src/outlook_rwa/pipeline.py) in
# addition to module / installed-entry-point execution. When run as a script
# __package__ is empty and relative imports fail, so put src/ on the path and
# use absolute imports (which resolve in both modes).
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pylint: disable=wrong-import-position
# Imports must follow the sys.path manipulation above so the script-mode runner
# can resolve the outlook_rwa package without an editable install.
from outlook_rwa.functions import (
    _int_str,
    assign_quarter_id,
    assign_year_month_from_quarter,
    calculate_sa_rwa,
    calculate_aa_rwa,
    assign_erba_rwa,
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
    concat_addon,
)
from outlook_rwa.models import EntityBundle
from outlook_rwa.transforms import (
    UPLOAD_ACTUALS_LABEL,
    quarter_label,
)
from outlook_rwa.dq import run_all_checks, export_dq_results
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
    ERBA_RWA,
    FINANCE_PMF_LEVEL_5_DESC,
    QRTR_ID,
    PMF_ACCOUNTS,
    MARKETS_L2,
    SA_RWA_AMT,
    PROJECTED_QUARTER_TO_MONTH,
    MANAGED_GEO_L3_DESC,
    MANAGED_GEO_L4_DESC,
    MANAGED_SEGMENT_L2_DESCR,
    MANAGED_SEGMENT_L3_DESCR,
    MANAGED_SEGMENT_L4_DESCR,
    REPORTING_LAYER,
    MNGD_GEO_L3_DESC,
    MNGD_GEO_L4_DESC,
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
# This flag is now configured in config/config.yaml under `parameters`.


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


def _build_rwa_long(entities, quarter_labels):
    """Build a tidy long-format RWA table across all entities.

    One row per (Entity, Reporting Layer, Segment L2/L3/L4, PMF L5, RWA Calc,
    Period, RWA Amount). Period is the descriptive quarter label; the actuals
    bucket (quarter id 0) becomes "RWA Actuals".

    Args:
        entities: List of EntityBundle with populated frm_output frames.
        quarter_labels: Dict mapping integer quarter id -> descriptive label.

    Returns:
        Long-format DataFrame suitable for Tableau / SQL consumption.
    """
    dim_cols = [
        "Entity",
        REPORTING_LAYER,
        MANAGED_SEGMENT_L2_DESCR,
        MANAGED_SEGMENT_L3_DESCR,
        MANAGED_SEGMENT_L4_DESCR,
        PMF_ACCOUNT_L5_DESCR,
    ]
    rwa_cols = {"SA RWA": "SA", "AA RWA": "AA", "ERBA RWA": "ERBA"}
    frames = []
    for entity in entities:
        df = entity.frm_output.copy()
        df["Entity"] = entity.raw_entity_code
        qid = pd.to_numeric(df[QUARTER_ID], errors="coerce").astype("Int64")
        df["Period"] = qid.map(quarter_labels)
        present = [c for c in dim_cols if c in df.columns]
        value_cols = [c for c in rwa_cols if c in df.columns]
        tidy = df.melt(
            id_vars=present + ["Period"],
            value_vars=value_cols,
            var_name="RWA Calc",
            value_name="RWA Amount",
        )
        tidy["RWA Calc"] = tidy["RWA Calc"].map(rwa_cols)
        frames.append(tidy)
    return pd.concat(frames, ignore_index=True)


# main() is the top-level pipeline orchestrator: each statement is a sequential
# stage (config load, schema resolution, stage 1 model convergence, stage 2 RWA
# rollups, control-file emission). Splitting it into helpers would only push the
# same orchestration into a thin caller without reducing the statement count
# anywhere it would help readability.
def main():  # pylint: disable=too-many-statements
    """Run the combined Outlook RWA pipeline end-to-end.

    Executes Stage 1 (model convergence: waterfall RWF lookup and SA/AA/ERBA RWA
    calculation) followed by Stage 2 (outlook RWA: PUG/PMF joins, legacy breakout,
    upload template pivots, and control file export). Reads configuration from
    config/config.yaml (with optional config.local.yaml overrides). Output paths
    are resolved from config['outputs'] with fallback keys for alternate locations.

    Raises:
        FileNotFoundError: If a required input file or output root directory does
            not exist and no fallback path is configured.
    """
    # =============================================================================
    # Setup — config, paths, schema registry
    # =============================================================================

    config = load_config(Path(__file__).resolve().parents[2] / "config")

    Q0 = config["parameters"]["Q0"]
    export_intermediate_xlsx = config.get("parameters", {}).get("export_intermediate_xlsx", False)
    key_defs = config["parameters"]["waterfall_keys"]
    n_keys = len(key_defs)

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

    convergence = src_convergence.copy(deep=True)

    # One EntityBundle per reportable entity. The pipeline iterates this list
    # instead of carrying ~20 paired cg_*/cbna_* variables. raw_entity_code is
    # the value written to the stage-2 "Entity" column ("BA"=CG, "BB"=CBNA).
    entities = [
        EntityBundle(
            name="CG",
            adv_rwa_col=ADV_CG_TOTAL_RWA_AMT,
            entity_filter_col=REPORTABLE_ENTITY_IS_CG,
            raw_entity_code="BA",
            balance_sheet=src_cg.copy(deep=True),
            adjustments=src_cg_adjustments.copy(deep=True),
        ),
        EntityBundle(
            name="CBNA",
            adv_rwa_col=ADV_CBNA_TOTAL_RWA_AMT,
            entity_filter_col=REPORTABLE_ENTITY_IS_CBNA,
            raw_entity_code="BB",
            balance_sheet=src_cbna.copy(deep=True),
            adjustments=src_cbna_adjustments.copy(deep=True),
        ),
    ]

    print(f"✅ Convergence rows: {len(convergence):,}")
    for entity in entities:
        print(f"✅ {entity.name} rows:          {len(entity.balance_sheet):,}")

    # --- 1.2 Merge Geography Level 3 into convergence ---------------------------
    # The geo L3->L4 lookup is entity-agnostic; CG's balance sheet supplies it.
    cg_balance_sheet = entities[0].balance_sheet
    dummy_df = cg_balance_sheet[[MANAGED_GEO_L3_DESC, MANAGED_GEO_L4_DESC]].drop_duplicates()
    dummy_df = dummy_df.rename(columns={
        MANAGED_GEO_L3_DESC: MNGD_GEO_L3_DESC,
        MANAGED_GEO_L4_DESC: MNGD_GEO_L4_DESC,
    })
    dummy_df = dummy_df.drop_duplicates(subset=MNGD_GEO_L4_DESC, keep="first")

    if MNGD_GEO_L3_DESC not in convergence.columns:
        convergence = convergence.merge(
            dummy_df[[MNGD_GEO_L3_DESC, MNGD_GEO_L4_DESC]],
            on=MNGD_GEO_L4_DESC,
            how="left",
        )

    # --- 1.3 Normalise PMF Account / Finance PMF column types -------------------
    for entity in entities:
        entity.balance_sheet[PMF_ACCOUNT_L5_DESCR] = (
            entity.balance_sheet[PMF_ACCOUNT_L5_DESCR].astype(str)
        )
    convergence[FINANCE_PMF_LEVEL_5_DESC] = (
        convergence[FINANCE_PMF_LEVEL_5_DESC].astype(str)
    )

    # --- 1.4 Split convergence into credit-risk buckets -------------------------
    # split_convergence stays entity-agnostic: it returns the CG and CBNA slices
    # interleaved, which we distribute onto the entity bundles in list order.
    (
        credit_risk_cg, credit_risk_cbna,
        non_waterfall_cg, non_waterfall_cbna,
        markets_cg, markets_cbna,
    ) = split_convergence(convergence, PMF_ACCOUNTS, MARKETS_L2)
    entities[0].credit_risk = credit_risk_cg
    entities[1].credit_risk = credit_risk_cbna
    entities[0].addon_non_waterfall = non_waterfall_cg
    entities[1].addon_non_waterfall = non_waterfall_cbna
    entities[0].addon_markets = markets_cg
    entities[1].addon_markets = markets_cbna

    for entity in entities:
        print(f"{entity.name} credit-risk rows:   {len(entity.credit_risk):,}")

    # --- 1.5 Build key pivot tables and compute RWFs ----------------------------
    for entity in entities:
        entity.waterfall_lookups = create_key_pivots(
            entity.credit_risk, entity.adv_rwa_col, key_defs,
        )
        for key_df in entity.waterfall_lookups:
            compute_rwf(key_df, entity.adv_rwa_col)
            set_markets_rwf(key_df)

    # --- 1.6 Reshape balance sheet: pivot then melt to long ---------------------
    for entity in entities:
        rename_month_columns(entity.balance_sheet)
        pivot = create_quarterly_pivot(entity.balance_sheet)
        entity.outlook = melt_quarterly_pivot(pivot)
        print(f"{entity.name} outlook long rows:   {len(entity.outlook):,}")

    # --- 1.7 Quarter mapping + quarter IDs --------------------------------------
    max_quarters = check_and_get_max_quarters(
        convergence, entities[0].outlook, entities[1].outlook,
    )
    quarter_map, quarter_id_mapping = build_quarter_mappings(Q0, max_quarters)

    for entity in entities:
        assign_quarter_id(entity.outlook, quarter_id_mapping)

    # --- 1.8 Build waterfall key strings and apply RWF lookups ------------------
    for entity in entities:
        build_outlook_key_strings(entity.outlook, key_defs)
        entity.outlook = _apply_waterfall_lookups(
            entity.outlook, entity.waterfall_lookups, key_defs,
        )

    # --- 1.9 Apply adjustments --------------------------------------------------
    key_cols = [f"Key{i + 1}" for i in range(n_keys)]
    rwf_cols = ["SA RWF", "AA RWF"]
    for i in range(1, n_keys):
        rwf_cols += [f"SA RWF_key{i + 1}", f"AA RWF_key{i + 1}"]
    base_adj_cols = ["Key1", "SA RWA", "AA RWA", "ERBA RWA", "Comment", "RWA Exposure Type"]
    all_adj_cols = list(dict.fromkeys(base_adj_cols + key_cols[1:] + rwf_cols))

    key1_def = key_defs[0]
    for entity in entities:
        adj_df = entity.adjustments
        parts = []
        for f in key1_def["fields"]:
            if f.get("pivot_only"):
                continue
            col = adj_df[f["outlook_col"]]
            parts.append(_int_str(col) if f.get("int_str") else col.astype(str))
        parts.append(adj_df[QUARTER_ID].astype(str))
        adj_df["Key1"] = parts[0]
        for part in parts[1:]:
            adj_df["Key1"] = adj_df["Key1"] + part

        adj_cols = [c for c in all_adj_cols if c in adj_df.columns]
        entity.outlook = pd.merge(
            entity.outlook,
            adj_df[adj_cols],
            on="Key1",
            how="left",
            suffixes=("", "_adj"),
        )

    # --- 1.10 Calculate RWA -----------------------------------------------------
    for entity in entities:
        calculate_sa_rwa(entity.outlook)
        calculate_aa_rwa(entity.outlook)
        assign_erba_rwa(entity.outlook)

    # --- 1.11 Addon: markets / non-waterfall ------------------------------------
    # Quarter Id is a pivot key, so derive it (via YEAR / Month) on the raw rows
    # first; both add-on buckets are then aggregated to the addon-pivot grain so
    # the export carries one summarized row per index group instead of one row
    # per raw convergence record.
    for entity in entities:
        for addon_df in (entity.addon_markets, entity.addon_non_waterfall):
            q_num = pd.to_numeric(
                addon_df["Projected Quarter"].str[0], errors="coerce",
            ).astype("Int64")
            addon_df["YEAR"] = pd.to_numeric(
                addon_df["Projected Quarter"].str[2:].apply(
                    lambda x: "20" + x if pd.notna(x) else x
                ),
                errors="coerce",
            ).astype("Int64")
            addon_df["Month"] = q_num.map(PROJECTED_QUARTER_TO_MONTH)
            assign_quarter_id(addon_df, quarter_id_mapping)

        # Markets credit-risk: fill null PMF keys, pivot, then add ERBA RWA /
        # Comment from the aggregated totals (mirrors production's §11 handling;
        # build_addon_pivot does the equivalent internally for the non-waterfall
        # bucket).
        entity.addon_markets[FINANCE_PMF_LEVEL_5_DESC] = (
            entity.addon_markets[FINANCE_PMF_LEVEL_5_DESC].fillna(0)
        )
        entity.addon_markets = build_markets_addon_pivot(
            entity.addon_markets, entity.adv_rwa_col, ADDON_PIVOT_INDEX,
        )
        entity.addon_markets[ERBA_RWA] = entity.addon_markets[SA_RWA_AMT].where(
            entity.addon_markets[QRTR_ID].isin(["5", "6"]))
        entity.addon_markets["Comment"] = ""

        entity.addon_non_waterfall = build_addon_pivot(
            entity.addon_non_waterfall, entity.adv_rwa_col, ADDON_PIVOT_INDEX,
        )

        # Re-derive YEAR / Month (dropped by the pivot) from the surviving
        # Quarter Id.
        assign_year_month_from_quarter(
            {
                f"{entity.name}_addon_markets_credit_risk": entity.addon_markets,
                f"non_credit_risk_non_waterfall_{entity.name}": entity.addon_non_waterfall,
            },
            quarter_map=quarter_map,
        )

        entity.addon_all = concat_addon(entity.addon_markets, entity.addon_non_waterfall)


    for entity in entities:
        print(f"{entity.name} addon non-waterfall rows:   {len(entity.addon_all):,}")

    # --- 1.12 Export intermediate artifacts (parquet always; xlsx if debug) -----
    cg, cbna = entities
    intermediate_files = {
        config["outputs"]["step1"][0]["cg_outlook"]: cg.outlook,
        config["outputs"]["step1"][0]["cbna_outlook"]: cbna.outlook,
        config["outputs"]["step1"][0]["cg_addon_non_waterfall_rwa"]: cg.addon_all,
        config["outputs"]["step1"][0]["cbna_addon_non_waterfall_rwa"]: cbna.addon_all,
    }
    intermediate_formats = (
        ("xlsx", "parquet") if export_intermediate_xlsx else ("parquet",)
    )
    export_outputs(intermediate_files, model_convergence_dir, formats=intermediate_formats)

    # --- 1.13 Hand off to stage 2 in memory -------------------------------------
    # Reproduce the null semantics the old parquet round-trip applied (empty string
    # -> NaN), so the in-memory handoff is identical to re-reading from disk.
    src_outlook = {e.name: normalize_nulls(e.outlook) for e in entities}
    src_addon_all = {e.name: normalize_nulls(e.addon_all) for e in entities}

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
        (
            ExcelInputSpec("pug", pug_file, "pug", "pug_mapping.parquet"),
            input_dir,
        ),
        (
            ExcelInputSpec(
                "pmf_rwa_mapping", pmf_mapping_file, "pmf",
                "pmf_rwa_mapping.parquet", "Sheet1",
            ),
            input_dir,
        ),
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

    pug_df           = src_pug.copy(deep=True)
    rwa_pmf_mapping  = src_rwa_pmf_mapping.copy(deep=True)
    convergence      = src_convergence.copy(deep=True)

    # Re-seat the stage-2 inputs onto the entity bundles: disk-loaded adjustments
    # plus the in-memory outlook / addon frames handed off from stage 1.
    stage2_adjustments = {
        "CG": src_cg_adjustments.copy(deep=True),
        "CBNA": src_cbna_adjustments.copy(deep=True),
    }
    for entity in entities:
        entity.adjustments = stage2_adjustments[entity.name]
        entity.outlook = src_outlook[entity.name].copy(deep=True)
        entity.addon_all = src_addon_all[entity.name].copy(deep=True)
        print(f"{entity.name} Adjustments rows:   {len(entity.adjustments):,}")
    print(f"Convergence rows:      {len(convergence):,}")
    print(f"PUG Mapping rows:      {len(pug_df):,}")

    # --- 2.2 Format adjustments / 2.3 Rename addon columns to outlook schema -----
    for entity in entities:
        entity.adjustments = format_adjustments(entity.adjustments.copy())
        entity.addon_all = rename_addon_columns(entity.addon_all, entity.name)

    # --- 2.4 PUG / PMF mapping data-quality checks ------------------------------
    pug_dupes = src_pug.duplicated(subset=[MANAGED_SEGMENT_L4_DESCR], keep=False)
    if pug_dupes.sum() > 0:
        warnings.warn(f"PUG mapping has {pug_dupes.sum()} duplicates on Managed Segment L4 Descr")

    rwa_pmf_mapping = rwa_pmf_mapping.rename(columns={"PMF L5": PMF_ACCOUNT_L5_DESCR})

    pmf_dupes = rwa_pmf_mapping.duplicated(subset=[PMF_ACCOUNT_L5_DESCR], keep=False)
    if pmf_dupes.sum() > 0:
        warnings.warn(f"PMF RWA mapping has {pmf_dupes.sum()} duplicates on PMF L5")

    # Descriptive upload-template quarter headers, derived from the quarter_map:
    #   id 0   -> "RWA Actuals"  (the actuals bucket)
    #   id > 0 -> "Mon YYYY"     (e.g. "Jun 2025")
    quarter_labels = {
        qid: (UPLOAD_ACTUALS_LABEL if qid == 0 else quarter_label(year, month))
        for qid, (year, month) in quarter_map.items()
    }
    quarter_ids = sorted(quarter_map.keys())
    # Value columns build_frm_control sums: actuals first, then projected quarters.
    frm_control_value_cols = [UPLOAD_ACTUALS_LABEL] + [
        quarter_labels[q] for q in quarter_ids if q != 0
    ]

    # --- 2.5–2.13 Per-entity stage-2 transforms ---------------------------------
    for entity in entities:
        # 2.5 Concatenate adjustments + outlook + addon
        concat = pd.concat([
            entity.adjustments,
            entity.outlook,
            entity.addon_all,
        ], ignore_index=True).copy()

        concat[QUARTER_ID] = pd.to_numeric(concat[QUARTER_ID], errors='coerce')
        concat = concat[concat[QUARTER_ID] != 'Unknown']
        concat = concat[concat[QUARTER_ID].notna()]
        print(f"{entity.name} after filtering unknowns:   {len(concat)}")

        # 2.6 Save raw data (before further transforms)
        concat["Entity"] = entity.raw_entity_code
        entity.raw_data = concat.copy()

        # 2.7 Legacy franchises breakout / 2.8 FRM output
        entity.frm_output = legacy_franchises_breakout(concat)

        # 2.9 Join PUG mapping
        pre = len(entity.frm_output)
        entity.frm_output = entity.frm_output.merge(
            pug_df[[MANAGED_SEGMENT_L4_DESCR, "PUG"]], how="left",
            on=MANAGED_SEGMENT_L4_DESCR,
        )
        if len(entity.frm_output) != pre:
            warnings.warn(
                f"{entity.name}: PUG join caused row expansion: "
                f"{pre} -> {len(entity.frm_output)}"
            )
        unmatched = entity.frm_output[entity.frm_output["PUG"].isna()]
        pct = len(unmatched) / (len(entity.frm_output) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(
                f"{entity.name}: (unmatched: {pct:.1f}%) rows "
                f"({len(unmatched):,}) have no PUG mapping match!"
            )

        # 2.10 Join PMF mapping
        pre = len(entity.frm_output)
        entity.frm_output = entity.frm_output.merge(
            rwa_pmf_mapping[[PMF_ACCOUNT_L5_DESCR, SA_ACCOUNT_NUM, AA_ACCOUNT_NUM]],
            how="left", on=PMF_ACCOUNT_L5_DESCR,
        )
        if len(entity.frm_output) != pre:
            warnings.warn(
                f"{entity.name}: PMF join caused row expansion: "
                f"{pre} -> {len(entity.frm_output)}"
            )
        unmatched = entity.frm_output[
            entity.frm_output[SA_ACCOUNT_NUM].isna()
            & entity.frm_output[PMF_ACCOUNT_L5_DESCR].notna()
        ]
        pct = len(unmatched) / (len(entity.frm_output) + 1) * 100
        if len(unmatched) > 0:
            warnings.warn(
                f"{entity.name}: PMF RWA mapping has {len(unmatched):,} "
                f"({pct:.1f}%) rows with no match!"
            )

    cg, cbna = entities

    # --- 2.10b DQ checks (cross-entity; before format_columns_before_pivots so
    # join-miss nulls are still real NaN) ---------------------------------------
    dq_results = run_all_checks(
        convergence=convergence,
        cg_outlook=cg.outlook,
        cbna_outlook=cbna.outlook,
        cg_adjustments=cg.adjustments,
        cbna_adjustments=cbna.adjustments,
        frm_output_cg=cg.frm_output,
        frm_output_cbna=cbna.frm_output,
        n_keys=n_keys,
    )
    dq_parquet_path, dq_xlsx_path = export_dq_results(dq_results, output_dir)
    print(f"Exported: {dq_xlsx_path}")
    print(f"Exported: {dq_parquet_path}")
    n_fail = (dq_results["status"] == "FAIL").sum()
    n_warn = (dq_results["status"] == "WARN").sum()
    n_pass = len(dq_results) - n_fail - n_warn
    print(
        f"DQ summary: {len(dq_results)} checks — "
        f"{n_fail} FAIL, {n_warn} WARN, {n_pass} PASS"
    )

    # --- 2.11–2.13 Format columns, pivot, format upload template, export --------
    upload_paths = {
        "CG": (output_cg_upload_full_filename_path, output_cg_raw_data_filename_path),
        "CBNA": (output_cbna_upload_full_filename_path, output_cbna_raw_data_filename_path),
    }
    for entity in entities:
        # 2.11 Format columns before pivots
        entity.frm_output = format_columns_before_pivots(entity.frm_output.copy())

        # 2.12 Upload template pivots (ERBA / AA / SA) + markets filter (inert,
        # applied after pivots to mirror production ordering).
        pivoted = create_upload_template_pivots(entity.frm_output, quarter_ids)
        pivoted = create_markets_filter(pivoted)
        print(f"{entity.name} pivot rows:   {len(pivoted):,}")

        # 2.13 Format upload template
        entity.upload_template = format_upload_template(pivoted, quarter_labels)
        print(f"{entity.name} Upload template rows:   {len(entity.upload_template):,}")

        upload_path, raw_path = upload_paths[entity.name]
        entity.upload_template.to_excel(upload_path, index=False)
        entity.raw_data.to_excel(raw_path, index=False)
        print(f"Exported: {upload_path}")
        print(f"Exported: {raw_path}")

    # --- 2.14 Build controls ----------------------------------------------------
    cg_convergence_control = build_convergence_control(
        convergence, REPORTABLE_ENTITY_IS_CG, ADV_CG_TOTAL_RWA_AMT,
    )
    cbna_convergence_control = build_convergence_control(
        convergence, REPORTABLE_ENTITY_IS_CBNA, ADV_CBNA_TOTAL_RWA_AMT,
    )

    cg_frm_control   = build_frm_control(cg.upload_template, frm_control_value_cols)
    cbna_frm_control = build_frm_control(cbna.upload_template, frm_control_value_cols)

    cg_raw_data_control   = build_raw_data_control(cg.raw_data)
    cbna_raw_data_control = build_raw_data_control(cbna.raw_data)

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

    # --- 2.17 Tidy long-format export (Tableau-friendly) ------------------------
    # One row per (Entity, Reporting Layer, Segment L2/L3/L4, PMF L5, RWA Calc,
    # Period, RWA Amount). Additive only — does not touch the upload templates.
    rwa_long = _build_rwa_long(entities, quarter_labels)
    rwa_long_path = output_dir / "rwa_long.parquet"
    rwa_long.to_parquet(rwa_long_path, index=False)
    print(f"Exported: {rwa_long_path}")

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
