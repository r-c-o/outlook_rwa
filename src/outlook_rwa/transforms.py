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

# ---------------------------------------------------------------------------
# Upload-template column layout (data-driven; replaces UPLOAD_TEMPLATE_COL_ORDER)
# ---------------------------------------------------------------------------
# The upload template column order is composed from three groups so the layout
# is data, not magic numbers:
#   1. UPLOAD_DIMENSION_COLS  — leading identity / metadata columns
#   2. UPLOAD_ACTUALS_LABEL + the dynamically-labelled quarter columns
#      (derived from quarter_map, e.g. "Jun 2025")
#   3. UPLOAD_TRAILING_COLS   — trailing metadata
#
# Header redesign (Phase 1 Track A): every header cell is a string, quarter
# columns carry descriptive labels derived from the quarter_map, and the
# zero-filled "MonthN" stub columns are dropped entirely.
UPLOAD_DIMENSION_COLS: list[str] = [
    "Reporting Layer",
    "Managed Segment L2 Descr",
    "Managed Segment L3 Descr",
    "RWA Calc",
    "PMF Account L5 Descr",
    "FileType",
    "Managed Segment L4 Descr",
    "ManagedGeo",
    "PUG",
    "FrsBu",
    "CustomerSegment",
    "Product",
    "Entity",
    "Affiliate",
    "Project",
    "TransactionId",
    "Account",
    "BalanceType",
    "Currency",
    "Layer",
    "ModelId",
    "MDRM",
    "ReasonCode",
    "Comments",
]
UPLOAD_TRAILING_COLS: list[str] = [
    "Comment",
    "RWA Exposure Type",
    "Markets Filter",
]

# Header for the actuals bucket (quarter id 0 in the upload pivot).
UPLOAD_ACTUALS_LABEL = "RWA Actuals"


def quarter_label(year: int, month_abbr: str) -> str:
    """Descriptive quarter-column header, e.g. (2025, 'Jun') -> 'Jun 2025'."""
    return f"{month_abbr} {year}"


def build_upload_col_order(quarter_labels: list[str]) -> list[str]:
    """Compose the upload-template column order from the three column groups.

    Args:
        quarter_labels: Ordered descriptive quarter headers (e.g.
            ['Jun 2025', 'Sep 2025', ...]) derived from the quarter_map; does
            NOT include the actuals bucket, which is inserted here.

    Returns:
        Full ordered list of upload-template column names — 100% strings, no
        bare integers and no zero-filled stub columns.
    """
    return [
        *UPLOAD_DIMENSION_COLS,
        UPLOAD_ACTUALS_LABEL,
        *quarter_labels,
        *UPLOAD_TRAILING_COLS,
    ]
