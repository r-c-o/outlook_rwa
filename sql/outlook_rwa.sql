-- GENERATED FILE — do not edit by hand.
-- Rendered from sql/templates/outlook_rwa.sql.j2 by scripts/generate_sql.py.
-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.
-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)

-- outlook_rwa: SA/AA RWA = balance * RWF, zeroed for non-credit-risk PMF accounts.
-- Maps to functions.calculate_sa_rwa / calculate_aa_rwa:
--   SA RWA = CASE WHEN pmf_l5 IN (non_credit_risk_pmf) THEN 0
--                 ELSE balances * FINAL_SA_RWF END
-- and the same for AA. ERBA RWA mirrors assign_erba_rwa_and_metadata:
--   ERBA RWA = CASE WHEN quarter_id IN (5, 6) THEN SA RWA ELSE NULL END.
--
-- pmf_l5 IN ('Commitments to Purchase Forward-Dated Securities (L2)', 'Commitments to Sell Forward-Dated Securities (L2)', 'Trading Account Assets (L2)', 'Trading Account Liabilities (L2)', 'Unsettled Trading Loans (L2)', 'Brokerage Receivables (L2)', 'Federal Funds Purch and Sec Loaned or Sold Under Repurchase Agreements (L2)', 'Federal Funds Sold and Resales (L2)', 'Securities Borrowed (L2)', 'Securities Lent (L2)', 'Other Liabilities (L2)', 'Indirect Assets (L2)', 'Premise and Equipment Net of Depreciation and Amortization (L2)', 'Other Assets L3') injected from constants.NON_CREDIT_RISK_PMF (SQLGlot).
-- quarter_id IN (5, 6) injected from the assign_erba_rwa_and_metadata quarters (5, 6).
CREATE OR REPLACE TABLE outlook_rwa AS
SELECT
    entity, year, month, quarter_id,
    seg_l4_desc, seg_l3_desc, seg_l2_desc, geo_l4_desc, geo_l3_desc, pmf_l5,
    seg_l4_id, seg_l3_id, seg_l2_id,
    balances, final_sa_rwf, final_aa_rwf,
    CASE WHEN pmf_l5 IN ('Commitments to Purchase Forward-Dated Securities (L2)', 'Commitments to Sell Forward-Dated Securities (L2)', 'Trading Account Assets (L2)', 'Trading Account Liabilities (L2)', 'Unsettled Trading Loans (L2)', 'Brokerage Receivables (L2)', 'Federal Funds Purch and Sec Loaned or Sold Under Repurchase Agreements (L2)', 'Federal Funds Sold and Resales (L2)', 'Securities Borrowed (L2)', 'Securities Lent (L2)', 'Other Liabilities (L2)', 'Indirect Assets (L2)', 'Premise and Equipment Net of Depreciation and Amortization (L2)', 'Other Assets L3') THEN 0
         ELSE balances * final_sa_rwf END AS sa_rwa,
    CASE WHEN pmf_l5 IN ('Commitments to Purchase Forward-Dated Securities (L2)', 'Commitments to Sell Forward-Dated Securities (L2)', 'Trading Account Assets (L2)', 'Trading Account Liabilities (L2)', 'Unsettled Trading Loans (L2)', 'Brokerage Receivables (L2)', 'Federal Funds Purch and Sec Loaned or Sold Under Repurchase Agreements (L2)', 'Federal Funds Sold and Resales (L2)', 'Securities Borrowed (L2)', 'Securities Lent (L2)', 'Other Liabilities (L2)', 'Indirect Assets (L2)', 'Premise and Equipment Net of Depreciation and Amortization (L2)', 'Other Assets L3') THEN 0
         ELSE balances * final_aa_rwf END AS aa_rwa,
    CASE WHEN quarter_id IN (5, 6)
         THEN CASE WHEN pmf_l5 IN ('Commitments to Purchase Forward-Dated Securities (L2)', 'Commitments to Sell Forward-Dated Securities (L2)', 'Trading Account Assets (L2)', 'Trading Account Liabilities (L2)', 'Unsettled Trading Loans (L2)', 'Brokerage Receivables (L2)', 'Federal Funds Purch and Sec Loaned or Sold Under Repurchase Agreements (L2)', 'Federal Funds Sold and Resales (L2)', 'Securities Borrowed (L2)', 'Securities Lent (L2)', 'Other Liabilities (L2)', 'Indirect Assets (L2)', 'Premise and Equipment Net of Depreciation and Amortization (L2)', 'Other Assets L3') THEN 0
                   ELSE balances * final_sa_rwf END
         ELSE NULL END AS erba_rwa
FROM outlook_with_rwf;
