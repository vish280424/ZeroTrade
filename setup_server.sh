#!/bin/bash
# ZeroTrade — one-time server setup script
# Run this on a fresh Ubuntu 24.04 AWS EC2 instance:
#   bash setup_server.sh

set -e

REPO_URL="https://github.com/I341519/ZeroTrade.git"  # update if different
APP_DIR="/home/ubuntu/ZeroTrade"
PYTHON="python3"

echo "==> Updating system packages"
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git

echo "==> Cloning repository"
if [ -d "$APP_DIR" ]; then
  echo "Directory exists, pulling latest..."
  cd "$APP_DIR" && git pull
else
  git clone "$REPO_URL" "$APP_DIR"
fi

echo "==> Setting up Python virtual environment"
cd "$APP_DIR"
$PYTHON -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Checking .env"
if [ ! -f "$APP_DIR/.env" ]; then
  echo ""
  echo "  .env not found!"
  echo "  From your local machine run:"
  echo "    scp .env ubuntu@<your-vps-ip>:$APP_DIR/.env"
  echo "  Then re-run this script."
  echo ""
  exit 1
fi

echo "==> Checking APP_PASSWORD_HASH in .env"
if ! grep -q "APP_PASSWORD_HASH=\$" "$APP_DIR/.env" 2>/dev/null; then
  if grep -q "^APP_PASSWORD_HASH=$" "$APP_DIR/.env"; then
    echo ""
    echo "  APP_PASSWORD_HASH is empty!"
    echo "  Run: python setup_password.py"
    echo "  Then paste the hash into .env"
    echo ""
    exit 1
  fi
fi

echo "==> Creating systemd service"
sudo tee /etc/systemd/system/zerotrade.service > /dev/null <<EOF
[Unit]
Description=ZeroTrade FastAPI app
After=network.target

[Service]
User=ubuntu
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

echo "==> Enabling and starting service"
sudo systemctl daemon-reload
sudo systemctl enable zerotrade
sudo systemctl restart zerotrade

echo ""
echo "==> Done!"
echo ""
echo "  App running at: http://$(curl -s ifconfig.me):8080"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status zerotrade   # check status"
echo "    sudo journalctl -u zerotrade -f   # live logs"
echo "    sudo systemctl restart zerotrade  # restart"
echo ""
