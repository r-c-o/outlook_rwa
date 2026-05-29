-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/outlook_with_keys.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- outlook_with_keys: attach Quarter Id and the Key1 waterfall string.
-- Maps to functions.assign_quarter_id + build_outlook_key_strings (Key1).
--
-- Quarter Id comes from quarter_map (year, month_abbr) -> quarter_id, a base
-- table the loader builds from Q0 (configuration, not transforms.py). Rows whose
-- (year, month) is not in quarter_map get a NULL quarter_id (the Python pipeline
-- later drops these "Unknown" rows).
--
-- Key1 reproduces the Python concatenation exactly: CAST(seg_l4_id AS INT)::TEXT
-- (int_str=true) || geo_l4_desc || pmf_l5 || CAST(quarter_id AS TEXT). The
-- waterfall pivot_only field (seg_l2) is excluded from the string, matching
-- build_outlook_key_strings.
CREATE OR REPLACE TABLE outlook_with_keys AS
SELECT
    o.*,
    qm.quarter_id AS quarter_id,
    CASE WHEN qm.quarter_id IS NULL THEN NULL ELSE
        CAST(CAST(o.seg_l4_id AS BIGINT) AS VARCHAR)
        || CAST(o.geo_l4_desc AS VARCHAR)
        || CAST(o.pmf_l5 AS VARCHAR)
        || CAST(qm.quarter_id AS VARCHAR)
    END AS key1
FROM outlook_long o
LEFT JOIN quarter_map qm
       ON o.year = qm.year
      AND o.month = qm.month_abbr;
