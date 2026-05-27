from dataclasses import dataclass

import polars as pl

# ---------------------------------------------------------------------------
# Polars dtype mappings for Excel/Parquet loading
# ---------------------------------------------------------------------------

balancesheet_polars_dtypes = {
    "FRS BU (Leaf)": pl.Int64,
    "PMF Account (Leaf)": pl.Int64,
    "YEAR": pl.Int64,
    "Managed Segment L2 Id": pl.Int64,
    "Managed Segment L3 Id": pl.Int64,
    "Managed Segment L4 Id": pl.Int64,
    "Managed Segment L5 Id": pl.Int64,
    "Managed Geography L2  Id": pl.Utf8,  # some IDs are strings (e.g. 'US', 'KR')
    "Managed Geography L3  Id": pl.Int64,
    "Managed Geography L4  Id": pl.Utf8,  # some IDs are strings
    "Managed Geography L5  Id": pl.Utf8,  # some IDs are strings (e.g. 'None', 'MX')
    "PMF Account L2 Id": pl.Int64,
    "PMF Account L3 Id": pl.Int64,
    "PMF Account L4 Id": pl.Int64,
    "PMF Account L5 Id": pl.Int64,
    "PMF Account L6 Id": pl.Int64,
    "PMF Account L7 Id": pl.Int64,
    "PMF Account L8 Id": pl.Int64,
    "PMF_FLIP_SIGN": pl.Int64,
    "M3_USDOLLAR": pl.Float64,
    "M6_USDOLLAR": pl.Float64,
    "M9_USDOLLAR": pl.Float64,
    "M12_USDOLLAR": pl.Float64,
    "AFFILIATE": pl.Int64,
    "Managed Segment L1 Id": pl.Int64,
    "Managed Geography L1  Id": pl.Int64,
    "SCENARIO": pl.Utf8,
    "Balance Type": pl.Utf8,
    "Managed Segment L1 Descr": pl.Utf8,
    "FRS BU (Node) Descr": pl.Utf8,
    "PMF Account L1  Descr": pl.Utf8,
    "Managed Segment L2 Descr": pl.Utf8,
    "Managed Segment L3 Descr": pl.Utf8,
    "Managed Segment L4 Descr": pl.Utf8,
    "Managed Segment L5 Descr": pl.Utf8,
    "Managed Geography L1 Descr": pl.Utf8,
    "Managed Geography L2 Descr": pl.Utf8,
    "Managed Geography L3 Descr": pl.Utf8,
    "Managed Geography L4 Descr": pl.Utf8,
    "Managed Geography L5 Descr": pl.Utf8,
    "PMF Account L2 Descr": pl.Utf8,
    "PMF Account L3 Descr": pl.Utf8,
    "PMF Account L4 Descr": pl.Utf8,
    "PMF Account L5 Descr": pl.Utf8,
    "PMF Account L6 Descr": pl.Utf8,
    "PMF Account L7 Descr": pl.Utf8,
    "PMF Account L8 Descr": pl.Utf8,
    "FRS BU (Node)": pl.Int64,
}

convergence_polars_dtypes = {
    "CCAR Cycle": pl.Utf8,                                         # e.g. "QMMF_202512"
    "Scope": pl.Utf8,                                              # e.g. "CHALLENGER"
    "Managed Segment Level 1 Code": pl.Int64,
    "Managed Segment Level 2 Code": pl.Int64,
    "Managed Segment Level 3 Code": pl.Int64,
    "Managed Segment Level 4 Code": pl.Int64,
    "Version Number": pl.Int64,
    "Data Category": pl.Utf8,
    "RWA Exposure Type Description": pl.Utf8,
    "Projected Quarter": pl.Utf8,
    "Fiscal Year Accounting Period": pl.Int64,
    "Scenario Id": pl.Utf8,
    "Scenario Name": pl.Utf8,
    "Quarter Id": pl.Int64,
    "Error Flag": pl.Utf8,
    "Reportable Entity is CBNA": pl.Utf8,
    "Reportable Entity is CG": pl.Utf8,
    "GAAP Amount": pl.Float64,
    "Adv. CG Total RWA Amount with 1.06 Multiplier": pl.Float64,
    "Adv. CBNA Total RWA Amount with 1.06 Multiplier": pl.Float64,
    "SA RWA Amount": pl.Float64,
    "Managed Segment Level 1 Description": pl.Utf8,
    "Managed Segment Level 2 Description": pl.Utf8,
    "Managed Segment Level 3 Description": pl.Utf8,
    "Managed Geography Level 3 Description": pl.Utf8,
    "Managed Segment Level 4 Description": pl.Utf8,
    "Managed Geography Level 4 Description": pl.Utf8,
    "Finance PMF Level 5 Description": pl.Utf8,
    "Comments": pl.Utf8,
}

DTYPE_MAP = {
    "decimal": pl.Decimal,
    "float16": pl.Float16,
    "float32": pl.Float32,
    "float64": pl.Float64,
    "int8": pl.Int8,
    "int16": pl.Int16,
    "int32": pl.Int32,
    "int64": pl.Int64,
    "int128": pl.Int128,
    "uint8": pl.UInt8,
    "uint16": pl.UInt16,
    "uint32": pl.UInt32,
    "uint64": pl.UInt64,
    "bool": pl.Boolean,
    "date": pl.Date,
    "time": pl.Time,
    "datetime": pl.Datetime,
    "duration": pl.Duration,
    # nested types
    "array": pl.Array,
    "list": pl.List,
    "field": pl.Field,
    "struct": pl.Struct,
    # string types
    "string": pl.String,
    "categorical": pl.Categorical,
    "categories": pl.Categories,
    "enum": pl.Enum,
    "utf8": pl.Utf8,
    # other
    "binary": pl.Binary,
    "boolean": pl.Boolean,
    "extension": pl.Extension,
    "null": pl.Null,
    "object": pl.String,
    "unknown": pl.Unknown,
}

# ---------------------------------------------------------------------------
# Balancesheet column name constants
# ---------------------------------------------------------------------------

MANAGED_SEGMENT_L4_DESCR = "Managed Segment L4 Descr"
MANAGED_SEGMENT_L3_DESCR = "Managed Segment L3 Descr"
MANAGED_SEGMENT_L2_DESCR = "Managed Segment L2 Descr"
MANAGED_GEOGRAPHY_L3_DESCR = "Managed Geography L3 Descr"
PMF_ACCOUNT_L5_DESCR = "PMF Account L5 Descr"

SA_RWA = "SA RWA"
AA_RWA = "AA RWA"
ERBA_RWA = "ERBA RWA"
QUARTER_ID = "Quarter Id"
REPORTING_LAYER = "Reporting Layer"
SA_ACCOUNT_NUM = "SA Account #"
AA_ACCOUNT_NUM = "AA Account #"
RWA_EXPOSURE_TYPE = "RWA Exposure Type"
RWA_CALC = "RWA Calc"
MARKETS_FILTER = "Markets Filter"
DISCONTINUED_OPS_L2 = "Discontinued Ops [L2]"
LEGACY_FRANCHISES_L3 = "Legacy Franchises [L3]"
LEGACY_HOLDINGS_ASSETS_L4 = "Legacy Holdings Assets [L4]"
LATIN_AMERICA = "Latin America"
MARKETS_L2 = "Markets [L2]"
BANKING_L2 = "Banking [L2]"
WEALTH_L2 = "Wealth [L2]"
SERVICES_L2 = "Services [L2]"

# Balancesheet geography (L4) + segment id columns
MANAGED_GEOGRAPHY_L4_DESCR = "Managed Geography L4 Descr"
MANAGED_SGMNT_L4_ID = "Managed Segment L4 Id"
MANAGED_SGMNT_L3_ID = "Managed Segment L3 Id"
MANAGED_SGMNT_L2_ID = "Managed Segment L2 Id"

PMF_ACCOUNTS = [
    "Deposits with Banks (L2)",
    "Investments (L2)",
    "Letters of Credit (L2)",
    "Other Assets (L2)",
    "Total Loans & Leases Net of Unearned (L2)",
    "Unused Commitments (L2)",
]

# ---------------------------------------------------------------------------
# Convergence column name constants
# ---------------------------------------------------------------------------

REPORTABLE_ENTITY_IS_CG = "Reportable Entity is CG"
REPORTABLE_ENTITY_IS_CBNA = "Reportable Entity is CBNA"
FINANCE_PMF_LEVEL_5_DESC = "Finance PMF Level 5 Description"
GAAP_AMOUNT = "GAAP Amount"
SA_RWA_AMT = "SA RWA Amount"
ADV_CG_TOTAL_RWA_AMT = "Adv. CG Total RWA Amount with 1.06 Multiplier"
ADV_CBNA_TOTAL_RWA_AMT = "Adv. CBNA Total RWA Amount with 1.06 Multiplier"
MNGD_SGMT_L4_CDE = "Managed Segment Level 4 Code"
MNGD_SGMT_L3_CDE = "Managed Segment Level 3 Code"
MNGD_SGMT_L2_CDE = "Managed Segment Level 2 Code"
MNGD_GEO_L4_DESC = "Managed Geography Level 4 Description"
MNGD_GEO_L3_DESC = "Managed Geography Level 3 Description"
MNGD_SGMT_L4_DESC = "Managed Segment Level 4 Description"
MNGD_SGMT_L3_DESC = "Managed Segment Level 3 Description"
MNGD_SGMT_L2_DESC = "Managed Segment Level 2 Description"

SA_RWF = "SA RWF"
AA_RWF = "AA RWF"

NON_CREDIT_RISK_PMF = [
    "Commitments to Purchase Forward-Dated Securities (L2)",
    "Commitments to Sell Forward-Dated Securities (L2)",
    "Trading Account Assets (L2)",
    "Trading Account Liabilities (L2)",
    "Unsettled Trading Loans (L2)",
    "Brokerage Receivables (L2)",
    "Federal Funds Purch and Sec Loaned or Sold Under Repurchase Agreements (L2)",
    "Federal Funds Sold and Resales (L2)",
    "Securities Borrowed (L2)",
    "Securities Lent (L2)",
    "Other Liabilities (L2)",
    "Indirect Assets (L2)",
    "Premise and Equipment Net of Depreciation and Amortization (L2)",
    "Other Assets L3",
]

EXPECTED_BALANCESHEET_COLS = [
    "M3_USDOLLAR", "M6_USDOLLAR", "M9_USDOLLAR", "M12_USDOLLAR", "YEAR",
    "Managed Segment L4 Descr", "Managed Segment L3 Descr", "Managed Segment L2 Descr",
    "Managed Geography L4 Descr", "Managed Geography L3 Descr", "PMF Account L5 Descr",
    "Managed Segment L4 Id", "Managed Segment L3 Id", "Managed Segment L2 Id",
]

# ---------------------------------------------------------------------------
# Outlook addon: projected-quarter number -> quarter-end month abbreviation
# ---------------------------------------------------------------------------

PROJECTED_QUARTER_TO_MONTH = {1: "Mar", 2: "Jun", 3: "Sep", 4: "Dec"}

# ---------------------------------------------------------------------------
# Parquet/polars -> pandas dtype compatibility map. Used when casting a parquet
# load to the dtypes the waterfall/RWA logic expects (e.g. integer id columns
# read as float64, datetimes as datetime64[ns]).
# ---------------------------------------------------------------------------

POLARS_PANDAS_DTYPE_COMPAT = {
    "int8": "float64", "int16": "float64", "int32": "float64", "int64": "float64",
    "uint8": "float64", "uint16": "float64", "uint32": "float64", "uint64": "float64",
    "boolean": "object", "bool": "object",
    "string": "object", "utf8": "object", "large_string": "object", "large_utf8": "object",
    "categorical": "object", "date": "object", "duration": "object",
    "datetime": "datetime64[ns]",
}

# ---------------------------------------------------------------------------
# Upload-template layout (step2 final CG/CBNA templates)
# ---------------------------------------------------------------------------

# Month placeholder columns; quarter-end values live in the integer columns.
UPLOAD_TEMPLATE_MONTH_STUBS = [
    "Month1", "Month2", "Month4", "Month5", "Month7", "Month8",
    "Month10", "Month11", "Month13", "Month14",
]

# Column order transcribed from the production upload template: RWA Actuals sits
# near the front; the quarter value columns (1-7) are interleaved with the Month
# placeholders; Comment / RWA Exposure Type / Markets Filter trail at the end.
UPLOAD_TEMPLATE_COL_ORDER = [
    REPORTING_LAYER,
    MANAGED_SEGMENT_L2_DESCR,
    MANAGED_SEGMENT_L3_DESCR,
    RWA_CALC,
    PMF_ACCOUNT_L5_DESCR,
    "RWA Actuals",
    "FileType",
    MANAGED_SEGMENT_L4_DESCR,
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
    1, "Month1", "Month2",
    2, "Month4", "Month5",
    3, "Month7", "Month8",
    4, "Month10", "Month11",
    5, "Month13", "Month14",
    6,
    7,
    "Comment",
    RWA_EXPOSURE_TYPE,
    MARKETS_FILTER,
]

# ---------------------------------------------------------------------------
# Entity configuration (CG / CBNA)
# ---------------------------------------------------------------------------
# CG and CBNA run through the same pipeline; they differ only in which Advanced
# RWA column feeds AA RWA, which "Reportable Entity is …" flag selects their
# rows, and the Entity code stamped on the raw-data output.


@dataclass(frozen=True)
class EntityConfig:
    name: str          # "CG" / "CBNA"
    code: str          # "BA" / "BB"  (Entity column on raw data)
    adv_rwa_col: str   # ADV_CG_TOTAL_RWA_AMT / ADV_CBNA_TOTAL_RWA_AMT
    reportable_col: str  # REPORTABLE_ENTITY_IS_CG / REPORTABLE_ENTITY_IS_CBNA


CG_ENTITY = EntityConfig("CG", "BA", ADV_CG_TOTAL_RWA_AMT, REPORTABLE_ENTITY_IS_CG)
CBNA_ENTITY = EntityConfig("CBNA", "BB", ADV_CBNA_TOTAL_RWA_AMT, REPORTABLE_ENTITY_IS_CBNA)
ENTITIES = (CG_ENTITY, CBNA_ENTITY)

# ---------------------------------------------------------------------------
# 5-key RWF waterfall — single source of truth
# ---------------------------------------------------------------------------
# Each key joins an outlook (balance-sheet) row to a convergence RWF pivot on a
# composite string: <segment id/code> [+ <geography>] + <PMF L5> + <Quarter Id>.
# Keys run most-specific (Key1) to broadest (Key5). One spec drives all three
# consumers: create_key_pivots (convergence pivot index), build_outlook_key_strings
# (outlook-side key), and _apply_waterfall_lookups (convergence-side key + merge).


@dataclass(frozen=True)
class WaterfallKey:
    name: str                # "Key1".."Key5"
    outlook_segment_id: str  # balance-sheet segment id column
    conv_segment_code: str   # convergence segment code column
    outlook_geo: str | None  # balance-sheet geography column (None = no geo)
    conv_geo: str | None     # convergence geography column (None = no geo)
    sa_rwf_col: str          # output SA RWF column on the outlook frame
    aa_rwf_col: str          # output AA RWF column on the outlook frame


# (name, outlook_segment_id, conv_segment_code, outlook_geo, conv_geo)
_WATERFALL_KEY_DEFS = [
    ("Key1", MANAGED_SGMNT_L4_ID, MNGD_SGMT_L4_CDE, MANAGED_GEOGRAPHY_L4_DESCR, MNGD_GEO_L4_DESC),
    ("Key2", MANAGED_SGMNT_L3_ID, MNGD_SGMT_L3_CDE, MANAGED_GEOGRAPHY_L4_DESCR, MNGD_GEO_L4_DESC),
    ("Key3", MANAGED_SGMNT_L2_ID, MNGD_SGMT_L2_CDE, MANAGED_GEOGRAPHY_L4_DESCR, MNGD_GEO_L4_DESC),
    ("Key4", MANAGED_SGMNT_L3_ID, MNGD_SGMT_L3_CDE, MANAGED_GEOGRAPHY_L3_DESCR, MNGD_GEO_L3_DESC),
    ("Key5", MANAGED_SGMNT_L3_ID, MNGD_SGMT_L3_CDE, None, None),
]

WATERFALL_KEYS = tuple(
    WaterfallKey(
        name=name,
        outlook_segment_id=outlook_seg,
        conv_segment_code=conv_seg,
        outlook_geo=outlook_geo,
        conv_geo=conv_geo,
        # Key1 lands on the base "SA RWF"/"AA RWF" columns; Key2-5 are suffixed.
        sa_rwf_col=SA_RWF if i == 0 else f"{SA_RWF}_key{i + 1}",
        aa_rwf_col=AA_RWF if i == 0 else f"{AA_RWF}_key{i + 1}",
    )
    for i, (name, outlook_seg, conv_seg, outlook_geo, conv_geo) in enumerate(_WATERFALL_KEY_DEFS)
)

# Columns pulled from the adjustments frame onto an outlook frame (left-merge on
# Key1). Order: the 5 key strings, the RWA values, then every key's RWF columns,
# then Comment / RWA Exposure Type.
ADJUSTMENT_MERGE_COLS = (
    [k.name for k in WATERFALL_KEYS]
    + [SA_RWA, AA_RWA, ERBA_RWA, SA_RWF, AA_RWF]
    + [c for k in WATERFALL_KEYS[1:] for c in (k.sa_rwf_col, k.aa_rwf_col)]
    + ["Comment", RWA_EXPOSURE_TYPE]
)
