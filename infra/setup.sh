#!/usr/bin/env bash
#
# One-time setup: create a DigitalOcean droplet for CordFeeder.
# Prerequisites: brew install doctl && doctl auth init
#
set -euo pipefail

DROPLET_NAME="${DROPLET_NAME:-cordfeeder}"
DROPLET_REGION="${DROPLET_REGION:-sfo3}"
DROPLET_SIZE="${DROPLET_SIZE:-s-1vcpu-1gb}"
DROPLET_IMAGE="ubuntu-24-04-x64"
FIREWALL_NAME="${DROPLET_NAME}-fw"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_rsa.pub}"
REPO_URL="https://github.com/thommackey/cordfeeder.git"

log() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

# -- Preflight ---------------------------------------------------------------
command -v doctl >/dev/null 2>&1 || die "doctl not found. Install with: brew install doctl"
doctl account get >/dev/null 2>&1 || die "doctl not authenticated. Run: doctl auth init"

[[ -f "$SSH_KEY_PATH" ]] || die "SSH public key not found at $SSH_KEY_PATH"

# -- SSH key ------------------------------------------------------------------
SSH_KEY_FINGERPRINT=$(ssh-keygen -l -E md5 -f "$SSH_KEY_PATH" | awk '{print $2}' | sed 's/^MD5://')

if doctl compute ssh-key get "$SSH_KEY_FINGERPRINT" >/dev/null 2>&1; then
    log "SSH key already registered with DigitalOcean"
else
    log "Uploading SSH key to DigitalOcean..."
    doctl compute ssh-key import "${DROPLET_NAME}-key" --public-key-file "$SSH_KEY_PATH"
fi

SSH_KEY_ID=$(doctl compute ssh-key get "$SSH_KEY_FINGERPRINT" --format ID --no-header)

# -- Droplet ------------------------------------------------------------------
if doctl compute droplet get "$DROPLET_NAME" >/dev/null 2>&1; then
    die "Droplet '$DROPLET_NAME' already exists. Delete it first or choose a different name."
fi

log "Creating droplet '$DROPLET_NAME' ($DROPLET_SIZE in $DROPLET_REGION)..."
doctl compute droplet create "$DROPLET_NAME" \
    --image "$DROPLET_IMAGE" \
    --region "$DROPLET_REGION" \
    --size "$DROPLET_SIZE" \
    --ssh-keys "$SSH_KEY_ID" \
    --wait

DROPLET_IP=$(doctl compute droplet get "$DROPLET_NAME" --format PublicIPv4 --no-header)
log "Droplet ready at $DROPLET_IP"

# -- Firewall -----------------------------------------------------------------
DROPLET_ID=$(doctl compute droplet get "$DROPLET_NAME" --format ID --no-header)

log "Creating firewall '$FIREWALL_NAME'..."
doctl compute firewall create \
    --name "$FIREWALL_NAME" \
    --droplet-ids "$DROPLET_ID" \
    --inbound-rules "protocol:tcp,ports:22,address:0.0.0.0/0,address:::/0" \
    --outbound-rules "protocol:tcp,ports:all,address:0.0.0.0/0,address:::/0 protocol:udp,ports:all,address:0.0.0.0/0,address:::/0 protocol:icmp,address:0.0.0.0/0,address:::/0"

# -- Provision ----------------------------------------------------------------
log "Waiting for SSH to become available..."
for i in $(seq 1 30); do
    if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new "root@$DROPLET_IP" true 2>/dev/null; then
        break
    fi
    sleep 2
done

log "Provisioning droplet..."
ssh "root@$DROPLET_IP" bash <<'REMOTE'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -yqq ca-certificates curl git

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
REMOTE

log "Cloning repository..."
ssh "root@$DROPLET_IP" "git clone $REPO_URL /root/cordfeeder"

log "Installing dependencies and systemd unit..."
ssh "root@$DROPLET_IP" bash <<'REMOTE'
set -euo pipefail
cd /root/cordfeeder

# Install Python dependencies
~/.local/bin/uv sync --frozen --no-dev

# Install and enable systemd service
cp infra/cordfeeder.service /etc/systemd/system/cordfeeder.service
systemctl daemon-reload
systemctl enable cordfeeder
REMOTE

# -- Done ---------------------------------------------------------------------
log ""
log "=== Setup complete ==="
log "Droplet IP: $DROPLET_IP"
log ""
log "Next steps:"
log "  1. Copy your .env file to the droplet:"
log "     scp .env root@${DROPLET_IP}:~/cordfeeder/.env"
log "  2. Deploy the bot:"
log "     DROPLET_IP=${DROPLET_IP} ./deploy.sh"
