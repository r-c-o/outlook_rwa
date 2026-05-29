"""Oracle DDL generation + column-mapping logs (offline, no DB connection).

Infers Oracle column types from a pandas DataFrame, sanitizes column names to
Oracle identifier rules, emits CREATE TABLE DDL, and writes an auditable
column-mapping CSV per table. None of this requires an Oracle connection — it is
exercised offline so the schema decisions are reviewable without live Oracle.

Type inference (per docs/DECISION_LOG.md):
    int64       -> NUMBER(18,0)
    float64     -> FLOAT
    bool        -> CHAR(1)
    datetime64  -> TIMESTAMP(6)
    object/str  -> VARCHAR2(min(max_observed_len + 10, 4000))

The loader (load_oracle.py) imports these helpers; oracledb itself is imported
lazily there, so this module stays importable in environments without Oracle.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ORACLE_MAX_IDENTIFIER = 30
ORACLE_MAX_VARCHAR2 = 4000
VARCHAR2_PADDING = 10


def sanitize_oracle_name(name: str, max_len: int = ORACLE_MAX_IDENTIFIER) -> str:
    """Map an arbitrary column name to a legal, uppercase Oracle identifier.

    Rules enforced: only alphanumerics + underscore, no leading digit, length
    capped at `max_len` (Oracle pre-12.2 default of 30), never empty.
    """
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(name))[:max_len]
    if s and s[0].isdigit():
        s = ("COL_" + s)[:max_len]
    return (s or "COL").upper()


def infer_oracle_type(series: pd.Series) -> tuple[str, dict]:
    """Infer an Oracle column type from a pandas Series dtype + observed data.

    Returns (oracle_type, metadata) where metadata records the observed max
    string length for VARCHAR2 columns (empty dict otherwise).
    """
    dtype = series.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "CHAR(1)", {}
    if pd.api.types.is_integer_dtype(dtype):
        return "NUMBER(18,0)", {}
    if pd.api.types.is_float_dtype(dtype):
        return "FLOAT", {}
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP(6)", {}
    # object / string: size VARCHAR2 from the observed data, padded and capped.
    non_null = series.dropna().astype(str)
    observed = int(non_null.str.len().max()) if not non_null.empty else 1
    width = min(observed + VARCHAR2_PADDING, ORACLE_MAX_VARCHAR2)
    return f"VARCHAR2({width})", {"max_observed_length": observed}


def build_mapping(df: pd.DataFrame) -> dict[str, tuple[str, str, dict]]:
    """Return {original_col: (oracle_name, oracle_type, metadata)} for a frame.

    Disambiguates collisions: if two source columns sanitize to the same Oracle
    name, later ones get a numeric suffix so the DDL stays valid.
    """
    mapping: dict[str, tuple[str, str, dict]] = {}
    used: set[str] = set()
    for col in df.columns:
        base = sanitize_oracle_name(col)
        name = base
        n = 1
        while name in used:
            suffix = f"_{n}"
            name = base[: ORACLE_MAX_IDENTIFIER - len(suffix)] + suffix
            n += 1
        used.add(name)
        otype, meta = infer_oracle_type(df[col])
        mapping[col] = (name, otype, meta)
    return mapping


def write_mapping_log(mapping: dict[str, tuple[str, str, dict]], path: str | Path) -> Path:
    """Persist the column mapping as an audit CSV (original -> oracle name/type)."""
    rows = [
        {
            "original": orig,
            "oracle_name": name,
            "dtype": None,  # filled by caller-friendly variant below if needed
            "oracle_type": otype,
            "max_observed_length": meta.get("max_observed_length", ""),
        }
        for orig, (name, otype, meta) in mapping.items()
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def write_mapping_log_with_dtypes(df: pd.DataFrame,
                                  mapping: dict[str, tuple[str, str, dict]],
                                  path: str | Path) -> Path:
    """Like write_mapping_log but records the pandas dtype too (full audit row)."""
    rows = [
        {
            "original": orig,
            "oracle_name": name,
            "dtype": str(df[orig].dtype),
            "oracle_type": otype,
            "max_observed_length": meta.get("max_observed_length", ""),
        }
        for orig, (name, otype, meta) in mapping.items()
    ]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def generate_ddl(df: pd.DataFrame, table_name: str, schema: str | None = None,
                 mapping: dict | None = None) -> str:
    """Generate a CREATE TABLE statement for `df` using the inferred mapping."""
    mapping = mapping or build_mapping(df)
    qualified = f"{schema}.{table_name}" if schema else table_name
    cols = ",\n".join(f"    {name} {otype}" for (name, otype, _meta) in mapping.values())
    return f"CREATE TABLE {qualified} (\n{cols}\n)"


def create_table_if_not_exists(cursor, schema: str, table_name: str, ddl: str) -> bool:
    """Create the table only if ALL_TABLES has no matching row (Oracle idiom).

    Oracle has no CREATE TABLE IF NOT EXISTS, so existence is checked against the
    ALL_TABLES data-dictionary view. Returns True if the table was created.
    """
    cursor.execute(
        "SELECT COUNT(*) FROM ALL_TABLES WHERE OWNER = :owner AND TABLE_NAME = :tname",
        {"owner": schema.upper(), "tname": table_name.upper()},
    )
    if cursor.fetchone()[0] == 0:
        cursor.execute(ddl)
        cursor.connection.commit()
        return True
    return False


def bulk_insert(cursor, df: pd.DataFrame, table_name: str,
                mapping: dict[str, tuple[str, str, dict]],
                batch_size: int = 1000) -> None:
    """Bulk-load a DataFrame via executemany with batch errors enabled.

    Uses positional bind variables (:1, :2, ...) and batcherrors=True so a bad
    row is reported individually instead of aborting the whole batch.
    """
    oracle_cols = [name for (name, _t, _m) in mapping.values()]
    placeholders = ", ".join(f":{i + 1}" for i in range(len(oracle_cols)))
    col_list = ", ".join(oracle_cols)
    sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
    # Convert NaN/NaT to None so Oracle binds NULL.
    safe = df.where(pd.notnull(df), None)
    data = [tuple(row) for row in safe.itertuples(index=False, name=None)]
    cursor.executemany(sql, data, batcherrors=True, batch_size=batch_size)
    for err in cursor.getbatcherrors():
        print(f"  row {err.offset}: {err.message}")
    cursor.connection.commit()
