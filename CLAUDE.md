# CLAUDE.md

Project guidance for Claude Code. This file is auto-loaded into context at the start of every
session.

## What this project does

`outlook_rwa` projects **SA** (standardized) and **AA** (advanced) **RWA** (risk-weighted assets)
for an outlook/forecast horizon, by applying convergence-derived **risk weight factors (RWFs)** to
balance-sheet balances via a **5-key waterfall**.

## Pipeline

1. `src/main/tools/step1_model_convergence.py` — reads balance sheet + convergence data, builds the
   **5-key RWF waterfall** lookups, applies them to the outlook, computes SA/AA RWA, and exports
   parquet/xlsx for step 2.
2. `src/main/tools/step2_outlook_rwa.py` — consumes step1 outputs + adjustments/PUG/PMF mappings and
   builds the CG/CBNA upload templates and control files.

## Key code

- `src/main/tools/constants.py`
  - `PMF_ACCOUNTS` — credit-risk accounts that go through the RWF waterfall.
  - `NON_CREDIT_RISK_PMF` — accounts whose SA/AA RWA is force-set to 0.
- `src/main/tools/functions.py`
  - `_first_valid_rwf` — picks the first **present** RWF across the 5 keys. A present `0` is a
    valid factor; only null/None/empty (coerced to NaN) are skipped.
  - `calculate_sa_rwa` / `calculate_aa_rwa` — `RWA = Balances × FINAL_RWF` (0 when the account is
    in `NON_CREDIT_RISK_PMF`).
  - `build_outlook_key_strings` — builds the composite waterfall keys
    (`Key1 = Managed Segment L4 Id + Managed Geography L4 Descr + PMF Account L5 Descr + Quarter Id`).

## Domain rule (important)

The waterfall uses the **first present RWF** across keys 1→5 (most specific → broadest). A present
`0` must be used as-is — only truly missing values (null/None/empty) fall through to the next key.

## Config & data

- `config.toml` (tracked) + `config.local.toml` (git-ignored: machine paths, `Q0`); merged by
  `load_config` in `functions.py`.
- `create_mock_data.py` generates runnable mock input files under `data/input/`.

## Session history

For recent session context — the inputs/screenshots, clarifying questions and answers, and the
fixes applied — see **`instructions.env`** at the repo root. In Claude Code on the web, a
SessionStart hook (`.claude/hooks/session-start.sh`) also surfaces it automatically.
