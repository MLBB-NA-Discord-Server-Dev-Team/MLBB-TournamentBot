"""Replace Setup + Systemd sections with a unified Deployment section."""
path = "/root/MLBB-TournamentBot/README.md"
with open(path, "r") as f:
    content = f.read()

# Find the Setup section and the end of Systemd Service section
# Replace everything from "## Setup" through the end of "## Systemd Service"
# (which ends just before "## Player Journey")

setup_start = content.find("## Setup")
player_journey_start = content.find("## Player Journey")
if setup_start == -1 or player_journey_start == -1:
    raise SystemExit("Could not find section markers")

old_section = content[setup_start:player_journey_start]

new_section = """## Deployment

The bot ships with an idempotent deployment script that handles the entire provisioning: dependencies, database migration, WordPress infrastructure, systemd service, cron jobs, and log rotation.

### Quick start (fresh server)

```bash
# 1. Clone the repo to your target path
git clone <repo-url> /root/MLBB-TournamentBot
cd /root/MLBB-TournamentBot

# 2. Copy the env template and fill in your values
cp .env.sample .env
nano .env    # required: DISCORD_TOKEN, WP_PLAY_MLBB, DB_PASSWORD, MATCH_VOICE_CATEGORY_ID

# 3. Run the bootstrap script
sudo bash scripts/deploy.sh
```

That's it. The bootstrap:
1. Verifies Python 3.11+, creates the venv, installs `requirements.txt`
2. Hands off to `scripts/deploy.py` which runs 7 provisioning phases
3. On success, the bot is running as `mlbb-tournament-bot.service` and both cron jobs are installed

### What `deploy.py` does (7 phases)

| Phase | What |
|-------|------|
| **1. Pre-flight** | Validates `.env` keys, MySQL connectivity, WP REST API auth, Discord token, WP-CLI |
| **2. Database** | Runs `db/migrate.py` + verifies all 12 `mlbb_*` tables + schema drift check |
| **3. WordPress** | Runs `scripts/league_pages.py` (sp_league terms, format hubs, `/custom-leagues/`, `/bot-leagues/`) then `scripts/season_init.py` (seasons, tables, registration periods) |
| **4. Systemd** | Writes `/etc/systemd/system/mlbb-tournament-bot.service`, enables, restarts, waits for "Scheduler started" in bot.log (up to 30s). The bot then auto-bootstraps the 4 Discord channels and writes their IDs back to `.env`. |
| **5. Cron** | Adds `*/30 * * * * persistent_league.py` and `0 4 * * * autonomous_sim.py` if missing |
| **6. Log rotation** | Writes `/etc/logrotate.d/mlbb-tournament-bot` (weekly rotation, 4 copies, compressed) |
| **7. Verification** | Confirms bot service is active, all 4 channel IDs populated, seasons exist, registration periods exist |

### CLI flags

```bash
bash scripts/deploy.sh                # full deploy with confirmation
bash scripts/deploy.sh --check        # preflight checks only, no changes
bash scripts/deploy.sh --skip-wp      # skip WP infrastructure (dev environments)
bash scripts/deploy.sh --force        # no confirmation prompts
INSTALL_PATH=/opt/mlbb bash scripts/deploy.sh  # custom install path
```

All flags are idempotent — safe to re-run on an already-deployed server.

### Prerequisites on the host

Before running `deploy.sh`, the server needs:

- **Linux with systemd** (tested on Debian/Ubuntu)
- **Python 3.11+** (`apt install python3 python3-venv python3-pip`)
- **MySQL client libraries** (`apt install default-libmysqlclient-dev build-essential`)
- **WP-CLI** in `$PATH` (`curl -O https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar && chmod +x wp-cli.phar && mv wp-cli.phar /usr/local/bin/wp`)
- **A running WordPress site** with SportsPress plugin installed (the bot writes to it)
- **MySQL database** shared with the WP site (same `DB_NAME` as WordPress)
- **Discord application** with bot + `applications.commands` scopes and **Server Members** + **Voice State** privileged intents enabled
- **Claude API key** (for match screenshot parsing)

### `.env` configuration

Copy `.env.sample` to `.env` and fill in:

```env
# Discord
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_client_id
GUILD_IDS=comma,separated,guild,ids

# Roles
ORGANIZER_ROLES=Tournament Organizer,DEV
ADMIN_ROLES=admins,DEV

# WordPress / SportsPress
WP_PLAY_MLBB_URL=https://play.mlbb.site
WP_PLAY_MLBB_USER=admin
WP_PLAY_MLBB=your_wp_app_password

# MySQL
DB_HOST=localhost
DB_NAME=playmlbb_db
DB_USER=wpdbuser
DB_PASSWORD=your_db_password

# Claude
ANTHROPIC_API_KEY=sk-ant-...

# Discord category where channels get auto-created
MATCH_VOICE_CATEGORY_ID=your_category_id

# These are auto-populated by the bot on first startup — leave blank
MATCH_NOTIFICATIONS_CHANNEL_ID=
ADMIN_LOG_CHANNEL_ID=
BOT_COMMANDS_CHANNEL_ID=
BOT_LEAGUE_CHANNEL_ID=
```

See `.env.sample` for the full documented template.

### Invite URL

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=369468123907121&scope=bot+applications.commands
```

### Upgrading an existing deployment

```bash
cd /root/MLBB-TournamentBot
git pull
sudo bash scripts/deploy.sh --force    # re-runs all phases; idempotent
```

### Troubleshooting

| Problem | Check |
|---------|-------|
| Pre-flight fails on MySQL | `DB_PASSWORD` correct? `mysql -u $DB_USER -p` works? |
| Pre-flight fails on WP API | Is `WP_PLAY_MLBB` an Application Password (not login pwd)? Site reachable? |
| Pre-flight fails on Discord 403 | Token rotated? Check Discord Developer Portal |
| Channels not bootstrapped | `MATCH_VOICE_CATEGORY_ID` set? Bot has **Manage Channels** permission in that category? |
| WP pages missing after deploy | Run `python scripts/league_pages.py` and `python scripts/season_init.py` manually |
| Bot won't start | `journalctl -u mlbb-tournament-bot -n 50` or `tail -n 50 bot.log` |

"""

content = content.replace(old_section, new_section)

with open(path, "w") as f:
    f.write(content)
print("Deployment section inserted")
