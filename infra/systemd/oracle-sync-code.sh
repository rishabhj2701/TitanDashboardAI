#!/usr/bin/env bash
# Push local repo to Oracle VM (faster than waiting for GitHub). Run from Mac.
set -euo pipefail

VM_HOST="${1:-}"
SSH_USER="${SSH_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/titan}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -z "$VM_HOST" ]]; then
  echo "Usage: $0 VM_PUBLIC_IP"
  echo "  SSH_KEY=~/.ssh/oracle.key $0 1.2.3.4"
  exit 1
fi

RSYNC_SSH="ssh -o StrictHostKeyChecking=accept-new"
if [[ -n "$SSH_KEY" ]]; then
  RSYNC_SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new"
fi

echo ">>> Syncing $ROOT to ${SSH_USER}@${VM_HOST}:${INSTALL_DIR} ..."
rsync -avz --delete \
  --exclude '.git' \
  --exclude 'node_modules' \
  --exclude 'dist' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  --exclude 'iowa-260516.osm.pbf' \
  --exclude 'demo-traffic.dump' \
  --exclude 'uploads/*' \
  -e "$RSYNC_SSH" \
  "$ROOT/" "${SSH_USER}@${VM_HOST}:${INSTALL_DIR}/"

echo ""
echo ">>> On VM, install/update systemd units and restart:"
echo "  ssh ${SSH_USER}@${VM_HOST} 'cd ${INSTALL_DIR} && sudo cp infra/systemd/*.service /etc/systemd/system/ && sudo systemctl daemon-reload'"
echo "  ssh ${SSH_USER}@${VM_HOST} 'sudo systemctl restart titan-demo cloudflared-titan'"
