#!/bin/bash
set -euo pipefail

# Inject recent project context only in Claude Code on the web (remote) sessions,
# so local CLI sessions stay uncluttered. stdout is added to the session context.
[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

echo "=== outlook_rwa: recent session context (instructions.env) ==="
cat "$CLAUDE_PROJECT_DIR/instructions.env" 2>/dev/null || echo "(instructions.env not found)"
echo
echo "=== Recent commits ==="
git -C "$CLAUDE_PROJECT_DIR" log --oneline -8 2>/dev/null || true
