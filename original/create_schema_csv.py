import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from pathlib import Path
import pandas as pd
import argparse
from constants import (
    balancesheet_polars_dtypes,
    convergence_polars_dtypes,
)


def build_schema_csv(output_path: Path):
    rows = []
    for schema_key, schema_dict in {
        "balancesheet": balancesheet_polars_dtypes,
        "convergence":  convergence_polars_dtypes,
    }.items():
        for col, dtype in schema_dict.items():
            rows.append({
                "schema_key":   schema_key,
                "column_name":  col,
                "polars_dtype": dtype.__name__,
            })
    df = pd.DataFrame(rows)
    df.to_csv(output_path, index=False)
    print(f"✅ Schema CSV written: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create schema CSV for balancesheet and convergence."
    )
    parser.add_argument(
        "--output",
        type=str,
        default="schema_registry.csv",
        help="Output CSV file path (default: schema_registry.csv)",
    )
    args = parser.parse_args()
    build_schema_csv(Path(args.output))
