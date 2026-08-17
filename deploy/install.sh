#!/usr/bin/env bash
# One-shot install on Ubuntu 24.04 (run as root on the VPS).
set -euo pipefail

APP=/var/www/sthiraroute
REPO_URL="${REPO_URL:?Set REPO_URL to the public git clone URL}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx git curl

# Stop temporary MIP occupancy of the public site (leave /var/www/mip intact).
if command -v pm2 >/dev/null 2>&1; then
  pm2 stop mip-web || true
  pm2 save || true
fi

if [ ! -d "$APP/.git" ]; then
  mkdir -p /var/www
  git clone "$REPO_URL" "$APP"
else
  cd "$APP" && git pull --ff-only
fi

cd "$APP/apps/api"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  # Same-origin behind nginx; SQLite is fine for the demo.
  sed -i 's|JWT_SECRET=change-me|JWT_SECRET='"$(openssl rand -hex 24)"'|' .env
fi
PYTHONPATH=. python scripts/seed.py --reset

cd "$APP/apps/web"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
npm ci --no-audit --no-fund
npm run build

cp "$APP/deploy/sthiraroute-api.service" /etc/systemd/system/sthiraroute-api.service
systemctl daemon-reload
systemctl enable --now sthiraroute-api

# Swap nginx site (keep a backup of the previous mip vhost).
if [ -f /etc/nginx/sites-available/mip ]; then
  cp /etc/nginx/sites-available/mip /etc/nginx/sites-available/mip.bak.sthiraroute || true
fi
cp "$APP/deploy/nginx-sthiraroute.conf" /etc/nginx/sites-available/sthiraroute
ln -sfn /etc/nginx/sites-available/sthiraroute /etc/nginx/sites-enabled/sthiraroute
rm -f /etc/nginx/sites-enabled/mip
nginx -t
systemctl reload nginx

echo "SthiraRoute is live. Health: curl -fsS https://rohensingh.in/health"
