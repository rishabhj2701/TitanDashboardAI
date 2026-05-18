#!/usr/bin/env bash
# Run ON the Oracle Cloud VM (Ubuntu 22.04/24.04 ARM) after SSH login.
# Installs Docker, cloudflared, clones the repo, and prepares systemd units.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/rishabhj2701/TitanDashboardAI.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/titan}"
BRANCH="${BRANCH:-main}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root:  sudo bash scripts/oracle-vm-bootstrap.sh"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ca-certificates curl git rsync ufw

# Docker
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

if id ubuntu &>/dev/null; then
  usermod -aG docker ubuntu
fi

# cloudflared (quick tunnel + optional named tunnel later)
if ! command -v cloudflared >/dev/null 2>&1; then
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64|arm64) CF_ARCH=arm64 ;;
    x86_64|amd64) CF_ARCH=amd64 ;;
    *) echo "Unsupported arch: $ARCH"; exit 1 ;;
  esac
  CF_VER="$(curl -fsSL https://api.github.com/repos/cloudflare/cloudflared/releases/latest | grep -m1 tag_name | cut -d\" -f4)"
  curl -fsSL -o /usr/local/bin/cloudflared \
    "https://github.com/cloudflare/cloudflared/releases/download/${CF_VER}/cloudflared-linux-${CF_ARCH}"
  chmod +x /usr/local/bin/cloudflared
fi

mkdir -p "$INSTALL_DIR"
if [[ ! -d "$INSTALL_DIR/.git" ]]; then
  git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH" || true
fi

cd "$INSTALL_DIR"

if [[ ! -f .env ]]; then
  cp .env.oracle.example .env
  if command -v openssl >/dev/null 2>&1; then
    JWT="$(openssl rand -hex 32)"
    sed -i "s/^JWT_SECRET=.*/JWT_SECRET=${JWT}/" .env
  fi
  echo ""
  echo "Created $INSTALL_DIR/.env — set these before first start:"
  echo "  nano $INSTALL_DIR/.env"
  echo "    GOOGLE_API_KEY=...          (or AGENT_LITELLM_API_KEY + litellm backend)"
  echo "    VITE_MAPBOX_TOKEN=...       (required for map tiles in web image)"
fi

# Firewall: SSH only (tunnel exposes the app; no public 8080/5432)
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

install -m 0644 infra/systemd/titan-demo.service /etc/systemd/system/titan-demo.service
install -m 0644 infra/systemd/cloudflared-titan.service /etc/systemd/system/cloudflared-titan.service
systemctl daemon-reload

cat <<EOF

============================================================
 Oracle VM bootstrap complete
============================================================
Install dir: $INSTALL_DIR

Next steps:
  1. Edit secrets:
       nano $INSTALL_DIR/.env

  2. (From your Mac) upload the Iowa DB dump:
       ./scripts/oracle-upload-db.sh YOUR_VM_PUBLIC_IP

  3. Start the stack:
       sudo systemctl enable --now titan-demo
       sudo systemctl enable --now cloudflared-titan

  4. Public URL (after ~30s):
       journalctl -u cloudflared-titan -f
     Look for a line like:  https://....trycloudflare.com

  5. Update .env with that URL, rebuild web if needed, restart:
       OAUTH_REDIRECT_BASE=https://....
       cd $INSTALL_DIR && docker compose --profile localdb \\
         -f docker-compose.yml -f docker-compose.oracle.yml up -d --build web app_api
============================================================
EOF
