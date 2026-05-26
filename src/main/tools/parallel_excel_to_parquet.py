import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

import numpy as np
import polars as pl
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed


@dataclass(frozen=True)
class ExcelInputSpec:
    label: str
    path: Path
    schema_key: str
    output_name: str
    sheet_name: int | str | None = 0


def make_input_specs(input_dir: Path) -> Dict[str, ExcelInputSpec]:
    """Source Excel specs shared by step1 and step2, keyed by label.

    step2 reuses the convergence and adjustments specs from here so the two
    pipeline stages reference one definition of each source file rather than
    duplicating paths/schemas. The parquet cache directory is supplied
    separately by each caller (step1 caches in its output dir, step2 in
    input_dir / model_convergence_dir).
    """
    input_dir = Path(input_dir)
    return {
        "cg":               ExcelInputSpec("cg", input_dir / "outlook_balancesheet_cg.xlsx", "balancesheet", "outlook_balancesheet_cg.parquet"),
        "cbna":             ExcelInputSpec("cbna", input_dir / "outlook_balancesheet_cbna.xlsx", "balancesheet", "outlook_balancesheet_cbna.parquet"),
        "convergence":      ExcelInputSpec("convergence", input_dir / "aggregator_for_convergence.xlsx", "convergence", "aggregator_for_convergence.parquet"),
        "cg_adjustments":   ExcelInputSpec("cg_adjustments", input_dir / "adjustment_master_file.xlsx", "adjustments", "adjustments_cg.parquet", "Adjustments - CG"),
        "cbna_adjustments": ExcelInputSpec("cbna_adjustments", input_dir / "adjustment_master_file.xlsx", "adjustments", "adjustments_cbna.parquet", "Adjustments - CBNA"),
    }


# =============================================================================
# LOAD SCHEMA REGISTRY FROM CSV
# =============================================================================

def load_schema_registry_from_csv(csv_path: Path) -> Dict[str, dict]:
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
    elif isinstance(spec.sheet_name, str):
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
    """
    out_path = Path(parquet_dir) / spec.output_name
    df = None
    if out_path.exists():
        try:
            df = pd.read_parquet(out_path)
        except Exception as e:
            print(f"⚠ parquet read failed for {spec.output_name} ({e}); reading Excel")
    else:
        try:
            _convert_spec_to_parquet(spec, registry, Path(parquet_dir))
            df = pd.read_parquet(out_path)
        except Exception as e:
            print(f"⚠ parquet build failed for {spec.output_name} ({e}); reading Excel")

    if df is None:
        pandas_sheet = 0 if spec.sheet_name is None else spec.sheet_name
        df = pd.read_excel(spec.path, sheet_name=pandas_sheet)

    # Mimic pd.read_excel null semantics so the parquet and Excel paths are
    # interchangeable: Excel reads empty cells as NaN, whereas parquet preserves
    # them as empty strings. Without this, an empty-string key column survives
    # group-bys (e.g. pivot_table) that would otherwise drop NaN-keyed rows.
    return df.replace("", np.nan)


def convert_files_to_parquet(
    specs: List[ExcelInputSpec],
    output_dir: Path,
    registry_csv: Path,
    max_workers: int | None = None,
) -> None:
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
