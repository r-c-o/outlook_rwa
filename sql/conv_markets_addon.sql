-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/conv_markets_addon.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- conv_markets_addon: Markets [L2] add-on rows (credit-risk treatment bypassed).
-- One row per entity where the entity flag is 'Y' AND the L2 segment is the
-- Markets segment. Maps to functions.split_convergence (cg/cbna_addon_markets_credit_risk).
--
-- "Managed Segment Level 2 Description" = 'Markets [L2]' is injected from constants.MARKETS_L2 via SQLGlot.
CREATE OR REPLACE TABLE conv_markets_addon AS
SELECT
    'CG' AS entity,
    "Quarter Id"                              AS quarter_id,
    "Managed Segment Level 2 Description"     AS seg_l2_desc,
    "Finance PMF Level 5 Description"         AS pmf_l5,
    "SA RWA Amount"                           AS sa_rwa_amt,
    "Adv. CG Total RWA Amount with 1.06 Multiplier"   AS adv_rwa_amt
FROM convergence
WHERE "Reportable Entity is CG" = 'Y'
  AND "Managed Segment Level 2 Description" = 'Markets [L2]'
UNION ALL
SELECT
    'CBNA' AS entity,
    "Quarter Id"                              AS quarter_id,
    "Managed Segment Level 2 Description"     AS seg_l2_desc,
    "Finance PMF Level 5 Description"         AS pmf_l5,
    "SA RWA Amount"                           AS sa_rwa_amt,
    "Adv. CBNA Total RWA Amount with 1.06 Multiplier" AS adv_rwa_amt
FROM convergence
WHERE "Reportable Entity is CBNA" = 'Y'
  AND "Managed Segment Level 2 Description" = 'Markets [L2]';
