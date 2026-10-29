#!/usr/bin/env bash
set -euo pipefail
# Native Render variables also reach builds; refuse secrets before dependency code runs.
python - <<'PY'
import os
for key, value in os.environ.items():
    key = key.upper()
    if value and (
        key.startswith(("COLLECTOR_", "WORKSPACE_", "AI_", "PG"))
        or key.endswith(("DATABASE_URL", "_TOKEN", "_API_KEY", "_SECRET", "_PASSWORD"))
    ):
        raise SystemExit("Build refused: credentials must be absent")
PY
test "$(uv --version | cut -d ' ' -f 2)" = 0.11.6
if test "$(npm --version)" != 11.11.0; then
  npm install --global npm@11.11.0 --ignore-scripts --no-audit --no-fund
fi
python -c 'import sys; assert sys.version_info[:3] == (3, 12, 13)'
test "$(node --version)" = v24.14.1
test "$(npm --version)" = 11.11.0
# Run from source: avoid an unfrozen, isolated hatchling installation for this app.
uv sync --frozen --no-dev --no-install-project
python deploy/build_frontend.py
