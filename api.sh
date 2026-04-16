#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "[OSISS] ERROR: Virtual environment not found at $PROJECT_ROOT/.venv"
  echo "[OSISS] Run ./setup.sh first, then retry."
  exit 1
fi

echo "[OSISS] Checking API runtime dependencies..."
if ! "$VENV_PYTHON" -c "import fastapi, uvicorn, tiktoken, google.protobuf, sentencepiece" >/dev/null 2>&1; then
  echo "[OSISS] Missing modules detected. Installing requirements..."
  "$VENV_PYTHON" -m pip install -r "$PROJECT_ROOT/requirements.txt"
fi

exec "$VENV_PYTHON" -m uvicorn src.api_server:app --host 0.0.0.0 --port 8000
