#!/bin/bash
#
# SessionStart hook for local-whisper.
#
# 1. Pins the git author identity to the over-code account. GitHub attributes a
#    commit by matching its author *email* to an account, so any other address
#    silently credits the wrong user — this makes that impossible to get wrong.
# 2. In Claude Code on the web, installs the project so the tests and linter run.
set -euo pipefail

PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
cd "$PROJECT_DIR"

# --- git identity (every session, local and remote) --------------------------
if git rev-parse --git-dir >/dev/null 2>&1; then
    git config --local user.name  "over-code"
    git config --local user.email "lab@over-code.de"
    echo "git identity: $(git config --local user.name) <$(git config --local user.email)>"
fi

# --- everything below only matters in the web sandbox ------------------------
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

# Qt needs a platform plugin; there is no display in the sandbox.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
    echo 'export QT_QPA_PLATFORM=offscreen' >> "$CLAUDE_ENV_FILE"
fi

# Idempotent: pip skips what is already satisfied, and the container image is
# cached once the hook finishes.
# (no `pip install --upgrade pip` here: a distro-managed pip refuses to
# uninstall itself and would fail the whole hook.)
python3 -m pip install --quiet -e '.[hotkey,dev]'
python3 -m pip install --quiet pyflakes

echo "local-whisper dev environment ready"
