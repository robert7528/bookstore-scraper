#!/bin/bash
set -e

APP_DIR="/opt/bookstore-scraper"
SERVICE_NAME="bookstore-scraper"

echo "=== Installing bookstore-scraper ==="

# Clone or update
if [ -d "$APP_DIR" ]; then
    cd "$APP_DIR" && git pull
else
    git clone https://github.com/robert7528/bookstore-scraper.git "$APP_DIR"
fi

cd "$APP_DIR"

# Create venv and install
python3 -m venv .venv
.venv/bin/pip install -e .

# Install systemd service
cp deploy/bookstore-scraper.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo "=== Done ==="
echo "Status: systemctl status $SERVICE_NAME"
echo "Logs:   journalctl -u $SERVICE_NAME -f"
