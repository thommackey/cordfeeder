# Discord Bot Setup Guide

This guide walks through creating a Discord application, configuring the bot, and getting CordFeeder running.

---

## 1. Create a Discord Application

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and sign in.
2. Click **New Application** (top-right).
3. Give it a name (e.g. `CordFeeder`) and click **Create**.

---

## 2. Create a Bot User and Get the Token

1. In your application, open the **Bot** tab (left sidebar).
2. Click **Add Bot**, then confirm.
3. Under **Token**, click **Reset Token** and copy the token that appears.
   - Store this securely — you will not see it again without resetting it.
   - Never commit it to version control.
4. Under **Privileged Gateway Intents**, leave all three toggles **off**.
   CordFeeder does not require any privileged intents.

---

## 3. Required Permissions

CordFeeder needs three permissions:

| Permission               | Bit value      |
|--------------------------|----------------|
| Send Messages            | 2,048          |
| Embed Links              | 16,384         |
| Use Application Commands | 2,147,483,648  |

**Combined permission integer: `2147502080`**

---

## 4. Generate the Invite URL

1. In your application, open the **OAuth2** tab, then **URL Generator**.
2. Under **Scopes**, tick:
   - `bot`
   - `applications.commands`
3. Under **Bot Permissions**, tick:
   - Send Messages
   - Embed Links
   - Use Application Commands
4. Verify the permission integer shown at the bottom reads `2147502080`.
5. Copy the generated URL.

Alternatively, construct the URL manually:

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=2147502080&scope=bot+applications.commands
```

Replace `YOUR_CLIENT_ID` with the **Application ID** shown on the **General Information** tab.

---

## 5. Invite the Bot to Your Server

1. Open the invite URL in a browser.
2. Select the server you want to add the bot to.
3. Click **Authorise** and complete any CAPTCHA.

You must have the **Manage Server** permission on the target server.

---

## 6. Create the Feed Manager Role

CordFeeder restricts feed management commands (`/feed add`, `/feed remove`, `/feed config`) to users who hold a designated role. The default role name is **Feed Manager**.

1. In your Discord server, open **Server Settings → Roles**.
2. Click **Create Role**.
3. Name it exactly `Feed Manager` (case-sensitive, must match `FEED_MANAGER_ROLE` in your `.env`).
4. Assign this role to any users who should be able to manage feeds.

If you want a different role name, set `FEED_MANAGER_ROLE` in your `.env` accordingly and create a role with that exact name.

> `/feed list` and `/feed preview` are available to everyone without a role check.

---

## 7. Configure the `.env` File

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```dotenv
# Required
DISCORD_BOT_TOKEN=your-bot-token-here

# Role name for feed management commands (default: Feed Manager)
FEED_MANAGER_ROLE=Feed Manager

# How often to poll feeds in seconds (default: 900 = 15 minutes; min: 300, max: 43200)
DEFAULT_POLL_INTERVAL=900

# SQLite database path (relative to working directory)
DATABASE_PATH=cordfeeder.db

# Log level: TRACE, DEBUG, INFO, WARN, ERROR (default: INFO)
LOG_LEVEL=INFO
```

---

## 8. Run the Bot

Install dependencies and start CordFeeder:

```bash
uv sync
uv run cordfeeder
```

On first start you should see log lines similar to:

```json
{"ts":"2026-02-27T03:00:00.000Z","level":"INFO","logger":"cordfeeder.main","msg":"starting cordfeeder","host":"myhost","app":"cordfeeder"}
{"ts":"2026-02-27T03:00:01.123Z","level":"INFO","logger":"cordfeeder.bot","msg":"bot setup complete","host":"myhost","app":"cordfeeder"}
```

Once running, slash commands (`/feed add`, `/feed list`, etc.) will be available in any server the bot has joined. Discord can take a few minutes to propagate global slash commands after the first sync.

---

## Troubleshooting

**Commands not appearing in Discord**
Global command sync can take up to one hour on first deployment. Restart the bot and wait.

**"You need the Feed Manager role" on every command**
The role name in Discord must match `FEED_MANAGER_ROLE` exactly, including capitalisation.

**Bot posts nothing after `/feed add`**
Ensure the bot has permission to send messages and embeds in the target channel. Check that the feed URL is publicly accessible and returns valid RSS or Atom XML.

**Token errors on startup**
Double-check `DISCORD_BOT_TOKEN` in your `.env`. If you reset the token in the Developer Portal, you must update `.env` to match.
