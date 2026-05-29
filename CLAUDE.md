# Outlook RWA — Claude Project Memory

This file is auto-loaded by every Claude Code session in this repository. It gives the essential architecture overview and points to the deeper docs so a new session can pick up without reading the entire chat history.

## What this project does

Two-stage Python pipeline that computes Risk-Weighted Assets (RWA) for Outlook balance-sheet scenarios:

1. **Stage 1** — Load convergence data, build a 5-key RWF waterfall, compute SA/AA/ERBA RWA.
2. **Stage 2** — Join CG/CBNA balance sheets with convergence RWFs, produce upload templates and control files.

Entry point: `src/outlook_rwa/pipeline.py`. Config lives in `config/config.yaml`. Tests in `test/`.

## The refactor this branch is executing

See `docs/REFACTOR_PLAN.md` for the full plan. Summary:

- **Phase 0 (done)** — Extract `src/outlook_rwa/transforms.py` as the canonical business-rule registry. Refactor `functions.py` to import from it. Pure no-op: all 9 tests pass, output is byte-identical.
- **Phase 1 Track A** — EntityBundle dataclass (`models.py`) + loop refactor of `pipeline.py`/`functions.py` to replace ~20 paired CG/CBNA variable blocks. Branch: `claude/refactor-outlook-rwa-7b1Wl`.
- **Phase 1 Track D** — SQL + Oracle pipeline. New `sql/`, `scripts/generate_sql.py`, `scripts/oracle_ddl.py`, `scripts/load_oracle.py`. Branch: `claude/sql-oracle`.

## The canonical contract: `transforms.py`

`src/outlook_rwa/transforms.py` is the single source of truth for every business rule and structural mapping. **Do not hardcode values that belong here in `functions.py`, SQL files, or the Oracle loader.**

Key contents:
- `QUARTERLY_PERIODS` — maps source Excel columns to quarter-end labels with `agg` semantics (`"last"` = end-of-period snapshot, NOT sum/average)
- `BALANCE_SHEET_MONTH_COLS` / `MONTH_COL_ORDER` — convenience views
- `UPLOAD_STUB_DEFAULTS` — constant values for upload template placeholder columns
- `DEFAULT_SA_ACCOUNT` / `DEFAULT_AA_ACCOUNT` — fallback account codes

`constants.py` still holds dtype mappings, column-name constants, and business-rule lists (`PMF_ACCOUNTS`, `NON_CREDIT_RISK_PMF`, etc.) — `transforms.py` covers what `constants.py` does NOT.

## Aggregation rule for balance-sheet columns

**Quarter columns are end-of-period snapshots, NOT sums.** `RWA = balance × RWF` is computed per quarter independently (`functions.py:318-321`). If the source ever switches to 12 monthly columns, take the last month of each quarter — do not sum or average. The `agg: "last"` field in `QUARTERLY_PERIODS` encodes this.

## Change propagation (one-click)

```bash
scripts/update.sh
```

Validates imports → runs pytest → regenerates SQL if `sql/templates/` exists. All version-specific logic flows from `transforms.py`.

## Running tests

```bash
python -m pytest test/ -q              # all 9 tests
python -m pytest test/test_integration.py -v  # integration (writes artifacts to output/)
```

## Key files

| File | Purpose |
|---|---|
| `src/outlook_rwa/transforms.py` | Canonical business-rule registry (THE contract) |
| `src/outlook_rwa/constants.py` | Column-name constants, dtype maps, PMF/segment name lists |
| `src/outlook_rwa/functions.py` | All transformation logic |
| `src/outlook_rwa/pipeline.py` | Orchestration (Stage 1 + Stage 2) |
| `config/config.yaml` | Input file paths and output directory |
| `docs/REFACTOR_PLAN.md` | Full refactor plan with approach trade-offs |
| `docs/DECISION_LOG.md` | Why each architectural decision was made |
| `docs/REGENERATION_PROMPT.md` | Paste-ready prompt to resume this work in a future session |

## Output files (Stage 2)

| File | Contents |
|---|---|
| `CG_Upload_Template_Full.xlsx` | CG RWA upload template (entity/segment/PMF × quarter) |
| `CBNA_Upload_Template_Full.xlsx` | CBNA upload template |
| `CG_RAW_DATA.xlsx` | CG raw data (pre-pivot) |
| `CBNA_RAW_DATA.xlsx` | CBNA raw data |
| `control_file.xlsx` | Multi-sheet control totals (CG, CBNA, Raw, Parameters) |

**Note:** The current upload templates use bare integers (1–7) as quarter column headers and zero-filled `MonthN` placeholder columns. Phase 1 Track A will redesign these headers to descriptive strings (e.g., `Mar 2024`) and drop the zero-filled stubs. See `docs/REFACTOR_PLAN.md § Output File Format`.
