# Deployment Guide — MLBB Tournament Bot

End-to-end provisioning guide for deploying the bot to a production server.

The bot ships with an **idempotent, 7-phase deployment script** that handles dependency installation, database migration, WordPress infrastructure, systemd service, cron jobs, and log rotation. Re-running it on an already-deployed server is safe.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [`.env` Configuration](#env-configuration)
- [What `deploy.py` Does](#what-deploypy-does)
- [CLI Reference](#cli-reference)
- [Multi-Guild Support](#multi-guild-support)
- [Upgrading an Existing Deployment](#upgrading-an-existing-deployment)
- [Cron Jobs](#cron-jobs)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

On a fresh server with Python 3.11+ and MySQL/WordPress already installed:

```bash
# 1. Clone the repo
git clone https://github.com/MLBB-NA-Discord-Server-Dev-Team/MLBB-TournamentBot.git /root/MLBB-TournamentBot
cd /root/MLBB-TournamentBot

# 2. Copy the env template and fill in your values
cp .env.sample .env
nano .env     # required: DISCORD_TOKEN, WP_PLAY_MLBB, DB_PASSWORD

# 3. Run the bootstrap script
sudo bash scripts/deploy.sh
```

The script takes ~2 minutes. When it finishes, the bot is running as a systemd service, cron jobs are installed, and all 4 Discord channels are auto-bootstrapped.

---

## Prerequisites

Before running `deploy.sh`, the server needs:

| Requirement | How to install |
|-------------|----------------|
| **Linux with systemd** | Tested on Debian/Ubuntu 22.04+ |
| **Python 3.11+** | `apt install python3 python3-venv python3-pip` |
| **MySQL client libs** | `apt install default-libmysqlclient-dev build-essential` |
| **WP-CLI** | `curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar && chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp` |
| **Running WordPress site** | With SportsPress plugin installed at `/var/www/sites/play.mlbb.site` (or update `WP_PATH` in `services/league_lifecycle.py`) |
| **MySQL database** | Shared with the WordPress site |
| **Discord application** | With bot + `applications.commands` scopes and **Server Members** + **Voice State** privileged intents enabled |
| **Claude API key** | For match screenshot parsing |

You will also need:
- The Discord guild ID(s) the bot will operate in
- A category in Discord where the bot can create text channels (or let it auto-create `#MLBB Tournaments`)
- WordPress **Application Password** (not the login password) for REST API writes

---

## `.env` Configuration

Copy `.env.sample` to `.env` and fill in the values. Required keys:

```env
# Discord
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_client_id
GUILD_IDS=comma,separated,guild,ids      # at least one

# Roles (Discord role names, not IDs)
ORGANIZER_ROLES=Tournament Organizer,DEV
ADMIN_ROLES=admins,DEV

# WordPress / SportsPress
WP_PLAY_MLBB_URL=https://play.mlbb.site
WP_PLAY_MLBB_USER=admin
WP_PLAY_MLBB=your_wp_application_password

# MySQL (shared with WordPress)
DB_HOST=localhost
DB_NAME=playmlbb_db
DB_USER=wpdbuser
DB_PASSWORD=your_db_password

# Claude API (for screenshot parsing)
ANTHROPIC_API_KEY=sk-ant-...

# Discord category (bot creates 4 channels inside)
MATCH_VOICE_CATEGORY_ID=your_category_id
```

See `.env.sample` in the repo root for the full documented template.

**Auto-populated fields**: the bot writes these back to `.env` on first startup — leave them blank on a fresh install:
- `MATCH_NOTIFICATIONS_CHANNEL_ID`
- `ADMIN_LOG_CHANNEL_ID`
- `BOT_COMMANDS_CHANNEL_ID`
- `BOT_LEAGUE_CHANNEL_ID`

---

## What `deploy.py` Does

`scripts/deploy.py` runs 7 phases in order. Each step prints `[OK]`, `[FAIL]`, or `[SKIP]`. Critical phases abort on failure; non-critical ones continue with warnings.

| Phase | What happens |
|-------|--------------|
| **1. Pre-flight** | Validates `.env` keys, MySQL connectivity, WP REST API auth, Discord token (with proper User-Agent), WP-CLI availability |
| **2. Database** | Runs `db/migrate.py` + verifies all 12 `mlbb_*` tables + checks `mlbb_season_schedule.play_end` schema drift |
| **3. WordPress** | Runs `scripts/league_pages.py` (creates sp_league terms, format hub pages, `/custom-leagues/`, `/bot-leagues/`) then `scripts/season_init.py` (seasons, tables, registration periods) |
| **4. Systemd** | Writes `/etc/systemd/system/mlbb-tournament-bot.service`, enables, restarts, waits up to 30s for "Scheduler started" in `bot.log`. The bot then auto-bootstraps the 4 Discord channels and writes their IDs back to `.env`. |
| **5. Cron** | Adds (if missing) `*/30 * * * * persistent_league.py` and `0 4 * * * autonomous_sim.py` |
| **6. Log rotation** | Writes `/etc/logrotate.d/mlbb-tournament-bot` (weekly × 4 rotations, compressed, copytruncate) |
| **7. Verification** | Confirms service is active, 4 channel IDs are populated in `.env`, current season exists, registration periods exist |

---

## CLI Reference

```bash
# Full deploy (with confirmation prompt)
sudo bash scripts/deploy.sh

# Pre-flight checks only, no changes made
sudo bash scripts/deploy.sh --check

# Skip WordPress infrastructure (for dev environments that use a shared WP)
sudo bash scripts/deploy.sh --skip-wp

# No confirmation prompts
sudo bash scripts/deploy.sh --force

# Custom install path
INSTALL_PATH=/opt/mlbb-tournament-bot sudo bash scripts/deploy.sh
```

All flags are **idempotent** — safe to re-run on an already-deployed server.

### Under the hood

`deploy.sh` is a bash bootstrap that handles pre-Python steps (root check, venv creation, `pip install`) then hands off to `scripts/deploy.py`. You can call `deploy.py` directly if the venv already exists:

```bash
cd /root/MLBB-TournamentBot
venv/bin/python scripts/deploy.py --check
```

---

## Multi-Guild Support

The bot supports running in multiple Discord guilds simultaneously (e.g., DEV + PROD) while sharing a single WordPress/SportsPress backend. Each guild gets its own category and 4 channels; all notifications are **broadcast** to every configured guild.

### Adding the bot to a new Discord server

1. **Invite the bot** to the new guild using the standard invite URL (needs `bot` + `applications.commands` scopes and **Manage Channels** permission).

2. **Add the new guild ID** to `GUILD_IDS` in `.env`:
   ```env
   GUILD_IDS=850386581135163489,999999999999999999
   ```
   This enables slash command sync to the new guild.

3. **Run the provisioning script**:
   ```bash
   cd /root/MLBB-TournamentBot
   venv/bin/python scripts/provision_guild.py <new_guild_id> --name PROD
   ```
   The script:
   - Finds an existing category (via `MATCH_VOICE_CATEGORY_ID` env var, or named "MLBB Tournaments") — or creates a new "MLBB Tournaments" category
   - Finds or creates the 4 text channels with correct role permissions
   - Appends the guild + channel IDs to `data/guilds.json`

4. **Restart the bot** so slash commands sync to the new guild:
   ```bash
   systemctl restart mlbb-tournament-bot
   ```

From then on, all admin-log events, match notifications, persistent-league state transitions, and autonomous-sim health reports broadcast to **every** guild listed in `data/guilds.json`.

### Other `provision_guild.py` commands

```bash
python scripts/provision_guild.py --list                   # show configured guilds
python scripts/provision_guild.py <guild_id> --remove      # drop from guilds.json (keeps the Discord channels)
python scripts/provision_guild.py <guild_id> --name NAME   # re-provision / update existing
```

### How broadcast routing works

| Component | Behavior |
|-----------|----------|
| `services/admin_log.py` | Reads `data/guilds.json` on each call, posts to every guild's `admin_log` channel |
| `bot/cogs/match.py` (`_send_notification`) | Broadcasts match notifications to every guild's `match_notifications` channel |
| `scripts/persistent_league.py` | Loads `guilds.json` at startup, posts state transitions to every `bot_leagues` channel |
| `scripts/autonomous_sim.py` | Same — daily health report goes to every `bot_leagues` channel |

**Fallback**: if `data/guilds.json` is missing (fresh install), everything falls back to the legacy single-channel env vars (`ADMIN_LOG_CHANNEL_ID`, `MATCH_NOTIFICATIONS_CHANNEL_ID`, `BOT_LEAGUE_CHANNEL_ID`). This keeps single-guild installs working with zero config.

---

## Upgrading an Existing Deployment

```bash
cd /root/MLBB-TournamentBot
git pull
sudo bash scripts/deploy.sh --force   # re-runs all phases; idempotent
```

The deploy script detects already-migrated databases, already-installed systemd services, already-present cron entries, etc. and reports them as `[OK]` instead of failing.

---

## Cron Jobs

The deploy script installs two cron jobs:

```cron
# Persistent weekly bot-league (every 30 min, runs state machine)
*/30 * * * * cd /root/MLBB-TournamentBot && venv/bin/python scripts/persistent_league.py >> /var/log/mlbb-persistent-league.log 2>&1

# Daily autonomous simulation health check (04:00 UTC)
0 4 * * * cd /root/MLBB-TournamentBot && venv/bin/python scripts/autonomous_sim.py >> /var/log/mlbb-autosim.log 2>&1
```

View logs:
```bash
tail -f /var/log/mlbb-persistent-league.log   # weekly bot-league state transitions
tail -f /var/log/mlbb-autosim.log             # daily simulation results
tail -f /root/MLBB-TournamentBot/bot.log      # main bot log (scheduler, cogs, errors)
```

Check current state:
```bash
python scripts/persistent_league.py --status
```

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Pre-flight fails on MySQL | `DB_PASSWORD` correct? `mysql -u $DB_USER -p` works? |
| Pre-flight fails on WP API | Is `WP_PLAY_MLBB` an Application Password (not login pwd)? Site reachable? |
| Pre-flight fails on Discord 403 | Token rotated? Check Discord Developer Portal. User-Agent issue (already handled by deploy.py). |
| Channels not bootstrapped | `MATCH_VOICE_CATEGORY_ID` set? Bot has **Manage Channels** permission in that category? |
| WP pages missing after deploy | Run `venv/bin/python scripts/league_pages.py` and `venv/bin/python scripts/season_init.py` manually |
| Bot won't start | `journalctl -u mlbb-tournament-bot -n 50` or `tail -n 50 bot.log` |
| Persistent league idle | It only inits on Monday. Run `python scripts/persistent_league.py --force-init` to test mid-week |
| Broadcast not reaching new guild | Check `data/guilds.json` has an entry; re-run `provision_guild.py <guild_id>` |
| Cron jobs not firing | `crontab -l` shows entries? `systemctl status cron`? |

### Manual one-off commands

```bash
# Re-run database migration
venv/bin/python db/migrate.py

# Re-bootstrap WordPress infrastructure
venv/bin/python scripts/league_pages.py     # sp_league terms + format hubs
venv/bin/python scripts/season_init.py      # seasons + tables + reg periods

# Force a persistent league to start mid-week (for testing)
venv/bin/python scripts/persistent_league.py --force-init

# Run the daily health check on demand
venv/bin/python scripts/autonomous_sim.py --teams 2
```

---

## Related Documentation

- [`README.md`](./README.md) — project overview, architecture, commands reference
- [`.env.sample`](./.env.sample) — full env var template with comments
- [`PLAN.md`](./PLAN.md) — original implementation plan (historical)
