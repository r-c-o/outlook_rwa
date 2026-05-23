# Dynamic RWF Key Instructions (revised)

## Overview

The waterfall join uses **5 progressively looser composite keys** to match each
outlook row to a Risk Weight Factor (RWF) from the convergence data.

Keys are now defined in `config.toml` as **TOML arrays** (not comma strings),
making them diffable, syntax-checked, and self-documenting:

```toml
[[rwf_keys.key]]
label = "Key1 — Sgmt L4 + Geo L4"
index = ["Quarter Id", "Managed Segment Level 4 Code",
         "Managed Geography Level 4 Description",
         "Finance PMF Level 5 Description",
         "Managed Segment Level 2 Description"]
```

---

## Key Hierarchy (Most → Least Granular)

| Key | Segment | Geography | PMF L5 | Sgmt L2 |
|-----|---------|-----------|--------|---------|
| **1** | L4 Code | L4 Desc | ✓ | ✓ |
| **2** | L3 Code | L4 Desc | ✓ | ✓ |
| **3** | L2 Code | L4 Desc | ✓ | ✓ |
| **4** | L3 Code | L3 Desc | ✓ | ✓ |
| **5** | L3 Code | *(none)* | ✓ | ✓ |

Each step drops one level of granularity to widen the match. Key 5 drops
geography entirely — the broadest possible match that still respects Segment L3
and PMF classification.

---

## How It Works in Code

```python
# create_key_pivots() builds all 5 pivot tables in one call
cg_lookups = create_key_pivots(credit_risk_cg, ADV_CG_TOTAL_RWA_AMT)

# merge_rwf_waterfall() applies each key in order, validates m:1 per key
cg_outlook = merge_rwf_waterfall(cg_outlook, *cg_lookups, label="CG")
```

The **first non-null match wins** — implemented by coalescing `SA_RWF_key1`
through `SA_RWF_key5` in the downstream step.

---

## How to Add or Modify Keys

1. Add a `[[rwf_keys.key]]` block in `config.toml`:
   ```toml
   [[rwf_keys.key]]
   label = "Key6 — Quarter + PMF only"
   index = ["Quarter Id", "Finance PMF Level 5 Description"]
   ```
2. Extend `create_key_pivots()` in `functions.py` to produce `key6`.
3. Update `merge_rwf_waterfall()` signature to accept `k6`.

> **All column names in `index` must exist in the convergence DataFrame.**
> Run `check_expected_columns()` after adding a key to verify.

---

## RWF Computation

```python
# Revised: uses .clip(upper=1.0) for clarity and a zero-GAAP guard
gaap_abs = key_df[GAAP_AMOUNT].abs().replace(0, np.nan)
key_df[SA_RWF] = (key_df[SA_RWA_AMT].abs() / gaap_abs).clip(upper=1.0)
key_df[AA_RWF] = (key_df[adv_rwa_col].abs() / gaap_abs).clip(upper=1.0)
```

- Rows where `GAAP_AMOUNT == 0` produce `NaN` (not `inf`) — visible in QA.
- `.clip(upper=1.0)` replaces the original `loc[abs > 12.5] = 1` mask.

---

## Markets [L2] Treatment

After RWF is computed for each key table, `set_markets_rwf_zero()` sets
`SA_RWF = AA_RWF = 0` for all Markets [L2] rows:

```python
is_markets = key_df[MNGD_SGMT_L2_DESC].isin([MARKETS_L2])
key_df.loc[is_markets, [SA_RWF, AA_RWF]] = 0
```

Markets RWA is captured separately via `build_markets_addon_pivot()` and
does **not** flow through the waterfall.

---

## Data Quality Checks

| Check | Function | Trigger |
|-------|----------|---------|
| RWF capped at 1.0 | `check_rwf_capping()` | After `compute_rwf()` |
| No match across all 5 keys | `check_key_match_coverage()` | After `merge_rwf_waterfall()` |
| Unknown Quarter Id | `check_unknown_quarters()` | After `assign_quarter_id()` |
