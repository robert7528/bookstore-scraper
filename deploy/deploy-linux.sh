#!/usr/bin/env bash
# Bookstore Scraper - Deployment Script (Linux)
# Usage: sudo bash deploy-linux.sh
set -euo pipefail

APP_DIR="/opt/bookstore-scraper"
SERVICE_NAME="bookstore-scraper"
LOG_DIR="/var/log/$SERVICE_NAME"

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  -> $*"; }

# --- [1/5] Check prerequisites ---

echo "=== [1/5] Check prerequisites ==="
[ "$(id -u)" -eq 0 ] || die "Please run with sudo"
command -v python3 >/dev/null 2>&1 || die "Python3 not found. Install Python 3.11+ first."
PY_VER=$(python3 --version)
info "Python: $PY_VER"

# --- [2/5] Clone or update ---

echo ""
echo "=== [2/5] Clone or update ==="
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR" && git pull
    info "Updated: $APP_DIR"
else
    git clone https://github.com/robert7528/bookstore-scraper.git "$APP_DIR"
    cd "$APP_DIR"
    info "Cloned: $APP_DIR"
fi

# --- [3/5] Install dependencies ---

echo ""
echo "=== [3/5] Install dependencies ==="
if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv .venv
    info "Created venv"
fi
.venv/bin/pip install -e . --quiet
info "Dependencies installed"

# --- [4/5] Create directories ---

echo ""
echo "=== [4/5] Create directories ==="
mkdir -p "$LOG_DIR"
info "$LOG_DIR"

# --- [5/5] Install and start service ---

echo ""
echo "=== [5/5] Install and start service ==="

.venv/bin/python -m src.cli service stop 2>/dev/null || true
.venv/bin/python -m src.cli service install
.venv/bin/python -m src.cli service start
sleep 1
.venv/bin/python -m src.cli service status

echo ""
echo "Done."
echo "  App:      $APP_DIR"
echo "  Config:   $APP_DIR/configs/settings.yaml"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  API:      http://localhost:8000"
echo "  Docs:     http://localhost:8000/docs"
echo ""
echo "Commands:"
echo "  .venv/bin/python -m src.cli service status"
echo "  .venv/bin/python -m src.cli service stop"
echo "  .venv/bin/python -m src.cli service start"
echo "  systemctl status $SERVICE_NAME"
