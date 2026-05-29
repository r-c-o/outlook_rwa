-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/conv_non_waterfall.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- conv_non_waterfall: non-credit-risk, non-Markets rows (the residual bucket).
-- One row per entity where the entity flag is 'Y' AND the PMF account is NOT in
-- the credit-risk list AND the L2 segment is NOT Markets. Maps to
-- functions.split_convergence (non_credit_risk_non_waterfall_cg / _cbna).
--
-- "Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)') from constants.PMF_ACCOUNTS, "Managed Segment Level 2 Description" = 'Markets [L2]' from constants.MARKETS_L2.
CREATE OR REPLACE TABLE conv_non_waterfall AS
SELECT
    'CG' AS entity,
    "Quarter Id"                              AS quarter_id,
    "Managed Segment Level 2 Description"     AS seg_l2_desc,
    "Finance PMF Level 5 Description"         AS pmf_l5,
    "SA RWA Amount"                           AS sa_rwa_amt,
    "Adv. CG Total RWA Amount with 1.06 Multiplier"   AS adv_rwa_amt
FROM convergence
WHERE "Reportable Entity is CG" = 'Y'
  AND NOT ("Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)'))
  AND NOT ("Managed Segment Level 2 Description" = 'Markets [L2]')
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
  AND NOT ("Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)'))
  AND NOT ("Managed Segment Level 2 Description" = 'Markets [L2]');
