-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/outlook_long.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- outlook_long: balance sheet pivoted-then-melted to long format.
-- Maps to functions.create_quarterly_pivot + melt_quarterly_pivot:
--   * SUM the period balances across the dimensional grain (one row per
--     YEAR x seg_l4/l3/l2 desc x geo_l4/l3 x pmf_l5 x seg ids).
--   * UNPIVOT the period columns into (Month, Balances) rows.
--
-- The UNPIVOT (Month, source-column) pairs are injected by generate_sql.py from
-- transforms.QUARTERLY_PERIODS (agg="last" => one column per quarter-end snapshot;
-- balances are NOT summed across months). The unpivot pairs expand to:
--   ('Mar', SUM(M3_USDOLLAR)), ('Jun', SUM(M6_USDOLLAR)), ...
CREATE OR REPLACE TABLE outlook_long AS
WITH bs AS (
    SELECT 'CG' AS entity, * FROM balance_sheet_cg
    UNION ALL BY NAME
    SELECT 'CBNA' AS entity, * FROM balance_sheet_cbna
),
pivoted AS (
    SELECT
        entity,
        "YEAR"                       AS year,
        "Managed Segment L4 Descr"   AS seg_l4_desc,
        "Managed Segment L3 Descr"   AS seg_l3_desc,
        "Managed Segment L2 Descr"   AS seg_l2_desc,
        "Managed Geography L4 Descr" AS geo_l4_desc,
        "Managed Geography L3 Descr" AS geo_l3_desc,
        "PMF Account L5 Descr"       AS pmf_l5,
        "Managed Segment L4 Id"      AS seg_l4_id,
        "Managed Segment L3 Id"      AS seg_l3_id,
        "Managed Segment L2 Id"      AS seg_l2_id,
        SUM(M3_USDOLLAR) AS "Mar",
        SUM(M6_USDOLLAR) AS "Jun",
        SUM(M9_USDOLLAR) AS "Sep",
        SUM(M12_USDOLLAR) AS "Dec"
    FROM bs
    GROUP BY entity, "YEAR",
        "Managed Segment L4 Descr", "Managed Segment L3 Descr", "Managed Segment L2 Descr",
        "Managed Geography L4 Descr", "Managed Geography L3 Descr", "PMF Account L5 Descr",
        "Managed Segment L4 Id", "Managed Segment L3 Id", "Managed Segment L2 Id"
)
SELECT
    entity, year, seg_l4_desc, seg_l3_desc, seg_l2_desc,
    geo_l4_desc, geo_l3_desc, pmf_l5, seg_l4_id, seg_l3_id, seg_l2_id,
    m.month AS month,
    m.balances AS balances
FROM pivoted
CROSS JOIN LATERAL (VALUES ('Mar', Mar), ('Jun', Jun), ('Sep', Sep), ('Dec', Dec)) AS m(month, balances);
