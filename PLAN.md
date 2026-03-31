# MLBB-TournamentBot — Full Implementation Plan

**Created**: March 29, 2026
**Updated**: March 30, 2026
**Status**: Approved for implementation
**Database**: `playmlbb_db` (MySQL, localhost) — shared with play.mlbb.site WordPress/SportsPress
**Bot Framework**: Discord.py 2.x, async, cog-based (follows MLBB-TeddyBot-v3 pattern)

---

## 1. Architecture Overview

```
Discord Users
     │
     ▼
TournamentBot (Discord.py)
     │
     ├─── MySQL (aiomysql) ──────────────────────────────────┐
     │         ├── wp_posts          (sp_team, sp_player,    │
     │         │                      sp_event, sp_tournament│
     │         │                      sp_table)              │
     │         ├── wp_postmeta       (sp_metrics → discordid)│
     │         ├── wp_mo_discord_linked_user                  │
     │         └── mlbb_*            (custom extension tables)│
     │                                                        │
     ├─── WP REST API (aiohttp) ─── play.mlbb.site           │
     │         └── Writes results back to SportsPress         │
     │                                                        │
     ├─── Claude API (vision) ─── Screenshot parsing         │
     │                                                        │
     └─── Discord API ─── Voice channels, events, DMs        │
                                                              │
playmlbb_db ◄─────────────────────────────────────────────────┘
```

### Key Discovery (existing data)
- `sp_metrics` on every `sp_player` post already stores `discordid`, `discordusername`,
  `discorddiscriminator` as a PHP-serialized array — this is the primary Discord↔player link.
- `wp_mo_discord_linked_user` maps Discord IDs to WP user accounts.
- No existing `sp_team`, `sp_event`, or `sp_tournament` posts yet — fresh slate.

---

## 2. Custom Database Schema (mlbb_* extension tables)

All tables use prefix `mlbb_` and live in `playmlbb_db` alongside WordPress tables.
Migration script: `db/migrate.py` — idempotent, safe to re-run.

### 2.1 `mlbb_season_schedule`
Defines season calendar dates.

```sql
CREATE TABLE mlbb_season_schedule (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    sp_season_id  BIGINT UNSIGNED NOT NULL,  -- wp_term_taxonomy.term_id
    season_name   VARCHAR(100) NOT NULL,
    play_start    DATE NOT NULL,
    play_end      DATE NOT NULL,
    reg_opens     DATE NOT NULL,
    reg_closes    DATE NOT NULL,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 `mlbb_registration_periods`
Controls when registration is open for a tournament or league.

```sql
CREATE TABLE mlbb_registration_periods (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    entity_type   ENUM('tournament','league') NOT NULL,
    entity_id     BIGINT UNSIGNED NOT NULL,        -- wp_posts.ID (sp_tournament or sp_table)
    opens_at      DATETIME NOT NULL,
    closes_at     DATETIME NOT NULL,
    max_teams     SMALLINT UNSIGNED DEFAULT NULL,  -- NULL = unlimited
    created_by    VARCHAR(20) NOT NULL,            -- Discord ID of admin who opened it
    status        ENUM('scheduled','open','closed') DEFAULT 'scheduled',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX (entity_type, entity_id),
    INDEX (status)
);
```

### 2.3 `mlbb_team_registrations`
Tracks team applications to a registration period.

```sql
CREATE TABLE mlbb_team_registrations (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    period_id       INT NOT NULL,                  -- FK mlbb_registration_periods.id
    sp_team_id      BIGINT UNSIGNED NOT NULL,      -- wp_posts.ID (sp_team)
    registered_by   VARCHAR(20) NOT NULL,          -- Discord ID (captain)
    status          ENUM('pending','approved','rejected','withdrawn') DEFAULT 'pending',
    registered_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    reviewed_at     DATETIME DEFAULT NULL,
    reviewed_by     VARCHAR(20) DEFAULT NULL,      -- Discord ID of admin
    notes           TEXT DEFAULT NULL,
    UNIQUE KEY (period_id, sp_team_id),
    INDEX (sp_team_id),
    INDEX (status)
);
```

### 2.4 `mlbb_player_roster`
Discord-managed player↔team assignments. Mirrors the SportsPress `sp_player`→`sp_team`
relationship but allows the bot to manage it without PHP serialization.

```sql
CREATE TABLE mlbb_player_roster (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    discord_id    VARCHAR(20) NOT NULL,
    sp_player_id  BIGINT UNSIGNED NOT NULL,   -- wp_posts.ID (sp_player)
    sp_team_id    BIGINT UNSIGNED NOT NULL,   -- wp_posts.ID (sp_team)
    role          ENUM('captain','player','substitute') DEFAULT 'player',
    joined_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    status        ENUM('active','inactive') DEFAULT 'active',
    UNIQUE KEY (sp_player_id, sp_team_id),
    INDEX (discord_id),
    INDEX (sp_team_id)
);
```

### 2.5 `mlbb_match_submissions`
Stores screenshot-based match result submissions and AI parsing output.

```sql
CREATE TABLE mlbb_match_submissions (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    sp_event_id      BIGINT UNSIGNED NOT NULL,   -- wp_posts.ID (sp_event)
    submitted_by     VARCHAR(20) NOT NULL,       -- Discord ID
    screenshot_url   TEXT NOT NULL,              -- Discord CDN URL
    ai_home_team     VARCHAR(100) DEFAULT NULL,
    ai_away_team     VARCHAR(100) DEFAULT NULL,
    ai_home_score    TINYINT UNSIGNED DEFAULT NULL,
    ai_away_score    TINYINT UNSIGNED DEFAULT NULL,
    ai_confidence    FLOAT DEFAULT NULL,         -- 0.0–1.0
    ai_raw           TEXT DEFAULT NULL,          -- full Claude response JSON
    status           ENUM('pending','confirmed','disputed','rejected') DEFAULT 'pending',
    confirmed_by     VARCHAR(20) DEFAULT NULL,
    submitted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    confirmed_at     DATETIME DEFAULT NULL,
    INDEX (sp_event_id),
    INDEX (status),
    INDEX (submitted_by)
);
```

### 2.6 `mlbb_match_schedule`
Maps sp_event matchups to scheduled play windows (Thu/Fri/Sat/Sun blocks).

```sql
CREATE TABLE mlbb_match_schedule (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    sp_event_id     BIGINT UNSIGNED NOT NULL,   -- wp_posts.ID (sp_event)
    sp_league_id    BIGINT UNSIGNED NOT NULL,   -- sp_league term ID
    round_number    TINYINT UNSIGNED NOT NULL,
    window_start    DATETIME NOT NULL,          -- e.g. Thursday 7:00 PM PST
    window_end      DATETIME NOT NULL,          -- e.g. Sunday 11:00 PM PST
    status          ENUM('scheduled','active','completed','cancelled') DEFAULT 'scheduled',
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX (sp_event_id),
    INDEX (sp_league_id),
    INDEX (window_start)
);
```

### 2.7 `mlbb_discord_events`
Tracks Discord scheduled events created per match window.

```sql
CREATE TABLE mlbb_discord_events (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    match_schedule_id     INT NOT NULL,             -- FK mlbb_match_schedule.id
    discord_event_id      VARCHAR(20) NOT NULL,     -- Discord event snowflake ID
    guild_id              VARCHAR(20) NOT NULL,
    channel_id            VARCHAR(20) DEFAULT NULL, -- associated voice channel
    event_name            TEXT NOT NULL,
    scheduled_start       DATETIME NOT NULL,
    scheduled_end         DATETIME NOT NULL,
    status                ENUM('scheduled','active','completed','cancelled') DEFAULT 'scheduled',
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY (discord_event_id),
    INDEX (match_schedule_id)
);
```

### 2.8 `mlbb_voice_channels`
Tracks temporary voice channels created for match windows.

```sql
CREATE TABLE mlbb_voice_channels (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    match_schedule_id INT NOT NULL,              -- FK mlbb_match_schedule.id
    discord_event_id  VARCHAR(20) DEFAULT NULL,  -- FK mlbb_discord_events.discord_event_id
    channel_id        VARCHAR(20) NOT NULL,      -- Discord channel snowflake ID
    channel_name      VARCHAR(100) NOT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    deleted_at        DATETIME DEFAULT NULL,
    INDEX (match_schedule_id)
);
```

---

## 3. Data Access Layer

### 3.1 `services/db.py`
- `aiomysql` connection pool, initialized at bot startup
- Reads DB credentials from `wp-config.php` (or `.env` override)
- Provides `get_conn()` context manager

### 3.2 `services/sportspress.py`
Direct MySQL reads from SportsPress tables (faster than REST for reads).
REST API used for writes (ensures WP hooks/cache fire correctly).

**Key read queries:**
- `get_player_by_discord_id(discord_id)` — reads `wp_postmeta` where `meta_key=sp_metrics`,
  deserializes PHP array, matches `discordid`
- `get_team(team_id)` — `wp_posts` + `wp_postmeta`
- `get_event(event_id)` — `wp_posts` + `wp_postmeta` (sp_teams, sp_results, sp_outcome)
- `get_league_standings(table_id)` — reads `sp_table` meta + related events
- `get_remaining_matchups(table_id)` — events in league where `sp_results` is empty
- `get_player_teams(sp_player_id)` — via `mlbb_player_roster`
- `get_player_leagues(sp_player_id)` — via roster → registrations → periods

**Key write operations (via WP REST API):**
- Create `sp_team`, `sp_player`, `sp_event`, `sp_tournament`, `sp_table`
- Update `sp_event` with result after match confirmation
- Update `sp_player` metrics (discord linkage) on registration
- Publish team roster page (shortcode-based) after season assignment

### 3.3 `services/match_parser.py`
AI screenshot parsing using **Claude claude-haiku-4-5** (vision, cost-efficient).

**Flow:**
1. Receive Discord attachment URL
2. Download image bytes
3. Send to Claude with structured prompt requesting JSON output:
   ```json
   {
     "home_team": "string",
     "away_team": "string",
     "home_score": int,
     "away_score": int,
     "confidence": float,
     "notes": "string"
   }
   ```
4. Validate response, return parsed result

### 3.4 `services/registration.py`
- `open_registration(entity_type, entity_id, opens_at, closes_at, max_teams, created_by)`
- `close_registration(period_id)`
- `register_team(period_id, sp_team_id, discord_id)` — enforces max_teams, open window
- `approve_registration(reg_id, reviewed_by)`
- `get_open_periods()` — auto-updates status based on datetime

### 3.5 `services/scheduler.py`
Background task loop. Runs every 60 seconds.

**Responsibilities:**
1. **Registration transitions**: query `mlbb_registration_periods` where `status != 'closed'`
   and `opens_at <= NOW()` (→ set `open`) or `closes_at <= NOW()` (→ set `closed`).
   On transition, post announcement embed to `#announcements` channel.

2. **Match window creation** (Thursdays at midnight): for each league's active season,
   generate the week's Thu–Sun play window entries in `mlbb_match_schedule`.

3. **Voice channel creation** (Thursday at play start time): create one voice channel per
   active league match window. Write to `mlbb_voice_channels`.

4. **Discord event creation**: create a Discord scheduled event for each match window.
   Name format: `🏟️ [League Name] — Week N Play Window`. Write to `mlbb_discord_events`.

5. **Voice channel teardown** (Sunday at window_end): delete expired voice channels,
   cancel or complete associated Discord events. Update `deleted_at` in `mlbb_voice_channels`.

6. **Season start** (on `play_start` date): trigger round-robin bracket generation
   for each league with confirmed team registrations.

7. **Match reminders**: post 24h-before and 1h-before reminders to `#match-reminders`.

### 3.6 `services/round_robin.py`
Generates a round-robin match schedule for N teams.

**Algorithm** (circle method):
- Fix team 0, rotate remaining N-1 teams across rounds
- For N teams: N-1 rounds, each team plays once per round
- For odd N: one team gets a bye per round
- Outputs list of `(round, home_team_id, away_team_id)` tuples

**Season start flow:**
1. Query approved teams for each `sp_league_id` this season
2. Run round-robin algorithm
3. Create one `sp_event` post per matchup via WP REST API
4. Populate `mlbb_match_schedule` with Thu–Sun windows for each round

### 3.7 `services/eruditio.py`
Random team assignment for the Free Play (Eruditio) league.

**Season start flow:**
1. Query all players registered for Eruditio season via `mlbb_team_registrations`
2. Shuffle player list
3. Split into groups of 5 (with fills if uneven)
4. For each group: create `sp_team` post, populate `mlbb_player_roster`, update `sp_player`
5. Publish team roster pages with `[sp_players]` shortcode
6. Register created teams into Eruditio league round-robin

---

## 4. Bot Cogs & Commands

### 4.1 `cogs/player.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/player register [ign]` | Link Discord account to a new or existing sp_player. Creates WP post if IGN doesn't exist. Updates `sp_metrics` with Discord ID. | Everyone |
| `/player profile [@user\|ign]` | Embed showing: IGN, nationality, avatar, active teams, active leagues, career stats | Everyone |

**Profile embed fields:**
- Player name, avatar (from sp_player thumbnail)
- Teams: list with role (captain/player/sub)
- Active leagues: name + current standings position
- Stats (from SportsPress sp_statistics if populated)
- Link to `play.mlbb.site/player/[ign]/`

**On `/player register`:**
1. Create `sp_player` post via WP REST API (title = IGN, status = publish)
2. Set `sp_metrics` meta with `discordid`, `discordusername` (via REST)
3. Insert into `mlbb_player_roster` with no team yet
4. DM player confirmation with profile link

### 4.2 `cogs/team.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/team create [name]` | Create sp_team, add creator as captain in `mlbb_player_roster` | Everyone |
| `/team list` | List all teams with roster count | Everyone |
| `/team roster [team]` | Show full roster embed with roles | Everyone |
| `/team invite [@user]` | Send invite to a registered player (must be captain) | Captain |
| `/team accept [team]` | Accept a pending team invite | Invited player |
| `/team kick [@user]` | Remove a player from team roster | Captain |
| `/team register [team] [league]` | Register team for an open league registration period | Captain |
| `/team withdraw [team] [league]` | Withdraw registration before period closes | Captain |

**Player constraint enforcement:**
- One team per league per player (checked at `/team register` time)
- Captain cannot kick themselves — use `/team disband` or transfer captaincy first
- A player on a registered team cannot join another team in the same league

**After team registration approved:**
1. Bot creates/updates `sp_team` page on play.mlbb.site with `[sp_players]` shortcode
2. Players' `sp_player` posts linked to `sp_team` via WP REST API
3. Roster page published and linked back in Discord confirmation embed

### 4.3 `cogs/league.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/league list` | List all active leagues | Everyone |
| `/league standings [name]` | Standings table embed (W/L/Pts) | Everyone |
| `/league remaining [name]` | List unplayed matchups grouped by round | Everyone |
| `/league schedule [name]` | Full match schedule embed | Everyone |
| `/league register [league]` | Register your team for an open league period | Captain |

**`/league remaining` output example:**
```
Spring 2026 — Moniyan League — Remaining Matchups (6)
──────────────────────────────────────────────────────
Round 3:  Team A  vs  Team B   (window: Apr 3–6)
Round 3:  Team C  vs  Team D   (window: Apr 3–6)
Round 4:  Team A  vs  Team C   (window: Apr 10–13)
...
Full schedule: play.mlbb.site/moniyan-league/
```

### 4.4 `cogs/tournament.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/tournament create [name] [description?]` | Create sp_tournament post | Organizer |
| `/tournament list` | List all tournaments | Everyone |
| `/tournament info [name]` | Details, registered teams, status | Everyone |
| `/tournament bracket [name]` | Link to play.mlbb.site bracket page | Everyone |

### 4.5 `cogs/match.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/match list [league\|team]` | List upcoming and recent matches | Everyone |
| `/match get [event_id\|name]` | Show event details, result if played | Everyone |
| `/match submit [event] [screenshot]` | Submit screenshot result — triggers AI parsing | Player |
| `/match confirm [submission_id]` | Confirm a pending result (opposing captain) | Captain |
| `/match dispute [submission_id] [reason]` | Flag a result for admin review | Captain |

**`/match submit` flow:**
1. Bot defers response (processing)
2. Screenshot downloaded, sent to Claude claude-haiku-4-5 vision
3. Bot posts parsing result embed to `#match-results` channel:
   ```
   📸 Match Result Submitted
   ─────────────────────────
   Event:       Round 2 — Team A vs Team B
   Parsed:      Team A  3 – 1  Team B
   Confidence:  94%
   Submitted by: @p3hndrx

   ✅ Confirm   ❌ Dispute
   (Opposing captain must react within 24h)
   ```
4. On confirm: REST API updates `sp_event` with result; record marked confirmed;
   `mlbb_match_schedule` status → `completed`
5. On dispute: flagged for admin, both captains notified via DM
6. Timeout (24h no confirm): auto-flag for admin review

**AI confidence thresholds:**
- ≥ 0.85: Display result, require only opposing captain confirmation
- 0.60–0.84: Display with warning, require admin + captain confirmation
- < 0.60: Reject automatically, request clearer screenshot

### 4.6 `cogs/admin.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/registration open [league] [opens] [closes] [max_teams?]` | Schedule a registration window | Organizer |
| `/registration close [league]` | Force-close registration early | Organizer |
| `/registration list` | Show all periods and their status | Organizer |
| `/admin approve [registration_id]` | Approve a team registration | Organizer |
| `/admin reject [registration_id] [reason]` | Reject a team registration | Organizer |
| `/admin pending` | List all pending team registrations | Organizer |
| `/admin link-player [discord_user] [sp_player_id]` | Manually link Discord → sp_player | Admin |
| `/admin season-start [season_name]` | Trigger round-robin generation for all leagues | Admin |
| `/admin assign-eruditio [season_name]` | Run random team assignment for Eruditio | Admin |
| `/admin resolve-dispute [submission_id] [home_score] [away_score]` | Admin override for disputed result | Admin |

---

## 5. Voice Channel & Event Lifecycle

### Weekly Match Window (Thu–Sun)

```
Monday 00:00 UTC
    └── scheduler: generate mlbb_match_schedule rows for the week

Thursday 19:00 PST (03:00 UTC Friday)
    ├── create voice channels: one per league
    │     name format: "🏟️ Moniyan League — Round N"
    │     category: "League Matches" (created if missing)
    │     permissions: @everyone can join, captains can manage
    └── create Discord scheduled events: one per league
          name: "🏟️ Moniyan League — Round N Play Window"
          start: Thu 7:00 PM PST, end: Sun 11:00 PM PST
          description: includes match pairings + play.mlbb.site link

Sunday 23:00 PST (07:00 UTC Monday)
    ├── delete all voice channels created this window
    ├── mark Discord events as completed
    └── post reminders to #match-results for any uncompleted matchups
```

### Notifications

| Trigger | Channel | Message |
|---------|---------|---------|
| Registration opens | #announcements | "📋 Registration is now open for [League] — [Season]! Use `/league register` to sign up." |
| Registration closes (24h) | #announcements | "⏰ Registration for [League] closes in 24 hours!" |
| Registration closed | #announcements | "🔒 Registration for [League] is now closed. [N] teams registered." |
| Season start | #announcements | "🏆 [Season] has begun! Round-robin schedule generated. First match window: Thu [date]." |
| Match window opens | #match-reminders | "🎮 Match window open! [League] Round [N] — play your matches by Sunday 11 PM PST." |
| Match window closes (24h) | #match-reminders | "⏰ Match window closes in 24 hours. Unplayed matches will be reviewed by admins." |
| Match confirmed | #match-results | Confirmation embed with final score |
| Dispute raised | #match-results + DMs | "⚠️ Result disputed — admins have been notified." |

---

## 6. Round-Robin Season Flow

```
1. Admin runs /admin season-start "Spring 2026"
2. For each of the 13 leagues:
   a. Query mlbb_team_registrations where status='approved' and period maps to this season
   b. Run circle-method round-robin for N teams
   c. Create sp_event WP posts for each matchup (via REST)
   d. Populate mlbb_match_schedule with Thu–Sun windows for each round
3. Post #announcements embed: season bracket ready, link to league pages

4. Eruditio:
   a. Admin runs /admin assign-eruditio "Spring 2026"
   b. All Eruditio registrants shuffled and split into teams of 5
   c. Teams created as sp_team posts, rosters populated
   d. Round-robin generated for new teams
   e. Team rosters published on play.mlbb.site (shortcode pages)
```

---

## 7. Discord ↔ SportsPress Synchronization

### Player Sync (on `/player register`)
1. Create `sp_player` post: title = IGN, status = publish
2. Set `sp_metrics`: `discordid`, `discordusername`, `discorddiscriminator`
3. Set `_thumbnail_id` if player has Discord avatar uploaded (optional, Phase 5)
4. Insert `mlbb_player_roster` row (no team yet)

### Team Roster Sync (on team registration approval)
1. Create `sp_team` post (if not exists): title = team name, status = publish
2. Link `sp_player` posts to `sp_team` via `sp_player` relationship meta
3. Set page content: `[sp_players team="ID"]` shortcode block
4. Post permalink: `play.mlbb.site/team/[team-slug]/`

### Standings Sync (on match result confirmation)
1. REST PATCH to `sp_event` post with `sp_results` and `sp_outcome` meta
2. SportsPress auto-recalculates `sp_table` standings on next page load
3. Bot can optionally post updated standings embed to `#league-updates`

---

## 8. File Structure

```
MLBB-TournamentBot/
├── run.py
├── config.py
├── requirements.txt
├── .env / .env.example
├── PLAN.md                          ← this file
│
├── db/
│   ├── migrate.py                   ← creates mlbb_* tables (idempotent)
│   └── schema.sql                   ← raw SQL for reference
│
├── scripts/
│   ├── league_pages.py              ← WP league page setup (run manually)
│   └── season_init.py               ← sp_season + sp_table + reg period init
│
├── bot/
│   ├── __init__.py
│   ├── main.py
│   └── cogs/
│       ├── __init__.py
│       ├── player.py
│       ├── team.py
│       ├── tournament.py
│       ├── league.py
│       ├── match.py
│       └── admin.py
│
└── services/
    ├── __init__.py
    ├── db.py                        ← aiomysql pool
    ├── sportspress.py               ← MySQL reads + REST writes
    ├── match_parser.py              ← Claude vision AI
    ├── registration.py              ← registration period logic
    ├── scheduler.py                 ← background task loop (60s tick)
    ├── round_robin.py               ← circle-method bracket generation
    └── eruditio.py                  ← random team assignment for Free Play
```

---

## 9. Configuration (.env)

```env
# Discord
DISCORD_TOKEN=
GUILD_IDS=850386581135163489
LOG_LEVEL=INFO

# Roles
ORGANIZER_ROLES=Tournament Organizer
ADMIN_ROLES=admins

# WordPress / SportsPress
WP_PLAY_MLBB_URL=https://play.mlbb.site
WP_PLAY_MLBB_USER=admin
WP_PLAY_MLBB=<app-password>

# Database (read from wp-config.php if not set)
DB_HOST=localhost
DB_NAME=playmlbb_db
DB_USER=wpdbuser
DB_PASSWORD=<password>
DB_PREFIX=wp_

# Claude API (for screenshot parsing)
ANTHROPIC_API_KEY=

# Channel IDs
ANNOUNCEMENTS_CHANNEL_ID=
MATCH_RESULTS_CHANNEL_ID=
MATCH_REMINDERS_CHANNEL_ID=
LEAGUE_UPDATES_CHANNEL_ID=

# Voice channel settings
MATCH_VOICE_CATEGORY_ID=       # Discord category for auto-created match channels
MATCH_WINDOW_START_HOUR=19     # 7 PM PST — Thursday window open
MATCH_WINDOW_END_HOUR=23       # 11 PM PST — Sunday window close
MATCH_WINDOW_TZ=America/Los_Angeles
```

---

## 10. Implementation Phases

### Phase 1 — Foundation ✅ (partially)
- [x] `db/migrate.py` + `db/schema.sql` (5 tables)
- [ ] Add missing tables: `mlbb_match_schedule`, `mlbb_discord_events`, `mlbb_voice_channels`
- [x] `scripts/league_pages.py` — 13 league pages, format hubs, nav menus, league directory
- [x] `scripts/season_init.py` — 5 seasons, sp_tables, registration periods
- [ ] `services/db.py` connection pool
- [ ] `services/sportspress.py` core reads (player by discord ID, team, event, standings)
- [ ] `cogs/player.py` — `/player register`, `/player profile`

### Phase 2 — Team & Registration
- [ ] `services/registration.py`
- [ ] `cogs/team.py` — full team management + invites + kick
- [ ] `cogs/admin.py` — registration open/close/approve/reject
- [ ] Player constraint enforcement (one team per league)

### Phase 3 — League & Season
- [ ] `services/round_robin.py` — circle-method bracket generation
- [ ] `services/eruditio.py` — random team assignment
- [ ] `cogs/league.py` — standings, remaining matchups, schedule
- [ ] `cogs/tournament.py` — create, info, bracket link
- [ ] `/admin season-start` + `/admin assign-eruditio` commands
- [ ] Team roster publication (shortcode pages on play.mlbb.site)

### Phase 4 — Match Results
- [ ] `services/match_parser.py` — Claude vision integration
- [ ] `cogs/match.py` — submit, confirm, dispute flow
- [ ] REST API write-back to SportsPress on confirmation
- [ ] 24h auto-flag for unconfirmed results

### Phase 5 — Automation & Scheduling
- [ ] `services/scheduler.py` — 60-second background task loop
- [ ] Automated registration status transitions (scheduled → open → closed)
- [ ] Weekly match window creation (mlbb_match_schedule rows)
- [ ] Voice channel auto-creation (Thu 7 PM PST)
- [ ] Discord scheduled event creation (Thu–Sun per league)
- [ ] Voice channel auto-teardown (Sun 11 PM PST)
- [ ] Notification embeds (registration, match reminders, season start)
- [ ] Post-window admin flag for uncompleted matches

### Phase 6 — Polish & Reliability
- [ ] Systemd service definition (`/etc/systemd/system/tournament.service`)
- [ ] Graceful shutdown (drain pending tasks, close DB pool)
- [ ] Admin dashboard: `/admin pending`, `/admin disputes`
- [ ] Dispute escalation workflow (DMs + admin ping)
- [ ] Discord avatar → sp_player thumbnail sync (optional)
- [ ] `/league standings` embed with auto-refresh on result confirmation
- [ ] Monday morning `#match-reminders` post (remaining matchups for the week)

---

## 11. Key Technical Notes

### PHP Serialized Meta
SportsPress stores arrays as PHP-serialized strings in `wp_postmeta`.
Python reads these with `phpserialize` library. Writes go through WP REST API (PHP handles serialization).

```python
import phpserialize
metrics = phpserialize.loads(raw_meta.encode(), decode_strings=True)
discord_id = metrics.get('discordid')
```

### Discord ID as Primary Link
The `sp_metrics` `discordid` field on `sp_player` is the canonical Discord↔player link.
On `/player register`, bot:
1. Checks if Discord ID already exists in any `sp_metrics`
2. If not: creates `sp_player` post via REST, sets `sp_metrics` with Discord ID
3. If yes: returns existing player profile

### Round-Robin — Circle Method
For N teams, fix team[0] and rotate others through N-1 rounds:
```
Round 1: [0 vs N-1], [1 vs N-2], [2 vs N-3], ...
Round 2: [0 vs N-2], [N-1 vs N-3], [1 vs N-4], ...
```
Home/away alternation: even rounds swap home/away for all pairs.

### League Format
All leagues use **round-robin** format. MLBB does not use snake draft.
Draft pick leagues use in-game hero draft during the match itself — this is not managed by the bot.

### Voice Channel Permissions
Auto-created match voice channels:
- `@everyone`: View Channel = Allow, Connect = Allow
- `@Tournament Organizer`: Manage Channels = Allow, Move Members = Allow
- Muted if channel is empty (bot checks after window_end and deletes)

### WP REST API Write Pattern
All WordPress writes use Basic Auth (WP Application Password):
```python
auth = aiohttp.BasicAuth(WP_USER, WP_APP_PASSWORD)
async with session.post(f"{WP_URL}/wp-json/wp/v2/posts", json=payload, auth=auth) as r:
    ...
```

### Remaining Matchups Logic
A matchup is "remaining" when:
- `sp_event` post exists with both teams assigned (`sp_team` meta)
- `sp_results` meta is empty (`a:0:{}`) or null
- `post_status = 'publish'` or `'future'`

---

## 12. Dependencies

```
discord.py>=2.3.0
python-dotenv>=1.0.0
aiohttp>=3.9.0
aiomysql>=0.2.0
phpserialize>=1.3
anthropic>=0.25.0
pytz>=2024.1
```
