-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/conv_credit_risk.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- conv_credit_risk: convergence rows that feed the waterfall RWF.
-- One row per entity (CG/CBNA) where the entity flag is 'Y' AND the Finance PMF
-- Level 5 account is in the credit-risk PMF list. Maps to functions.split_convergence
-- (credit_risk_convergence_cg / _cbna).
--
-- "Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)') is injected by scripts/generate_sql.py from constants.PMF_ACCOUNTS
-- (via SQLGlot, so account names are safely quoted).
CREATE OR REPLACE TABLE conv_credit_risk AS
SELECT
    'CG' AS entity,
    "Quarter Id"                              AS quarter_id,
    "Managed Segment Level 4 Code"            AS seg_l4_code,
    "Managed Segment Level 3 Code"            AS seg_l3_code,
    "Managed Segment Level 2 Code"            AS seg_l2_code,
    "Managed Geography Level 4 Description"   AS geo_l4_desc,
    "Managed Geography Level 3 Description"   AS geo_l3_desc,
    "Finance PMF Level 5 Description"         AS pmf_l5,
    "Managed Segment Level 2 Description"     AS seg_l2_desc,
    "GAAP Amount"                             AS gaap_amt,
    "SA RWA Amount"                           AS sa_rwa_amt,
    "Adv. CG Total RWA Amount with 1.06 Multiplier"   AS adv_rwa_amt
FROM convergence
WHERE "Reportable Entity is CG" = 'Y'
  AND "Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)')
UNION ALL
SELECT
    'CBNA' AS entity,
    "Quarter Id"                              AS quarter_id,
    "Managed Segment Level 4 Code"            AS seg_l4_code,
    "Managed Segment Level 3 Code"            AS seg_l3_code,
    "Managed Segment Level 2 Code"            AS seg_l2_code,
    "Managed Geography Level 4 Description"   AS geo_l4_desc,
    "Managed Geography Level 3 Description"   AS geo_l3_desc,
    "Finance PMF Level 5 Description"         AS pmf_l5,
    "Managed Segment Level 2 Description"     AS seg_l2_desc,
    "GAAP Amount"                             AS gaap_amt,
    "SA RWA Amount"                           AS sa_rwa_amt,
    "Adv. CBNA Total RWA Amount with 1.06 Multiplier" AS adv_rwa_amt
FROM convergence
WHERE "Reportable Entity is CBNA" = 'Y'
  AND "Finance PMF Level 5 Description" IN ('Deposits with Banks (L2)', 'Investments (L2)', 'Letters of Credit (L2)', 'Other Assets (L2)', 'Total Loans & Leases Net of Unearned (L2)', 'Unused Commitments (L2)');
