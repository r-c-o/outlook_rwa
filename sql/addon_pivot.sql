-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/addon_pivot.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- addon_pivot: aggregate the Markets and non-waterfall add-on buckets.
-- Maps to functions.build_markets_addon_pivot + build_addon_pivot: collapse to one
-- row per (entity, quarter_id, seg_l2_desc, pmf_l5) summing SA RWA and Adv RWA.
-- ERBA RWA = SA RWA Amount in quarters quarter_id IN (5, 6) (matches the add-on
-- ERBA derivation in pipeline §1.11 / build_addon_pivot), else NULL.
--
-- quarter_id IN (5, 6) injected from the ERBA quarters (5, 6).
CREATE OR REPLACE TABLE addon_pivot AS
WITH unioned AS (
    SELECT entity, quarter_id, seg_l2_desc, pmf_l5, sa_rwa_amt, adv_rwa_amt
    FROM conv_markets_addon
    UNION ALL
    SELECT entity, quarter_id, seg_l2_desc, pmf_l5, sa_rwa_amt, adv_rwa_amt
    FROM conv_non_waterfall
),
grouped AS (
    SELECT
        entity, quarter_id, seg_l2_desc, pmf_l5,
        SUM(sa_rwa_amt)  AS sa_rwa_amt,
        SUM(adv_rwa_amt) AS aa_rwa_amt
    FROM unioned
    GROUP BY entity, quarter_id, seg_l2_desc, pmf_l5
)
SELECT
    entity, quarter_id, seg_l2_desc, pmf_l5,
    sa_rwa_amt, aa_rwa_amt,
    CASE WHEN quarter_id IN (5, 6) THEN sa_rwa_amt ELSE NULL END AS erba_rwa
FROM grouped;
