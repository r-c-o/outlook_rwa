-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/frm_base.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- frm_base: the long-format FRM input = outlook waterfall RWA UNION add-on RWA.
-- Maps to the pipeline §2.5 concat of outlook + add-on rows (the adjustments
-- master-file rows are layered in by the Python pipeline from a separate
-- multi-sheet workbook and are out of scope for the SQL numeric oracle; this
-- table covers the convergence-derived RWA, which is what the control totals and
-- upload template quantify).
--
-- One row per (entity, quarter_id, segment, pmf, rwa_calc-able amounts).
CREATE OR REPLACE TABLE frm_base AS
SELECT
    entity,
    quarter_id,
    seg_l4_desc,
    seg_l3_desc,
    seg_l2_desc,
    pmf_l5,
    sa_rwa,
    aa_rwa,
    erba_rwa,
    'outlook' AS source
FROM outlook_rwa
UNION ALL BY NAME
SELECT
    entity,
    quarter_id,
    CAST(NULL AS VARCHAR) AS seg_l4_desc,
    CAST(NULL AS VARCHAR) AS seg_l3_desc,
    seg_l2_desc,
    pmf_l5,
    sa_rwa_amt AS sa_rwa,
    aa_rwa_amt AS aa_rwa,
    erba_rwa,
    'addon' AS source
FROM addon_pivot;
