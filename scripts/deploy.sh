#!/usr/bin/env bash
# scripts/deploy.sh -- Bootstrap wrapper for MLBB-TournamentBot deployment.
#
# Handles pre-Python setup (venv, pip install, WP-CLI check) then hands off
# to scripts/deploy.py for the main provisioning.
#
# Usage:
#   bash scripts/deploy.sh                # full deploy
#   bash scripts/deploy.sh --check        # preflight only
#   bash scripts/deploy.sh --skip-wp      # skip WP infrastructure
#   bash scripts/deploy.sh --force        # no prompts
#   INSTALL_PATH=/opt/mlbb bash scripts/deploy.sh   # custom path

set -euo pipefail

# -- Color output --------------------------------------------------------------
if [ -t 1 ]; then
    RED='\033[91m'; GREEN='\033[92m'; YELLOW='\033[93m'; BLUE='\033[94m'
    BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; RESET=''
fi

ok()   { printf "  ${GREEN}[OK]${RESET}   %s\n" "$1"; }
fail() { printf "  ${RED}[FAIL]${RESET} %s\n" "$1"; }
info() { printf "  ${BLUE}[..]${RESET}   %s\n" "$1"; }
skip() { printf "  ${YELLOW}[SKIP]${RESET} %s\n" "$1"; }

banner() {
    echo
    printf "${BOLD}%s${RESET}\n" "============================================================"
    printf "${BOLD}%s${RESET}\n" "$(printf '%*s' $(( (60 + ${#1}) / 2 )) "$1")"
    printf "${BOLD}%s${RESET}\n" "============================================================"
    echo
}

# -- Configuration -------------------------------------------------------------
INSTALL_PATH="${INSTALL_PATH:-/root/MLBB-TournamentBot}"
PYTHON_MIN="3.11"
VENV_PATH="$INSTALL_PATH/venv"

banner "MLBB Tournament Bot — Bootstrap"
info "Install path: $INSTALL_PATH"

# -- Root check ----------------------------------------------------------------
if [ "$EUID" -ne 0 ]; then
    fail "Must run as root (systemd + crontab modifications required)"
    echo "  Try: sudo bash $0 $*"
    exit 1
fi
ok "Running as root"

# -- Install path exists -------------------------------------------------------
if [ ! -d "$INSTALL_PATH" ]; then
    fail "Install path does not exist: $INSTALL_PATH"
    echo "  Clone the repo first: git clone <repo> $INSTALL_PATH"
    exit 1
fi
ok "Install path exists"

if [ ! -f "$INSTALL_PATH/requirements.txt" ]; then
    fail "requirements.txt not found at $INSTALL_PATH"
    exit 1
fi
ok "requirements.txt found"

# -- Python version check ------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    fail "python3 not installed"
    echo "  Install: apt-get install python3 python3-venv python3-pip"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
MIN_MAJOR=$(echo "$PYTHON_MIN" | cut -d. -f1)
MIN_MINOR=$(echo "$PYTHON_MIN" | cut -d. -f2)

if [ "$PY_MAJOR" -lt "$MIN_MAJOR" ] || ([ "$PY_MAJOR" -eq "$MIN_MAJOR" ] && [ "$PY_MINOR" -lt "$MIN_MINOR" ]); then
    fail "Python $PY_VERSION too old (need $PYTHON_MIN+)"
    exit 1
fi
ok "Python $PY_VERSION"

# -- venv ----------------------------------------------------------------------
if [ ! -f "$VENV_PATH/bin/python" ]; then
    info "Creating venv at $VENV_PATH..."
    python3 -m venv "$VENV_PATH"
    ok "venv created"
else
    ok "venv exists"
fi

# -- pip install ---------------------------------------------------------------
info "Installing/upgrading pip..."
"$VENV_PATH/bin/pip" install --quiet --upgrade pip

info "Installing requirements.txt..."
"$VENV_PATH/bin/pip" install --quiet -r "$INSTALL_PATH/requirements.txt"
ok "Dependencies installed"

# -- WP-CLI check (non-fatal, just a warning) ---------------------------------
if ! command -v wp >/dev/null 2>&1; then
    fail "WP-CLI not installed (required for WP infrastructure phase)"
    echo "  Install: curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar"
    echo "           chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp"
    echo "  Or use --skip-wp to bypass WP provisioning"
else
    ok "WP-CLI: $(wp --version 2>/dev/null | head -1)"
fi

# -- .env check ----------------------------------------------------------------
if [ ! -f "$INSTALL_PATH/.env" ]; then
    fail ".env not found at $INSTALL_PATH/.env"
    if [ -f "$INSTALL_PATH/.env.sample" ]; then
        echo "  Template available: cp $INSTALL_PATH/.env.sample $INSTALL_PATH/.env"
        echo "  Then edit .env with your values and re-run this script"
    fi
    exit 1
fi
ok ".env present"

# -- Hand off to deploy.py -----------------------------------------------------
banner "Handing off to deploy.py"

cd "$INSTALL_PATH"
exec "$VENV_PATH/bin/python" scripts/deploy.py --path "$INSTALL_PATH" "$@"
