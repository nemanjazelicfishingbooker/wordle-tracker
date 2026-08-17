# Wordle Discord Tracker

Daily bot that scans your Discord server for Wordle results, posts a summary, and keeps an all-time scoreboard.

## Setup

### 1. Create a Discord Bot

1. Go to https://discord.com/developers/applications
2. Click **New Application**, name it (e.g. "Wordle Tracker")
3. Go to **Bot** tab → click **Reset Token** → copy the token
4. Under **Privileged Gateway Intents**, enable:
   - **Message Content Intent** ✅
   - **Server Members Intent** ✅
5. Go to **OAuth2** → **URL Generator**:
   - Scopes: `bot`
   - Bot Permissions: `Read Messages/View Channels`, `Send Messages`, `Read Message History`
6. Open the generated URL to invite the bot to your server

### 2. Get your Discord IDs

Enable **Developer Mode** in Discord (Settings → Advanced → Developer Mode), then:

- **Guild (Server) ID**: Right-click the server name → Copy Server ID
- **Summary Channel ID**: Right-click the channel where the bot should post → Copy Channel ID

### 3. Create a GitHub repo

```bash
cd wordle
git init
git add .
git commit -m "Initial commit"
gh repo create wordle-tracker --private --source=. --push
```

### 4. Add GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → **New repository secret**:

| Secret name                  | Value                                    |
| ---------------------------- | ---------------------------------------- |
| `DISCORD_BOT_TOKEN`         | The bot token from step 1                |
| `DISCORD_GUILD_ID`          | Your server ID                           |
| `DISCORD_SUMMARY_CHANNEL_ID`| The channel ID for daily summaries       |

### 5. Done!

The bot runs every day at 08:00 Belgrade time (06:00 UTC). You can also trigger it manually from the Actions tab (workflow_dispatch).

## How it works

- Scans **all text channels** the bot can read for Wordle share messages (last 26 hours)
- Tracks users by their **underlying Discord user ID**, not nicknames
- Maintains a persistent scoreboard in `data/scoreboard.json`
- Posts a daily summary + all-time scoreboard to the designated channel
- GitHub Actions commits the updated scoreboard back to the repo

## Adjusting the schedule

Edit the cron in `.github/workflows/wordle.yml`. The cron is in UTC.
