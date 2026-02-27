# CordFeeder Infrastructure

Scripts for deploying CordFeeder to a DigitalOcean droplet.

## Prerequisites

```bash
brew install doctl
doctl auth init  # paste your DO API token
```

## First-time setup

```bash
# Create droplet, firewall, install Docker, clone repo
./infra/setup.sh

# Copy secrets to the droplet (IP printed by setup.sh)
scp .env root@<DROPLET_IP>:~/cordfeeder/.env

# Build and start the bot
DROPLET_IP=<DROPLET_IP> ./deploy.sh
```

## Subsequent deploys

```bash
DROPLET_IP=<DROPLET_IP> ./deploy.sh
```

Or omit `DROPLET_IP` if you have `doctl` installed — the script will look it up.

## Tear down

```bash
./infra/teardown.sh
```

## Configuration

All scripts read these environment variables (with defaults):

| Variable | Default | Description |
|---|---|---|
| `DROPLET_NAME` | `cordfeeder` | Name for the droplet and firewall prefix |
| `DROPLET_REGION` | `sfo3` | DigitalOcean region |
| `DROPLET_SIZE` | `s-1vcpu-1gb` | Droplet size ($6/mo) |
| `DROPLET_IP` | (looked up via doctl) | Deploy target IP |
| `SSH_KEY_PATH` | `~/.ssh/id_rsa.pub` | SSH public key to register |
