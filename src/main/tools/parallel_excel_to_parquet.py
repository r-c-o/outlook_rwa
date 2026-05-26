import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Any

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
