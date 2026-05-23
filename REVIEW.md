# Outlook RWA Pipeline — Code Review & Optimization Notes

## Project Overview

Two-step Python pipeline for Risk-Weighted Asset (RWA) calculation:

| Step | Script | Purpose |
|------|--------|---------|
| 1 | `step1_model_convergence.py` | Waterfall-join balance-sheet data to convergence RWFs, compute SA/AA RWFs |
| 2 | `step2_outlook_rwa.py` | Apply PUG/PMF mapping, generate upload templates + control file |

Supporting files: `config.toml`, `functions.py`, `constants.py`,
`create_schema_csv.py`, `dynamic_rwf_keys.md`.

---

## Files Extracted

```
original/
├── config.toml
├── constants.py
├── create_schema_csv.py
├── dynamic_rwf_keys.md
├── functions.py
├── step1_model_convergence.py
└── step2_outlook_rwa.py

revised/
├── config.toml          ← primary changes here
├── functions.py         ← primary changes here
└── step1_model_convergence.py
```

---

## Issues Found & Fixes Applied

### 1. Hardcoded paths in config.toml (BUG — breaks on every new machine)

**Original:**
```toml
data_dir = "C:/Users/rl09895/Desktop/outlook-rwa-data/may2026"
```

**Problem:** Every analyst must manually edit `config.toml` for their own
username/path. Run IDs (`run_20may2026_1530`) are also copy-pasted into 6
separate output filenames, meaning a mislabeled run is a one-edit-breaks-all
situation.

**Fix (revised/config.toml):**
- Added `run_id = "20may2026_1530"` as a single field; all output filenames
  reference `${run_id}`.
- Replaced hardcoded user paths with `${OUTLOOK_RWA_DATA_DIR}` /
  `${OUTLOOK_RWA_APP_DIR}` env-variable placeholders. The Python loader
  (`os.path.expandvars`) expands them at runtime.
- Each analyst sets two env vars in their shell profile — no config edits.

---

### 2. RWF key definitions stored as opaque comma-strings (MAINTAINABILITY)

**Original:**
```toml
[[rwf_keys.key]]
index = 'Quarter Id,Managed Segment Level 4 Code,Managed Geography Level 4 Description,...'
```

**Problem:** A single long string makes it impossible to diff changes, easy to
mistype a column name, and hard to understand the waterfall hierarchy at a glance.
No label indicates *why* each key exists.

**Fix (revised/config.toml):**
```toml
[[rwf_keys.key]]
label = "Key1 — Sgmt L4 + Geo L4"
index = ["Quarter Id", "Managed Segment Level 4 Code", ...]
```

- `index` is now a **TOML array of strings** — each column on its own line,
  diffable, syntax-checked by any TOML parser.
- `label` field documents the hierarchy intent.
- The Python code reads `key_cfg["index"]` directly as a list — no `.split(",")`.

---

### 3. Missing `[parallel]` section in config.toml (BUG — parallel settings not configurable)

**Problem:** The parallel Excel-to-Parquet conversion existed in code but had
no config knobs. `max_workers` and `if_exists` were hardcoded in the script.
Changing them required editing Python, not config.

**Fix (revised/config.toml):**
```toml
[parallel]
max_workers    = 4
if_exists      = "new"     # "new" = skip existing | "replace" = always reconvert
parquet_subdir = "parquet_cache"
```

The step1 script reads these via `config.get("parallel", {})`.

---

### 4. `compute_rwf` used boolean mask for capping — should use `.clip()` (CORRECTNESS + CLARITY)

**Original:**
```python
key_df[SA_RWF] = key_df[SA_RWA_AMT].abs() / key_df[GAAP_AMOUNT].abs()
key_df.loc[key_df[SA_RWF].abs() > 12.5, SA_RWF] = 1
```

**Problems:**
1. The cap condition is `abs(ratio) > 12.5`, which sets those to 1 — but the
   correct meaning is "ratio capped at 1.0", not "12.5 → 1". The 12.5 threshold
   appears to proxy for "GAAP is tiny relative to RWA", which is more cleanly
   expressed as `.clip(upper=1.0)`.
2. Division by zero when `GAAP_AMOUNT == 0` produces `inf`, which silently
   passes the cap test and becomes 1 — masking data errors.

**Fix (revised/functions.py):**
```python
gaap_abs = key_df[GAAP_AMOUNT].abs().replace(0, np.nan)   # explicit zero guard
key_df[SA_RWF] = (key_df[SA_RWA_AMT].abs() / gaap_abs).clip(upper=1.0)
key_df[AA_RWF] = (key_df[adv_rwa_col].abs() / gaap_abs).clip(upper=1.0)
```

- `.replace(0, np.nan)` makes zero-GAAP rows produce NaN RWF (visible, auditable).
- `.clip(upper=1.0)` is semantically exact and faster than a boolean mask.

---

### 5. `export_excel_specs_to_parquet` was sequential (PERFORMANCE)

**Original:**
```python
for spec in file_specs:
    df = pl.read_excel(...)
    df.write_parquet(...)
```

**Problem:** With 3+ large Excel files, this is serial I/O. Excel parsing is
CPU-bound (openpyxl/calamine); files can be converted independently.

**Fix (revised/functions.py):**
```python
with ThreadPoolExecutor(max_workers=max_workers) as pool:
    futures = {pool.submit(_convert, spec): spec["variable_name"] for spec in file_specs}
    for future in as_completed(futures):
        var_name, result = future.result()
        results[var_name] = result
```

- `ThreadPoolExecutor` is appropriate here because Polars releases the GIL
  during Excel parsing and Parquet writes.
- `max_workers` comes from `config.toml [parallel]` — default 4.
- `compression="zstd"` added to `write_parquet` for ~30% smaller files at
  near-zero decompression cost.

---

### 6. `merge_rwf_waterfall` — `try/except Exception` was too broad (CORRECTNESS)

**Original:**
```python
try:
    outlook_df = outlook_df.merge(k1, ..., validate='m:1')
    ...
except Exception:
    # fall back — redo ALL 5 merges without validate
```

**Problems:**
1. Catching bare `Exception` masks real bugs (memory errors, key typos, etc.).
2. On failure, ALL 5 merges are retried without `validate`, even if only Key1
   failed — so a bad Key3 causes Key1 and Key2 to lose their validation silently.

**Fix (revised/functions.py):**
- Catch `pd.errors.MergeError` specifically (what `validate` raises).
- Apply the fallback **per key** in a loop — only the failing key loses
  validation, the others remain strict.

---

### 7. `create_key_pivots` repeated pivot_table call 5 times (DRY)

**Original:** 5 nearly-identical `crd_df.pivot_table(...)` calls with only
`index=` differing.

**Fix (revised/functions.py):**
```python
def _pivot(index_cols):
    return crd_df.pivot_table(values=_values, index=index_cols, aggfunc="sum")

key1 = _pivot([QRTR_ID, MNGD_SGMT_L4_CDE, ...])
...
```

- Single inner function eliminates duplication and makes adding a Key6 trivial.

---

### 8. Step 1 script listed 10 lookup tables by name (FRAGILITY)

**Original:** 10 individual variables (`cg_waterfall_rwf_lookup_1` … `_5`,
`cbna_waterfall_rwf_lookup_1` … `_5`), each explicitly named in every loop.

**Fix (revised/step1_model_convergence.py):**
```python
cg_lookups   = create_key_pivots(credit_risk_cg,   ADV_CG_TOTAL_RWA_AMT)
cbna_lookups = create_key_pivots(credit_risk_cbna, ADV_CBNA_TOTAL_RWA_AMT)

for k in (*cg_lookups, *cbna_lookups):
    k.reset_index(inplace=True)
    compute_rwf(k, ...)
    set_markets_rwf_zero(k)
```

- `create_key_pivots` returns a tuple — unpacked into loops rather than 10 names.
- Adding a 6th key means editing `create_key_pivots` and `config.toml` only.

---

## Dynamic RWF Key Summary (see `dynamic_rwf_keys.md`)

The waterfall join is deliberately designed to cascade from most-granular to
least-granular to maximize RWF match rates while preserving accuracy:

```
Key 1: Quarter · Sgmt L4 Code · Geo L4 Desc · PMF L5 · Sgmt L2 Desc   ← tightest
Key 2: Quarter · Sgmt L3 Code · Geo L4 Desc · PMF L5 · Sgmt L2 Desc
Key 3: Quarter · Sgmt L2 Code · Geo L4 Desc · PMF L5 · Sgmt L2 Desc
Key 4: Quarter · Sgmt L3 Code · Geo L3 Desc · PMF L5 · Sgmt L2 Desc
Key 5: Quarter · Sgmt L3 Code ·              · PMF L5 · Sgmt L2 Desc   ← loosest
```

Markets [L2] rows always get RWF = 0 after the join — their RWA is captured
via the addon markets pivot, not the waterfall.

**To add a new key:** add a `[[rwf_keys.key]]` block in `config.toml` and
extend `create_key_pivots()` to return one more pivot table (and update
`merge_rwf_waterfall` if you go beyond 5).

---

## What Was Not Changed

- The overall two-step pipeline structure — correct and appropriate.
- The use of Polars for Excel/Parquet and pandas for joins — right tool for
  each job (Polars is faster for typed loading; pandas is more ergonomic for
  complex multi-key merges).
- The `split_convergence` logic and ERBA quarter assignment — correct as-is.
- The `check_*` family of data quality functions — kept and slightly tightened.
