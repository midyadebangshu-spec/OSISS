#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

# Server secrets and settings (ADMIN_TOKEN, ...) written by setup.sh; the environment wins over the file.
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ && -z "${!key:-}" ]] && export "$key=$value"
  done < "$PROJECT_ROOT/.env"
fi
cd "$PROJECT_ROOT"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[OSISS] ERROR: Virtual environment not found at $PROJECT_ROOT/.venv"
  echo "[OSISS] Run ./setup.sh first, then retry."
  exit 1
fi

exec "$VENV_PYTHON" "$PROJECT_ROOT/src/ingest.py" "$@"
