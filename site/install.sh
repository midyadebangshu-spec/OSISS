#!/usr/bin/env bash
# OSISS server installer: sets up everything an OSISS server needs, from the system packages to the models.
#
#   curl -fsSL https://midyadebangshu-spec.github.io/OSISS/install.sh | bash
#   curl -fsSL https://midyadebangshu-spec.github.io/OSISS/install.sh | bash -s -- --listen all --service
#
# or download it, read it, and run it:  bash install.sh --help
#
# What it does, in order:
#   1. (Ubuntu/Debian) installs the missing system packages, Docker and Node.js. This is the only step that needs
#      administrator rights, and it is skipped when nothing is missing. On macOS it checks for Homebrew and
#      Docker Desktop instead, because those are installed by hand.
#   2. downloads the OSISS code (a tarball, no Git needed) into the install folder, keeping .env, data/ and models/
#      when it is run again on an existing install.
#   3. runs the project's own setup.sh: Python environment, PostgreSQL + Elasticsearch in Docker, the schema,
#      the ADMIN_TOKEN and the models (about 12 GB, so the first run takes a while).
#   4. optionally installs a systemd service (--service) and prints the address and the admin token.
#
# Windows is not supported for the server; run this on Ubuntu/Debian or macOS (WSL 2 with Ubuntu may work, untested).
# It works with the bash 3.2 that macOS ships, so it avoids newer bash features.

set -euo pipefail

OSISS_REPO="${OSISS_REPO:-midyadebangshu-spec/OSISS}"
SCRIPT_URL="${OSISS_INSTALL_URL:-https://midyadebangshu-spec.github.io/OSISS/install.sh}"
ARCHIVE_BASE="${OSISS_ARCHIVE_BASE:-https://codeload.github.com}"

# Exit codes the OSISS Admin app relies on.
EXIT_ROOT_NEEDED=10   # --check: a step that needs administrator rights is pending
EXIT_UNSUPPORTED=20   # unsupported system, or a prerequisite that must be installed by hand is missing

# Where this script lives; empty when it is piped into bash. Captured at the top level on purpose: inside a
# function bash reports the source of a piped script as "main".
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"

DIR=""
REF="main"
LISTEN="local"
SERVICE=0
NO_WEBSITE=0
DRY_RUN=0
CHECK=0
PRINT_TOKEN=1
PHASE="all"           # all | root | user | start (the last three are used when the script calls itself)
TARGET_USER=""
SELF=""
OS=""
FORWARD=()            # arguments handed on when the script calls itself for a phase

PACKAGES_DEBIAN="curl ca-certificates tar gzip python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-eng tesseract-ocr-ben tesseract-ocr-hin wamerican"

log() { printf '[OSISS] %s\n' "$*"; }
warn() { printf '[OSISS] WARNING: %s\n' "$*" >&2; }
die() {
  local code=1
  if [[ "${1:-}" == --code=* ]]; then code="${1#--code=}"; shift; fi
  printf '[OSISS] ERROR: %s\n' "$*" >&2
  exit "$code"
}

usage() {
  cat <<'EOF'
Usage: install.sh [options]

  --dir DIR          install folder (default: /opt/osiss when administrator rights are available, else ~/osiss)
  --ref REF          branch, tag or commit of the OSISS code to install (default: main)
  --listen local|all who may reach the API: only this computer (default), or the whole network.
                     Use "all" when the admin app runs on another computer.
  --service          also install and start a systemd service so the API runs at boot (Linux)
  --no-website       do not install Node.js (the website cannot then be built on this server)
  --no-print-token   do not print the admin token at the end (it stays in DIR/.env)
  --user NAME        run the server as this user (default: the user who runs the script; "osiss" when run as root)
  --check            only report whether administrator rights are needed (exit 10) and exit
  --dry-run          print what would be done without changing anything
  -h, --help         show this help

Run it again at any time: it keeps .env, data/ and models/ and only refreshes the code.
EOF
}

# ---- arguments -----------------------------------------------------------------------------------------------

parse_args() {
  while (( $# )); do
    case "$1" in
      --dir) DIR="${2:-}"; shift 2 || die "--dir needs a value" ;;
      --ref) REF="${2:-}"; shift 2 || die "--ref needs a value" ;;
      --listen) LISTEN="${2:-}"; shift 2 || die "--listen needs local or all" ;;
      --user) TARGET_USER="${2:-}"; shift 2 || die "--user needs a name" ;;
      --phase) PHASE="${2:-}"; shift 2 || die "--phase needs a value" ;;
      --service) SERVICE=1; shift ;;
      --no-website) NO_WEBSITE=1; shift ;;
      --no-print-token) PRINT_TOKEN=0; shift ;;
      --check) CHECK=1; shift ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "Unknown option: $1" ;;
    esac
  done
  case "$LISTEN" in local|all) ;; *) die "--listen must be local or all (got: $LISTEN)" ;; esac
  case "$PHASE" in all|root|user|start) ;; *) die "Unknown phase: $PHASE" ;; esac
  [[ -n "$REF" ]] || die "--ref must not be empty"
  if [[ -n "$TARGET_USER" && ! "$TARGET_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then die "Invalid user name: $TARGET_USER"; fi
  if [[ -n "$DIR" ]]; then
    [[ "$DIR" == /* ]] || die "--dir must be an absolute path (got: $DIR)"
    case "$DIR" in
      /|/usr|/usr/*|/bin|/sbin|/etc|/boot|/dev|/proc|/sys|/var|/lib|/root) die "Refusing to install into $DIR" ;;
    esac
    [[ "$DIR" != *$'\n'* ]] || die "--dir must not contain a newline"
  fi
}

# Arguments to hand on when the script calls itself (after the folder and user are known).
build_forward_args() {
  FORWARD=(--dir "$DIR" --ref "$REF" --listen "$LISTEN" --user "$TARGET_USER")
  if (( SERVICE )); then FORWARD+=(--service); fi
  if (( NO_WEBSITE )); then FORWARD+=(--no-website); fi
  if (( ! PRINT_TOKEN )); then FORWARD+=(--no-print-token); fi
  if (( DRY_RUN )); then FORWARD+=(--dry-run); fi
}

# Print, or run, a command. Everything that changes the system goes through here so --dry-run is honest.
run() {
  if (( DRY_RUN )); then printf '+ %s\n' "$*"; return 0; fi
  "$@"
}

# Same for a shell snippet that needs a pipe.
run_shell() {
  if (( DRY_RUN )); then printf '+ %s\n' "$1"; return 0; fi
  bash -c "$1"
}

# ---- the system ---------------------------------------------------------------------------------------------

detect_os() {
  local kernel="${OSISS_UNAME:-$(uname -s)}"
  case "$kernel" in
    Darwin) OS=macos ;;
    Linux)
      local rel="${OSISS_OS_RELEASE:-/etc/os-release}" id="" like=""
      if [[ -r "$rel" ]]; then
        # shellcheck source=/dev/null
        id="$(. "$rel"; printf '%s' "${ID:-}")"
        # shellcheck source=/dev/null
        like="$(. "$rel"; printf '%s' "${ID_LIKE:-}")"
      fi
      case " $id $like " in
        *" ubuntu "*|*" debian "*) OS=debian ;;
        *) OS=unsupported-linux ;;
      esac ;;
    MINGW*|MSYS*|CYGWIN*) OS=windows ;;
    *) OS=unsupported ;;
  esac
}

explain_unsupported() {
  case "$OS" in
    windows)
      die --code="$EXIT_UNSUPPORTED" "The OSISS server does not run on Windows. Install it on Ubuntu/Debian or macOS (WSL 2 with Ubuntu may work but is untested); the Windows admin app can manage a server running elsewhere." ;;
    unsupported-linux)
      die --code="$EXIT_UNSUPPORTED" "This installer supports Ubuntu and Debian (apt). Install the prerequisites listed in the README (section 10) by hand, then run ./setup.sh." ;;
    *)
      die --code="$EXIT_UNSUPPORTED" "Unsupported system. The installer supports Ubuntu/Debian and macOS." ;;
  esac
}

can_use_root() { [[ "$(id -u)" == 0 ]] || command -v sudo >/dev/null 2>&1; }

home_of() {
  local user="$1" home=""
  if [[ "$user" == "$(id -un)" ]]; then printf '%s' "$HOME"; return; fi
  if command -v getent >/dev/null 2>&1; then home="$(getent passwd "$user" | cut -d: -f6 || true)"; fi
  printf '%s' "${home:-/home/$user}"
}

resolve_target() {
  if [[ -z "$TARGET_USER" ]]; then
    if [[ "$(id -u)" == 0 ]]; then
      TARGET_USER="${SUDO_USER:-}"
      if [[ -z "$TARGET_USER" || "$TARGET_USER" == root ]]; then TARGET_USER=osiss; fi
    else
      TARGET_USER="$(id -un)"
    fi
  fi
  if [[ -z "$DIR" ]]; then
    if [[ "$OS" == debian ]] && can_use_root; then DIR=/opt/osiss; else DIR="$(home_of "$TARGET_USER")/osiss"; fi
  fi
}

bullets() { while IFS= read -r line; do [[ -n "$line" ]] && printf '  - %s\n' "$line"; done <<<"$1"; return 0; }

# Small probes, separate functions so tests can replace them.
have_pkg() { dpkg -s "$1" >/dev/null 2>&1; }
have_compose() { docker compose version >/dev/null 2>&1 || { command -v sudo >/dev/null 2>&1 && sudo -n docker compose version >/dev/null 2>&1; }; }
user_exists() { id "$1" >/dev/null 2>&1; }
in_docker_group() { id -nG "$1" 2>/dev/null | tr ' ' '\n' | grep -qx docker; }
dir_usable() {
  local d="$DIR"
  while [[ ! -e "$d" && "$d" != / ]]; do d="$(dirname "$d")"; done
  if [[ "$(id -un)" == "$TARGET_USER" ]]; then [[ -w "$d" ]]; else return 1; fi
}
node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local major minor
  IFS=. read -r major minor _ <<<"$(node -v | tr -d v)"
  (( major > 22 || (major == 22 && minor >= 12) || (major == 20 && minor >= 19) ))
}

# What needs administrator rights on Debian/Ubuntu, one reason per line (empty = nothing).
root_reasons() {
  local missing="" p
  for p in $PACKAGES_DEBIAN; do have_pkg "$p" || missing="$missing $p"; done
  if [[ -n "$missing" ]]; then printf 'system packages:%s\n' "$missing"; fi
  if ! have_compose; then echo "Docker with the Compose plugin"; fi
  if (( ! NO_WEBSITE )) && ! node_ok; then echo "Node.js 22"; fi
  if ! user_exists "$TARGET_USER"; then echo "the user $TARGET_USER"; fi
  if user_exists "$TARGET_USER" && command -v docker >/dev/null 2>&1 && ! in_docker_group "$TARGET_USER"; then
    echo "adding $TARGET_USER to the docker group"
  fi
  if ! dir_usable; then echo "the folder $DIR"; fi
  if (( SERVICE )); then echo "the systemd service"; fi
  return 0
}

# macOS: Homebrew and Docker Desktop are installed by hand; report what is missing.
macos_missing() {
  command -v brew >/dev/null 2>&1 || echo "Homebrew (https://brew.sh)"
  if ! command -v docker >/dev/null 2>&1; then
    echo "Docker Desktop (https://www.docker.com/products/docker-desktop/), installed and started"
  elif ! docker info >/dev/null 2>&1; then
    echo "Docker Desktop is installed but not running; start it"
  fi
  return 0
}

# ---- phase: root (Debian/Ubuntu) -----------------------------------------------------------------------------

require_root() {
  if (( DRY_RUN )); then return 0; fi
  [[ "$(id -u)" == 0 ]] || die "This step needs administrator rights (run it with sudo)."
}

write_service_unit() {
  local unit=/etc/systemd/system/osiss-api.service
  if (( DRY_RUN )); then
    printf '+ write %s (User=%s, WorkingDirectory=%s)\n' "$unit" "$TARGET_USER" "$DIR"
    return 0
  fi
  cat >"$unit" <<EOF
# Written by the OSISS installer. The API starts at boot and restarts if it stops.
# Settings (ADMIN_TOKEN, API_HOST, ...) are read from $DIR/.env by api.sh.
[Unit]
Description=OSISS search API
Wants=network-online.target docker.service
After=network-online.target docker.service

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$DIR
ExecStart=$DIR/api.sh
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF
}

root_phase() {
  [[ "$OS" == debian ]] || die --code="$EXIT_UNSUPPORTED" "The administrator step is only needed on Ubuntu/Debian."
  require_root
  local reasons; reasons="$(root_reasons)"
  if [[ -z "$reasons" ]]; then log "No administrator step needed."; return 0; fi
  log "Administrator step:"; bullets "$reasons"

  if ! user_exists "$TARGET_USER"; then run useradd -m -s /bin/bash "$TARGET_USER"; fi

  local missing="" p
  for p in $PACKAGES_DEBIAN; do have_pkg "$p" || missing="$missing $p"; done
  if [[ -n "$missing" ]]; then
    run apt-get update
    # shellcheck disable=SC2086
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y $missing
  fi

  if ! have_compose; then
    log "Installing Docker with Docker's own script (https://get.docker.com)..."
    run_shell 'curl -fsSL https://get.docker.com | sh'
  fi
  if command -v systemctl >/dev/null 2>&1; then run systemctl enable --now docker || warn "Could not start the Docker service."; fi

  if (( ! NO_WEBSITE )) && ! node_ok; then
    log "Installing Node.js 22 (NodeSource)..."
    run_shell 'curl -fsSL https://deb.nodesource.com/setup_22.x | bash -'
    run env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
  fi

  if ! in_docker_group "$TARGET_USER"; then run usermod -aG docker "$TARGET_USER"; fi

  run mkdir -p "$DIR"
  run chown "$TARGET_USER": "$DIR"

  if (( SERVICE )); then
    write_service_unit
    run systemctl daemon-reload
    run systemctl enable osiss-api
  fi
}

# ---- phase: user ---------------------------------------------------------------------------------------------

set_env() {
  local key="$1" value="$2" file="$DIR/.env" tmp
  if (( DRY_RUN )); then printf '+ set %s=%s in %s\n' "$key" "$value" "$file"; return 0; fi
  touch "$file"; chmod 600 "$file"
  tmp="$(mktemp "$file.XXXXXX")"
  grep -v "^$key=" "$file" >"$tmp" || true
  printf '%s=%s\n' "$key" "$value" >>"$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$file"
}

macos_install_packages() {
  eval "$(brew shellenv)" || true
  local formulae="python tesseract tesseract-lang"
  if (( ! NO_WEBSITE )); then formulae="$formulae node"; fi
  # shellcheck disable=SC2086
  run brew install $formulae
}

download_code() {
  local url="$ARCHIVE_BASE/$OSISS_REPO/tar.gz/$REF"
  if [[ -f "$DIR/api.sh" ]]; then log "Updating the existing install in $DIR (keeps .env, data and models)."; else log "Installing into $DIR."; fi
  run mkdir -p "$DIR"
  if (( DRY_RUN )); then printf '+ curl -fsSL %s | tar -xz -C %s --strip-components=1\n' "$url" "$DIR"; return 0; fi
  curl -fsSL "$url" | tar -xz -C "$DIR" --strip-components=1
}

lan_address() {
  local ip=""
  if command -v hostname >/dev/null 2>&1; then ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"; fi
  if [[ -z "$ip" ]] && command -v ipconfig >/dev/null 2>&1; then ip="$(ipconfig getifaddr en0 2>/dev/null || true)"; fi
  printf '%s' "${ip:-<this-computer>}"
}

summary() {
  local token=""
  if (( ! DRY_RUN )) && [[ -f "$DIR/.env" ]]; then token="$(grep '^ADMIN_TOKEN=' "$DIR/.env" | head -n1 | cut -d= -f2- || true)"; fi
  echo
  log "OSISS is installed in $DIR"
  echo "  Address on this computer : http://localhost:8000"
  if [[ "$LISTEN" == all ]]; then echo "  Address on the network   : http://$(lan_address):8000"; fi
  if (( PRINT_TOKEN )) && [[ -n "$token" ]]; then
    echo
    echo "  Admin token (also in $DIR/.env; keep it private, anyone with it can change the library):"
    echo "    $token"
  fi
  echo
  echo "Next steps:"
  if (( SERVICE )); then
    echo "  1. The API runs as the osiss-api service:  systemctl status osiss-api"
  else
    echo "  1. Start the API:  cd $DIR && ./api.sh      (or from the admin app's Dashboard)"
  fi
  echo "  2. In OSISS Admin, add this server and ingest your PDFs. README section 11 has the full guide."
  if [[ "$LISTEN" == local ]]; then
    echo "     The API listens on this computer only. If the admin app runs elsewhere, rerun with: --listen all"
  fi
}

user_phase() {
  command -v curl >/dev/null 2>&1 || die "curl is required."
  command -v tar >/dev/null 2>&1 || die "tar is required."

  if [[ "$OS" == macos ]]; then
    local missing; missing="$(macos_missing)"
    if [[ -n "$missing" ]]; then printf '%s\n' "$missing" | sed 's/^/[OSISS] Missing: /' >&2; die --code="$EXIT_UNSUPPORTED" "Install the items above, then run this again."; fi
    macos_install_packages
  fi

  # A fresh docker group membership only applies to new logins; "sg" lets this run use it right away.
  if (( ! DRY_RUN )) && [[ "$OS" == debian ]] && ! docker info >/dev/null 2>&1 && [[ -z "${OSISS_SG:-}" ]] \
     && command -v sg >/dev/null 2>&1 && in_docker_group "$(id -un)" && ! id -nG | tr ' ' '\n' | grep -qx docker; then
    log "Applying the new docker group membership..."
    local cmd; cmd="$(printf '%q ' bash "$SELF" --phase user "${FORWARD[@]}")"
    exec env OSISS_SG=1 sg docker -c "$cmd"
  fi

  if (( ! DRY_RUN )) && [[ "$OS" == debian ]] && ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    die "Python 3.10 or newer is required (this system has $(python3 --version 2>&1)). Use Ubuntu 22.04 or newer."
  fi

  download_code
  if (( DRY_RUN )); then
    printf '+ (cd %s && bash ./setup.sh)\n' "$DIR"
  else
    (cd "$DIR" && bash ./setup.sh)
  fi
  if [[ "$LISTEN" == all ]]; then set_env API_HOST 0.0.0.0; else set_env API_HOST 127.0.0.1; fi
  summary
}

start_phase() {
  require_root
  run systemctl start osiss-api
  log "Started the osiss-api service."
}

# ---- orchestration -------------------------------------------------------------------------------------------

# Piped (curl | bash) or process-substituted runs have no file to call again; save a copy.
ensure_script_file() {
  case "$SCRIPT_SOURCE" in
    ""|/dev/fd/*|/proc/self/fd/*) SELF="" ;;
    *) if [[ -f "$SCRIPT_SOURCE" ]]; then SELF="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)/$(basename "$SCRIPT_SOURCE")"; else SELF=""; fi ;;
  esac
  if [[ -z "$SELF" ]]; then
    SELF="$(mktemp "${TMPDIR:-/tmp}/osiss-install.XXXXXX")"
    curl -fsSL "$SCRIPT_URL" -o "$SELF" || die "Could not download the installer from $SCRIPT_URL"
    chmod 755 "$SELF"
  fi
}

# Run a command as root: directly when already root, otherwise through sudo.
as_root() {
  if (( DRY_RUN )); then printf '+ (as root) %s\n' "$*"; return 0; fi
  if [[ "$(id -u)" == 0 ]]; then "$@"; else sudo "$@"; fi
}

orchestrate() {
  log "System: $OS · install folder: $DIR · server user: $TARGET_USER · API reachable from: $LISTEN"
  if [[ "$OS" == debian ]]; then
    local reasons; reasons="$(root_reasons)"
    if [[ -n "$reasons" ]]; then
      log "Administrator rights are needed for:"; bullets "$reasons"
      can_use_root || die "Run this as root, or install sudo, or ask an administrator to run: sudo bash install.sh ${FORWARD[*]}"
      as_root bash "$SELF" --phase root "${FORWARD[@]}"
    fi
  fi

  if [[ "$(id -un)" == "$TARGET_USER" ]]; then
    user_phase
  else
    # Running as root for another user: hand the rest to that user.
    run runuser -u "$TARGET_USER" -- bash "$SELF" --phase user "${FORWARD[@]}"
  fi

  if (( SERVICE )); then as_root bash "$SELF" --phase start "${FORWARD[@]}"; fi
}

main() {
  parse_args "$@"
  detect_os
  if [[ "$OS" != debian && "$OS" != macos ]]; then explain_unsupported; fi
  resolve_target
  build_forward_args

  if (( CHECK )); then
    if [[ "$OS" == debian ]]; then
      local reasons; reasons="$(root_reasons)"
      if [[ -n "$reasons" ]]; then printf '%s\n' "$reasons"; exit "$EXIT_ROOT_NEEDED"; fi
    else
      local missing; missing="$(macos_missing)"
      if [[ -n "$missing" ]]; then printf '%s\n' "$missing"; exit "$EXIT_UNSUPPORTED"; fi
    fi
    exit 0
  fi

  # Only the phases that call the script again need a file; the privileged ones run from whatever started them.
  case "$PHASE" in all|user) ensure_script_file ;; esac
  case "$PHASE" in
    root) root_phase ;;
    user) user_phase ;;
    start) start_phase ;;
    all) orchestrate ;;
  esac
}

# Run only when executed (or piped into bash), not when a test sources the file for its functions.
if [[ "${BASH_SOURCE[0]:-}" == "${0}" || -z "${BASH_SOURCE[0]:-}" ]]; then
  main "$@"
fi
