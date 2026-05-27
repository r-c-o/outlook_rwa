"""Create mock Excel data files for outlook_rwa project."""
import pandas as pd
import numpy as np
from pathlib import Path

data_dir = Path(__file__).resolve().parents[1] / "data" / "input"
data_dir.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(42)

# ---------------------------------------------------------------------------
# pug_mapping.xlsx
# ---------------------------------------------------------------------------

pug_rows = [
    ("Banking [L2]",   12214, "Investment Banking [L3]",         2067,  "Debt Capital Markets [L4]",      "IB"),
    ("Banking [L2]",   12214, "Investment Banking [L3]",         2068,  "Equity Capital Markets [L4]",    "IB"),
    ("Banking [L2]",   28614, "Corporate Lending [L3]",          39384, "Commercial Banking [L4]",        "CL"),
    ("Banking [L2]",   28614, "Corporate Lending [L3]",          39385, "Mid-Corp Lending [L4]",          "CL"),
    ("Services [L2]",  28610, "Securities Services [L3]",        25457, "Custody [L4]",                   "SS"),
    ("Services [L2]",  28610, "Securities Services [L3]",        22928, "Fund Services [L4]",             "SS"),
    ("Services [L2]",  3891,  "Treasury and Trade Solutions [L3]", 3899, "Payments [L4]",                 "TTS"),
    ("Services [L2]",  3891,  "Treasury and Trade Solutions [L3]", 57742,"Total Liquidity [L4]",          "TTS"),
    ("Markets [L2]",   14001, "Fixed Income [L3]",               14002, "Rates [L4]",                     "MKT"),
    ("Markets [L2]",   14001, "Fixed Income [L3]",               14003, "Credit [L4]",                    "MKT"),
    ("Wealth [L2]",    20001, "Private Bank [L3]",               20002, "UHNW [L4]",                      "PB"),
    ("All Other [L2]", 4921,  "Legacy Franchises [L3]",          8278,  "Legacy Holdings Assets [L4]",    "LF"),
]

pug_df = pd.DataFrame(pug_rows, columns=[
    "Managed Segment L2 Descr",
    "Managed Segment L3 Id",
    "Managed Segment L3 Descr",
    "Managed Segment L4 Id",
    "Managed Segment L4 Descr",
    "PUG",
])
pug_df.to_excel(data_dir / "pug_mapping.xlsx", index=False)
print("✅ pug_mapping.xlsx written")

# ---------------------------------------------------------------------------
# adjustment_master_file.xlsx  (6 sheets)
# ---------------------------------------------------------------------------

adj_cols = [
    "YEAR", "Managed Segment L4 Descr", "Managed Segment L3 Descr",
    "Managed Segment L2 Descr", "Managed Geography L4 Descr",
    "Managed Geography L3 Descr", "PMF Account L5 Descr",
    "Managed Segment L4 Id", "Managed Segment L3 Id", "Managed Segment L2 Id",
    "Month", "Balances", "Quarter Id",
    "Key1", "Key2", "Key3", "Key4", "Key5",
    "SA RWA", "AA RWA", "ERBA RWA", "Comment", "RWA Exposure Type",
    "SA RWF", "AA RWF",
    "SA RWF_key2", "AA RWF_key2",
    "SA RWF_key3", "AA RWF_key3",
    "SA RWF_key4", "AA RWF_key4",
    "SA RWF_key5", "AA RWF_key5",
]

n = 20
base_adj = pd.DataFrame({
    "YEAR": [2025] * n,
    "Managed Segment L4 Descr": rng.choice(["Legacy Holdings Assets [L4]", "Debt Capital Markets [L4]", "Payments [L4]"], n),
    "Managed Segment L3 Descr": rng.choice(["Legacy Franchises [L3]", "Investment Banking [L3]", "Treasury and Trade Solutions [L3]"], n),
    "Managed Segment L2 Descr": rng.choice(["All Other [L2]", "Banking [L2]", "Services [L2]"], n),
    "Managed Geography L4 Descr": rng.choice(["US", "EMEA", "Asia Pacific", "Latin America"], n),
    "Managed Geography L3 Descr": rng.choice(["NAM", "Europe", "Japan Asia Pacific", "Latin America"], n),
    "PMF Account L5 Descr": rng.choice(["Other Liabilities (L2)", "Total Loans & Leases Net of Unearned (L2)", "Other Assets (L2)"], n),
    "Managed Segment L4 Id": rng.integers(1000, 9999, n),
    "Managed Segment L3 Id": rng.integers(1000, 9999, n),
    "Managed Segment L2 Id": rng.integers(1000, 9999, n),
    "Month": rng.choice(["Mar", "Jun", "Sep", "Dec"], n),
    "Balances": rng.uniform(0, 1e8, n).round(2),
    "Quarter Id": rng.integers(0, 4, n).astype(str),
    "Key1": [""] * n, "Key2": [""] * n, "Key3": [""] * n, "Key4": [""] * n, "Key5": [""] * n,
    "SA RWA": rng.uniform(0, 1e7, n).round(2),
    "AA RWA": rng.uniform(0, 1e7, n).round(2),
    "ERBA RWA": rng.uniform(0, 1e7, n).round(2),
    "Comment": [""] * n,
    "RWA Exposure Type": ["Banking Book"] * n,
    "SA RWF": rng.uniform(0, 1, n).round(4),
    "AA RWF": rng.uniform(0, 1, n).round(4),
    "SA RWF_key2": [None] * n, "AA RWF_key2": [None] * n,
    "SA RWF_key3": [None] * n, "AA RWF_key3": [None] * n,
    "SA RWF_key4": [None] * n, "AA RWF_key4": [None] * n,
    "SA RWF_key5": [None] * n, "AA RWF_key5": [None] * n,
})

orr_df = base_adj.copy()
fx_df = base_adj.head(5).copy()
markets_df = base_adj.head(8).copy()
cap_ded_df = base_adj.head(4).copy()

sheets = {
    "Adjustments - CG": base_adj,
    "Adjustments - CBNA": base_adj.copy(),
    "ORR": orr_df,
    "FX": fx_df,
    "Markets Overlays": markets_df,
    "Capital Deductions": cap_ded_df,
}

with pd.ExcelWriter(data_dir / "adjustment_master_file.xlsx", engine="openpyxl") as writer:
    for sheet_name, df in sheets.items():
        df.to_excel(writer, sheet_name=sheet_name, index=False)
print("✅ adjustment_master_file.xlsx written")

# ---------------------------------------------------------------------------
# outlook_balancesheet_cg.xlsx
# ---------------------------------------------------------------------------

n = 100
bs_df = pd.DataFrame({
    "FRS BU (Leaf)": rng.integers(10000, 99999, n),
    "AFFILIATE": rng.integers(1, 5, n),
    "PMF Account (Leaf)": rng.integers(100000, 999999, n),
    "SCENARIO": ["EOP"] * n,
    "YEAR": [2025] * n,
    "Balance Type": ["ABL"] * n,
    "Managed Segment L1 Descr": ["Total CB [L1]"] * n,
    "Managed Segment L2 Descr": rng.choice(["Banking [L2]", "Services [L2]", "Markets [L2]", "All Other [L2]", "Wealth [L2]"], n),
    "Managed Segment L3 Descr": rng.choice(["Corporate Lending [L3]", "Securities Services [L3]", "Treasury and Trade Solutions [L3]", "Legacy Franchises [L3]", "Investment Banking [L3]"], n),
    "Managed Segment L4 Descr": rng.choice(["Commercial Banking [L4]", "Custody [L4]", "Payments [L4]", "Legacy Holdings Assets [L4]", "Debt Capital Markets [L4]"], n),
    "Managed Segment L5 Descr": [""] * n,
    "Managed Geography L1 Descr": ["Total Citi Geography"] * n,
    "Managed Geography L2 Descr": rng.choice(["International", "North America"], n),
    "Managed Geography L3 Descr": rng.choice(["NAM", "Europe", "Japan Asia North & Australia (JANA)", "Latin America", "Asia South", "International Hub", "Middle East & Africa (MEA)"], n),
    "Managed Geography L4 Descr": rng.choice(["US", "UK", "Japan", "Mexico", "Korea"], n),
    "Managed Geography L5 Descr": [""] * n,
    "PMF Account L1  Descr": ["Total Assets [L1]"] * n,
    "PMF Account L2 Descr": rng.choice(["Total Loans & Leases Net of Unearned (L2)", "Other Assets (L2)", "Investments (L2)"], n),
    "PMF Account L3 Descr": rng.choice(["Commercial Loans [L3]", "Other [L3]"], n),
    "PMF Account L4 Descr": rng.choice(["C&I Loans [L4]", "Other Assets [L4]"], n),
    "PMF Account L5 Descr": rng.choice(["Total Loans & Leases Net of Unearned (L2)", "Other Assets (L2)", "Investments (L2)"], n),
    "PMF Account L6 Descr": [""] * n,
    "PMF Account L7 Descr": [""] * n,
    "PMF Account L8 Descr": [""] * n,
    "Managed Segment L1 Id": [1] * n,
    "Managed Segment L2 Id": rng.integers(1000, 9999, n),
    "Managed Segment L3 Id": rng.integers(10000, 99999, n),
    "Managed Segment L4 Id": rng.integers(10000, 99999, n),
    "Managed Segment L5 Id": [0] * n,
    "Managed Geography L1  Id": [1] * n,
    "Managed Geography L2  Id": rng.choice(["US", "KR", "JP", "UK", "MX"], n),
    "Managed Geography L3  Id": rng.integers(1001, 1177, n),
    "Managed Geography L4  Id": rng.choice(["US", "KR", "JP", "UK", "MX"], n),
    "Managed Geography L5  Id": rng.choice(["None", "MX", "KR"], n),
    "PMF Account L2 Id": rng.integers(100, 999, n),
    "PMF Account L3 Id": rng.integers(1000, 9999, n),
    "PMF Account L4 Id": rng.integers(10000, 99999, n),
    "PMF Account L5 Id": rng.integers(100000, 999999, n),
    "PMF Account L6 Id": [0] * n,
    "PMF Account L7 Id": [0] * n,
    "PMF Account L8 Id": [0] * n,
    "PMF_FLIP_SIGN": [1] * n,
    "FRS BU (Node)": rng.integers(1000, 9999, n),
    "FRS BU (Node) Descr": ["CITIBANK N.A.CONSOLIDATED"] * n,
    "M3_USDOLLAR": rng.uniform(-1e9, 1e9, n).round(2),
    "M6_USDOLLAR": rng.uniform(-1e9, 1e9, n).round(2),
    "M9_USDOLLAR": rng.uniform(-1e9, 1e9, n).round(2),
    "M12_USDOLLAR": rng.uniform(-1e9, 1e9, n).round(2),
})

bs_df.to_excel(data_dir / "outlook_balancesheet_cg.xlsx", index=False)
# Reuse same structure for CBNA
bs_df.to_excel(data_dir / "outlook_balancesheet_cbna.xlsx", index=False)
print("✅ outlook_balancesheet_cg.xlsx and outlook_balancesheet_cbna.xlsx written")

# ---------------------------------------------------------------------------
# aggregator_for_convergence.xlsx  (convergence data)
# ---------------------------------------------------------------------------

_pq_quarters = ["1Q25", "2Q25", "3Q25", "4Q25"]
_pq_to_qid = {"1Q25": 0, "2Q25": 1, "3Q25": 2, "4Q25": 3}
_pq_to_fyap = {"1Q25": 202503, "2Q25": 202506, "3Q25": 202509, "4Q25": 202512}
scopes = ["CHALLENGER", "BASELINE"]
n = 200

_projected_quarters = rng.choice(_pq_quarters, n)

conv_df = pd.DataFrame({
    "CCAR Cycle": rng.choice(["QMMF_202503", "QMMF_202506", "QMMF_202509", "QMMF_202512"], n),
    "Scope": rng.choice(scopes, n),
    "Managed Segment Level 1 Code": [1] * n,
    "Managed Segment Level 2 Code": rng.choice([12214, 28614, 28610, 3891, 14001, 20001, 4921], n),
    "Managed Segment Level 3 Code": rng.integers(1000, 99999, n),
    "Managed Segment Level 4 Code": rng.integers(1000, 99999, n),
    "Version Number": [1] * n,
    "Data Category": rng.choice(["STDBBK", "RTL", "FXD_RSLT", "WHSL", "SLR", "SECU", "OPS", "RECON", "EQT", "MKT"], n),
    "RWA Exposure Type Description": rng.choice([
        "Direct", "Available for Sale", "Contingent", "Unused Committed",
        "Fails", "Purchased Receivables", "Securities Financing Transaction",
        "Securitization", "Derivatives", "Ops RWA", "Equity Investments", "VaR", "IRC",
    ], n),
    "Projected Quarter": _projected_quarters,
    "Fiscal Year Accounting Period": [_pq_to_fyap[q] for q in _projected_quarters],
    "Scenario Id": ["S1"] * n,
    "Scenario Name": ["Internal Baseline"] * n,
    "Quarter Id": [_pq_to_qid[q] for q in _projected_quarters],
    "Error Flag": [""] * n,
    "Reportable Entity is CBNA": rng.choice(["Y", "N"], n),
    "Reportable Entity is CG": rng.choice(["Y", "N"], n),
    "GAAP Amount": rng.uniform(-1e9, 1e9, n).round(2),
    "Adv. CG Total RWA Amount with 1.06 Multiplier": rng.uniform(0, 1e8, n).round(2),
    "Adv. CBNA Total RWA Amount with 1.06 Multiplier": rng.uniform(0, 1e8, n).round(2),
    "SA RWA Amount": rng.uniform(0, 1e8, n).round(2),
    "Managed Segment Level 1 Description": ["Citigroup [L1]"] * n,
    "Managed Segment Level 2 Description": rng.choice(["Banking [L2]", "Services [L2]", "Markets [L2]", "All Other [L2]", "Wealth [L2]"], n),
    "Managed Segment Level 3 Description": rng.choice(["Corporate Lending [L3]", "Securities Services [L3]", "Legacy Franchises [L3]"], n),
    "Managed Geography Level 3 Description": rng.choice(["NAM", "Europe", "Japan Asia North & Australia (JANA)", "Latin America"], n),
    "Managed Segment Level 4 Description": rng.choice(["Commercial Banking [L4]", "Custody [L4]", "Legacy Holdings Assets [L4]"], n),
    "Managed Geography Level 4 Description": rng.choice(["US", "UK", "Japan", "Mexico"], n),
    "Finance PMF Level 5 Description": rng.choice([
        "Total Loans & Leases Net of Unearned (L2)",
        "Other Assets (L2)",
        "Investments (L2)",
        "Trading Account Assets (L2)",
        "Other Liabilities (L2)",
    ], n),
    "Comments": [""] * n,
})

conv_df.to_excel(data_dir / "aggregator_for_convergence.xlsx", index=False)
print("✅ aggregator_for_convergence.xlsx written")

# ---------------------------------------------------------------------------
# pmf_rwa_mapping.xlsx
# ---------------------------------------------------------------------------

pmf_map_df = pd.DataFrame({
    "PMF L5": [
        "Total Loans & Leases Net of Unearned (L2)",
        "Other Assets (L2)",
        "Investments (L2)",
        "Deposits with Banks (L2)",
        "Letters of Credit (L2)",
        "Unused Commitments (L2)",
        "Trading Account Assets (L2)",
        "Other Liabilities (L2)",
        "Securities Borrowed (L2)",
        "Securities Lent (L2)",
    ],
    "SA Account #":         [f"SA-{i:04d}" for i in range(1, 11)],
    "SA Leaf Account Name": [f"SA Leaf {i}" for i in range(1, 11)],
    "AA Account #":         [f"AA-{i:04d}" for i in range(1, 11)],
    "AA Leaf Account Name": [f"AA Leaf {i}" for i in range(1, 11)],
})

with pd.ExcelWriter(data_dir / "pmf_rwa_mapping.xlsx", engine="openpyxl") as writer:
    pmf_map_df.to_excel(writer, sheet_name="Sheet1", index=False)
print("✅ pmf_rwa_mapping.xlsx written")

print("\n🎉 All mock data files created in:", data_dir)
