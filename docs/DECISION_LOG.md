# Decision Log — Outlook RWA Refactor

Entries record WHY each architectural decision was made, what alternatives were rejected, and when. Intended to make future sessions and reviewers self-sufficient without reading the original chat transcript.

---

## 2026-05-29 — Consolidation approach: EntityBundle + SQL/Oracle two-track

**Decision**: Phase 1 runs two parallel tracks (A: Python EntityBundle refactor; D: SQL/Oracle pipeline) that fan out after a shared foundation phase (Phase 0: `transforms.py`).

**Rationale**:
- Tracks A and D edit nearly disjoint file sets; parallelising them is safe and saves time.
- A sequential chain (A then D) would waste available concurrency.
- Fully parallel (all at once, before `transforms.py` is frozen) would cause merge conflicts on the shared contract file — the one place where concurrent edits cause the most damage.
- A "lead + sub-agent dispatcher" pattern adds coordination overhead that the frozen contract + shared integration test already provide structurally.

**Alternatives rejected**:
- "Parallel from day 1" → contract not frozen → merge conflicts.
- "B (unified long-format DataFrame)" as primary track → most invasive, highest integration-test risk; shelved as optional Tableau export within Track A.
- "Strict sequential A → D" → safe but slower than necessary.

---

## 2026-05-29 — Canonical business-rule registry: `transforms.py`

**Decision**: Introduce `src/outlook_rwa/transforms.py` as the single source of truth for business rules and structural mappings. `constants.py` remains for column-name constants and dtype maps.

**Rationale**:
- ~6 sites in `functions.py` hardcoded strings that belong to business rules (month column names, upload stub defaults, account number fallbacks). A single-file change propagating to all 6 sites is unsafe; a centralised registry makes it a one-line edit.
- `constants.py` already centralises PMF account lists, segment names, etc. `transforms.py` covers only the gaps: quarterly period mapping, waterfall prefixes, upload stub defaults.
- Keeping them separate avoids one massive file and respects the existing import graph.

**Alternatives rejected**:
- Merge everything into `constants.py` → single 400-line file, harder to scan.
- Use a YAML config file → adds a parse step, loses type checking.

---

## 2026-05-29 — Aggregation semantics: `agg: "last"` (no sum, no average)

**Decision**: Balance-sheet quarterly columns (`M3_USDOLLAR` → `Mar`, etc.) are end-of-period snapshots. If the source ever switches to 12 monthly columns, take the last month of each quarter — do NOT sum or average.

**Evidence**: `functions.py:318-321` computes `RWA = balance × RWF` per quarter independently. `create_quarterly_pivot` aggregates (sums) across *dimensional rows* sharing the same key, not across months. `melt_quarterly_pivot` produces one row per (dimension, quarter) — each is an independent observation.

**Rationale**: Summing Jan+Feb+Mar for a balance-sheet snapshot would triple-count the position. Averaging would dilute the quarter-end value. The `agg: "last"` field in `QUARTERLY_PERIODS` encodes this semantics explicitly so it is never silently lost during a grain change.

**Alternatives rejected**:
- `agg: "sum"` → incorrect for balance-sheet positions (would be correct only for flow metrics like income).
- `agg: "mean"` → dilutes the end-of-period position, not the business intent.

---

## 2026-05-29 — SQL generation library: SQLGlot (not Jinja2)

**Decision**: Use SQLGlot to inject `transforms.py` values (account lists, column names) into SQL at generation time.

**Rationale**:
- SQLGlot is zero-dependency and uses an AST to build SQL — no string concatenation, no SQL-injection risk from quoting edge cases.
- Supports DuckDB + Snowflake + BigQuery dialects; if the database changes, only the `dialect=` argument changes.
- Lighter than SQLAlchemy (which also executes queries) and simpler than dbt (a full DAG framework).
- No Jinja2 templating means no hand-rolled `sql_list()` escaping function to maintain.

**Alternatives rejected**:
- Hand-rolled Jinja2 → requires custom escaping, security-critical, fragile on edge cases (quotes in account names).
- dbt → full framework overhead, CVE-2024-40637 SQL injection risk on untrusted vars, assumes a full project structure.
- SQLAlchemy → good choice if also executing queries; overkill for pure SQL text generation.

---

## 2026-05-29 — Oracle loader: `python-oracledb`, `executemany`, `ALL_TABLES` check

**Decision**: Use `python-oracledb` (thin mode) for Oracle connectivity; `cursor.executemany(batch_size=1000)` for bulk loads; `ALL_TABLES` query to simulate `IF NOT EXISTS` (Oracle has no native syntax for this).

**Rationale**:
- `python-oracledb` is the official successor to `cx_Oracle`; thin mode requires no Oracle client installation.
- `executemany` with `batcherrors=True` gives bulk-load performance without losing per-row error detail.
- `ALL_TABLES` check is the cleanest Oracle idiom; PL/SQL exception-handling block works but is harder to read.

**Column type inference**:
- `int64` → `NUMBER(18,0)`, `float64` → `FLOAT`, `object` → `VARCHAR2(max_observed_length + 10)` capped at 4000, `datetime64` → `TIMESTAMP(6)`.
- Max observed string length is computed at load time from the actual data, so VARCHAR2 is always wide enough.

---

## 2026-05-29 — Output format redesign: string headers, no integer columns

**Decision**: Replace bare integer quarter column headers (1–7) and zero-filled `MonthN` placeholder stubs with descriptive string headers derived from `quarter_map` (e.g., `Mar 2024`, `Jun 2024`).

**Rationale**:
- Integer column headers in an Excel file are confusing and error-prone (sorting/lookup by header breaks).
- Zero-filled `Month1`, `Month2`, `Month4`... stubs carry no information and exist only for legacy template padding. Removing them simplifies the file by ~10 columns.
- No downstream loader depends on the exact layout (confirmed by the user), so the redesign is safe.

**Change scope**: Part of Phase 1 Track A. The `build_upload_col_order()` function in `transforms.py` derives the column order dynamically from `quarter_map` so it automatically reflects the actual quarters in the data.

---

## 2026-05-29 — Reproducibility kit: full committed artifact set

**Decision**: Commit `CLAUDE.md` + `docs/REFACTOR_PLAN.md` + `docs/DECISION_LOG.md` (this file) + `docs/REGENERATION_PROMPT.md` + `src/outlook_rwa/transforms.py` as the full reproducibility kit.

**Rationale**: The session container is ephemeral. Anything not committed is lost when the session ends. The combination covers three distinct reproducibility needs:
- `transforms.py` — machine-readable; an agent can execute from it directly.
- `DECISION_LOG.md` — human/agent-readable reasoning; answers "why" without the transcript.
- `REGENERATION_PROMPT.md` — a ready-to-paste prompt; lowers the barrier to restarting the process in a new session.
- `CLAUDE.md` — auto-loaded by Claude Code; ensures every future session starts with context.
