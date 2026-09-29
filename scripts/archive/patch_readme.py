"""Update README.md with bot-leagues, scheduler, and channel documentation."""
path = "/root/MLBB-TournamentBot/README.md"
with open(path, "r") as f:
    content = f.read()

# ============================================================================
# 1. Features section — add autonomous features
# ============================================================================
old_features = """- **Player registration** — links Discord accounts to SportsPress player profiles
- **Self-service team management** — create teams, invite players, manage rosters
- **Match result submission** — captains submit win screenshots; Claude AI parses scoreboard images (BattleID, kill scores, result, duration)
- **Win-claim model** — only the winning captain submits; DEFEAT screenshots are rejected
- **BattleID deduplication** — prevents double-submission of the same match
- **League & tournament management** — list/create/delete SportsPress tables, tournaments, events
- **Pick-up tournaments** — rolling 8-team single-elimination brackets (in development)
- **Automated Discord channels** — bootstrapped on startup: `#match-notifications`, `#tournament-admin`, `#bot-commands`
- **Admin log** — all registrations, submissions, disputes, and system events posted to `#tournament-admin`"""

new_features = """- **Player registration** — links Discord accounts to SportsPress player profiles
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
- **Bot-league log** — all bot-league and simulation activity posted to `#bot-leagues`"""

content = content.replace(old_features, new_features)

# ============================================================================
# 2. Project Structure — add new files
# ============================================================================
old_structure = """```
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
```"""

new_structure = """```
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
```"""

content = content.replace(old_structure, new_structure)

# ============================================================================
# 3. Channel Bootstrap — add #bot-leagues
# ============================================================================
old_channels = """On startup the bot auto-creates three channels inside `MATCH_VOICE_CATEGORY_ID` if they don't already exist:

| Channel | Visibility | Purpose |
|---------|-----------|---------|
| `#match-notifications` | Public read-only | Match results, bracket updates |
| `#tournament-admin` | Staff only | System events, registrations, disputes |
| `#bot-commands` | Admin only | Private management commands |

Resolved channel IDs are written back to `.env` automatically."""

new_channels = """On startup the bot auto-creates four channels inside `MATCH_VOICE_CATEGORY_ID` if they don't already exist:

| Channel | Visibility | Purpose |
|---------|-----------|---------|
| `#match-notifications` | Public read-only | Match results, bracket updates |
| `#tournament-admin` | Staff only | Real-league system events, registrations, disputes |
| `#bot-commands` | Admin only | Private management commands |
| `#bot-leagues` | Public read-only | Bot-league activity: persistent weekly leagues, daily sim health checks |

Resolved channel IDs are written back to `.env` automatically.

**Notification routing:**
- Real league events, scheduler transitions, disputes, errors → `#tournament-admin` (via `services/admin_log.py`)
- Persistent bot-league state changes, autonomous sim daily reports → `#bot-leagues` (via each script's own `DiscordHTTP` class)"""

content = content.replace(old_channels, new_channels)

# ============================================================================
# 4. Simulation section — expand with autonomous + persistent
# ============================================================================
old_sim_section = """## Simulation

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
Each league gets its own child page with teams, schedule, and final standings."""

new_sim_section = """## Autonomous Operations

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

Bot-league WordPress pages appear at [play.mlbb.site/bot-leagues/](https://play.mlbb.site/bot-leagues/). The persistent league's current week and any retained manual simulations are listed there with their standings, schedule, and teams."""

content = content.replace(old_sim_section, new_sim_section)

with open(path, "w") as f:
    f.write(content)
print("README updated")
print(f"New length: {len(content)} chars")
