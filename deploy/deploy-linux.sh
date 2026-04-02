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

# --- [3/7] Install dependencies ---

echo ""
echo "=== [3/7] Install dependencies ==="
if [ ! -d "$APP_DIR/.venv" ]; then
    python3 -m venv .venv
    info "Created venv"
fi
.venv/bin/pip install -e ".[browser,undetected]" --quiet
info "Dependencies installed"

# --- [4/7] Install Google Chrome + Xvfb ---

echo ""
echo "=== [4/7] Install Google Chrome + Xvfb ==="
if ! command -v google-chrome >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
        wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
        apt-get install -y /tmp/chrome.deb 2>/dev/null || true
        rm -f /tmp/chrome.deb
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm 2>/dev/null || true
    fi
fi
if command -v google-chrome >/dev/null 2>&1; then
    info "Google Chrome: $(google-chrome --version)"
else
    info "WARNING: Google Chrome not installed (browser fallback may not work)"
fi

# Install Xvfb for non-headless browser mode
if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y xvfb 2>/dev/null || true
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y xorg-x11-server-Xvfb 2>/dev/null || true
fi
if command -v xvfb-run >/dev/null 2>&1; then
    info "Xvfb installed"
else
    info "WARNING: Xvfb not installed (headless: false requires Xvfb)"
fi

# --- [5/7] Install Playwright browser (optional fallback) ---

echo ""
echo "=== [5/7] Install Playwright browser (optional) ==="
.venv/bin/python -m playwright install chromium 2>/dev/null || info "Playwright chromium skipped"

# --- [6/7] Create directories ---

echo ""
echo "=== [6/7] Create directories ==="
mkdir -p "$LOG_DIR"
info "$LOG_DIR"

# --- [7/7] Install and start service ---

echo ""
echo "=== [7/7] Install and start service ==="

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
