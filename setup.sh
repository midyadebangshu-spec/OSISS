#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$PROJECT_ROOT/.venv"

# System packages OSISS needs that are not installed yet (Debian/Ubuntu package names).
missing_system_packages() {
  local missing=()
  python3 -c 'import ensurepip, venv' >/dev/null 2>&1 || missing+=(python3-venv python3-pip)
  if command -v tesseract >/dev/null 2>&1; then
    local langs
    langs="$(tesseract --list-langs 2>/dev/null || true)"
    for lang in eng ben hin; do
      grep -qx "$lang" <<<"$langs" || missing+=("tesseract-ocr-$lang")
    done
  else
    missing+=(tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben tesseract-ocr-hin)
  fi
  [[ -f /usr/share/dict/words ]] || missing+=(wamerican)
  echo "${missing[*]}"
}

install_system_packages() {
  local missing
  missing="$(missing_system_packages)"
  if [[ -z "$missing" ]]; then
    echo "[OSISS] System packages already installed."
    return 0
  fi

  if command -v apt-get >/dev/null 2>&1; then
    local apt_cmd=""
    if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
      apt_cmd="apt-get"
    elif command -v sudo >/dev/null 2>&1 && { sudo -n true 2>/dev/null || { [[ -t 0 ]] && id -nG | grep -qwE 'sudo|admin|wheel'; }; }; then
      apt_cmd="sudo apt-get"
    fi
    if [[ -z "$apt_cmd" ]]; then
      echo "[OSISS] WARNING: missing system packages, and this user cannot install them: $missing" >&2
      echo "[OSISS] Ask an administrator to run: sudo apt-get install -y $missing" >&2
      return 0
    fi
    echo "[OSISS] Installing system packages: $missing"
    # shellcheck disable=SC2086
    if ! { $apt_cmd update -y && $apt_cmd install -y $missing; }; then
      echo "[OSISS] WARNING: could not install: $missing (run: sudo apt-get install -y $missing)" >&2
    fi
  elif command -v brew >/dev/null 2>&1; then
    if [[ "$missing" == *tesseract* ]]; then
      echo "[OSISS] Installing Tesseract with Homebrew..."
      brew install tesseract tesseract-lang
    fi
  else
    echo "[OSISS] WARNING: install these yourself (README section 10): $missing" >&2
  fi
}

# Stop early, with a pointer to the README, when a prerequisite cannot be installed by this script.
check_prerequisites() {
  local problems=()
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    problems+=("Python 3.10 or newer (python3) is required.")
  fi
  if ! docker compose version >/dev/null 2>&1 && ! sudo -n docker compose version >/dev/null 2>&1; then
    problems+=("Docker with the Compose plugin is required (docker compose version).")
  fi
  if command -v node >/dev/null 2>&1; then
    local major minor
    IFS=. read -r major minor _ <<<"$(node -v | tr -d v)"
    if ! (( major > 22 || (major == 22 && minor >= 12) || (major == 20 && minor >= 19) )); then
      problems+=("Node.js $(node -v) is too old for the website: install 22.12+ (or 20.19+).")
    fi
  else
    echo "[OSISS] WARNING: Node.js not found; the website and the admin app cannot be built until it is installed." >&2
  fi
  if (( ${#problems[@]} )); then
    printf '[OSISS] ERROR: %s\n' "${problems[@]}" >&2
    echo "[OSISS] README section 10 (Prerequisites) shows how to install them; then rerun ./setup.sh." >&2
    exit 1
  fi
}

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

check_prerequisites

echo "[OSISS] Creating required directories..."
mkdir -p "$PROJECT_ROOT/data/pdfs"
mkdir -p "$PROJECT_ROOT/models"

install_system_packages

echo "[OSISS] Creating Python virtual environment..."
if [[ ! -d "$VENV_PATH" ]]; then
  python3 -m venv "$VENV_PATH"
fi

echo "[OSISS] Activating virtual environment..."
source "$VENV_PATH/bin/activate"

echo "[OSISS] Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r "$PROJECT_ROOT/requirements.txt"

if [[ -f "$PROJECT_ROOT/frontend/package.json" ]]; then
  if command -v npm >/dev/null 2>&1; then
    echo "[OSISS] Installing frontend dependencies..."
    (cd "$PROJECT_ROOT/frontend" && npm install)
  else
    echo "[OSISS] npm not found; skipping frontend dependency install."
  fi
fi

echo "[OSISS] Starting PostgreSQL and Elasticsearch with Docker Compose..."
DOCKER_COMPOSE_CMD="$(docker_compose_cmd)"
$DOCKER_COMPOSE_CMD -f "$PROJECT_ROOT/docker-compose.yml" up -d postgres elasticsearch

echo "[OSISS] Waiting for PostgreSQL and Elasticsearch to become healthy..."
python "$PROJECT_ROOT/src/wait_for_services.py"

echo "[OSISS] Initializing PostgreSQL schema and Elasticsearch index..."
python "$PROJECT_ROOT/src/db_init.py"

ENV_FILE="$PROJECT_ROOT/.env"
if ! grep -qs '^ADMIN_TOKEN=' "$ENV_FILE"; then
  echo "[OSISS] Generating ADMIN_TOKEN for the admin app into .env..."
  touch "$ENV_FILE" && chmod 600 "$ENV_FILE"
  echo "ADMIN_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$ENV_FILE"
fi

echo "[OSISS] Downloading and caching local models..."
python "$PROJECT_ROOT/src/download_models.py"

echo "[OSISS] Building the English word list (tells English questions from romanized Bengali/Hindi)..."
python "$PROJECT_ROOT/src/build_english_vocab.py" || echo "[OSISS] WARNING: word list not built; rerun src/build_english_vocab.py later." >&2

echo "[OSISS] Setup completed."
echo "[OSISS] Next steps (README section 11 has the full guide for your setup):"
echo "  1) Add PDFs into data/pdfs/ and ingest them: ./ingestion.sh --once"
echo "  2) Refresh the English word list: .venv/bin/python src/build_english_vocab.py"
echo "  3) Start the API: ./api.sh   (admin token: ADMIN_TOKEN in .env)"
echo "  4) Website: cd frontend && npm run build, add SERVE_FRONTEND=true to .env, restart the API, open http://localhost:8000"
