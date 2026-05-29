"""Render sql/templates/*.sql.j2 -> sql/*.sql, injecting values from transforms.py.

Uses SQLGlot (AST-based) to build every injected SQL fragment so account names,
column names, and literals are quoted safely — no hand-rolled string escaping,
no SQL-injection surface. The business-rule values come from the canonical
registry (transforms.py) and constants.py; the SQL files are committed *outputs*
and must never be hand-edited (re-run this script instead, e.g. via update.sh).

Target dialect: DuckDB.

Run:
    python scripts/generate_sql.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from outlook_rwa.constants import (  # noqa: E402
    DISCONTINUED_OPS_L2,
    MARKETS_L2,
    NON_CREDIT_RISK_PMF,
    PMF_ACCOUNTS,
)
from outlook_rwa.transforms import QUARTERLY_PERIODS  # noqa: E402

DIALECT = "duckdb"
TEMPLATE_DIR = REPO_ROOT / "sql" / "templates"
OUT_DIR = REPO_ROOT / "sql"

# ---------------------------------------------------------------------------
# Pipeline-level constants that live in the Python transforms, surfaced here so
# the SQL inherits them rather than hardcoding. These mirror functions.py.
# ---------------------------------------------------------------------------
RWF_ABS_CAP = 12.5                  # functions.compute_rwf abs-cap (-> RWF = 1)
ERBA_QUARTERS = [5, 6]              # functions.assign_erba_rwa_and_metadata
# Quarter columns rendered in the upload pivot. 0 is the RWA Actuals bucket;
# 1..N_QUARTERS are projected periods (matches create_upload_template_pivots,
# which materializes columns 0..7).
N_PROJECTED_QUARTERS = 7


def _col(name: str) -> exp.Column:
    """Return a SQLGlot column reference, quoted for DuckDB identifier safety.

    Accepts either a bare identifier or one already wrapped in double quotes;
    surrounding quotes are stripped so SQLGlot applies its own quoting exactly
    once (avoiding triple-quoted identifier output).
    """
    bare = name.strip()
    if len(bare) >= 2 and bare[0] == '"' and bare[-1] == '"':
        bare = bare[1:-1]
    quoted = any(ch in bare for ch in " .") or not bare.isidentifier()
    return exp.column(exp.to_identifier(bare, quoted=quoted))


def _in_predicate(column: str, values) -> str:
    """Build a safe `column IN (...)` SQL fragment via SQLGlot AST."""
    node = _col(column).isin(*[exp.Literal.string(str(v)) for v in values])
    return node.sql(dialect=DIALECT)


def _eq_predicate(column: str, value: str) -> str:
    """Build a safe `column = 'value'` SQL fragment via SQLGlot AST."""
    node = exp.EQ(this=_col(column), expression=exp.Literal.string(str(value)))
    return node.sql(dialect=DIALECT)


def _ne_predicate(column: str, value: str) -> str:
    """Build a safe `column <> 'value'` SQL fragment via SQLGlot AST."""
    node = exp.NEQ(this=_col(column), expression=exp.Literal.string(str(value)))
    return node.sql(dialect=DIALECT)


def _quarter_in_predicate(column: str, values) -> str:
    """Build a safe numeric `column IN (n, ...)` fragment via SQLGlot AST."""
    node = _col(column).isin(*[exp.Literal.number(v) for v in values])
    return node.sql(dialect=DIALECT)


def _pivot_sums() -> str:
    """Per-quarter SUM(source_col) AS label, derived from QUARTERLY_PERIODS.

    agg="last" balances are end-of-period snapshots; the SUM here aggregates across
    the *dimensional* grain (matching create_quarterly_pivot's pivot_table sum),
    NOT across months. Each quarter maps to exactly one source column.
    """
    parts = []
    for period in QUARTERLY_PERIODS:
        src = period.get("source_col") or period["source_cols"][-1]  # agg="last"
        sum_expr = exp.func("SUM", _col(src))
        aliased = exp.alias_(sum_expr, period["label"], quoted=True)
        parts.append(aliased.sql(dialect=DIALECT))
    return ",\n        ".join(parts)


def _unpivot_pairs() -> str:
    """VALUES rows ('Mar', m3_label), ... for the CROSS JOIN LATERAL unpivot.

    References the per-quarter aggregated columns produced by _pivot_sums (aliased
    by label, e.g. "Mar"), pairing each quarter label with its summed balance.
    """
    rows = []
    for period in QUARTERLY_PERIODS:
        label = period["label"]
        tup = exp.Tuple(expressions=[
            exp.Literal.string(label),
            _col(label),
        ])
        rows.append(tup.sql(dialect=DIALECT))
    return ", ".join(rows)


def _quarter_pivot_cols() -> str:
    """Conditional-aggregation quarter columns for the upload pivot.

    SUM(CASE WHEN quarter_id = N THEN rwa END) AS "qN" for N in 0..N_PROJECTED.
    Quarter 0 is the RWA Actuals bucket.
    """
    parts = []
    for q in range(0, N_PROJECTED_QUARTERS + 1):
        case_expr = exp.func(
            "SUM",
            exp.Case(
                ifs=[exp.If(
                    this=exp.EQ(this=_col("quarter_id"),
                                expression=exp.Literal.number(q)),
                    true=_col("rwa"),
                )],
            ),
        )
        alias = "rwa_actuals" if q == 0 else f"q{q}"
        parts.append(exp.alias_(case_expr, alias, quoted=True).sql(dialect=DIALECT))
    return ",\n    ".join(parts)


def build_substitutions() -> dict[str, str]:
    """Build every {{ token }} -> SQL fragment, all via SQLGlot AST."""
    return {
        "pmf_in": _in_predicate('"Finance PMF Level 5 Description"', PMF_ACCOUNTS),
        "markets_eq": _eq_predicate('"Managed Segment Level 2 Description"', MARKETS_L2),
        # Same rule against the aliased column name (seg_l2_desc) used downstream.
        "markets_eq_alias": _eq_predicate("seg_l2_desc", MARKETS_L2),
        "discontinued_ne": _ne_predicate('"Managed Segment Level 2 Description"',
                                         DISCONTINUED_OPS_L2),
        "non_credit_pmf_in": _in_predicate("pmf_l5", NON_CREDIT_RISK_PMF),
        "erba_quarters_in": _quarter_in_predicate("quarter_id", ERBA_QUARTERS),
        "rwf_cap": str(RWF_ABS_CAP),
        "pivot_sums": _pivot_sums(),
        "unpivot_pairs": _unpivot_pairs(),
        "quarter_pivot_cols": _quarter_pivot_cols(),
    }


def render(template_text: str, subs: dict[str, str]) -> str:
    """Substitute {{ token }} placeholders with their pre-built SQL fragments."""
    out = template_text
    for token, fragment in subs.items():
        out = out.replace("{{ " + token + " }}", fragment)
    if "{{" in out:
        # Surface any unfilled placeholder loudly rather than emitting broken SQL.
        raise ValueError(f"Unfilled placeholder remains in rendered SQL: {out[out.index('{{'):][:80]}")
    return out


def main() -> int:
    """Render all templates to sql/*.sql and validate they parse for DuckDB."""
    subs = build_substitutions()
    templates = sorted(TEMPLATE_DIR.glob("*.sql.j2"))
    if not templates:
        print(f"No templates found in {TEMPLATE_DIR}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "-- GENERATED FILE — do not edit by hand.\n"
        "-- Rendered from sql/templates/{name} by scripts/generate_sql.py.\n"
        "-- Business-rule values are injected from transforms.py / constants.py via SQLGlot.\n"
        "-- Re-run: python scripts/generate_sql.py  (or scripts/update.sh)\n\n"
    )

    written = []
    for tpl in templates:
        rendered = render(tpl.read_text(encoding="utf-8"), subs)
        # Validate each statement parses for the DuckDB dialect.
        for statement in sqlglot.parse(rendered, dialect=DIALECT):
            if statement is None:
                continue
        out_name = tpl.name[: -len(".j2")]  # strip .j2 -> keep .sql
        out_path = OUT_DIR / out_name
        out_path.write_text(header.format(name=tpl.name) + rendered, encoding="utf-8")
        written.append(out_path.name)
        print(f"  rendered {tpl.name} -> sql/{out_path.name}")

    print(f"\nGenerated {len(written)} SQL file(s) in {OUT_DIR} (dialect={DIALECT}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
