#!/usr/bin/env bash
# Run FROM your Mac (or any machine with the dump and SSH to the VM).
# Uploads demo-traffic.dump and restores into the VM's PostGIS container.
set -euo pipefail

VM_HOST="${1:-}"
SSH_USER="${SSH_USER:-ubuntu}"
SSH_KEY="${SSH_KEY:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/titan}"
DUMP_LOCAL="${2:-./demo-traffic.dump}"
REMOTE_DUMP="/tmp/demo-traffic.dump"

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [[ -n "$SSH_KEY" ]]; then
  SSH_OPTS+=(-i "$SSH_KEY")
fi
ssh_cmd() { ssh "${SSH_OPTS[@]}" "$@"; }

if [[ -z "$VM_HOST" ]]; then
  echo "Usage: $0 VM_PUBLIC_IP [path/to/demo-traffic.dump]"
  echo ""
  echo "Export locally first:"
  echo "  ./scripts/export-demo-db.sh ./demo-traffic.dump"
  exit 1
fi

if [[ ! -f "$DUMP_LOCAL" ]]; then
  echo "Missing dump file: $DUMP_LOCAL"
  echo "Create it with: ./scripts/export-demo-db.sh $DUMP_LOCAL"
  exit 1
fi

RSYNC_SSH="ssh ${SSH_OPTS[*]}"
echo ">>> Uploading $(du -h "$DUMP_LOCAL" | cut -f1) to ${SSH_USER}@${VM_HOST} ..."
rsync -avP -e "$RSYNC_SSH" "$DUMP_LOCAL" "${SSH_USER}@${VM_HOST}:${REMOTE_DUMP}"

echo ">>> Starting PostGIS on VM (if not running) ..."
ssh_cmd "${SSH_USER}@${VM_HOST}" \
  "cd ${INSTALL_DIR} && sudo docker compose --env-file .env --profile localdb -f docker-compose.yml -f docker-compose.oracle.yml up -d postgis"

echo ">>> Waiting for Postgres ..."
ssh_cmd "${SSH_USER}@${VM_HOST}" bash -s <<REMOTE
set -euo pipefail
for i in \$(seq 1 60); do
  if sudo docker exec postgis pg_isready -U postgres -d traffic >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
sudo docker exec postgis pg_isready -U postgres -d traffic
REMOTE

echo ">>> Restoring (30–60+ minutes for full Iowa data) ..."
ssh_cmd "${SSH_USER}@${VM_HOST}" bash -s <<REMOTE
set -euo pipefail
sudo docker cp ${REMOTE_DUMP} postgis:/tmp/demo-traffic.dump
sudo docker exec postgis pg_restore -U postgres -d traffic --clean --if-exists --no-owner /tmp/demo-traffic.dump || true
sudo docker exec postgis rm -f /tmp/demo-traffic.dump
rm -f ${REMOTE_DUMP}
REMOTE

echo ""
echo ">>> Starting full stack on VM ..."
ssh_cmd "${SSH_USER}@${VM_HOST}" \
  "sudo systemctl enable titan-demo cloudflared-titan && sudo systemctl restart titan-demo && sudo systemctl restart cloudflared-titan"

echo ""
echo ">>> Public URL (wait ~30s, then run on VM):"
echo "  ssh ${SSH_USER}@${VM_HOST} 'sudo journalctl -u cloudflared-titan -n 80 | grep trycloudflare'"
echo ""
echo ">>> Then set OAUTH_REDIRECT_BASE in /opt/titan/.env to that https URL and:"
echo "  ssh ${SSH_USER}@${VM_HOST} 'cd /opt/titan && sudo docker compose --env-file .env -f docker-compose.yml -f docker-compose.oracle.yml up -d --build web app_api'"
