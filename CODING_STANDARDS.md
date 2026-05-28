# Coding Standards

This document outlines the coding standards and best practices for the Outlook RWA project, following PEP 8, PEP 257, and industry best practices.

## Code Style and Formatting

### PEP 8 Compliance

All code must adhere to [PEP 8](https://www.python.org/dev/peps/pep-0008/) standards:

- **Indentation**: 4 spaces (never tabs)
- **Line Length**: Maximum 99 characters (enforced by pylint)
- **Imports**: 
  - On separate lines (one import per line)
  - Grouped: standard library, third-party, local
  - Alphabetically sorted within groups
- **Whitespace**: No extraneous whitespace in expressions
- **Naming Conventions**:
  - Classes: `PascalCase` (e.g., `MyClass`, `DataProcessor`)
  - Functions/Variables: `snake_case` (e.g., `calculate_rwa`, `output_dir`)
  - Constants: `UPPER_SNAKE_CASE` (e.g., `MAX_VARCHAR_LENGTH`, `DEFAULT_BATCH_SIZE`)
  - Private: Leading underscore (e.g., `_internal_function`, `_config`)

### Code Organization

- **No commented code**: Remove unused code entirely (use git history if needed)
- **Single responsibility**: Each function does one thing
- **Clear structure**: Related functions grouped, logical flow obvious
- **Configuration isolation**: All configurable parameters in `config/` directory

## Documentation

### Module Docstrings

Every module must start with a docstring explaining its purpose:

```python
"""Data quality checks for the Outlook RWA pipeline.

Each check function returns a DQResult. run_all_checks bundles them into
a single DataFrame exported by export_dq_results as both Parquet and Excel.
"""
```

### Function Docstrings

All functions must have docstrings following PEP 257 (Google style):

```python
def calculate_sa_rwa(rwf_values: pd.Series, risk_weights: dict) -> pd.Series:
    """Calculate Standardized Approach RWA.
    
    Args:
        rwf_values: Series of risk-weighted factors by entity.
        risk_weights: Mapping of risk categories to weights.
    
    Returns:
        Series of calculated RWA values.
    
    Raises:
        ValueError: If risk_weights contains negative values.
    """
```

### Inline Comments

Use inline comments sparingly—only when the **WHY** is non-obvious:

```python
# Correct: explains non-obvious intent
# Join must happen before format_columns to preserve NaN from misses
dq_results = run_all_checks(convergence, cg_outlook)

# Avoid: just describes what code does
# Set x to 5
x = 5
```

## Functionality and Logic

- Verify code meets all functional requirements
- Check for logical errors or edge case handling
- Handle exceptions appropriately
- Validate user input at system boundaries
- Use meaningful variable names that express intent

## Performance and Efficiency

- Profile before optimizing
- Avoid redundant computations (e.g., duplicate calculations in loops)
- Eliminate unnecessary iterations
- Use vectorized operations (pandas/numpy) over loops

## Security and Validation

- **Input Validation**: Validate all external/user input (files, config, API responses)
- **Secrets Management**: No hardcoded secrets; use environment variables or `.env` files
- **SQL Injection Prevention**: Use parameterized queries (if applicable)
- **Access Control**: Respect file permissions; don't bypass security checks

## Testing Standards

### Test Requirements

- **Coverage**: Minimum 80% code coverage
- **Edge Cases**: Test boundary conditions and error scenarios
- **Repeatability**: Tests must produce same results every run
- **Isolation**: Use mocks for external dependencies (databases, APIs)
- **Test Framework**: pytest (not unittest, unless legacy)

### Test Structure

```python
"""Unit tests for RWA calculations."""
import pytest
from outlook_rwa.functions import calculate_sa_rwa


class TestCalculateSaRwa:
    """Tests for SA RWA calculation."""
    
    def test_calculate_sa_rwa_basic(self, sample_rwf_data):
        """Test basic RWA calculation with standard inputs."""
        result = calculate_sa_rwa(sample_rwf_data, RISK_WEIGHTS)
        assert result.notna().all()
        assert (result >= 0).all()
    
    def test_calculate_sa_rwa_empty_input(self):
        """Test behavior with empty input."""
        result = calculate_sa_rwa(pd.Series([], dtype=float), RISK_WEIGHTS)
        assert len(result) == 0
    
    def test_calculate_sa_rwa_negative_weights_raises(self):
        """Test that negative risk weights raise ValueError."""
        bad_weights = {"HIGH_RISK": -0.5}
        with pytest.raises(ValueError):
            calculate_sa_rwa(sample_rwf_data, bad_weights)
```

### Running Tests

```bash
# Run all tests
pytest test/

# Run with coverage report
pytest --cov=src/outlook_rwa --cov-report=html test/

# Run specific test file
pytest test/test_functions.py

# Run specific test
pytest test/test_functions.py::TestCalculateSaRwa::test_calculate_sa_rwa_basic

# Verbose output
pytest -v test/
```

## Code Quality Tools

### Pylint

Configuration: `.pylintrc`

```bash
# Check code
pylint src/outlook_rwa/

# Common issues checked:
# - Unused imports
# - Undefined variables
# - Line too long
# - Invalid names
# - Missing docstrings
```

### Coverage

Configuration: `.coveragerc`

```bash
# Generate coverage report (HTML)
pytest --cov=src/outlook_rwa --cov-report=html test/

# Open report in browser
open htmlcov/index.html
```

Target: **80% minimum coverage**

## Imports

### Correct Import Style

```python
# Good
import pandas as pd
import numpy as np
from pathlib import Path
from outlook_rwa.constants import DEFAULT_BATCH_SIZE
from outlook_rwa.functions import calculate_sa_rwa

# Avoid
from pandas import *  # Don't use wildcard imports
import pandas, numpy  # Don't combine imports on one line
```

## Configuration Management

### Configuration Files

- **Location**: `config/` directory
- **Format**: YAML (human-readable, standard for config)
- **Environment Variables**: Use `.env` for secrets (not in git)
- **Defaults**: Hard-code sensible defaults; allow override via config

Example `config/config.yaml`:

```yaml
# Data paths
data_dir: data/
output_dir: output/

# Feature flags
export_intermediate_xlsx: false

# Schema inference settings
schema_inference:
  max_varchar_length: 2000
  date_formats:
    - "%Y-%m-%d"
    - "%m/%d/%Y"

# Data mappings
mappings:
  - file: data/outlook.xlsx
    sheet: Outlook
    table: OUTLOOK_DATA
    skip_rows: 0
```

## Project Structure

```
outlook_rwa/
├── src/outlook_rwa/        # Application code
│   ├── __init__.py
│   ├── pipeline.py         # Main orchestration
│   ├── functions.py        # Computation functions
│   ├── constants.py        # Constants & enums
│   ├── dq.py              # Data quality checks
│   └── parallel_excel_to_parquet.py
├── test/                   # Test suite (mirrors src structure)
│   ├── conftest.py        # Shared fixtures
│   ├── test_functions.py
│   ├── test_dq.py
│   └── test_integration.py
├── config/                 # Configuration files
│   └── config.yaml
├── data/                   # Input data (not in git)
├── sql/                    # SQL queries (if used)
├── output/                 # Output artifacts
├── scripts/                # Standalone scripts
├── .coveragerc            # Coverage config
├── .pylintrc              # Pylint config
├── requirements.txt       # Production dependencies
├── requirements-dev.txt   # Dev dependencies (pytest, pylint, coverage)
├── pyproject.toml         # Project metadata
├── README.md              # Project overview
└── CODING_STANDARDS.md    # This file
```

## Development Workflow

1. **Plan**: Design changes before coding
2. **Code**: Follow standards above
3. **Test**: Write tests first (TDD) or after, ensure 80%+ coverage
4. **Review**: Self-review against checklist (see REVIEW_CHECKLIST.md)
5. **Lint**: Run pylint; fix all warnings
6. **Merge**: Only merge with all checks passing

## References

- [PEP 8 Style Guide](https://www.python.org/dev/peps/pep-0008/)
- [PEP 257 Docstrings](https://www.python.org/dev/peps/pep-0257/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [Pytest Documentation](https://docs.pytest.org/)
- [Pylint Documentation](https://pylint.readthedocs.io/)
