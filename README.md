# MLBB-TournamentBot

Discord bot for managing MLBB community tournaments on [play.mlbb.site](https://play.mlbb.site).
Built with Discord.py 2.x (slash commands, cog-based), aiomysql, and the SportsPress REST API.

---

## Features

- **Player registration** — links Discord accounts to SportsPress player profiles
- **Self-service team management** — create teams, invite players, manage rosters
- **Match result submission** — captains submit win screenshots; Claude AI parses scoreboard images (BattleID, kill scores, result, duration)
- **Win-claim model** — only the winning captain submits; DEFEAT screenshots are rejected
- **BattleID deduplication** — prevents double-submission of the same match
- **League & tournament management** — list/create/delete SportsPress tables, tournaments, events
- **Pick-up tournaments** — rolling 8-team single-elimination brackets (in development)
- **Automated Discord channels** — bootstrapped on startup: `#match-notifications`, `#tournament-admin`, `#bot-commands`
- **Admin log** — all registrations, submissions, disputes, and system events posted to `#tournament-admin`

---

## Architecture

```
Discord Users
     │
     ▼
TournamentBot (Discord.py 2.x)
     │
     ├── MySQL (aiomysql) ──── direct reads from playmlbb_db
     │       ├── wp_posts / wp_postmeta   (SportsPress entities)
     │       └── mlbb_*                   (custom extension tables)
     │
     ├── WP REST API (aiohttp) ── writes only → play.mlbb.site/wp-json/sportspress/v2/
     │       └── triggers WordPress hooks (save_post, updated_post_meta)
     │           for SportsPress standings recalculation
     │
     ├── Claude API (vision) ── screenshot parsing (claude-haiku-4-5)
     │
     └── Discord API ── voice channels, DMs, embeds
```

### Read / Write Split

| Data | Read | Write |
|------|------|-------|
| `mlbb_*` custom tables | Direct MySQL | Direct MySQL |
| `wp_posts` / `wp_postmeta` (SP entities) | Direct MySQL | REST API (hooks must fire) |
| SportsPress standings | Direct MySQL | REST API (triggers SP recalc) |

All reads go direct to MySQL — the bot and WordPress are co-located on the same server, so this avoids HTTP overhead and authentication round-trips entirely.

---

## Project Structure

```
MLBB-TournamentBot/
├── bot/
│   ├── main.py              # Bot entry point, cog loader, channel bootstrap
│   └── cogs/
│       ├── player.py        # /player register, /player profile
│       ├── teams.py         # /team create/invite/accept/kick/roster/list
│       ├── match.py         # /match submit/confirm/dispute
│       ├── leagues.py       # /league list/create/delete
│       ├── tournaments.py   # /tournament list/create/delete/add-event/help
│       ├── pickup.py        # /pickup join/leave/status/bracket (in development)
│       └── admin.py         # /admin pending/resolve-dispute
├── services/
│   ├── db.py               # aiomysql connection pool
│   ├── db_helpers.py       # All read queries (MySQL direct)
│   ├── sportspress.py      # Write-only REST API client
│   ├── match_parser.py     # Claude vision screenshot parser
│   └── admin_log.py        # #tournament-admin embed logger
├── db/
│   └── migrate.py          # Idempotent schema migration (12 mlbb_* tables)
├── config.py               # .env loader, role helpers
├── .env                    # Runtime secrets (not committed)
├── PLAN.md                 # Full implementation plan
└── requirements.txt
```

---

## Setup

### Prerequisites

- Python 3.11+
- MySQL (shared with `play.mlbb.site` WordPress/SportsPress)
- Discord application with **bot** + **applications.commands** scopes
- Privileged intents enabled: **Server Members**, **Voice State**
- Claude API key (for screenshot parsing)

### Install

```bash
cd /root/MLBB-TournamentBot
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure `.env`

```env
# Discord
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_client_id
GUILD_IDS=your_guild_id            # comma-separated for multiple guilds

# Roles
ORGANIZER_ROLES=Tournament Organizer,DEV
ADMIN_ROLES=admins,DEV
LOG_LEVEL=INFO

# WordPress / SportsPress (write API)
WP_PLAY_MLBB_URL=https://play.mlbb.site
WP_PLAY_MLBB_USER=admin
WP_PLAY_MLBB=your_wp_app_password

# MySQL (direct reads)
DB_HOST=localhost
DB_NAME=playmlbb_db
DB_USER=wpdbuser
DB_PASSWORD=your_db_password

# Discord channel/category IDs (auto-populated on first boot)
MATCH_VOICE_CATEGORY_ID=your_category_id
MATCH_NOTIFICATIONS_CHANNEL_ID=
ADMIN_LOG_CHANNEL_ID=
BOT_COMMANDS_CHANNEL_ID=
```

### Run database migration

```bash
source venv/bin/activate
python db/migrate.py
```

### Run the bot

```bash
source venv/bin/activate
python -m bot.main
```

### Invite URL

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=369468123907121&scope=bot+applications.commands
```

---

## Channel Bootstrap

On startup the bot auto-creates three channels inside `MATCH_VOICE_CATEGORY_ID` if they don't already exist:

| Channel | Visibility | Purpose |
|---------|-----------|---------|
| `#match-notifications` | Public read-only | Match results, bracket updates |
| `#tournament-admin` | Staff only | System events, registrations, disputes |
| `#bot-commands` | Admin only | Private management commands |

Resolved channel IDs are written back to `.env` automatically.

---

## Systemd Service

```ini
# /etc/systemd/system/tournament-bot.service
[Unit]
Description=MLBB Tournament Bot
After=network.target mysql.service

[Service]
WorkingDirectory=/root/MLBB-TournamentBot
ExecStart=/root/MLBB-TournamentBot/venv/bin/python -m bot.main
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tournament-bot
sudo systemctl start tournament-bot
sudo journalctl -u tournament-bot -f
```

---

## Commands Reference

Run `/tournament help` in Discord for a live list with role-gated admin commands.

| Group | Command | Who |
|-------|---------|-----|
| `/player` | `register [ign]` | Anyone |
| `/player` | `profile [@user]` | Anyone |
| `/team` | `create [name]` | Registered players |
| `/team` | `invite [@user]` | Captain |
| `/team` | `accept` | Invited player |
| `/team` | `kick [@user]` | Captain |
| `/team` | `roster [team_id]` | Anyone |
| `/team` | `list` | Anyone |
| `/match` | `submit [screenshot]` | Captain (winning team) |
| `/match` | `confirm [#id]` | Opposing captain |
| `/match` | `dispute [#id] [reason]` | Captain |
| `/pickup` | `join / leave / status / bracket` | Captain / Anyone |
| `/tournament` | `list / create / delete / add-event` | Organizer |
| `/league` | `list / create / delete` | Organizer (list: anyone) |
| `/admin` | `pending / resolve-dispute` | Admin |

---

## Related Projects

- **play.mlbb.site** — WordPress + SportsPress (shared database)
- **MLBB-SkinSquads** — Skin squad builder at mlbb.site
- **MLBB-TeddyBot-v3** — Tier data query bot
- **MLBB-Observability** — OpenSearch analytics dashboard
