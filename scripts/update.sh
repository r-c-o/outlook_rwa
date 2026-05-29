#!/usr/bin/env bash
# update.sh — run after any change to transforms.py or constants.py
#
# Validates the canonical business-rule registry, runs all tests, and
# regenerates SQL files from transforms.py if the sql/templates/ directory
# exists (SQL pipeline branch).
#
# Oracle connection variables (for load_oracle.py) — edit these or set
# them as environment variables before running load_oracle.py:
#   DB_USER=rwa_user
#   DB_PASSWORD=secret
#   DB_DSN=ora-host:1521/ORCLPDB
#   DB_SCHEMA=RWA
#
# Usage:
#   ./scripts/update.sh
#   make update     (if Makefile is present)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> [1/3] Validating transforms.py ..."
python -c "import src.outlook_rwa.transforms; print('    transforms.py: OK')"

echo "==> [2/3] Running tests ..."
python -m pytest test/ -q

# Only regenerate SQL if the SQL pipeline branch is checked out
if [ -d "sql/templates" ]; then
  echo "==> [3/3] Regenerating SQL from transforms.py ..."
  python scripts/generate_sql.py
  echo "    SQL files updated in sql/"
else
  echo "==> [3/3] sql/templates/ not found — skipping SQL regeneration (SQL branch not active)"
fi

echo ""
echo "All checks passed. Review any changed files, then commit."
