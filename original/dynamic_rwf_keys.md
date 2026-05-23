# Dynamic RWF Key Instructions

## Overview

The waterfall join uses **5 progressively looser composite keys** to match each
outlook row to a Risk Weight Factor (RWF) from the convergence data. The keys
are defined in `config.toml` under `[[rwf_keys.key]]` and applied in order
(Key 1 = most granular → Key 5 = least granular).

---

## Key Hierarchy (Most → Least Granular)

| Key | Dimensions |
|-----|-----------|
| **Key 1** | Quarter Id · Managed Segment Level **4** Code · Managed Geography Level **4** Description · Finance PMF Level 5 Description · Managed Segment Level 2 Description |
| **Key 2** | Quarter Id · Managed Segment Level **3** Code · Managed Geography Level **4** Description · Finance PMF Level 5 Description · Managed Segment Level 2 Description |
| **Key 3** | Quarter Id · Managed Segment Level **2** Code · Managed Geography Level **4** Description · Finance PMF Level 5 Description · Managed Segment Level 2 Description |
| **Key 4** | Quarter Id · Managed Segment Level **3** Code · Managed Geography Level **3** Description · Finance PMF Level 5 Description · Managed Segment Level 2 Description |
| **Key 5** | Quarter Id · Managed Segment Level **3** Code · Finance PMF Level 5 Description · Managed Segment Level 2 Description *(no geography)* |

---

## How It Works

1. Each outlook row gets **5 key strings** built by concatenating the relevant
   columns with a `|` delimiter.
2. The waterfall merge left-joins the convergence RWF lookup tables onto the
   outlook DataFrame in order (Key1 → Key5).
3. The **first non-null match wins** — implemented by coalescing the five
   `SA_RWF_keyN` columns in step 2 downstream.

---

## How to Add or Modify Keys

Edit `config.toml` — add/remove/reorder `[[rwf_keys.key]]` blocks:

```toml
[[rwf_keys.key]]
index = 'Quarter Id,<col1>,<col2>,...'
```

The `index` string is a **comma-separated list of column names** that will be
used as the pivot index in `create_key_pivots()`. The columns must exist in the
convergence DataFrame.

> **Rule of thumb:** start with the most granular key (most columns) and
> progressively drop geography or segment columns to widen the match.

---

## Data Quality Checks

After the waterfall merge runs, two checks fire automatically:

- **`check_rwf_capping(keys)`** — warns if any RWF > 1 (capped at 1; abs
  GAAP > 12.5× is treated as data error).
- **`check_key_match_coverage(cg_outlook, cbna_outlook)`** — reports the
  percentage of rows with *no match across all 5 keys* (should be ~0%).

---

## Markets [L2] Treatment

After RWF is computed, `set_markets_rwf_zero()` forces `SA_RWF = AA_RWF = 0`
for all rows where `Managed Segment Level 2 Description == "Markets [L2]"`.
Markets RWA is handled via the addon pivot, not the waterfall.
