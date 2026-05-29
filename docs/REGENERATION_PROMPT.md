# Regeneration Prompt

Paste this prompt into a new Claude Code session on this repository to resume or extend the refactor.

---

## Paste-ready prompt

```
Read the following files in this repository before doing anything else:
1. CLAUDE.md  — project memory and current state
2. docs/REFACTOR_PLAN.md  — full implementation plan with all design decisions baked in
3. docs/DECISION_LOG.md  — rationale for every major choice (why transforms.py, SQLGlot, Oracle patterns, etc.)
4. src/outlook_rwa/transforms.py  — the canonical business-rule contract that all pipeline versions import

Context:
- This project is a two-stage Python pipeline computing Risk-Weighted Assets (RWA) for
  Outlook balance-sheet scenarios at a bank.
- Phase 0 (foundation) is complete: transforms.py created, functions.py refactored to
  import from it, all 9 tests pass, output is byte-identical to pre-refactor.
- What remains: Phase 1 — two parallel tracks:
    Track A (branch: claude/refactor-outlook-rwa-7b1Wl):
      - EntityBundle dataclass in models.py
      - Loop refactor of pipeline.py replacing ~20 paired CG/CBNA variable blocks
      - Output format redesign (descriptive quarter headers, drop zero-filled MonthN stubs)
    Track D (branch: claude/sql-oracle):
      - SQL pipeline in sql/ (DuckDB primary, Oracle secondary)
      - scripts/generate_sql.py using SQLGlot to inject transforms.py values
      - scripts/oracle_ddl.py and scripts/load_oracle.py for Oracle DDL + bulk loading
      - Column mapping log: logs/column_mapping_<table>.csv

The shared integration test (test/test_integration.py) is the equality oracle:
both tracks must reproduce the current output against the same fixture.

Start by confirming your understanding of the plan, then ask which track to execute.
```

---

## What the session will need

- The repository cloned and `python -m pytest test/ -q` passing (Phase 0 is committed).
- `requirements.txt` installed (`pip install -r requirements.txt`).
- For Oracle work: `DB_USER`, `DB_PASSWORD`, `DB_DSN`, `DB_SCHEMA` env vars set (see `scripts/update.sh` for the template).

## Branch structure

| Branch | Track | Status |
|---|---|---|
| `claude/refactor-outlook-rwa-7b1Wl` | Phase 0 foundation + Track A | Phase 0 complete |
| `claude/sql-oracle` | Track D (SQL + Oracle) | Not yet started |
