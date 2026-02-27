#!/usr/bin/env bash
#
# Tear down CordFeeder DigitalOcean infrastructure.
#
set -euo pipefail

DROPLET_NAME="${DROPLET_NAME:-cordfeeder}"
FIREWALL_NAME="${DROPLET_NAME}-fw"

log() { printf '[teardown] %s\n' "$*"; }
die() { printf '[teardown] ERROR: %s\n' "$*" >&2; exit 1; }

command -v doctl >/dev/null 2>&1 || die "doctl not found. Install with: brew install doctl"

# ── Show what will be destroyed ────────────────────────────────────────
echo ""
echo "This will destroy:"

DROPLET_EXISTS=false
if doctl compute droplet get "$DROPLET_NAME" >/dev/null 2>&1; then
    DROPLET_IP=$(doctl compute droplet get "$DROPLET_NAME" --format PublicIPv4 --no-header)
    echo "  - Droplet: $DROPLET_NAME ($DROPLET_IP)"
    DROPLET_EXISTS=true
else
    echo "  - Droplet: $DROPLET_NAME (not found)"
fi

FIREWALL_EXISTS=false
FIREWALL_ID=""
if FIREWALL_ID=$(doctl compute firewall list --format ID,Name --no-header | awk -v name="$FIREWALL_NAME" '$2 == name {print $1}') && [[ -n "$FIREWALL_ID" ]]; then
    echo "  - Firewall: $FIREWALL_NAME"
    FIREWALL_EXISTS=true
else
    echo "  - Firewall: $FIREWALL_NAME (not found)"
fi

if ! $DROPLET_EXISTS && ! $FIREWALL_EXISTS; then
    log "Nothing to tear down."
    exit 0
fi

echo ""
read -rp "Are you sure? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { log "Aborted."; exit 0; }

# ── Destroy ────────────────────────────────────────────────────────────
if $DROPLET_EXISTS; then
    log "Deleting droplet '$DROPLET_NAME'..."
    doctl compute droplet delete "$DROPLET_NAME" --force
fi

if $FIREWALL_EXISTS; then
    log "Deleting firewall '$FIREWALL_NAME'..."
    doctl compute firewall delete "$FIREWALL_ID" --force
fi

log "Teardown complete."
