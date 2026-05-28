# Review Checklist

Use this checklist before opening or merging a pull request. Items are derived
from `CODING_STANDARDS.md` and `ARCHITECTURE.md`.

## Code Style (PEP 8)

- [ ] Indentation is 4 spaces (no tabs)
- [ ] All lines are 99 characters or fewer
- [ ] One import per line; grouped stdlib / third-party / local; alphabetized
- [ ] No wildcard imports (`from x import *`)
- [ ] No combined imports (`import a, b`)
- [ ] Naming follows convention: `PascalCase` classes, `snake_case` functions
      and variables, `UPPER_SNAKE_CASE` constants, leading underscore for
      private members
- [ ] No commented-out code; remove dead code entirely

## Documentation

- [ ] Every module starts with a docstring
- [ ] Every public function has a Google-style docstring with `Args:`,
      `Returns:`, and `Raises:` (where applicable)
- [ ] Inline comments explain *why*, never restate *what*

## Functionality

- [ ] Inputs at system boundaries (files, config, API responses) are validated
- [ ] Edge cases and error paths are handled deliberately
- [ ] Vectorized pandas / numpy operations are used in place of Python loops

## Tests

- [ ] New behavior has at least one unit test
- [ ] Edge / boundary conditions are covered
- [ ] Tests use `pytest`, not `unittest`
- [ ] External dependencies are mocked or fixtured
- [ ] `pytest --cov=src/outlook_rwa test/` reports >= 80% coverage
- [ ] `test/test_integration.py` still passes end-to-end

## Tooling

- [ ] `pylint src/outlook_rwa/` exits clean (or each finding has a justified
      `# pylint: disable=` comment)
- [ ] No new secrets, credentials, or hardcoded paths in source
- [ ] Configuration changes go in `config/config.yaml` (or `config/example.yaml`
      for shipped defaults), not in source

## Architecture Alignment

- [ ] Stage 1 (convergence) and Stage 2 (outlook) boundaries remain clear
- [ ] DQ checks still run after joins and before
      `format_columns_before_pivots()`
- [ ] Public API exports in `src/outlook_rwa/__init__.py` reflect any new
      module-level surface
- [ ] Architectural changes are reflected in `ARCHITECTURE.md`
