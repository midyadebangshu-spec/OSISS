#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"

docker_compose_cmd() {
  if docker info >/dev/null 2>&1; then
    echo "docker compose"
    return 0
  fi

  if command -v sudo >/dev/null 2>&1 && sudo -n docker info >/dev/null 2>&1; then
    echo "sudo docker compose"
    return 0
  fi

  cat >&2 <<'EOF'
[OSISS] ERROR: Docker daemon is not accessible for the current user.
[OSISS] Fix one of the following, then rerun ./setup.sh:
[OSISS]   1) Add your user to docker group and re-login:
[OSISS]      sudo usermod -aG docker "$USER"
[OSISS]   2) Or run setup with sudo privileges.
EOF
  return 1
}

echo "[OSISS] Creating required directories..."
mkdir -p "$PROJECT_ROOT/data/pdfs"
mkdir -p "$PROJECT_ROOT/models"

echo "[OSISS] Creating Python virtual environment..."
if [[ ! -d "$VENV_PATH" ]]; then
  python3 -m venv "$VENV_PATH"
fi

echo "[OSISS] Activating virtual environment..."
source "$VENV_PATH/bin/activate"

echo "[OSISS] Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_ROOT/requirements.txt"

echo "[OSISS] Starting PostgreSQL and Elasticsearch with Docker Compose..."
DOCKER_COMPOSE_CMD="$(docker_compose_cmd)"
echo "[OSISS] Resetting existing OSISS containers (if any)..."
$DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" down --remove-orphans || true
$DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" up -d postgres elasticsearch

echo "[OSISS] Waiting for PostgreSQL and Elasticsearch to become healthy..."
python "$PROJECT_ROOT/src/wait_for_services.py"

echo "[OSISS] Initializing PostgreSQL schema and Elasticsearch index..."
python "$PROJECT_ROOT/src/db_init.py"

echo "[OSISS] Downloading and caching local models..."
python "$PROJECT_ROOT/src/download_models.py"

echo "[OSISS] Setup completed."
echo "[OSISS] Next steps:"
echo "  1) Add PDFs into data/pdfs/"
echo "  2) Run ingestion: ./ingestion.sh --once"
echo "  3) Run search: .venv/bin/python src/search.py --query \"Any question from the PDF\""
