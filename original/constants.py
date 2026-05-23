import polars as pl

# ---------------------------------------------------------------------------
# Polars dtype mappings for Excel → Parquet loading
# ---------------------------------------------------------------------------

balancesheet_polars_dtypes = {
    # Integer columns
    "ERS BU (Clean)":               pl.Int64,
    "PMF Account (Count)":          pl.Int64,
    "YEAR":                         pl.Int64,
    "Managed Segment L2 Id":        pl.Int64,
    "Managed Segment L3 Id":        pl.Int64,
    "Managed Segment L4 Id":        pl.Int64,
    "Managed Segment L5 Id":        pl.Int64,
    "Managed Geography L2 Id":      pl.Int64,
    "Managed Geography L3 Id":      pl.Int64,
    "Managed Geography L4 Id":      pl.Int64,
    "Managed Geography L5 Id":      pl.Int64,
    "PMF Account L2 Id":            pl.Int64,
    "PMF Account L3 Id":            pl.Int64,
    "PMF Account L4 Id":            pl.Int64,
    "PMF Account L5 Id":            pl.Int64,
    "PMF Account L6 Id":            pl.Int64,
    "PMF Account L7 Id":            pl.Int64,
    "PMF_FLIP_SIGN":                pl.Int64,
    "AFFILIATE":                    pl.Int64,
    # Float columns (monthly USD balances)
    "M3_USDOLLAR":                  pl.Float64,
    "M6_USDOLLAR":                  pl.Float64,
    "M9_USDOLLAR":                  pl.Float64,
    "M12_USDOLLAR":                 pl.Float64,
}

convergence_polars_dtypes = {
    "YEAR":                         pl.Int64,
    "Managed Segment L2 Id":        pl.Int64,
    "Managed Segment L3 Id":        pl.Int64,
    "Managed Segment L4 Id":        pl.Int64,
    "Managed Geography L4 Id":      pl.Int64,
    "PMF Account L5 Id":            pl.Int64,
    "GAAP Amount":                  pl.Float64,
    "SA RWA Amount":                pl.Float64,
    "Adv. Total RWA Amount with 1.06 Multiplier":      pl.Float64,
    "Adv. CBNA Total RWA Amount with 1.06 Multiplier": pl.Float64,
}

# ---------------------------------------------------------------------------
# String constants — segment / geography hierarchy
# ---------------------------------------------------------------------------
REPORTING_LAYER         = "Reporting Layer"
SA_ACCOUNT_NUM          = "SA Account #"
AA_ACCOUNT_NUM          = "AA Account #"
RWA_EXPOSURE_TYPE       = "RWA Exposure Type"
RWA_CALC                = "RWA Calc"
MARKETS_FILTER          = "Markets Filter"
DISCONTINUED_OPS_L2     = "Discontinued Ops [L2]"
LEGACY_FRANCHISES_L3    = "Legacy Franchises [L3]"
LEGACY_HOLDINGS_ASSETS_L4 = "Legacy Holdings Assets [L4]"
LATIN_AMERICA           = "Latin America"
MARKETS_L2              = "Markets [L2]"
BANKING_L2              = "Banking [L2]"
WEALTH_L2               = "Wealth [L2]"
SERVICES_L2             = "Services [L2]"

# Credit-risk PMF accounts in scope
PMF_ACCOUNTS = [
    "Deposits with Banks [L2]",
    "Investments [L2]",
    "Letters of Credit [L2]",
    "Other Assets [L2]",
    "Total Loans & Leases Net of Unearned [L2]",
    "Unused Commitments [L2]",
]

# ---------------------------------------------------------------------------
# Column name constants — convergence file
# ---------------------------------------------------------------------------
REPORTABLE_ENTITY_IS_CG   = "Reportable Entity is CG"
REPORTABLE_ENTITY_IS_CBNA = "Reportable Entity is CBNA"
FINANCE_PMF_LEVEL_5_DESC  = "Finance PMF Level 5 Description"
GAAP_AMOUNT               = "GAAP Amount"
SA_RWA_AMT                = "SA RWA Amount"
ADV_CG_TOTAL_RWA_AMT      = "Adv. Total RWA Amount with 1.06 Multiplier"
ADV_CBNA_TOTAL_RWA_AMT    = "Adv. CBNA Total RWA Amount with 1.06 Multiplier"
QRTR_ID                   = "Quarter Id"
MNGD_SGMT_L4_CDE          = "Managed Segment Level 4 Code"
MNGD_SGMT_L3_CDE          = "Managed Segment Level 3 Code"
MNGD_SGMT_L2_CDE          = "Managed Segment Level 2 Code"
MNGD_SGMT_L2_DESC         = "Managed Segment Level 2 Description"
MNGD_GEO_L4_DESC          = "Managed Geography Level 4 Description"
MNGD_GEO_L3_DESC          = "Managed Geography Level 3 Description"
MNGD_SGMT_L2_DESC         = "Managed Segment Level 2 Description"

# Derived RWF column names
SA_RWF  = "SA_RWF"
AA_RWF  = "AA_RWF"
ERBA_RWA = "ERBA_RWA"
