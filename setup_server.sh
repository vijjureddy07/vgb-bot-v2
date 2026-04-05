#!/bin/bash
# ============================================================
# VGB Bot v2 — DigitalOcean Setup Script
# ============================================================
# Run this on a fresh Ubuntu 22.04/24.04 droplet:
#   chmod +x setup_server.sh
#   sudo ./setup_server.sh
# ============================================================

set -e

echo "=========================================="
echo "VGB Bot v2 — Server Setup"
echo "=========================================="

# Update system
echo "[1/6] Updating system..."
apt update && apt upgrade -y

# Install Python & dependencies
echo "[2/6] Installing Python..."
apt install -y python3 python3-pip python3-venv git curl

# Create bot directory
echo "[3/6] Setting up bot directory..."
BOT_DIR="/opt/vgb_bot_v2"
mkdir -p $BOT_DIR
cp -r ./*.py $BOT_DIR/

# Create virtual environment
echo "[4/6] Creating virtual environment..."
cd $BOT_DIR
python3 -m venv venv
source venv/bin/activate
pip install requests pandas numpy

# Create systemd service
echo "[5/6] Creating systemd service..."
cat > /etc/systemd/system/vgb-bot.service << 'EOF'
[Unit]
Description=VGB Trading Bot v2
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/vgb_bot_v2
ExecStart=/opt/vgb_bot_v2/venv/bin/python3 /opt/vgb_bot_v2/main.py
Restart=always
RestartSec=30
StartLimitIntervalSec=600
StartLimitBurst=10

# Environment
Environment=PYTHONUNBUFFERED=1

# Logging
StandardOutput=append:/opt/vgb_bot_v2/stdout.log
StandardError=append:/opt/vgb_bot_v2/stderr.log

# Resource limits
MemoryMax=512M
CPUQuota=80%

[Install]
WantedBy=multi-user.target
EOF

# Create log rotation
echo "[6/6] Setting up log rotation..."
cat > /etc/logrotate.d/vgb-bot << 'EOF'
/opt/vgb_bot_v2/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    copytruncate
}
EOF

# Reload systemd
systemctl daemon-reload

echo ""
echo "=========================================="
echo "SETUP COMPLETE!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Edit config:  nano /opt/vgb_bot_v2/config.py"
echo "     - Add BINANCE_API_KEY and BINANCE_API_SECRET"
echo "     - Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
echo ""
echo "  2. Test:          cd /opt/vgb_bot_v2 && source venv/bin/activate && python3 test_bot.py"
echo ""
echo "  3. Start bot:     sudo systemctl start vgb-bot"
echo "  4. Check status:  sudo systemctl status vgb-bot"
echo "  5. View logs:     sudo journalctl -u vgb-bot -f"
echo "  6. Enable boot:   sudo systemctl enable vgb-bot"
echo ""
echo "Management commands:"
echo "  Stop:     sudo systemctl stop vgb-bot"
echo "  Restart:  sudo systemctl restart vgb-bot"
echo "  Logs:     tail -f /opt/vgb_bot_v2/vgb_bot.log"
echo "  Trades:   cat /opt/vgb_bot_v2/trade_log.csv"
echo ""
