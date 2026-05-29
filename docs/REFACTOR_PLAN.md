# Outlook RWA — Refactor Plan: CG/CBNA Entity Consolidation

## Context

`pipeline.py` processes CG and CBNA entities with nearly identical operations in ~20+ adjacent pairs of statements (`cg_outlook = …` / `cbna_outlook = …`). Several functions in `functions.py` already accept both frames at once (`assign_erba_rwa_and_metadata`, `build_markets_addon_pivot`, `concat_addon_all`, etc.) but were never unified into a shared data model.

The project's Tableau migration goal favors readability, long-format data, and an `Entity` dimension column rather than split DataFrames. The production output (separate CG/CBNA upload templates) doesn't change.

---

## Three Proposed Approaches

### A — EntityBundle dataclass: loop over entities in `pipeline.py`

Introduce a lightweight `EntityBundle` dataclass in a new `models.py`:

```python
@dataclass
class EntityBundle:
    name: str            # "CG" | "CBNA"
    balance_sheet: pd.DataFrame
    adjustments: pd.DataFrame
    adv_rwa_col: str     # entity-specific ADV RWA column name
    entity_filter_col: str  # REPORTABLE_ENTITY_IS_CG / _CBNA
    # populated as pipeline runs:
    credit_risk: pd.DataFrame | None = None
    outlook: pd.DataFrame | None = None
    addon_non_waterfall: pd.DataFrame | None = None
    # ...
```

Replace the ~20 paired blocks in `pipeline.py` with `for entity in entities:` loops. Each function that currently takes `(cg_df, cbna_df)` is refactored to take a single `EntityBundle` (or a list of them).

**Tradeoffs**:  
+ Smallest blast radius — `pipeline.py` changes heavily, `functions.py` lightly  
+ Functions stay DataFrame-in / DataFrame-out (easy to test)  
+ Adding a third entity (e.g., CBNA-sub) is trivial  
− Some cross-entity steps (`assign_erba_rwa_and_metadata`, control file layout) must stay outside the entity loop  

---

### B — Unified long-format DataFrame with `Entity` column (SQL/Tableau-first)

Load both balance sheets into a single DataFrame with an `Entity` column (`'CG'`/`'CBNA'`) from step 1. Process everything in one pass using `groupby('Entity')` instead of parallel variables. The ADV RWA amounts are renamed to a common `AA_RWA_SRC` column with entity-specific rows.

```
convergence  → add Entity column → unified_df
balance_sheet_cg + balance_sheet_cbna → pd.concat → outlook_df (Entity col)
```

**Tradeoffs**:  
+ Best SQL/Tableau fit — one table, `WHERE Entity = 'CG'` is idiomatic  
+ Eliminates all `cg_`/`cbna_` variable duplication  
− Most invasive: every function signature changes  
− ADV RWA column differs per entity — requires remapping or conditional logic  
− Contradicts the production upload format (separate CG/CBNA files); extra split step needed at export  
− Highest risk to the integration test  

---

### C — EntityBundle (processing) + SQL export layer (two-track)

**Recommended.** Build Approach A for the main branch (entity-loop processing), then add an optional export function that merges the entity outputs into long-format for SQL/Tableau on a separate branch.

**Main branch** (`claude/refactor-outlook-rwa-7b1Wl`):
- `models.py`: `EntityBundle` dataclass
- `pipeline.py`: entity-loop refactor (Approach A)
- Functions stay single-entity

**SQL/Tableau branch** (`claude/tableau-compat`):
- Adds `export_tableau_format(entities: list[EntityBundle]) -> pd.DataFrame` in `functions.py`
- Stacks CG + CBNA into one long DataFrame with `Entity` column
- Writes `output/tableau_export.parquet` (and optionally `.csv`)
- No changes to production upload template logic

**Tradeoffs**:  
+ Processing correctness preserved; Tableau readiness is additive  
+ Separate branch is explicit — can be reviewed, tested, and shipped independently  
+ Least risk to existing tests  
− Two branches to maintain until Tableau migration is complete  

---

### D — Pure SQL pipeline: base tables → mapping tables → CTEs / intermediate tables

Re-express the entire pipeline as a SQL schema with no Python transformation logic. Python becomes a thin loader (read Excel → INSERT into tables) and a thin exporter (SELECT → write xlsx). All joins, waterfall lookups, RWF computation, pivots, and control totals happen in SQL.

**Schema layout:**

```
Base tables (loaded once per run)
──────────────────────────────────
balance_sheet_cg        (YEAR, seg_l4, seg_l3, seg_l2, geo_l4, geo_l3, pmf_l5,
                         m3_usd, m6_usd, m9_usd, m12_usd)
balance_sheet_cbna      (same schema)
convergence             (entity_cg, entity_cbna, seg_l2_desc, seg_l4_code,
                         geo_l4_desc, pmf_l5_desc, quarter_id, gaap_amt,
                         sa_rwa_amt, adv_cg_rwa, adv_cbna_rwa, ...)
adjustments             (entity, seg_l4, geo_l4, pmf_l5, quarter_id,
                         sa_rwa, aa_rwa, erba_rwa, comment, sa_rwf, aa_rwf)

Mapping tables
──────────────
pug_mapping             (seg_l4_descr, pug)
pmf_rwa_mapping         (pmf_l5_descr, sa_account_num, aa_account_num)
quarter_map             (quarter_id, year, month_abbr)
waterfall_key_def       (key_rank, field_name, source)   -- from config

Intermediate tables / CTEs
──────────────────────────
conv_credit_risk        -- WHERE entity_flag = 'Y' AND pmf_l5 IN (credit_pmf_list)
conv_markets_addon      -- WHERE entity_flag = 'Y' AND seg_l2 = 'Markets [L2]'
conv_non_waterfall      -- WHERE entity_flag = 'Y' AND NOT credit_risk AND NOT markets
waterfall_rwf           -- SELECT key, SUM(sa_rwa)/SUM(gaap) AS sa_rwf, ... GROUP BY key
outlook_long            -- UNPIVOT balance_sheet (m3,m6,m9,m12) → (month, balance)
outlook_with_keys       -- JOIN balance_sheet quarter_map + composite key strings
outlook_with_rwf        -- LEFT JOIN waterfall_rwf ON key1..key5 (waterfall priority)
outlook_rwa             -- CASE WHEN pmf IN non_credit THEN 0 ELSE balance*rwf
addon_pivot             -- GROUP BY (entity, quarter_id, seg, geo, pmf) SUM(sa_rwa_amt)
frm_base                -- UNION ALL: adjustments + outlook_rwa + addon_pivot
frm_with_mappings       -- LEFT JOIN pug_mapping, pmf_rwa_mapping
upload_template_pivot   -- PIVOT quarter_id across columns (conditional aggregation)
control_summary         -- GROUP BY entity, seg_l2, rwa_calc SUM per quarter
```

**Key SQL patterns:**

- Waterfall priority: `COALESCE(rwf_key1, rwf_key2, rwf_key3, rwf_key4, rwf_key5)` — maps directly to `_first_valid_rwf`
- Balance sheet unpivot: `UNPIVOT (balance FOR month IN (mar, jun, sep, dec))` (or `CROSS JOIN (VALUES ('Mar', m3_usd), ('Jun', m6_usd), ...)`) — maps to `rename_month_columns` + `melt_quarterly_pivot`
- ERBA assignment: `CASE WHEN quarter_id IN (5, 6) THEN sa_rwa ELSE NULL END` — maps to `assign_erba_rwa_and_metadata`
- Upload template pivot: conditional aggregation `SUM(CASE WHEN quarter_id = 1 THEN rwa END) AS q1` — maps to `create_upload_template_pivots`

**Python's role in this version (thin glue only):**

```
load_excel_to_db()      # read xlsx → INSERT INTO base tables
run_sql_pipeline()      # execute intermediate CTEs in order
export_to_excel()       # SELECT final tables → write xlsx templates
```

**Tradeoffs**:  
+ Most transparent for SQL/Tableau consumers — every transform is a named, queryable step  
+ Tableau can connect directly to the intermediate tables (no Python needed at query time)  
+ Column lineage and join logic are self-documenting in SQL  
+ Each CTE / intermediate table is independently testable with `SELECT COUNT(*)`  
− Full rewrite — no Python transformation code survives  
− Requires a database (DuckDB is zero-config and file-based; Postgres for production)  
− SQL pivot syntax varies across databases (DuckDB `PIVOT`, Postgres conditional aggregation)  
− The waterfall key concatenation (`Key1 = seg_l4 || geo_l4 || pmf_l5 || quarter_id`) must be reproduced exactly in SQL  

**Recommended database: DuckDB** — runs in-process, reads/writes parquet natively, supports `UNPIVOT` and `PIVOT`, no server required. SQLite fallback for environments without DuckDB.

#### Oracle target (additional layer on top of Approach D)

If the destination is Oracle Database instead of (or in addition to) DuckDB/Parquet, a Python layer handles schema creation and bulk loading. This does **not** change any SQL transformation logic — Oracle receives the same intermediate and final tables; only the loader and DDL differ.

**Connection config** — kept in `.env` (never committed) and read via `os.environ`:

```
# .env  (add to .gitignore)
DB_USER=rwa_user
DB_PASSWORD=secret
DB_DSN=ora-host:1521/ORCLPDB
DB_SCHEMA=RWA
```

**DDL generation + column mapping log** (`scripts/oracle_ddl.py`):

```python
import re, os, oracledb, pandas as pd

def sanitize_oracle_name(name: str, max_len: int = 30) -> str:
    s = re.sub(r'[^a-zA-Z0-9_]', '_', name)[:max_len]
    return ('col_' + s if s and s[0].isdigit() else s or 'col').upper()

def infer_oracle_type(series: pd.Series) -> tuple[str, dict]:
    dt = series.dtype
    if pd.api.types.is_integer_dtype(dt):   return "NUMBER(18,0)", {}
    if pd.api.types.is_float_dtype(dt):     return "FLOAT", {}
    if pd.api.types.is_bool_dtype(dt):      return "CHAR(1)", {}
    if pd.api.types.is_datetime64_any_dtype(dt): return "TIMESTAMP(6)", {}
    max_len = min(int(series.dropna().astype(str).str.len().max() or 1) + 10, 4000)
    return f"VARCHAR2({max_len})", {"max_observed_length": max_len}

def build_mapping(df: pd.DataFrame) -> dict:
    """Returns {orig_col: (safe_name, oracle_type, metadata)}"""
    return {c: (sanitize_oracle_name(c), *infer_oracle_type(df[c])) for c in df.columns}

def write_mapping_log(mapping: dict, path: str):
    """Persist column mapping as CSV log for audit."""
    rows = [{"original": k, "oracle_name": v[0], "oracle_type": v[1], **v[2]}
            for k, v in mapping.items()]
    pd.DataFrame(rows).to_csv(path, index=False)

def generate_ddl(df: pd.DataFrame, table_name: str, schema: str) -> str:
    cols = "\n".join(f"    {v[0]} {v[1]}," for v in build_mapping(df).values()).rstrip(',')
    return f"CREATE TABLE {schema}.{table_name} (\n{cols}\n)"

def create_table_if_not_exists(cursor, schema: str, table_name: str, ddl: str):
    cursor.execute("SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER=:1 AND TABLE_NAME=:2",
                   [schema.upper(), table_name.upper()])
    if cursor.fetchone()[0] == 0:
        cursor.execute(ddl)
        cursor.connection.commit()

def bulk_insert(cursor, df: pd.DataFrame, table_name: str, mapping: dict,
                batch_size: int = 1000):
    cols = ", ".join(v[0] for v in mapping.values())
    ph   = ", ".join(f":{i+1}" for i in range(len(mapping)))
    sql  = f"INSERT INTO {table_name} ({cols}) VALUES ({ph})"
    data = [tuple(r) for r in df.itertuples(index=False, name=None)]
    cursor.executemany(sql, data, batcherrors=True, batch_size=batch_size)
    cursor.connection.commit()
```

**Files produced per pipeline run:**

```
logs/
  column_mapping_balance_sheet_cg.csv     # original → oracle name, type, max len
  column_mapping_convergence.csv
  column_mapping_frm_output.csv
  ...
sql/oracle/
  ddl_balance_sheet_cg.sql               # CREATE TABLE statements (committed)
  ddl_convergence.sql
  ddl_frm_output.sql
  ...
```

**Loading flow** (`scripts/load_oracle.py`):

```
1. Connect via python-oracledb (thin mode, env vars)
2. For each base table (balance_sheet_cg, convergence, adjustments, mapping tables):
   a. build_mapping(df)
   b. write_mapping_log → logs/column_mapping_<table>.csv
   c. generate_ddl → sql/oracle/ddl_<table>.sql
   d. create_table_if_not_exists (query ALL_TABLES — Oracle has no IF NOT EXISTS)
   e. bulk_insert (executemany, batch_size=1000)
3. Execute SQL pipeline (CTEs / intermediate tables) via cursor.execute()
4. Export final tables → xlsx via SELECT → DataFrame → openpyxl
```

---

## Recommended Implementation (Approach C, main-branch portion = Approach A)

### Shape of the change

```
pipeline.py (before)                  pipeline.py (after)
──────────────────────                 ──────────────────────
cg = src_cg.copy()                     entities = [
cbna = src_cbna.copy()                     EntityBundle("CG",  src_cg,  ...),
                                           EntityBundle("CBNA", src_cbna, ...),
rename_month_columns(cg)               ]
rename_month_columns(cbna)             for e in entities:
                                           rename_month_columns(e.balance_sheet)
cg_pivot = create_quarterly_pivot(cg)      e.pivot = create_quarterly_pivot(e.balance_sheet)
cbna_pivot = ...                           e.outlook = melt_quarterly_pivot(e.pivot)
cg_outlook = melt_quarterly_pivot(...)
cbna_outlook = ...
```

### Files to create / modify

| File | Change |
|---|---|
| `src/outlook_rwa/models.py` | **New** — `EntityBundle` dataclass with fields for each pipeline stage's output |
| `src/outlook_rwa/pipeline.py` | Replace ~20 paired `cg_`/`cbna_` blocks with entity-loop; keep cross-entity steps (`assign_erba_rwa_and_metadata`, control file) explicit |
| `src/outlook_rwa/functions.py` | Refactor paired-arg functions (`build_markets_addon_pivot`, `build_addon_pivot`, `concat_addon_all`, `assign_year_month_from_quarter`) to accept a list of `EntityBundle` or single-entity signature |
| `test/test_functions.py` | Update placeholder tests to actually call real functions; add `EntityBundle` construction fixture |
| `test/conftest.py` | Add `entity_bundle` fixture |

### `EntityBundle` fields (minimal viable)

```python
@dataclass
class EntityBundle:
    name: str                     # "CG" | "CBNA"
    adv_rwa_col: str              # ADV_CG_TOTAL_RWA_AMT | ADV_CBNA_TOTAL_RWA_AMT
    entity_filter_col: str        # REPORTABLE_ENTITY_IS_CG | _CBNA
    balance_sheet: pd.DataFrame
    adjustments: pd.DataFrame
    # Stage 1 outputs (set during pipeline run)
    waterfall_lookups: list = field(default_factory=list)
    outlook: pd.DataFrame = None
    addon_markets: pd.DataFrame = None
    addon_non_waterfall: pd.DataFrame = None
    # Stage 2 outputs
    frm_output: pd.DataFrame = None
    upload_template: pd.DataFrame = None
    raw_data: pd.DataFrame = None
```

### Existing utilities to reuse

- `split_convergence` in `functions.py:260` — already entity-agnostic, just call once and store results on each bundle
- `create_key_pivots` / `compute_rwf` / `set_markets_rwf` — already take a single-entity DataFrame; just call inside the entity loop
- `build_convergence_control` / `build_frm_control` / `build_raw_data_control` — stay unchanged; called once per entity at the end

### Functions to simplify (remove duplicated paired-arg pattern)

| Current signature | Simplified to |
|---|---|
| `build_markets_addon_pivot(cg_df, cbna_df, index)` | called once per entity in a loop |
| `build_addon_pivot(cg_df, cbna_df, index)` | called once per entity in a loop |
| `concat_addon_all(cg_markets, cbna_markets, cg_nw, cbna_nw)` | `concat_addon(entity)` taking one bundle |
| `assign_year_month_from_quarter(cg_m, cbna_m, cg_nw, cbna_nw, ...)` | `assign_year_month(entity, quarter_map)` |
| `assign_erba_rwa_and_metadata(cg_outlook, cbna_outlook)` | `assign_erba_rwa(entity)` called in loop |

---

## Cross-version Change Propagation

The four versions share the same underlying business logic. A business change (e.g., new PMF account in the credit-risk list, new waterfall key, adjusted ERBA quarter threshold) must propagate to every version in use. To keep that tractable, each transform is defined canonically in one place and the other versions reference or mirror it.

### Canonical transform registry: `transforms.py`

**Research finding**: `constants.py` already centralizes most business-rule lists (`PMF_ACCOUNTS`, `NON_CREDIT_RISK_PMF`, segment names, quarter mapping). However, several structural constants are still scattered as string literals in `functions.py`: month column names (`"Mar"`, `"Jun"`, `"Sep"`, `"Dec"`), upload template stub column defaults, waterfall key prefixes, and account number defaults. `transforms.py` consolidates these gaps — it does not duplicate what `constants.py` already has.

```python
# transforms.py — what constants.py does NOT already cover

# ── Column name mappings (structural — changing these changes the pipeline schema) ──
#
# Each entry maps one source Excel column to a canonical quarter-end label.
# The "agg" field records HOW to reduce this period if the source ever changes grain:
#   "last"  = end-of-period snapshot (current and correct — do NOT sum or average)
#   "sum"   = cumulative flow metric (not used in current pipeline)
#
# CONFIRMED: The pipeline treats these as independent end-of-period balance snapshots.
# balance * RWF is computed per row (per quarter) independently (functions.py:318-321).
# If the source switches from quarterly to 12 monthly columns, use agg="last" and
# select months 3, 6, 9, 12 — do NOT sum or average the monthly values.
QUARTERLY_PERIODS = [
    {"source_col": "M3_USDOLLAR",  "label": "Mar", "agg": "last"},
    {"source_col": "M6_USDOLLAR",  "label": "Jun", "agg": "last"},
    {"source_col": "M9_USDOLLAR",  "label": "Sep", "agg": "last"},
    {"source_col": "M12_USDOLLAR", "label": "Dec", "agg": "last"},
]
# Convenience views (keep functions.py simple)
BALANCE_SHEET_MONTH_COLS = {p["source_col"]: p["label"] for p in QUARTERLY_PERIODS}
MONTH_COL_ORDER          = [p["label"] for p in QUARTERLY_PERIODS]  # ["Mar","Jun","Sep","Dec"]

WATERFALL_KEY_PREFIX    = "Key"          # Key1, Key2, ...
WATERFALL_SA_RWF_PREFIX = "SA RWF_key"  # SA RWF_key1, SA RWF_key2, ...
WATERFALL_AA_RWF_PREFIX = "AA RWF_key"  # AA RWF_key1, AA RWF_key2, ...
WATERFALL_DERIVED_SA    = "FINAL_SA_RWF"
WATERFALL_DERIVED_AA    = "FINAL_AA_RWF"

# ── Upload template defaults (hardcoded today in format_upload_template) ──
UPLOAD_STUB_DEFAULTS = {
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
DEFAULT_SA_ACCOUNT = "663722"
DEFAULT_AA_ACCOUNT = "664062"
```

### Handling structural changes (column renames AND grain changes)

`QUARTERLY_PERIODS` is the key to structural portability. Two distinct kinds of change must be handled — and they are NOT the same:

**Case 1 — pure rename (same grain).** Source still has 4 quarter-end columns, just renamed. Only the `source_col` values change:

```python
QUARTERLY_PERIODS = [
    {"source_col": "Q1_USD", "label": "Mar", "agg": "last"},
    {"source_col": "Q2_USD", "label": "Jun", "agg": "last"},
    {"source_col": "Q3_USD", "label": "Sep", "agg": "last"},
    {"source_col": "Q4_USD", "label": "Dec", "agg": "last"},
]
```

**Case 2 — grain change (12 monthly columns → 4 quarters).** This is the case you asked about. A quarter is the *last month* of the period (end-of-period snapshot), **not the sum of the three months** — confirmed against the code: balances are period-end snapshots and `balance * RWF` is computed per quarter independently (`functions.py:318-321`). Summing Jan+Feb+Mar would triple-count a snapshot balance.

```python
# 12 monthly source columns → 4 quarter-end snapshots
QUARTERLY_PERIODS = [
    {"source_cols": ["JAN", "FEB", "MAR"], "label": "Mar", "agg": "last"},  # take MAR
    {"source_cols": ["APR", "MAY", "JUN"], "label": "Jun", "agg": "last"},  # take JUN
    {"source_cols": ["JUL", "AUG", "SEP"], "label": "Sep", "agg": "last"},  # take SEP
    {"source_cols": ["OCT", "NOV", "DEC"], "label": "Dec", "agg": "last"},  # take DEC
]
```

The `agg` field makes the intent explicit and reviewable:
- `"last"` → take the final month of the period (current, correct behavior for balance snapshots)
- `"sum"` → add the months (only correct for *flow* metrics, e.g. period income — NOT used today)
- `"mean"` → average (explicitly rejected: dilutes the quarter-end position)

`rename_month_columns` becomes a small reducer that reads `agg` instead of a hardcoded rename:

```python
from outlook_rwa.transforms import QUARTERLY_PERIODS, MONTH_COL_ORDER

def collapse_to_quarter_labels(df):
    for p in QUARTERLY_PERIODS:
        srcs = p.get("source_cols", [p.get("source_col")])
        if p["agg"] == "last":
            df[p["label"]] = df[srcs[-1]]          # end-of-period snapshot
        elif p["agg"] == "sum":
            df[p["label"]] = df[srcs].sum(axis=1)  # flow metric
        elif p["agg"] == "mean":
            df[p["label"]] = df[srcs].mean(axis=1)
    return df
# downstream pivot/melt use MONTH_COL_ORDER instead of ["Mar","Jun","Sep","Dec"]
```

Because every version reads `QUARTERLY_PERIODS`, a grain change updates the Python reducer, the SQL `UNPIVOT`/aggregation, and the Oracle DDL column list from one edit — and the `agg` semantics are not silently lost.

For SQL (Approach D): `generate_sql.py` uses **SQLGlot** (not hand-rolled Jinja2) to inject the mapping. SQLGlot is zero-dependency, handles quoting/escaping safely via AST (no SQL injection risk), and supports DuckDB + Snowflake + BigQuery dialects if needed in the future.

```python
# scripts/generate_sql.py — uses SQLGlot to inject transforms.py values into SQL templates
import sqlglot
from outlook_rwa.transforms import CREDIT_RISK_PMF_ACCOUNTS, BALANCE_SHEET_MONTH_COLS

# Safe IN-clause injection (AST, not string concatenation)
query = sqlglot.parse_one("SELECT * FROM conv WHERE 1=1")
query = query.where(
    sqlglot.column("pmf_l5").isin(CREDIT_RISK_PMF_ACCOUNTS)
)
print(query.sql(dialect="duckdb"))
# SELECT * FROM conv WHERE pmf_l5 IN ('Deposits with Banks (L2)', ...)

# Column renaming for UNPIVOT generation
unpivot_cols = list(BALANCE_SHEET_MONTH_COLS.keys())   # ["M3_USDOLLAR", ...]
unpivot_aliases = list(BALANCE_SHEET_MONTH_COLS.values())  # ["Mar", ...]
```

### How each version consumes the registry

| Version | How rules are consumed |
|---|---|
| **A / C (EntityBundle Python)** | `functions.py` imports `BALANCE_SHEET_MONTH_COLS`, `UPLOAD_STUB_DEFAULTS`, etc.; replaces hardcoded string literals at the ~6 scatter sites found in exploration |
| **B (unified DataFrame)** | Same import — `transforms.py` is version-agnostic Python |
| **D (SQL)** | `generate_sql.py` uses SQLGlot to inject `transforms.py` values into SQL at generation time; SQL files are committed outputs, never hand-edited |

### One-click propagation: `scripts/update.sh`

All propagation steps are in a single shell script the business user (or developer) double-clicks or runs once:

```bash
#!/usr/bin/env bash
# scripts/update.sh — run after any change to transforms.py or constants.py
set -e

echo "==> Validating transforms.py ..."
python -c "import src.outlook_rwa.transforms"

echo "==> Running tests ..."
python -m pytest test/ -q

# Only runs if SQL branch is checked out (sql/templates/ exists)
if [ -d "sql/templates" ]; then
  echo "==> Regenerating SQL from transforms.py ..."
  python scripts/generate_sql.py
  echo "    SQL files updated in sql/"
fi

echo ""
echo "All checks passed. Review any changed files, then commit."
```

On Windows, the same logic lives in `scripts/update.bat` (or a PowerShell `.ps1`). A `Makefile` target (`make update`) wraps `scripts/update.sh` for users who have `make`.

### Change workflow (example: add a new PMF account to credit-risk)

```
1. Edit PMF_ACCOUNTS in constants.py  (already centralized — no scatter to fix)
2. Double-click scripts/update.sh (or run: make update)
   → validates imports → runs pytest → regenerates SQL if active
3. Commit once — one diff, one review
```

**Example: rename a balance sheet column (M3_USDOLLAR → Q1_USD)**

```
1. Edit QUARTERLY_PERIODS source_col in transforms.py (one value change)
2. Double-click scripts/update.sh
3. Commit — functions.py and any SQL UNPIVOT clauses update automatically
```

---

## Output File Format: Issues & Unified Redesign

**Decision: free to redesign** (no downstream loader depends on the exact layout).

### What's wrong today

The upload template (`CG_Upload_Template_Full.xlsx`, `CBNA_Upload_Template_Full.xlsx`) has a confusing header row, confirmed by inspection of `constants.py:UPLOAD_TEMPLATE_COL_ORDER` and `functions.py:format_upload_template`:

| Problem | Detail | Source |
|---|---|---|
| **Integer column headers** | Quarters are bare integers `1,2,3,4,5,6,7` and the actuals bucket is `0` (renamed to `"RWA Actuals"`). Excel shows literal numeric headers next to text headers | `functions.py:877, 957`; pivot produces int columns |
| **Zero-filled stub columns** | `Month1, Month2, Month4, Month5, Month7, Month8, Month10, Month11, Month13, Month14` are created and set to `0`, never populated — pure template padding | `constants.py:UPLOAD_TEMPLATE_MONTH_STUBS`; `functions.py:953-954` |
| **Mixed-type header row** | Strings and ints interleaved → ugly, error-prone (sorting/lookup by header breaks) | `UPLOAD_TEMPLATE_COL_ORDER` mixes `"RWA Calc"`, `1`, `"Month1"`, `2`, ... |
| **Opaque quarter meaning** | `1..7` give no hint which fiscal period they are; the year/month live only in `quarter_map` | `PROJECTED_QUARTER_TO_MONTH` not applied to headers |

### Unified proposal

1. **All headers are strings.** No bare integers in the header row, ever.
2. **Quarter columns get descriptive names derived from `quarter_map`** (quarter_id → year + month_abbr), e.g. `Mar 2024`, `Jun 2024`, … instead of `1,2,3`. The actuals bucket `0` stays `RWA Actuals` (already a sensible name).
3. **Drop the zero-filled month stubs entirely.** They carry no data and exist only for legacy template symmetry. Removing them simplifies the file and the `UPLOAD_TEMPLATE_COL_ORDER` list.
4. **Centralize the layout.** Replace the hand-maintained `UPLOAD_TEMPLATE_COL_ORDER` (with its embedded ints and stub names) with a builder that composes the order from three groups: dimension/metadata columns, then dynamically-labeled quarter columns from `quarter_map`, then trailing metadata (`Comment`, `RWA Exposure Type`, `Markets Filter`). This lives in `transforms.py` so it propagates to every version.

```python
# transforms.py — output layout as data, not magic numbers
UPLOAD_DIMENSION_COLS = [          # leading, stable identity columns
    "Reporting Layer", "Entity", "Managed Segment L2 Descr",
    "Managed Segment L3 Descr", "Managed Segment L4 Descr",
    "PMF Account L5 Descr", "RWA Calc", "Account", ...
]
UPLOAD_TRAILING_COLS  = ["Comment", "RWA Exposure Type", "Markets Filter"]

def build_upload_col_order(quarter_labels: list[str]) -> list[str]:
    """quarter_labels comes from quarter_map (e.g. ['Mar 2024', 'Jun 2024', ...])."""
    return [*UPLOAD_DIMENSION_COLS, "RWA Actuals", *quarter_labels, *UPLOAD_TRAILING_COLS]
```

5. **Before/after (header row):**

```
BEFORE:  Reporting Layer | RWA Calc | RWA Actuals | 1 | Month1 | Month2 | 2 | Month4 | ... | 7 | Comment
AFTER:   Reporting Layer | RWA Calc | RWA Actuals | Mar 2024 | Jun 2024 | Sep 2024 | Dec 2024 | Mar 2025 | Jun 2025 | Sep 2025 | Comment
```

### Optional clean long-format export (Approach C carry-over)

In addition to the cleaned wide upload template, emit a tidy long-format file for Tableau: one row per `(Entity, Reporting Layer, Segment L2/L3/L4, PMF L5, RWA Calc, Period, RWA Amount)`. This is the genuinely Tableau-friendly shape and avoids the pivot entirely. Written as `output/rwa_long.parquet` (+ optional `.csv`).

### Scope / risk note

This changes the integration test's expected columns. The redesign is its own commit within **Track A** (Phase 1), and `test_integration.py`'s golden assertions are updated in the same commit — the numeric RWA values per period must stay identical; only headers/structure change.

---

## Reproducibility & Future Recreation

**Decision: commit the full kit.** Critical constraint driving this: **this session's container is ephemeral**, and the working plan currently lives at `/root/.claude/plans/…`, which is *not* in the repo and will be lost when the session ends. The only durable record is what gets committed to git. So preservation = committing artifacts into the repository.

### Artifacts committed to the repo

| Artifact | Path | Purpose |
|---|---|---|
| **Project memory** | `CLAUDE.md` (repo root) | Auto-loaded by every future Claude Code session. Summarizes architecture, the `transforms.py` contract, the 4 approaches, how to run tests, and where the deeper docs live |
| **The plan** | `docs/REFACTOR_PLAN.md` | A committed copy of this plan file (moved out of ephemeral `~/.claude/plans/`) |
| **Decision log** | `docs/DECISION_LOG.md` | *Why* each choice was made: snapshots-not-sum aggregation, SQLGlot over hand-rolled Jinja, Oracle DDL inference, output redesign, 2-track fan-out. Each entry: decision · rationale · alternatives rejected · date |
| **Regeneration prompt** | `docs/REGENERATION_PROMPT.md` | A paste-ready prompt for a future session: "Read `docs/REFACTOR_PLAN.md`, `docs/DECISION_LOG.md`, and `src/outlook_rwa/transforms.py`, then reproduce/extend the pipeline per the contract." Lets someone re-run the agent process months later |
| **Machine-readable spec** | `src/outlook_rwa/transforms.py` | The canonical, executable source of truth for every business rule and the output layout — already central to the plan; doubles as the spec a future agent reads first |
| **Transformation logs** | `logs/column_mapping_*.csv` | Per-run record of every `original → oracle_name`, `dtype → oracle_type`, `max_observed_length`. Captures the exact schema decisions the loader made |

### How recreation works after this session ends

```
A future Claude Code session in this repo:
1. Auto-loads CLAUDE.md → immediately understands the architecture + contract
2. (Optional) User pastes docs/REGENERATION_PROMPT.md
3. Agent reads docs/REFACTOR_PLAN.md + DECISION_LOG.md + transforms.py
4. Agent can now reproduce a version from scratch, extend it, or audit it —
   because the contract (transforms.py) + rationale (DECISION_LOG) + steps
   (REFACTOR_PLAN) are all version-controlled, not trapped in chat history
```

The combination is deliberate: `transforms.py` makes the *rules* reproducible by machine, `DECISION_LOG.md` makes the *reasoning* reproducible by humans/agents, and `REGENERATION_PROMPT.md` makes the *process* re-runnable. None of these depend on this session's transcript surviving.

---

## Sub-plan Decomposition & Concurrent Execution

This refactor is too large for one linear pass, but it is **not** four independent tracks. Collapsing the redundancy:

- **B is an alternative to A**, not a parallel track — you choose one DataFrame model (EntityBundle vs. unified-long), you don't build both.
- **C is A plus one additive export function** — it folds into the A track as a final step.
- The only genuinely independent, parallelizable split is **two tracks**: Python-native (A) and SQL/Oracle (D).

Two tracks is deliberate: enough to exploit concurrency, few enough that coordination stays trivial.

### Why a foundation phase must land first (not parallel-from-start)

Both tracks import `transforms.py`. If they fork before that module is frozen, every signature change to the one shared file ripples into both branches → merge conflicts on the exact file that's supposed to be the single source of truth. So the shared core is sequential and lands first.

### Phase 0 — Foundation (sequential, one PR, the coordination point)

| Step | File | Note |
|---|---|---|
| Create the canonical registry | `src/outlook_rwa/transforms.py` | `QUARTERLY_PERIODS`, `UPLOAD_STUB_DEFAULTS`, waterfall prefixes, account defaults |
| Wire it into existing code | `src/outlook_rwa/functions.py` | Replace the ~6 scattered string-literal sites with imports — **pure no-op refactor**, integration test stays green |
| Lock the contract | `test/test_integration.py` | This test + its fixture become the equality oracle for both downstream tracks |

This PR **is** the contract. Nothing forks until it merges and the integration test is green. Branch: `claude/refactor-outlook-rwa-7b1Wl` (Phase 0 commits here first).

### Phase 1 — Fan-out (genuinely concurrent, isolated worktrees)

| Track | Branch | Touches | Imports (frozen) |
|---|---|---|---|
| **A** Python-native | `claude/refactor-outlook-rwa-7b1Wl` | `pipeline.py`, `functions.py`, new `models.py` | `transforms.py` |
| **D** SQL + Oracle | `claude/sql-oracle` | new `sql/`, `scripts/generate_sql.py`, `scripts/oracle_ddl.py`, `scripts/load_oracle.py` | `transforms.py` |

A and D edit **nearly disjoint file sets** after Phase 0, so they run in parallel git worktrees (`isolation: "worktree"`), each as a background agent on its own branch. No shared-file contention except the frozen `transforms.py`.

### Coordination — without chatty agent-to-agent messaging

Coordination is structural, not conversational:

1. **The frozen `transforms.py` interface is the contract.** Neither track may change its signatures unilaterally; a change forces a short re-sync round (and would surface as a test failure anyway).
2. **`test_integration.py`'s fixture is the equality oracle.** Both tracks must reproduce the *current* pipeline's output (byte-identical, or numeric-tolerance) against the same fixture. The shared golden output is what keeps two independently-developed implementations honest — no manual sync needed.
3. **Merge gate.** When both tracks return green: merge **A first** (it's the primary assigned branch), then rebase and merge **D** on top. D adds files rather than editing A's, so the rebase is conflict-light.

### Execution mechanics in this harness

- Spawn one background `Agent` per track with `isolation: "worktree"` so each works on an isolated checkout.
- They don't talk to each other; each targets (a) the frozen contract and (b) the shared integration test as its success criterion.
- Each track opens its own **draft PR**; review and merge in the order above.

### Why this beats the alternatives

| Alternative | Why not |
|---|---|
| Parallel-from-start (all tracks at once) | Concurrent edits to the *unfrozen* shared `transforms.py` = the one real source of merge pain |
| Strict sequential chain (A then D) | A and D genuinely don't depend on each other — serializing wastes available parallelism |
| Lead + sub-agent dispatcher | The contract + test-oracle already coordinate; a central dispatcher is overhead until merge/review time |

---

## Verification

**Phase 0 — Foundation (gate for everything else)**
1. `python -m pytest test/ -q` — all 9 tests must pass after the `transforms.py` extraction
2. `python -m pytest test/test_integration.py -v` — must write all 5 step2 artifacts + 5 step1 parquets
3. Confirm the extraction is a no-op: diff output DataFrames against the pre-change pipeline on `test_integration.py`'s fixture, assert `cg_upload_full.equals(new_cg_upload_full)` (numeric-tolerance variant if floats drift)
4. `pylint src/outlook_rwa/` — no new errors

**Track A — Python-native**
5. Same equality oracle: A's output must match the Phase-0 golden output on the shared fixture
6. Grain-change unit test: feed a 12-monthly-column fixture, assert `agg="last"` selects months 3/6/9/12 (not sum)

**Track D — SQL + Oracle**
7. `python scripts/generate_sql.py` runs clean; generated `sql/*.sql` contains the correct `IN (...)` lists from `transforms.py`
8. Run the SQL pipeline on DuckDB against the same fixture; final tables must match the Phase-0 golden output (`SELECT` → DataFrame → `.equals`)
9. Oracle loader (if Oracle reachable): `scripts/load_oracle.py` creates tables via `ALL_TABLES` check, writes `logs/column_mapping_*.csv`, and `VARCHAR2(n)` widths cover observed max lengths. If no Oracle instance is available, verify DDL generation + mapping logs offline (no live connection needed)

**Output format redesign (Track A commit)**
10. Open both `*_Upload_Template_Full.xlsx` files: header row is 100% strings, quarter columns read `Mar 2024 … Sep 2025` (from `quarter_map`), no bare integers, no zero-filled `MonthN` stubs
11. Numeric invariant: RWA value in each period column equals the old integer-column value for the same period (headers/structure change, numbers do not)
12. `output/rwa_long.parquet` exists with one row per (Entity, Segment, PMF, RWA Calc, Period)

**Reproducibility kit**
13. `CLAUDE.md`, `docs/REFACTOR_PLAN.md`, `docs/DECISION_LOG.md`, `docs/REGENERATION_PROMPT.md` exist and are committed (plan moved out of ephemeral `~/.claude/plans/`)
14. `logs/column_mapping_*.csv` generated on an Oracle/DDL dry-run
15. Sanity check: a fresh read of `CLAUDE.md` + `transforms.py` is sufficient to explain the architecture without the chat transcript

**Merge gate**
16. Both tracks green on the shared integration test before merge; merge A first, then rebase/merge D
17. If any step fails, narrow the change for that function (pure rename/dedup, no logic change) and re-verify against the golden output
