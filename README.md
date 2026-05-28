# Outlook RWA Pipeline

A comprehensive Python pipeline for computing Risk-Weighted Assets (RWA) for Outlook scenarios combined with model convergence analysis.

## Project Overview

This pipeline processes financial data through two integrated stages:

1. **Model Convergence**: Builds 5-key RWF waterfall and computes SA/AA/ERBA RWA
2. **Outlook RWA**: Joins PUG/PMF mappings, adjustments, and convergence data; exports CG/CBNA templates

The two stages share memory—convergence outputs are passed in-process to the outlook stage rather than re-read from disk, improving performance.

## Quick Start

### Prerequisites

- Python 3.11+
- Conda

### Installation

```bash
# Create conda environment
conda env create -f environment.yml
conda activate outlook_rwa

# Install development dependencies (testing, linting, coverage)
pip install -r requirements-dev.txt
```

### Configuration

Before running, ensure `schema_registry.csv` exists. Generate it if needed:

```bash
python scripts/create_schema_csv.py
```

Copy and update the configuration file:

```bash
cp config/example.yaml config/config.yaml
```

Edit `config/config.yaml` with your data paths and parameters.

### Running the Pipeline

```bash
# Full end-to-end pipeline
python -m outlook_rwa.pipeline

# Run tests
pytest test/

# Check code coverage
pytest --cov=src/outlook_rwa test/

# Code quality checks
pylint src/outlook_rwa/
```

## Project Structure

```
outlook_rwa/
├── src/
│   └── outlook_rwa/
│       ├── __init__.py
│       ├── pipeline.py              # Main orchestration
│       ├── functions.py             # Core computation functions
│       ├── constants.py             # Constants and enums
│       ├── dq.py                    # Data quality checks
│       └── parallel_excel_to_parquet.py  # Excel I/O
├── test/
│   ├── __init__.py
│   ├── conftest.py                  # Pytest fixtures
│   ├── test_functions.py            # Unit tests
│   ├── test_dq.py                   # DQ module tests
│   └── test_integration.py          # Integration tests
├── config/
│   └── config.yaml                  # Pipeline configuration
├── data/                            # Input Excel files
├── sql/                             # SQL queries (if any)
├── scripts/
│   ├── create_schema_csv.py
│   ├── create_mock_data.py
│   └── compare_outputs.py
├── output/                          # Pipeline outputs
├── .coveragerc                      # Coverage configuration
├── .pylintrc                        # Pylint configuration
├── requirements.txt                 # Production dependencies
├── requirements-dev.txt             # Development dependencies
├── pyproject.toml                   # Project metadata
└── README.md                        # This file
```

## Key Modules

### pipeline.py
Main orchestration module that coordinates stage 1 (convergence) and stage 2 (outlook RWA). Handles data loading, transformation, and output generation.

### functions.py
Core computation functions including:
- RWA calculation (SA/AA/ERBA)
- Waterfall key computation
- Data transformations and pivots

### dq.py
Data quality checks covering:
- Row counts and schema completeness
- Entity flag validation
- Key column null rates
- Join match rates
- Quarter coverage

### constants.py
Project constants including column names, mappings, and configuration defaults.

## Development Guidelines

See [CODING_STANDARDS.md](CODING_STANDARDS.md) for detailed coding standards and best practices.

### Code Quality

- **Linting**: `pylint src/outlook_rwa/` (config: `.pylintrc`)
- **Testing**: `pytest test/` (framework: pytest)
- **Coverage**: `pytest --cov=src/outlook_rwa test/` (target: >80%)
- **Type Hints**: Use type hints for all function signatures

### Testing

All new features require unit tests with edge case coverage:

```bash
# Run all tests with coverage
pytest --cov=src/outlook_rwa --cov-report=html test/

# Run specific test
pytest test/test_functions.py::test_calculate_sa_rwa
```

## Configuration

Pipeline behavior is controlled via `config/config.yaml`:

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

## Output Files

The pipeline generates:

- `output/schema.json` - Inferred schema
- `output/dq_results.xlsx` - Data quality report
- `output/dq_results.parquet` - DQ results (for further analysis)
- `output/cg_upload_template.xlsx` - CG template
- `output/cbna_upload_template.xlsx` - CBNA template
- Various parquet files for intermediate results

## Contributing

1. Follow [CODING_STANDARDS.md](CODING_STANDARDS.md)
2. Add tests for new features
3. Ensure 80%+ code coverage
4. Pass `pylint` checks
5. Update documentation

## License

Internal Use Only
