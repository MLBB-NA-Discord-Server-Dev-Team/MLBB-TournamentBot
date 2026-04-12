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
- **Autonomous league lifecycle** — scheduler auto-approves registrations, syncs results to SportsPress, manages season transitions, and generates WordPress pages with SP shortcodes (see [Autonomous Operations](#autonomous-operations))
- **Persistent bot-league** — weekly self-simulating bot-league (Mon-Wed reg, Thu-Sat play, Sun cleanup) that continuously exercises the full backend through `command_services.py`
- **Daily simulation health check** — autonomous end-to-end test of all 16 service functions, posts pass/fail report to `#bot-leagues`
- **Automated Discord channels** — bootstrapped on startup: `#match-notifications`, `#tournament-admin`, `#bot-commands`, `#bot-leagues`
- **Admin log** — all registrations, submissions, disputes, and system events posted to `#tournament-admin`
- **Bot-league log** — all bot-league and simulation activity posted to `#bot-leagues`

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
│   ├── main.py                    # Bot entry point, cog loader, channel bootstrap
│   └── cogs/
│       ├── player.py              # /player register, /player profile
│       ├── teams.py               # /team create/invite/accept/kick/roster/list
│       ├── match.py               # /match submit/confirm/dispute
│       ├── leagues.py             # /league list/create/delete
│       ├── tournaments.py         # /tournament list/create/delete/add-event/help
│       ├── pickup.py              # /pickup join/leave/status/bracket (in development)
│       └── admin.py               # /admin pending/resolve-dispute
├── services/
│   ├── db.py                      # aiomysql connection pool
│   ├── db_helpers.py              # All read queries (MySQL direct)
│   ├── sportspress.py             # Write-only REST API client
│   ├── match_parser.py            # Claude vision screenshot parser
│   ├── admin_log.py               # #tournament-admin embed logger
│   ├── command_services.py        # Headless service layer (16 funcs mirroring slash commands)
│   ├── league_lifecycle.py        # Auto-approve, result sync, WP page gen, season mgmt
│   ├── scheduler.py               # Background task loop (5 periodic tasks)
│   └── round_robin.py             # Round-robin schedule generator (Thu-Sat/Sun)
├── scripts/
│   ├── season_init.py             # Bootstrap seasons, lore leagues, tables, reg periods
│   ├── league_pages.py            # Create/update WP league pages and format hubs
│   ├── simulate_league_v2.py      # Manual end-to-end simulation via command_services
│   ├── autonomous_sim.py          # Daily cron (04:00 UTC) — health check, 10 steps
│   └── persistent_league.py       # 30-min cron — weekly bot-league state machine
├── data/
│   └── persistent_league.json     # State file for persistent bot-league (created at runtime)
├── db/
│   └── migrate.py                 # Idempotent schema migration (12 mlbb_* tables)
├── config.py                      # .env loader, role helpers
├── .env                           # Runtime secrets (not committed)
├── PLAN.md                        # Full implementation plan
└── requirements.txt
```

---

## Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for the full deployment guide.

### Quick start

```bash
# 1. Clone the repo
git clone <repo-url> /root/MLBB-TournamentBot
cd /root/MLBB-TournamentBot

# 2. Copy the env template and fill in your values
cp .env.sample .env
nano .env    # required: DISCORD_TOKEN, WP_PLAY_MLBB, DB_PASSWORD, MATCH_VOICE_CATEGORY_ID

# 3. Run the bootstrap script
sudo bash scripts/deploy.sh
```

The `scripts/deploy.py` script runs 7 idempotent phases: pre-flight checks, database migration, WordPress infrastructure, systemd service install, cron jobs, log rotation, and post-install verification. Safe to re-run for upgrades:

```bash
cd /root/MLBB-TournamentBot && git pull && sudo bash scripts/deploy.sh --force
```

### Adding the bot to a new Discord server

The bot supports multi-guild operation with shared backend (same WordPress/SportsPress) and broadcast notifications (all guilds see all events).

```bash
# 1. Invite bot to the new server
# 2. Add guild ID to GUILD_IDS in .env
# 3. Provision category + 4 channels
venv/bin/python scripts/provision_guild.py <guild_id> --name PROD

# 4. Restart for slash command sync
systemctl restart mlbb-tournament-bot
```

See [DEPLOYMENT.md § Multi-Guild Support](./DEPLOYMENT.md#multi-guild-support) for details.

### Invite URL

```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=369468123907121&scope=bot+applications.commands
```

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

## Autonomous Operations

The bot runs three independent automation layers, each addressing a different need.

### 1. Scheduler (inside the bot process)

`services/scheduler.py` runs inside the bot process on a 60-second loop. Each new task has its own interval gate:

| Task | Interval | What it does |
|------|----------|--------------|
| Registration transitions | 60s | `scheduled` → `open` → `closed` based on `opens_at` / `closes_at`. Triggers round-robin schedule generation on close. |
| Auto-approve registrations | 5 min | Calls `league_lifecycle.check_pending_approvals()` — auto-approves teams with valid 5-6 player rosters |
| Match result sync | 10 min | Calls `league_lifecycle.sync_confirmed_results()` — writes confirmed submissions to SportsPress via `set_event_results()` |
| League hub page update | 1 hr | Refreshes `/custom-leagues/` with all active league links and statuses |
| Season lifecycle check | 6 hr | Calls `ensure_next_season()` / `finalize_season()` based on current date vs `play_end` |

Schedule generation for periods with `created_by='persistent_league'` is **skipped** by the scheduler — those periods generate their own schedules (see Persistent Bot-League below).

### 2. Daily autonomous simulation (cron)

```
0 4 * * *  cd /root/MLBB-TournamentBot && venv/bin/python scripts/autonomous_sim.py
```

`scripts/autonomous_sim.py` runs once a day at 04:00 UTC as a burst-mode health check. It exercises all 16 `command_services.py` functions end-to-end in ~30 seconds, then cleans up:

| Step | Service functions exercised |
|------|----------------------------|
| 1. create_league | `create_league`, `create_table`, registration period |
| 2. player_register x10 | `player_register()` with pixel-art avatars |
| 3. team_create_invite_accept | `team_create()`, `team_invite()`, `team_accept()`, `team_roster()` |
| 4. league_register | `league_register()` |
| 5. auto_approve | `lifecycle.check_pending_approvals()` |
| 6. create_events | SP API event creation |
| 7. match_submit_confirm | `match_submit()`, `match_confirm()` |
| 8. sync_results | `lifecycle.sync_confirmed_results()` |
| 9. wp_page_gen | `lifecycle.generate_league_wp_page()` |
| 10. player_profile | `player_profile()` |
| 11. cleanup | `team_delete()` + artifact removal |

The final pass/fail report is posted to `#bot-leagues` as an embed.

### 3. Persistent bot-league (cron, weekly state machine)

```
*/30 * * * *  cd /root/MLBB-TournamentBot && venv/bin/python scripts/persistent_league.py
```

`scripts/persistent_league.py` runs every 30 minutes as a cron-driven state machine that lives a real weekly league lifecycle. Unlike `autonomous_sim.py` which cleans up after itself, the persistent league keeps all artifacts live on the WordPress site for the full week.

**Weekly cadence (UTC):**

| Day | State | What happens |
|-----|-------|--------------|
| **Mon** 00:00 | `INIT` → `REGISTRATION` | Creates league, sp_table, registration period, WP page under `/bot-leagues/` |
| **Mon-Wed** | `REGISTRATION` | Drip-feeds 1-3 players per tick, forms teams of 5, auto-registers. Scheduler auto-approves within 5 min. |
| **Wed 23:59** → **Thu** | `REGISTRATION` → `PLAYING` | Closes period, generates Thu-Sat round-robin schedule via `round_robin.generate_schedule()`, creates sp_events |
| **Thu-Sat** | `PLAYING` | Simulates 1-2 matches per tick as events come due via `match_submit()` + `match_confirm()`. Scheduler syncs results to SP every 10 min. |
| **Sun** | `PLAYING` → `CLEANUP` | Deletes ALL artifacts: teams, players, events, table, league term, WP page, DB rows. Archives to `history` in state file. |
| **Mon** | `CLEANUP` → `INIT` | Next cycle begins |

**State storage:** `data/persistent_league.json` — atomic writes, self-healing on corruption. Uses fake Discord ID prefix `777...` (distinct from `888...` autosim and `999...` simulate_league_v2).

**CLI:**
```bash
python scripts/persistent_league.py              # normal tick
python scripts/persistent_league.py --status     # print current state
python scripts/persistent_league.py --dry-run    # preview next tick
python scripts/persistent_league.py --reset      # wipe state and start fresh
python scripts/persistent_league.py --force-init # bypass day-of-week (creates league for next Mon if mid-week)
```

**Log:** `/var/log/mlbb-persistent-league.log`

**Cooperation with scheduler:**

| Action | Who does it |
|--------|-------------|
| Create league/table/period/players/teams | `persistent_league.py` via `command_services` |
| Auto-approve registrations | `scheduler.py` (5 min cycle) |
| Generate round-robin schedule | `persistent_league.py` (own Thu-Sat dates, scheduler skips `persistent_league` periods) |
| Submit/confirm matches | `persistent_league.py` via `command_services` |
| Sync results to SportsPress | `scheduler.py` (10 min cycle) |
| Update `/custom-leagues/` hub | `scheduler.py` (1 hr cycle) |

### Manual simulation (legacy)

The original burst-mode manual simulation still exists for ad-hoc testing:

```bash
# v1: raw API/DB calls
python scripts/simulate_league.py --teams 4 --rule BrawlBO3 --no-cleanup

# v2: routes everything through command_services.py
python scripts/simulate_league_v2.py --teams 6 --rule DPBO3 --round-delay 3
```

### Bot Leagues hub

Bot-league WordPress pages appear at [play.mlbb.site/bot-leagues/](https://play.mlbb.site/bot-leagues/). The persistent league's current week and any retained manual simulations are listed there with their standings, schedule, and teams.

---

## Related Projects

- **play.mlbb.site** — WordPress + SportsPress (shared database)
- **MLBB-SkinSquads** — Skin squad builder at mlbb.site
- **MLBB-TeddyBot-v3** — Tier data query bot
- **MLBB-Observability** — OpenSearch analytics dashboard
