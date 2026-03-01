#!/usr/bin/env bash
#
# Deploy CordFeeder to a running droplet.
# Usage: DROPLET_IP=x.x.x.x ./deploy.sh
#
set -euo pipefail

DROPLET_NAME="${DROPLET_NAME:-cordfeeder}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy] ERROR: %s\n' "$*" >&2; exit 1; }

# Resolve IP from env or doctl
if [[ -z "${DROPLET_IP:-}" ]]; then
    command -v doctl >/dev/null 2>&1 || die "DROPLET_IP not set and doctl not available"
    DROPLET_IP=$(doctl compute droplet get "$DROPLET_NAME" --format PublicIPv4 --no-header 2>/dev/null) \
        || die "Could not look up IP for droplet '$DROPLET_NAME'"
    log "Resolved droplet IP: $DROPLET_IP"
fi

log "Deploying to $DROPLET_IP..."

ssh "root@$DROPLET_IP" bash <<'REMOTE'
set -euo pipefail
cd /root/cordfeeder

echo "[deploy] Pulling latest code..."
git pull origin main

echo "[deploy] Syncing dependencies..."
~/.local/bin/uv sync --frozen --no-dev

echo "[deploy] Restarting service..."
systemctl restart cordfeeder

echo "[deploy] Recent logs:"
journalctl -u cordfeeder -n 20 --no-pager
REMOTE

log "Deploy complete."
