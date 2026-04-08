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

## Player Journey (Quick-Start)

The full guide lives at [play.mlbb.site/quickstart/](https://play.mlbb.site/quickstart/) and in-Discord via `/league quickstart`.

Each step maps to a slash command and a headless service function in `services/command_services.py`:

| Step | What you do | Slash command | Service function |
|------|------------|---------------|------------------|
| **1. Register** | Link Discord to MLBB profile | `/player register [ign]` | `player_register()` |
| **2. Create team** | Form a team (you become captain) | `/team create [name]` | `team_create()` |
| **2b. Invite** | Add teammates to your roster | `/team invite @user` | `team_invite()` |
| **2c. Accept** | Teammates accept the invite | `/team accept` | `team_accept()` |
| **3. Browse leagues** | See open leagues and formats | `/league list` | read-only |
| **3b. Sign up** | Register your team for a league | `/league register [id]` | `league_register()` |
| **4. Wait** | Admin approves + season starts | — | `admin_approve_registration()` |
| **5. Check schedule** | View events table on league page | — | read-only |
| **6. Play** | Join voice channel, play match | — | Discord VC lifecycle |
| **7. Submit result** | Winning captain uploads screenshot | `/match submit [screenshot]` | `match_submit()` |
| **7b. Confirm** | Losing captain verifies | `/match confirm [#id]` | `match_confirm()` |
| **7c. Dispute** | Losing captain flags issue | `/match dispute [#id] [reason]` | `match_dispute()` |
| **8. Standings** | Check your rank on the league page | `/player profile` | `player_profile()` |

### Additional management commands

| Command | Who | Service function |
|---------|-----|------------------|
| `/team edit` | Captain | `team_edit()` — upload logo, set team colours |
| `/team kick @user` | Captain | `team_kick()` — remove a player from roster |
| `/team delete` | Captain/Admin | `team_delete()` — disband team, clean up all links |
| `/team roster [id]` | Anyone | `team_roster()` — view a team's full roster |
| `/team list` | Anyone | `team_list()` — list your own team memberships |
| `/tournament register [id]` | Captain | `tournament_register()` — sign up for a tournament |

---

## Commands Reference

Run `/tournament help` in Discord for a live list with role-gated admin commands.

### Player-facing commands

| Group | Command | Who |
|-------|---------|-----|
| `/player` | `register [ign]` | Anyone |
| `/player` | `profile [@user]` | Anyone |
| `/team` | `create [name]` | Registered players |
| `/team` | `invite [@user] [role]` | Captain |
| `/team` | `accept` | Invited player |
| `/team` | `kick [@user]` | Captain |
| `/team` | `edit [picture] [color1] [color2]` | Captain |
| `/team` | `delete [team_id]` | Captain / Admin |
| `/team` | `roster [team_id]` | Anyone |
| `/team` | `list` | Anyone |
| `/league` | `list [search]` | Anyone |
| `/league` | `register [league_id]` | Captain |
| `/league` | `quickstart` | Anyone |
| `/match` | `submit [screenshot] [event_id]` | Captain (winning team) |
| `/match` | `confirm [submission_id]` | Opposing captain |
| `/match` | `dispute [submission_id] [reason]` | Captain |
| `/tournament` | `register [tournament_id]` | Captain |
| `/tournament` | `help` | Anyone |
| `/pickup` | `status` | Anyone |

### Staff commands

| Group | Command | Who |
|-------|---------|-----|
| `/league` | `create [name] [rule]` | Admin |
| `/league` | `delete [league_id]` | Admin |
| `/league-admin` | `open-registration` | Admin |
| `/league-admin` | `close-registration` | Admin |
| `/league-admin` | `registrations` | Admin |
| `/league-admin` | `approve-registration [#id]` | Admin |
| `/league-admin` | `pending` | Admin |
| `/league-admin` | `resolve-dispute [#id] [winner]` | Admin |
| `/league-admin` | `set-season` | Admin |
| `/tournament` | `list / create / delete / add-event` | Organizer |

---

## Service Layer (`services/command_services.py`)

Standalone async functions that mirror the business logic of every slash command.
Each returns a `Result(ok, data, error)` — no Discord dependency.

```python
from services.command_services import player_register, team_create, match_submit, Result

result = await player_register(api, discord_id, username, ign, avatar_png=png_bytes)
if not result.ok:
    print(result.error)
else:
    print(result.data["sp_player_id"])
```

Used by `scripts/simulate_league.py` to test the full player journey headlessly.

| Function | Mirrors | Validations |
|----------|---------|-------------|
| `player_register(api, discord_id, username, ign, avatar_png)` | `/player register` | dupe check, SP create, metrics, 3 photo fields |
| `player_profile(discord_id)` | `/player profile` | registered check, team list |
| `team_create(api, discord_id, name, logo_png, color1, color2)` | `/team create` | registered, SP team + roster + sp_list + ACF + logo |
| `team_invite(discord_id, invitee_id, team_id, role)` | `/team invite` | captain, invitee registered, not on team |
| `team_accept(api, invitee_id)` | `/team accept` | pending invite, SP sync + roster list sync |
| `team_kick(api, discord_id, target_id, team_id)` | `/team kick` | captain, self-kick, non-captain target |
| `team_edit(api, discord_id, team_id, logo, c1, c2)` | `/team edit` | captain, at least 1 param |
| `team_delete(api, discord_id, team_id, is_admin)` | `/team delete` | captain/admin, full teardown |
| `team_roster(discord_id, team_id)` | `/team roster` | team exists |
| `team_list(discord_id)` | `/team list` | — |
| `league_register(discord_id, league_id, team_id)` | `/league register` | captain, open period, dupe, conflict, cap |
| `admin_approve_registration(reg_id, reviewer_id)` | `/league-admin approve` | pending check |
| `match_submit(discord_id, match_data, event_id)` | `/match submit` | captain, battle ID dupe |
| `match_confirm(discord_id, submission_id)` | `/match confirm` | captain, exists, not self |
| `match_dispute(discord_id, submission_id, reason)` | `/match dispute` | captain, exists, not self |
| `tournament_register(discord_id, tournament_id, team_id)` | `/tournament register` | captain, open period, dupe, conflict, cap |

---

## Simulation

End-to-end league simulation that exercises all service functions under production conditions.

```bash
cd /root/MLBB-TournamentBot
source venv/bin/activate

# Full run (4 teams, random rule, cleanup after)
python scripts/simulate_league.py

# 6-team Brawl BO3, keep artifacts for inspection
python scripts/simulate_league.py --teams 6 --rule BrawlBO3 --round-delay 3 --no-cleanup

# Preview the plan without creating anything
python scripts/simulate_league.py --dry-run --teams 4
```

### What the simulation creates

| Phase | What | Service functions exercised |
|-------|------|----|
| 1 | Bot-* league with random name + rule | `create_league`, registration period |
| 2 | N players (pixel-art avatars, gamer-tag usernames) | `player_register()` x N |
| 3 | N/5 teams (pixel-art logos, palette colours) | `team_create()`, `team_invite()`, `team_accept()` |
| 4 | Team registrations (with conflict checks) | `league_register()`, `admin_approve_registration()` |
| 5 | Round-robin schedule | sp_event posts |
| 6 | Match results (VC create/delete, BO1/3/5 series) | `match_submit()`, `match_confirm()` |
| 7 | Final standings + champion | `player_profile()` |
| 8 | WordPress league page under /bot-leagues/ | teams, schedule, standings tables |
| 9 | Cleanup (unless `--no-cleanup`) | all artifacts removed |

### Bot Leagues hub

Simulated leagues appear at [play.mlbb.site/bot-leagues/](https://play.mlbb.site/bot-leagues/).
Each league gets its own child page with teams, schedule, and final standings.

---

## Related Projects

- **play.mlbb.site** — WordPress + SportsPress (shared database)
- **MLBB-SkinSquads** — Skin squad builder at mlbb.site
- **MLBB-TeddyBot-v3** — Tier data query bot
- **MLBB-Observability** — OpenSearch analytics dashboard
