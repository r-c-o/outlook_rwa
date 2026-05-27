"""Compare two Outlook RWA output files column by column and report invariance.

Aligns rows by a key (the RWA output files are not row-order stable across runs),
then for every shared column reports:
  * row-level invariance  -- are all key-aligned values equal? (numeric uses a
    tolerance; NaN==NaN counts as equal)
  * aggregate total       -- for numeric columns, sum(A) vs sum(B) and the delta,
    computed over the full files (independent of row matching)

It also reports schema differences (columns only on one side) and row-set
differences (keys only on one side).

Usage:
    python compare_outputs.py FILE_A FILE_B [--key COL ...] [--atol X] [--rtol X]
    python compare_outputs.py a.xlsx b.parquet --key "Quarter Id" --atol 0.01

Supports .parquet, .xlsx/.xls and .csv. If --key is omitted, a known RWA key is
auto-detected. Exit code is 0 when fully invariant, 1 when any difference is
found, 2 on a usage/setup error.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Known key column sets for the RWA output files, tried in order for
# auto-detection when --key is not supplied. The first set whose columns are all
# present in BOTH files wins.
KEY_CANDIDATES = [
    # step1 outlook files (cg_outlook / cbna_outlook)
    ["Managed Segment L4 Id", "Managed Geography L4 Descr", "PMF Account L5 Descr", "Quarter Id"],
    # step1 addon files (no L4 Id)
    ["Managed Segment L4 Descr", "Managed Geography L4 Descr", "PMF Account L5 Descr", "Quarter Id"],
    # step2 upload templates (pivoted by segment / RWA calc)
    ["Reporting Layer", "Managed Segment L2 Descr", "Managed Segment L3 Descr", "RWA Calc", "PMF Account L5 Descr"],
]


def read_table(path: Path, sheet) -> pd.DataFrame:
    """Load a tabular file by extension (.parquet / .xlsx / .xls / .csv)."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet if sheet is not None else 0)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported file type: {path.suffix} ({path})")


def detect_key(cols_a, cols_b, user_key):
    """Return the key column list to join on, or raise with guidance."""
    if user_key:
        missing_a = [c for c in user_key if c not in cols_a]
        missing_b = [c for c in user_key if c not in cols_b]
        if missing_a or missing_b:
            raise ValueError(
                f"Key column(s) missing -- in A: {missing_a or 'none'}; "
                f"in B: {missing_b or 'none'}"
            )
        return list(user_key)

    for candidate in KEY_CANDIDATES:
        if all(c in cols_a for c in candidate) and all(c in cols_b for c in candidate):
            return list(candidate)

    raise ValueError(
        "Could not auto-detect a key present in both files. "
        "Pass --key explicitly. Tried:\n  "
        + "\n  ".join(", ".join(c) for c in KEY_CANDIDATES)
    )


def normalize_key(df: pd.DataFrame, key) -> pd.DataFrame:
    """Cast key columns to a clean string so cross-format dtypes join cleanly
    (e.g. Quarter Id 4 vs 4.0 vs '4')."""
    out = df.copy()
    for col in key:
        s = out[col]
        if pd.api.types.is_float_dtype(s) or pd.api.types.is_integer_dtype(s):
            # render whole-number floats as ints: 4.0 -> "4"
            out[col] = s.map(lambda x: "" if pd.isna(x) else str(int(x)) if float(x).is_integer() else str(x))
        else:
            out[col] = s.astype(str).str.strip()
    return out


def _blank_mask(s: pd.Series):
    """True where a value is null or an empty/whitespace-only string. Excel reads
    a written "" back as NaN, so blank and null are treated as the same value."""
    na = s.isna().to_numpy()
    blank = s.astype(str).str.strip().eq("").to_numpy()
    return na | blank


def column_equal_mask(a: pd.Series, b: pd.Series, atol: float, rtol: float):
    """Element-wise equality. Numeric columns use a tolerance; NaN==NaN is True.
    For text columns, blank ("") and null are treated as equal."""
    numeric = pd.api.types.is_numeric_dtype(a) and pd.api.types.is_numeric_dtype(b)
    if numeric:
        return np.isclose(
            a.astype(float), b.astype(float), atol=atol, rtol=rtol, equal_nan=True
        )
    both_blank = _blank_mask(a) & _blank_mask(b)
    return (a.to_numpy() == b.to_numpy()) | both_blank


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare two Outlook RWA output files column by column."
    )
    parser.add_argument("file_a", type=Path, help="baseline / expected file")
    parser.add_argument("file_b", type=Path, help="candidate / actual file")
    parser.add_argument(
        "--key", action="append", default=None,
        help="key column to join on (repeatable). If omitted, auto-detected.",
    )
    parser.add_argument("--atol", type=float, default=1e-6, help="absolute tolerance for numeric columns")
    parser.add_argument("--rtol", type=float, default=1e-5, help="relative tolerance for numeric columns")
    parser.add_argument("--sheet", default=None, help="Excel sheet name/index (default first sheet)")
    parser.add_argument("--max-samples", type=int, default=10, help="sample differing rows to show per column")
    args = parser.parse_args(argv)

    for f in (args.file_a, args.file_b):
        if not f.exists():
            print(f"ERROR: file not found: {f}", file=sys.stderr)
            return 2

    try:
        df_a = read_table(args.file_a, args.sheet)
        df_b = read_table(args.file_b, args.sheet)
        key = detect_key(df_a.columns, df_b.columns, args.key)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("=" * 78)
    print("OUTLOOK RWA OUTPUT COMPARISON")
    print("=" * 78)
    print(f"A: {args.file_a}  shape={df_a.shape}")
    print(f"B: {args.file_b}  shape={df_b.shape}")
    print(f"Key: {key}")
    print(f"Numeric tolerance: atol={args.atol}, rtol={args.rtol}")

    differences = False

    # --- schema diff -------------------------------------------------------
    cols_a, cols_b = list(df_a.columns), list(df_b.columns)
    only_a = [c for c in cols_a if c not in cols_b]
    only_b = [c for c in cols_b if c not in cols_a]
    common = [c for c in cols_a if c in cols_b]
    compare_cols = [c for c in common if c not in key]

    print("\n" + "-" * 78)
    print("SCHEMA")
    print("-" * 78)
    print(f"common columns: {len(common)}  |  compared (non-key): {len(compare_cols)}")
    if only_a:
        differences = True
        print(f"columns ONLY in A ({len(only_a)}): {only_a}")
    if only_b:
        differences = True
        print(f"columns ONLY in B ({len(only_b)}): {only_b}")
    if not only_a and not only_b:
        print("columns: identical set")

    # --- row alignment -----------------------------------------------------
    ka = normalize_key(df_a, key)
    kb = normalize_key(df_b, key)

    dup_a = int(ka.duplicated(subset=key).sum())
    dup_b = int(kb.duplicated(subset=key).sum())
    if dup_a or dup_b:
        print("\n" + "-" * 78)
        print("ERROR: key is not unique -- cannot align rows reliably")
        print("-" * 78)
        print(f"duplicate key rows: A={dup_a}, B={dup_b}")
        print("Add more --key columns so each row is uniquely identified.")
        return 2

    merged = ka.merge(
        kb, on=key, how="outer", suffixes=("__a", "__b"), indicator=True
    )
    both = merged[merged["_merge"] == "both"]
    left_only = merged[merged["_merge"] == "left_only"]
    right_only = merged[merged["_merge"] == "right_only"]

    print("\n" + "-" * 78)
    print("ROW ALIGNMENT (outer join on key)")
    print("-" * 78)
    print(f"matched rows: {len(both)}")
    print(f"rows ONLY in A: {len(left_only)}")
    print(f"rows ONLY in B: {len(right_only)}")
    if len(left_only):
        differences = True
        print("  sample keys only in A:")
        print(left_only[key].head(args.max_samples).to_string(index=False))
    if len(right_only):
        differences = True
        print("  sample keys only in B:")
        print(right_only[key].head(args.max_samples).to_string(index=False))

    # --- per-column row-level invariance + aggregate totals ----------------
    print("\n" + "-" * 78)
    print("PER-COLUMN INVARIANCE")
    print("-" * 78)
    header = f"{'column':<42} {'row-level':>10} {'#diffs':>8} {'sum(A)':>16} {'sum(B)':>16} {'delta':>16}"
    print(header)
    print("-" * len(header))

    failed_cols = []
    for col in compare_cols:
        a = both[f"{col}__a"]
        b = both[f"{col}__b"]
        eq = column_equal_mask(a, b, args.atol, args.rtol)
        n_diff = int((~eq).sum())
        row_status = "PASS" if n_diff == 0 else "FAIL"
        if n_diff:
            differences = True
            failed_cols.append((col, ~eq))

        numeric = pd.api.types.is_numeric_dtype(df_a[col]) and pd.api.types.is_numeric_dtype(df_b[col])
        if numeric:
            sum_a = float(df_a[col].sum())
            sum_b = float(df_b[col].sum())
            delta = sum_b - sum_a
            if not np.isclose(sum_a, sum_b, atol=args.atol, rtol=args.rtol):
                differences = True
            print(f"{col:<42} {row_status:>10} {n_diff:>8} {sum_a:>16,.2f} {sum_b:>16,.2f} {delta:>16,.2f}")
        else:
            print(f"{col:<42} {row_status:>10} {n_diff:>8} {'-':>16} {'-':>16} {'-':>16}")

    # --- detail for failing columns ---------------------------------------
    if failed_cols:
        print("\n" + "-" * 78)
        print("DIFFERING ROWS (sample)")
        print("-" * 78)
        for col, diff_mask in failed_cols:
            sample = both[diff_mask].head(args.max_samples)
            print(f"\n[{col}] {int(diff_mask.sum())} differing row(s):")
            view = sample[key + [f"{col}__a", f"{col}__b"]].rename(
                columns={f"{col}__a": "A", f"{col}__b": "B"}
            )
            print(view.to_string(index=False))

    # --- summary -----------------------------------------------------------
    print("\n" + "=" * 78)
    if differences:
        print("RESULT: DIFFERENCES FOUND -- files are NOT invariant")
    else:
        print("RESULT: INVARIANT -- all compared columns match")
    print("=" * 78)
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
