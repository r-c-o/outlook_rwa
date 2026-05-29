"""Canonical business-rule registry for the Outlook RWA pipeline.

Contains every parameterised rule and structural mapping that any version
of the pipeline (Python-native, SQL, Oracle) should import from a single
place. No pandas, no SQL — pure Python data structures only.

To propagate a business-rule change:
  1. Edit this file.
  2. Run:  scripts/update.sh   (or: make update)
     → validates imports → runs pytest → regenerates SQL if active
  3. Commit once — one diff, one review.
"""

# ---------------------------------------------------------------------------
# Balance-sheet quarterly periods
# ---------------------------------------------------------------------------
# Each entry maps one source Excel column to a canonical quarter-end label.
# 'agg' records the reduction semantics:
#   "last" = end-of-period snapshot (correct for balance-sheet balances;
#            do NOT sum or average the three months in a quarter).
#   "sum"  = cumulative flow metric (not used in current pipeline).
#
# CONFIRMED: RWA = balance × RWF is computed per period independently
# (functions.py:318-321). Each value is an end-of-period snapshot.
#
# Grain-change guide — if the source switches to 12 monthly columns:
#   change "source_col" to a list "source_cols": ["JAN", "FEB", "MAR"]
#   keep agg="last" → the loader takes only the final month (MAR, JUN, SEP, DEC)
#   do NOT sum: summing Jan+Feb+Mar triple-counts a snapshot balance.
QUARTERLY_PERIODS = [
    {"source_col": "M3_USDOLLAR",  "label": "Mar", "agg": "last"},
    {"source_col": "M6_USDOLLAR",  "label": "Jun", "agg": "last"},
    {"source_col": "M9_USDOLLAR",  "label": "Sep", "agg": "last"},
    {"source_col": "M12_USDOLLAR", "label": "Dec", "agg": "last"},
]

# Convenience views used by functions.py and SQL/Oracle generators.
# These are derived from QUARTERLY_PERIODS — do not edit them directly.
BALANCE_SHEET_MONTH_COLS: dict[str, str] = {
    p["source_col"]: p["label"] for p in QUARTERLY_PERIODS
}
MONTH_COL_ORDER: list[str] = [p["label"] for p in QUARTERLY_PERIODS]

# ---------------------------------------------------------------------------
# Waterfall key / RWF column naming patterns
# ---------------------------------------------------------------------------
WATERFALL_KEY_PREFIX    = "Key"           # Key1, Key2, ...
WATERFALL_SA_RWF_PREFIX = "SA RWF_key"   # SA RWF_key1, SA RWF_key2, ...
WATERFALL_AA_RWF_PREFIX = "AA RWF_key"   # AA RWF_key1, AA RWF_key2, ...
WATERFALL_DERIVED_SA    = "FINAL_SA_RWF"
WATERFALL_DERIVED_AA    = "FINAL_AA_RWF"

# ---------------------------------------------------------------------------
# Upload template: fixed stub column defaults
# ---------------------------------------------------------------------------
# Every row in the upload template carries these constant values.
# Centralised here so format_upload_template() contains no hardcoded strings.
UPLOAD_STUB_DEFAULTS: dict[str, str] = {
    "FileType":         "R",
    "Affiliate":        "00000",
    "BalanceType":      "EOP",
    "Currency":         "USD",
    "ManagedGeo":       "",
    "FrsBu":            "",
    "CustomerSegment":  "",
    "Product":          "",
    "Project":          "",
    "TransactionId":    "",
    "Layer":            "",
    "ModelId":          "",
    "MDRM":             "",
    "ReasonCode":       "",
    "Comments":         "",
}

# Fallback account codes when the PMF → account mapping is missing ('None').
DEFAULT_SA_ACCOUNT = "663722"
DEFAULT_AA_ACCOUNT = "664062"
