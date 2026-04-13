#!/usr/bin/env bash
# Bookstore Scraper - Deployment Script (Linux)
# Usage: sudo bash deploy-linux.sh [--proxy]
#   --proxy  啟用 Forward Proxy 模式（HyProxy 整合）
set -euo pipefail

APP_DIR="/opt/bookstore-scraper"
SERVICE_NAME="bookstore-scraper"
ENABLE_PROXY=false

[ "${1:-}" = "--proxy" ] && ENABLE_PROXY=true

die()  { echo "ERROR: $*" >&2; exit 1; }
info() { echo "  -> $*"; }
warn() { echo "  ⚠️  $*"; }

# --- [1/9] Check prerequisites ---

echo "=== [1/9] Check prerequisites ==="
[ "$(id -u)" -eq 0 ] || die "Please run with sudo"

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    info "OS: $NAME $VERSION_ID"
else
    warn "Unknown OS"
fi

# Check IP stability (proxy mode)
if $ENABLE_PROXY; then
    echo ""
    info "Checking IP stability..."
    IPS=""
    for i in 1 2 3 4 5; do
        IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "error")
        IPS="$IPS $IP"
    done
    UNIQUE=$(echo $IPS | tr ' ' '\n' | sort -u | wc -l)
    if [ "$UNIQUE" -gt 1 ]; then
        warn "NAT pool detected ($UNIQUE IPs). JCR auth may need Angular JS patch."
    else
        info "IP stable: $(echo $IPS | awk '{print $1}')"
    fi
fi

# --- [2/9] Install Python 3.11 (Conda) ---

echo ""
echo "=== [2/9] Install Python 3.11 ==="

PY_VER=$(python3 --version 2>/dev/null | awk '{print $2}' | cut -d. -f1,2)
if [ "$(echo "$PY_VER < 3.11" | bc 2>/dev/null)" = "1" ] || [ -z "$PY_VER" ]; then
    info "System Python $PY_VER too old, installing Conda..."

    if [ ! -d /opt/miniconda3 ]; then
        curl -fsSL https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -o /tmp/miniconda.sh
        bash /tmp/miniconda.sh -b -p /opt/miniconda3
        rm -f /tmp/miniconda.sh
    fi

    # Accept TOS (required for Conda 2025+)
    /opt/miniconda3/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main 2>/dev/null || true
    /opt/miniconda3/bin/conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r 2>/dev/null || true

    if [ ! -d /opt/miniconda3/envs/scraper ]; then
        /opt/miniconda3/bin/conda create -p /opt/miniconda3/envs/scraper python=3.11 -y
    fi

    PYTHON="/opt/miniconda3/envs/scraper/bin/python"
    info "Python: $($PYTHON --version)"
else
    PYTHON="python3"
    info "Python: $(python3 --version)"
fi

# --- [3/9] Install Chrome + Xvfb + dependencies ---

echo ""
echo "=== [3/9] Install Chrome + Xvfb + dependencies ==="

if ! command -v google-chrome >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
        # Rocky/CentOS
        cat > /etc/yum.repos.d/google-chrome.repo << 'CHROMEEOF'
[google-chrome]
name=Google Chrome
baseurl=https://dl.google.com/linux/chrome/rpm/stable/x86_64
enabled=1
gpgcheck=1
gpgkey=https://dl.google.com/linux/linux_signing_key.pub
CHROMEEOF
        dnf install -y google-chrome-stable --disablerepo=kubernetes 2>/dev/null || \
        dnf install -y google-chrome-stable 2>/dev/null || true
    elif command -v apt-get >/dev/null 2>&1; then
        # Ubuntu/Debian
        wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
        apt-get install -y /tmp/chrome.deb 2>/dev/null || true
        rm -f /tmp/chrome.deb
    fi
fi

if command -v google-chrome >/dev/null 2>&1; then
    info "Chrome: $(google-chrome --version 2>/dev/null)"
else
    warn "Chrome not installed (browser fallback unavailable)"
fi

# Xvfb + dbus + Chrome dependencies
if command -v dnf >/dev/null 2>&1; then
    dnf install -y xorg-x11-server-Xvfb dbus-x11 dbus-libs \
        mesa-libGL mesa-libEGL libXcomposite libXdamage libXrandr \
        libXi libXtst alsa-lib atk at-spi2-atk cups-libs libdrm \
        libxkbcommon pango nss nspr gtk3 xdg-utils \
        --disablerepo=kubernetes 2>/dev/null || \
    dnf install -y xorg-x11-server-Xvfb dbus-x11 dbus-libs \
        mesa-libGL mesa-libEGL libXcomposite libXdamage libXrandr \
        libXi libXtst alsa-lib atk at-spi2-atk cups-libs libdrm \
        libxkbcommon pango nss nspr gtk3 xdg-utils 2>/dev/null || true
elif command -v apt-get >/dev/null 2>&1; then
    apt-get install -y xvfb 2>/dev/null || true
fi

if command -v xvfb-run >/dev/null 2>&1; then
    info "Xvfb installed"
else
    warn "Xvfb not installed (headless: false requires Xvfb)"
fi

# --- [4/9] Clone or update ---

echo ""
echo "=== [4/9] Clone or update ==="
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git checkout configs/settings.yaml 2>/dev/null || true
    git pull
    info "Updated: $APP_DIR"
else
    git clone https://github.com/robert7528/bookstore-scraper.git "$APP_DIR"
    cd "$APP_DIR"
    info "Cloned: $APP_DIR"
fi

# --- [5/9] Create venv + install ---

echo ""
echo "=== [5/9] Install dependencies ==="
if [ ! -d "$APP_DIR/.venv" ]; then
    $PYTHON -m venv .venv
    info "Created venv"
fi
.venv/bin/pip install -e ".[undetected]" --quiet
info "Dependencies installed"

# --- [6/9] Configure settings ---

echo ""
echo "=== [6/9] Configure settings ==="
if $ENABLE_PROXY; then
    sed -i 's/^  enabled: false/  enabled: true/' configs/settings.yaml
    info "Proxy enabled (port 8102)"
else
    info "Proxy disabled (Fetch API only)"
fi

# --- [7/9] Install systemd service ---

echo ""
echo "=== [7/9] Install systemd service ==="
cat > /etc/systemd/system/$SERVICE_NAME.service << SVCEOF
[Unit]
Description=Bookstore Scraper API
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/xvfb-run --auto-servernum --server-args="-screen 0 1280x720x24" $APP_DIR/.venv/bin/python -m uvicorn src.main:app --host 0.0.0.0 --port 8101
Restart=always
RestartSec=5
Environment=PYTHONPATH=$APP_DIR

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME
sleep 3

if systemctl is-active $SERVICE_NAME &>/dev/null; then
    info "Service running"
    MEM=$(systemctl status $SERVICE_NAME 2>/dev/null | grep Memory | awk '{print $2}')
    info "Memory: $MEM"
else
    warn "Service failed to start! Check: journalctl -u $SERVICE_NAME -n 20"
fi

# --- [8/9] Setup monitoring ---

echo ""
echo "=== [8/9] Setup monitoring ==="
mkdir -p $APP_DIR/logs
chmod +x $APP_DIR/tools/monitor_cookies.sh $APP_DIR/tools/monitor_proxy.sh 2>/dev/null || true

(crontab -l 2>/dev/null | grep -v "monitor_cookies" | grep -v "monitor_proxy"; \
 echo "0 * * * * $APP_DIR/tools/monitor_cookies.sh"; \
 echo "0 * * * * $APP_DIR/tools/monitor_proxy.sh") | crontab -

info "Crontab configured (hourly monitoring)"

# Run once
$APP_DIR/tools/monitor_cookies.sh 2>/dev/null && info "Cookie monitor OK" || warn "Cookie monitor failed (redis not available?)"
$APP_DIR/tools/monitor_proxy.sh 2>/dev/null && info "Proxy monitor OK" || true

# --- [9/9] Verify ---

echo ""
echo "=== [9/9] Verify ==="
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8101/ 2>/dev/null)
info "Fetch API: HTTP $HEALTH"

if $ENABLE_PROXY; then
    PROXY=$(ss -tlnp | grep 8102 | wc -l)
    if [ "$PROXY" -gt 0 ]; then
        info "Proxy 8102: listening"
    else
        warn "Proxy 8102: not listening!"
    fi
fi

echo ""
echo "============================================================"
echo "Deployment complete!"
echo "============================================================"
echo ""
echo "  App:      $APP_DIR"
echo "  Config:   $APP_DIR/configs/settings.yaml"
echo "  Logs:     journalctl -u $SERVICE_NAME -f"
echo "  Monitor:  $APP_DIR/logs/"
echo "  API:      http://localhost:8101"
if $ENABLE_PROXY; then
echo "  Proxy:    localhost:8102"
echo ""
echo "HyProxy setup required:"
echo "  1. config.yml: add proxys → antibot: 127.0.0.1:8102"
echo "  2. WoS + JCR: add use-proxy: antibot (from admin UI)"
echo "  3. redis-cli -n <select> FLUSHDB && systemctl restart hyproxy"
fi
echo ""
