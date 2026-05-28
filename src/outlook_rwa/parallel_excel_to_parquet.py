"""Excel file reading and schema inference for Outlook RWA pipeline.

Handles concurrent Excel file reading, Polars schema inference and type coercion,
parquet caching, and bulk data loading into pandas DataFrames with Oracle-compatible
types.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
import pandas as pd
import polars as pl

from .constants import POLARS_PANDAS_DTYPE_COMPAT


@dataclass(frozen=True)
class ExcelInputSpec:
    """Defines one Excel source: where to read it, which sheet, and what to name the output."""

    label: str
    path: Path
    schema_key: str
    output_name: str
    sheet_name: int | str | None = 0


def make_input_specs(input_dir: Path) -> Dict[str, ExcelInputSpec]:
    """Return source Excel specs shared by step1 and step2, keyed by label.

    step2 reuses the convergence and adjustments specs from here so the two
    pipeline stages reference one definition of each source file rather than
    duplicating paths/schemas. The parquet cache directory is supplied
    separately by each caller (step1 caches in its output dir, step2 in
    input_dir / model_convergence_dir).

    Args:
        input_dir: Path to the directory containing the source Excel files.

    Returns:
        Dict mapping label strings ('cg', 'cbna', 'convergence',
        'cg_adjustments', 'cbna_adjustments') to their ExcelInputSpec definitions.
    """
    input_dir = Path(input_dir)
    return {
        "cg": ExcelInputSpec(
            "cg", input_dir / "outlook_balancesheet_cg.xlsx",
            "balancesheet", "outlook_balancesheet_cg.parquet",
        ),
        "cbna": ExcelInputSpec(
            "cbna", input_dir / "outlook_balancesheet_cbna.xlsx",
            "balancesheet", "outlook_balancesheet_cbna.parquet",
        ),
        "convergence": ExcelInputSpec(
            "convergence", input_dir / "aggregator_for_convergence.xlsx",
            "convergence", "aggregator_for_convergence.parquet",
        ),
        "cg_adjustments": ExcelInputSpec(
            "cg_adjustments", input_dir / "adjustment_master_file.xlsx",
            "adjustments", "adjustments_cg.parquet", "Adjustments - CG",
        ),
        "cbna_adjustments": ExcelInputSpec(
            "cbna_adjustments", input_dir / "adjustment_master_file.xlsx",
            "adjustments", "adjustments_cbna.parquet", "Adjustments - CBNA",
        ),
    }


# =============================================================================
# LOAD SCHEMA REGISTRY FROM CSV
# =============================================================================

def load_schema_registry_from_csv(csv_path: Path) -> Dict[str, dict]:
    """Load the schema registry CSV into a nested dict keyed by schema_key.

    Args:
        csv_path: Path to the schema_registry.csv file with columns schema_key,
            column_name, and polars_dtype.

    Returns:
        Dict mapping schema_key -> {column_name: polars dtype type} for use as
        schema_overrides when reading Excel files with Polars.

    Raises:
        FileNotFoundError: If csv_path does not exist.
    """
    df = pd.read_csv(csv_path)
    registry = {}
    for schema_key, g in df.groupby("schema_key"):
        schema_map = {}
        for _, row in g.iterrows():
            schema_map[row["column_name"]] = getattr(pl, row["polars_dtype"])
        registry[schema_key] = schema_map
    return registry


def _convert_spec_to_parquet(
    spec: ExcelInputSpec,
    registry: Dict[str, dict],
    output_dir: Path,
) -> str:
    out_path = output_dir / spec.output_name
    if out_path.exists():
        return f"⏭  Skipped (exists): {spec.output_name}"

    schema_map = registry.get(spec.schema_key, {})

    # polars selects sheets by name (str) or 1-based sheet_id (int); the spec's
    # int sheet_name is a 0-based positional index, None means the first sheet.
    if isinstance(spec.sheet_name, bool):
        raise TypeError(f"sheet_name must be str/int/None, got bool for {spec.output_name}")
    if isinstance(spec.sheet_name, str):
        sheet_kwargs = {"sheet_name": spec.sheet_name}
    elif isinstance(spec.sheet_name, int):
        sheet_kwargs = {"sheet_id": spec.sheet_name + 1}
    else:
        sheet_kwargs = {}

    # Only override dtypes for columns actually present; polars raises if
    # schema_overrides references a column missing from the sheet.
    pandas_sheet = 0 if spec.sheet_name is None else spec.sheet_name
    present = pd.read_excel(spec.path, sheet_name=pandas_sheet, nrows=0).columns
    dtype_map = {col: dtype for col, dtype in schema_map.items() if col in present}

    df = pl.read_excel(
        spec.path,
        schema_overrides=dtype_map,
        **sheet_kwargs,
    )
    df.write_parquet(out_path, compression="zstd")
    return f"✅ Written: {spec.output_name}  ({len(df):,} rows)"


def load_spec_with_fallback(
    spec: ExcelInputSpec,
    parquet_dir: Path,
    registry: Dict[str, dict],
) -> pd.DataFrame:
    """Load one dataset, preferring parquet over Excel.

    Tier 1: read an existing parquet at parquet_dir/spec.output_name.
    Tier 2: if absent, build it from the source Excel (polars), then read it.
    Tier 3: if either parquet path fails, read the source Excel directly.

    The if/else split matters: _convert_spec_to_parquet skips writing when the
    parquet already exists, so a corrupt existing parquet falls through to the
    Excel read rather than a no-op rebuild.

    Args:
        spec: ExcelInputSpec describing the source file, sheet, schema key, and
            target parquet filename.
        parquet_dir: Directory where the parquet cache file is stored or will be
            written.
        registry: Schema registry dict as returned by load_schema_registry_from_csv,
            used for dtype overrides when building the parquet.

    Returns:
        pandas DataFrame with empty strings replaced by NaN (via normalize_nulls).
    """
    out_path = Path(parquet_dir) / spec.output_name
    df = None
    if out_path.exists():
        try:
            df = pd.read_parquet(out_path)
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"⚠ parquet read failed for {spec.output_name} ({e}); reading Excel")
    else:
        try:
            _convert_spec_to_parquet(spec, registry, Path(parquet_dir))
            df = pd.read_parquet(out_path)
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"⚠ parquet build failed for {spec.output_name} ({e}); reading Excel")

    if df is None:
        pandas_sheet = 0 if spec.sheet_name is None else spec.sheet_name
        df = pd.read_excel(spec.path, sheet_name=pandas_sheet)

    return normalize_nulls(df)


def normalize_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """Mimic pd.read_excel null semantics: treat empty strings as NaN.

    Excel reads empty cells as NaN, whereas parquet preserves them as empty
    strings. Applying this makes the parquet load, the Excel load, and an
    in-memory handoff interchangeable. Without it, an empty-string key column
    survives group-bys (e.g. pivot_table) that would otherwise drop NaN-keyed
    rows.

    Args:
        df: DataFrame whose empty-string cells should be replaced with NaN.

    Returns:
        New DataFrame with all empty string values replaced by NaN.
    """
    return df.replace("", np.nan)


def _flat_schema_from_registry(registry: Dict[str, dict]) -> Dict[str, Any]:
    """Flatten the schema registry to {column: numpy dtype}, mapping polars dtype
    names through POLARS_PANDAS_DTYPE_COMPAT to pandas-compatible dtypes."""
    return {
        col: np.dtype(POLARS_PANDAS_DTYPE_COMPAT.get(str(dtype).lower(), str(dtype).lower()))
        for d in registry.values()
        for col, dtype in d.items()
    }


def load_specs_with_schema_cast(
    specs: List[ExcelInputSpec],
    parquet_dir: Path,
    schema_csv: Path,
) -> List[pd.DataFrame]:
    """Load specs parquet-first from parquet_dir, casting to the pandas dtypes the
    waterfall/RWA logic expects.

    Reads each spec's parquet from parquet_dir and casts its columns via the
    flattened schema registry. If any parquet is missing/unreadable, builds them
    all from Excel (parallel) and retries. This is the loader the model-convergence
    stage relies on (distinct from load_spec_with_fallback, which does not cast
    dtypes), so it is kept separate to preserve that stage's numeric behavior.

    Args:
        specs: List of ExcelInputSpec objects describing all files to load.
        parquet_dir: Directory containing (or to receive) the parquet cache files.
        schema_csv: Path to schema_registry.csv used for dtype resolution and
            parquet generation when cache files are absent.

    Returns:
        List of pandas DataFrames in the same order as specs, with columns cast
        to the dtypes specified in the schema registry.
    """
    parquet_dir = Path(parquet_dir)
    registry = load_schema_registry_from_csv(schema_csv)
    flat_schema = _flat_schema_from_registry(registry)

    def _load(spec):
        df = pd.read_parquet(parquet_dir / spec.output_name)
        return df.astype(
            {c: flat_schema[c] for c in df.columns if c in flat_schema}, errors="ignore"
        )

    try:
        return [_load(s) for s in specs]
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"Error reading parquet files: {e}")
        print("Parquet files missing — building them from Excel via the parallel loader...")
        convert_files_to_parquet(specs, parquet_dir, schema_csv)
        return [_load(s) for s in specs]


def convert_files_to_parquet(
    specs: List[ExcelInputSpec],
    output_dir: Path,
    registry_csv: Path,
    max_workers: int | None = None,
) -> None:
    """Convert a list of Excel specs to zstd-compressed parquet files in parallel.

    Skips files whose parquet already exists in output_dir. Prints a status line
    per file on completion.

    Args:
        specs: List of ExcelInputSpec objects describing the Excel files to convert.
        output_dir: Directory where parquet files are written; created if absent.
        registry_csv: Path to schema_registry.csv used to load dtype overrides
            for each spec's schema_key.
        max_workers: Maximum number of threads to use; defaults to len(specs)
            when None.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    registry = load_schema_registry_from_csv(registry_csv)

    effective_workers = max_workers or len(specs)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = {
            executor.submit(_convert_spec_to_parquet, spec, registry, output_dir): spec.output_name
            for spec in specs
        }
        for future in as_completed(futures):
            result = future.result()
            print(result)


def _write_dataframe(df: pd.DataFrame, path: Path) -> str:
    if path.suffix == ".parquet":
        df.to_parquet(path, compression="zstd", index=False)
    else:
        df.to_excel(path, index=False)
    return f"✅ Written: {path.name}  ({len(df):,} rows)"


def export_outputs(
    outputs: Dict[str, pd.DataFrame],
    output_dir: Path,
    formats=("xlsx", "parquet"),
    max_workers: int | None = None,
) -> None:
    """Write each {name: DataFrame} to output_dir in every requested format, in parallel.

    `name` may carry an extension (e.g. 'cg_outlook.xlsx'); it is replaced by each
    format's extension, so the same data is written as both .xlsx and .parquet.

    Args:
        outputs: Dict mapping output filename (stem or with extension) to the
            DataFrame to write.
        output_dir: Directory where output files are written; created if absent.
        formats: Iterable of format strings ('xlsx', 'parquet') controlling which
            file types are written for each DataFrame.
        max_workers: Maximum number of threads to use; defaults to the total
            number of write tasks when None.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        (df, output_dir / f"{Path(name).stem}.{ext.lstrip('.')}")
        for name, df in outputs.items()
        for ext in formats
    ]
    effective_workers = max_workers or len(tasks)
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = [executor.submit(_write_dataframe, df, path) for df, path in tasks]
        for future in as_completed(futures):
            print(future.result())
