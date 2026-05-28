# Outlook RWA Pipeline Architecture

## Overview

The Outlook RWA pipeline is a two-stage financial data processing system that computes Risk-Weighted Assets (RWA) for Outlook scenarios combined with model convergence analysis. The architecture emphasizes data quality, performance through in-process memory sharing, and comprehensive validation.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Input Data Layer                              │
│  (Excel files in data/ directory with mappings in config.yaml)   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              Stage 1: Model Convergence                          │
│  ├─ Load convergence baseline data                              │
│  ├─ Build 5-key RWF waterfall                                   │
│  ├─ Compute SA/AA/ERBA RWA values                               │
│  ├─ Validate convergence schema and entity flags                │
│  └─ Output: convergence DataFrame (kept in memory)              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼ (in-memory handoff)
┌─────────────────────────────────────────────────────────────────┐
│              Stage 2: Outlook RWA Computation                    │
│  ├─ Load PUG/PMF mappings & adjustments                         │
│  ├─ Join convergence data (Stage 1 output)                      │
│  ├─ Join CG/CBNA adjustments                                    │
│  ├─ Join FRM output data                                        │
│  ├─ Run data quality checks (before formatting)                 │
│  ├─ Format columns for pivot operations                         │
│  ├─ Generate CG/CBNA upload templates                           │
│  └─ Output: Excel & Parquet files                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              Output Layer                                        │
│  ├─ dq_results.xlsx / dq_results.parquet (DQ report)           │
│  ├─ cg_upload_template.xlsx (CG template)                      │
│  ├─ cbna_upload_template.xlsx (CBNA template)                  │
│  └─ Additional parquet files for intermediate results           │
└─────────────────────────────────────────────────────────────────┘
```

## Module Organization

### pipeline.py (Main Orchestration)
Coordinates the two-stage pipeline:
- Loads configuration from `config/config.yaml`
- Manages Stage 1 (convergence) execution
- Manages Stage 2 (outlook RWA) execution
- Handles data loading via `parallel_excel_to_parquet.py`
- Calls DQ checks and exports reports

**Entry Point:** `main()` function — can be invoked as `python -m outlook_rwa.pipeline`

### functions.py (Core Computation)
Contains reusable business logic:
- RWA calculation functions (SA, AA, ERBA methods)
- Waterfall key computation for 5-key RWF
- Data transformation and pivot operations
- Waterfall-level matching and fallthrough calculations
- Column formatting utilities

### dq.py (Data Quality Checks)
Validates data integrity across all stages:
- **Row count checks:** Verify expected row counts per dataset
- **Schema completeness:** Ensure all required columns present
- **Entity flag validation:** Check valid entity flag values
- **Null rate checks:** Flag key columns exceeding null thresholds
- **Join match rates:** Verify join success rates for PUG/PMF mappings
- **Quarter coverage:** Validate quarter values are expected
- **Waterfall level matching:** Check waterfall key match rates per level
- **Fallthrough rates:** Validate fallthrough percentages

Returns structured `DQResult` objects; bundles into DataFrame and exports as both Parquet and Excel.

### constants.py (Configuration & Enums)
Centralizes all constant values:
- Column name mappings
- Default configuration values
- Enum definitions for entity types, quarters, risk categories
- SQL query strings (if applicable)

### parallel_excel_to_parquet.py (Data I/O)
Handles Excel file reading and schema inference:
- Infers Oracle-compatible schemas from Excel files
- Coerces data types based on inferred schema
- Loads data into pandas DataFrames
- Supports bulk reading of multiple Excel sheets

The inferred schema is materialized at `src/outlook_rwa/schema_registry.csv`
(shipped as package data) and regenerated via `scripts/create_schema_csv.py`.

### \_\_init\_\_.py (Package Root)
Exposes public API for the package; allows imports like:
```python
from outlook_rwa import functions, dq, pipeline
```

## Data Quality Strategy

Data quality checks run **after Stage 2 joins** but **before format_columns_before_pivots()**, ensuring:
- Join misses are captured as real `NaN` values (not converted to string `'None'`)
- All post-join data is validated before formatting
- DQ results accurately reflect data state before output

**Checks cover 7 datasets:**
1. Convergence baseline
2. CG Outlook
3. CBNA Outlook
4. CG Adjustments
5. CBNA Adjustments
6. FRM Output (CG/CBNA)
7. Intermediate join results

## Configuration Management

All configuration is centralized in `config/config.yaml`:

```yaml
data_dir: data/
output_dir: output/
export_intermediate_xlsx: false

schema_inference:
  max_varchar_length: 2000
  date_formats:
    - "%Y-%m-%d"
    - "%m/%d/%Y"

mappings:
  - file: data/outlook.xlsx
    sheet: Outlook
    table: OUTLOOK_DATA
    skip_rows: 0
```

- **data_dir:** Path to input Excel files
- **output_dir:** Path for output artifacts
- **schema_inference:** Rules for type coercion
- **mappings:** Defines which Excel sheets map to which logical tables

## Performance Optimizations

1. **In-Process Memory Sharing:** Stage 1 output (convergence DataFrame) is passed directly to Stage 2 in memory, avoiding disk I/O.
2. **Vectorized Operations:** All computations use pandas/numpy vectorized operations, not loops.
3. **Lazy Loading:** Excel data is loaded only when needed; intermediate parquet files are used for subsequent runs.
4. **Parallel Excel Reading:** Uses multiple processes to read large Excel files concurrently (via `parallel_excel_to_parquet.py`).

## Error Handling & Validation

- **Input validation:** All external inputs (files, config, mappings) are validated at system boundaries.
- **DQ checks:** Comprehensive checks before output generation catch data quality issues.
- **Graceful logging:** All steps log progress; errors are caught with informative messages.
- **Exit codes:** Pipeline exits with status 0 (success) or 1 (failure) for automation integration.

## Testing Strategy

Tests are organized by module:
- **test_functions.py:** Unit tests for computation functions (edge cases, boundary conditions)
- **test_dq.py:** Unit tests for DQ check functions
- **test_integration.py:** End-to-end integration tests with sample data

**Coverage Target:** 80% minimum code coverage via `pytest --cov`.

## Deployment & Invocation

### Development
```bash
# Run full pipeline with current code
python -m outlook_rwa.pipeline

# Run tests
pytest test/

# Check coverage
pytest --cov=src/outlook_rwa test/

# Lint
pylint src/outlook_rwa/
```

### Production
Invoke as a CLI command:
```bash
outlook-rwa
```

This entry point is defined in `pyproject.toml` under `[project.scripts]`.

## Future Enhancements

- [ ] Parallel processing of CG and CBNA stages
- [ ] Incremental data loading (delta processing)
- [ ] Database backend integration (replace Excel I/O)
- [ ] REST API for remote invocation
- [ ] Structured logging (replace print statements)
