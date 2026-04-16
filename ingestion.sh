#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[OSISS] ERROR: Virtual environment not found at $PROJECT_ROOT/.venv"
  echo "[OSISS] Run ./setup.sh first, then retry."
  exit 1
fi

exec "$VENV_PYTHON" "$PROJECT_ROOT/src/ingest.py" "$@"
