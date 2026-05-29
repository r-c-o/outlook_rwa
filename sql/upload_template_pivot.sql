-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/upload_template_pivot.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- upload_template_pivot: quarter columns pivoted across, per RWA Calc type.
-- Maps to functions.create_upload_template_pivots: for each RWA Calc (ERBA/AA/SA)
-- and each (entity, segment_l2, pmf_l5), SUM the RWA into one column per quarter
-- via conditional aggregation (SUM(CASE WHEN quarter_id = N THEN value END)).
--
-- The quarter columns are injected by generate_sql.py (token: quarter_pivot_cols)
-- from the runtime quarter range (q0..qN). Quarter 0 is the "RWA Actuals" bucket;
-- quarters 1..N are projected periods. NULL RWA is treated as 0 (fill_value=0).
CREATE OR REPLACE TABLE upload_template_pivot AS
WITH long AS (
    SELECT entity, seg_l2_desc, pmf_l5, quarter_id, 'SA'   AS rwa_calc, COALESCE(sa_rwa, 0)   AS rwa FROM frm_base
    UNION ALL
    SELECT entity, seg_l2_desc, pmf_l5, quarter_id, 'AA'   AS rwa_calc, COALESCE(aa_rwa, 0)   AS rwa FROM frm_base
    UNION ALL
    SELECT entity, seg_l2_desc, pmf_l5, quarter_id, 'ERBA' AS rwa_calc, COALESCE(erba_rwa, 0) AS rwa FROM frm_base
)
SELECT
    entity, seg_l2_desc, pmf_l5, rwa_calc,
    SUM(CASE WHEN quarter_id = 0 THEN rwa END) AS "rwa_actuals",
    SUM(CASE WHEN quarter_id = 1 THEN rwa END) AS "q1",
    SUM(CASE WHEN quarter_id = 2 THEN rwa END) AS "q2",
    SUM(CASE WHEN quarter_id = 3 THEN rwa END) AS "q3",
    SUM(CASE WHEN quarter_id = 4 THEN rwa END) AS "q4",
    SUM(CASE WHEN quarter_id = 5 THEN rwa END) AS "q5",
    SUM(CASE WHEN quarter_id = 6 THEN rwa END) AS "q6",
    SUM(CASE WHEN quarter_id = 7 THEN rwa END) AS "q7"
FROM long
GROUP BY entity, seg_l2_desc, pmf_l5, rwa_calc;
