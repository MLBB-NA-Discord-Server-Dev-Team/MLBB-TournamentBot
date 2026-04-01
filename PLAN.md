# MLBB-TournamentBot — Full Implementation Plan

**Created**: March 29, 2026
**Updated**: April 1, 2026
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

### 2.9 `mlbb_pickup_pool`
Teams waiting to be slotted into the next pick-up tournament bracket.
Rolls over continuously — every 8 teams that accumulate fire a new bracket automatically.

```sql
CREATE TABLE mlbb_pickup_pool (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    sp_team_id    BIGINT UNSIGNED NOT NULL UNIQUE,  -- wp_posts.ID (sp_team)
    joined_by     VARCHAR(20) NOT NULL,             -- Discord ID (captain)
    joined_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX (joined_at)   -- FIFO order for seeding
);
```

### 2.10 `mlbb_pickup_tournaments`
One row per auto-generated single-elimination bracket.

```sql
CREATE TABLE mlbb_pickup_tournaments (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    sp_tournament_id  BIGINT UNSIGNED NOT NULL,   -- wp_posts.ID (sp_tournament)
    tournament_name   VARCHAR(100) NOT NULL,      -- e.g. "Pick-up Cup #7"
    total_teams       TINYINT UNSIGNED DEFAULT 8,
    current_round     TINYINT UNSIGNED DEFAULT 1, -- 1=QF, 2=SF, 3=Final
    status            ENUM('active','completed','cancelled') DEFAULT 'active',
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at      DATETIME DEFAULT NULL
);
```

### 2.11 `mlbb_pickup_matches`
One row per individual match within a pick-up bracket.

```sql
CREATE TABLE mlbb_pickup_matches (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    pickup_tournament_id  INT NOT NULL,             -- FK mlbb_pickup_tournaments.id
    sp_event_id           BIGINT UNSIGNED NOT NULL, -- wp_posts.ID (sp_event)
    round                 TINYINT UNSIGNED NOT NULL, -- 1=QF, 2=SF, 3=Final
    match_number          TINYINT UNSIGNED NOT NULL, -- position within round (1–4 QF, 1–2 SF, 1 Final)
    home_team_id          BIGINT UNSIGNED NOT NULL,
    away_team_id          BIGINT UNSIGNED NOT NULL,
    winner_team_id        BIGINT UNSIGNED DEFAULT NULL,
    discord_channel_id    VARCHAR(20) DEFAULT NULL,  -- temp voice channel
    deadline              DATETIME NOT NULL,          -- created_at + 48h
    status                ENUM('pending','active','completed','forfeited','admin_review') DEFAULT 'pending',
    created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at          DATETIME DEFAULT NULL,
    INDEX (pickup_tournament_id),
    INDEX (round),
    INDEX (deadline)
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
   active league match window. Permissions set to matched teams only (see Voice Channel
   Permissions). Write to `mlbb_voice_channels`.

4. **Discord event creation**: create a Discord scheduled event for each match window.
   Name format: `🏟️ [League Name] — Week N Play Window`. Write to `mlbb_discord_events`.

5. **Voice channel teardown** (Sunday at window_end): delete expired voice channels,
   cancel or complete associated Discord events. Update `deleted_at` in `mlbb_voice_channels`.

6. **Season start** (on `play_start` date): trigger round-robin bracket generation
   for each league with confirmed team registrations.

7. **Match reminders**: post 24h-before and 1h-before reminders to `#match-reminders`.

8. **Pick-up pool monitor**: check `mlbb_pickup_pool` row count; if count % 8 == 0 and > 0,
   fire `pickup.create_bracket()`. Runs every 60 seconds alongside other scheduler tasks.

9. **Pick-up deadline enforcement**: query `mlbb_pickup_matches` where
   `status = 'active'` and `deadline < NOW()`. For each:
   - If one team submitted: advance submitting team, mark other forfeited
   - If neither submitted: mark `admin_review`, ping admins in `#pickup-tournaments`
   - Delete expired voice channel, log to `mlbb_voice_channels`

10. **Pick-up round advancement**: after each match result is confirmed, check if all
    matches in the current round are complete. If yes, call `pickup.advance_round()`
    to generate the next round's matches, channels, and deadlines.

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

### 3.8 `services/pickup.py`
Manages the rolling pick-up tournament pool and bracket lifecycle.

**Pool management:**
- `join_pool(sp_team_id, discord_id)` — insert into `mlbb_pickup_pool`; trigger bracket if count % 8 == 0
- `leave_pool(sp_team_id)` — remove from pool (only while not yet bracketed)
- `get_pool_count()` → current number of waiting teams
- `get_pool_position(sp_team_id)` → ordinal position in FIFO queue

**Bracket creation (fires automatically when pool reaches 8):**
1. Dequeue next 8 teams from pool (ordered by `joined_at` ASC)
2. Determine tournament number: `SELECT COUNT(*)+1 FROM mlbb_pickup_tournaments`
3. Create `sp_tournament` WP post: title = "Pick-up Cup #N", status = publish
4. Seed teams: positions 1–8 by join order
5. Generate QF pairings: seed 1 vs 8, 2 vs 7, 3 vs 6, 4 vs 5
6. For each QF pair:
   - Create `sp_event` WP post (teams assigned, no result yet)
   - Create temp voice channel: "🏆 Pick-up Cup #N — QF Match M"
   - Set deadline = NOW() + 48h
   - Insert `mlbb_pickup_matches` row
7. Insert `mlbb_pickup_tournaments` row
8. Post bracket announcement embed to `#pickup-tournaments` channel

**Round advancement (fires when all matches in current round are completed):**
1. Collect winners from completed round
2. For SF (round 2): pair QF1 winner vs QF2 winner, QF3 winner vs QF4 winner
3. For Final (round 3): pair SF1 winner vs SF2 winner
4. Repeat steps 6–8 from bracket creation for new round
5. On tournament completion: post final result embed, archive sp_tournament

**Deadline enforcement (checked by scheduler):**
- If `deadline < NOW()` and `status = 'active'` and no submission: mark `forfeited`
  - Team that submitted results (or submitted first if both did) advances
  - If neither submitted: admin review flag, ping `#pickup-tournaments`

### 3.9 `services/eruditio.py`
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

### 4.6 `cogs/pickup.py`

Pick-up tournament pool and bracket commands. Separate from league play — any team can join
the pool at any time, independent of the current league season.

| Command | Description | Roles |
|---------|-------------|-------|
| `/pickup join` | Add your team to the rolling tournament pool | Captain |
| `/pickup leave` | Remove your team from the pool (before bracket fires) | Captain |
| `/pickup status` | Show pool size and your team's queue position | Everyone |
| `/pickup bracket [#N\|current]` | Display bracket for a specific pick-up cup | Everyone |
| `/pickup history` | List completed pick-up cups with results | Everyone |

**`/pickup status` embed example:**
```
🎯 Pick-up Tournament Pool
──────────────────────────
Teams in pool:   5 / 8
Next bracket:    3 more teams needed

Your team:  Team Nexus — Queue position #3
            Joined: Today at 2:34 PM

Tip: Use /pickup leave to withdraw before the bracket fires.
```

**`/pickup bracket` embed example:**
```
🏆 Pick-up Cup #7 — Single Elimination
────────────────────────────────────────
Quarter-Finals          Semi-Finals         Final
Team Nexus      ─┐
  vs            ├──► Team Nexus ─┐
Team Vortex     ─┘               │
                                 ├──► ???
Team Storm      ─┐               │
  vs            ├──► ???      ───┘
Team Apex       ─┘

⏰ QF Deadline: Apr 3, 11:59 PM PST
📋 play.mlbb.site/pickup-cup-7/
```

### 4.7 `cogs/admin.py`

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
| `/admin pickup-advance [match_id] [winner_team]` | Manually advance a pick-up match (deadline miss, dispute) | Admin |
| `/admin pickup-cancel [tournament_id]` | Cancel an active pick-up cup and return teams to pool | Admin |

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
| Pick-up pool: 8 teams ready | #pickup-tournaments | "🏆 Bracket firing! Pick-up Cup #N is starting — 8 teams locked in." |
| Pick-up bracket created | #pickup-tournaments | Bracket embed with QF pairings + 48h deadline |
| Pick-up round advance | #pickup-tournaments | Updated bracket embed showing next round pairings + new deadline |
| Pick-up deadline approaching (12h) | #pickup-tournaments + DMs to captains | "⏰ Your pick-up match deadline is in 12 hours. Submit results or contact your opponent." |
| Pick-up deadline missed | #pickup-tournaments + admin ping | "⚠️ Match forfeited — [Team] did not submit by deadline." |
| Pick-up cup complete | #pickup-tournaments | "🥇 Pick-up Cup #N complete! Winner: [Team]. Results at play.mlbb.site/pickup-cup-N/" |

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
│       ├── pickup.py
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
    ├── eruditio.py                  ← random team assignment for Free Play
    └── pickup.py                    ← rolling pick-up tournament pool + bracket lifecycle
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

# Channel IDs (continued)
PICKUP_TOURNAMENT_CHANNEL_ID=  # #pickup-tournaments announcements channel

# Voice channel settings
# All auto-created match VCs (league + pick-up) are placed in this category
MATCH_VOICE_CATEGORY_ID=1488715625172959272
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

### Phase 5b — Pick-up Tournaments
- [ ] Add `mlbb_pickup_pool`, `mlbb_pickup_tournaments`, `mlbb_pickup_matches` to `db/migrate.py`
- [ ] `services/pickup.py` — pool management, bracket creation, round advancement, deadline enforcement
- [ ] `cogs/pickup.py` — `/pickup join`, `/pickup leave`, `/pickup status`, `/pickup bracket`
- [ ] Scheduler items 8–10: pool monitor, deadline enforcement, round advancement
- [ ] 12h deadline reminder DMs to captains
- [ ] `/admin pickup-advance`, `/admin pickup-cancel`
- [ ] Pick-up Cup `sp_tournament` page creation on play.mlbb.site (bracket view via shortcode)

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

### Pick-up Tournament — Bracket Flow

**Format**: Single elimination, BO1 (shoot-out). 8 teams → 3 rounds (QF/SF/Final).
**Trigger**: Automatic — fires the moment 8 teams accumulate in `mlbb_pickup_pool`.
**Seeding**: FIFO join order (first in = seed 1). No rank-based seeding.

```
Pool reaches 8 teams
    │
    ▼
Create sp_tournament "Pick-up Cup #N"   (WP REST API)
Dequeue 8 teams from mlbb_pickup_pool
Seed: 1 vs 8, 2 vs 7, 3 vs 6, 4 vs 5
    │
    ▼
Round 1 — Quarter-Finals (4 matches, parallel)
    ├── Create 4 sp_event posts
    ├── Create 4 voice channels: "🏆 Pick-up Cup #N — QF M"
    │       (private: only the two matched teams + staff — see Voice Channel Permissions)
    ├── deadline = NOW() + 48h
    └── Post bracket embed to #pickup-tournaments
    │
    │ (each match: /match submit → confirm → winner recorded)
    │ (when all 4 QF complete → advance_round fires)
    ▼
Round 2 — Semi-Finals (2 matches, parallel)
    ├── Winners: QF1W vs QF2W, QF3W vs QF4W
    ├── Create 2 sp_event posts
    ├── Create 2 voice channels: "🏆 Pick-up Cup #N — SF M"
    │       (private: only the two matched teams + staff)
    ├── deadline = NOW() + 48h
    └── Post updated bracket embed
    │
    ▼
Round 3 — Final (1 match)
    ├── Winners: SF1W vs SF2W
    ├── Create 1 sp_event post
    ├── Create 1 voice channel: "🏆 Pick-up Cup #N — Final"
    │       (private: only the two matched teams + staff)
    ├── deadline = NOW() + 48h
    └── Post final bracket embed
    │
    ▼
Tournament complete
    ├── Mark sp_tournament complete
    ├── Post winner announcement embed
    ├── Delete any remaining voice channels
    └── sp_tournament page on play.mlbb.site shows final bracket
```

**Deadline miss handling:**
- One team submitted, other did not → submitting team auto-advances (forfeit win)
- Neither submitted → admin review; admins choose to extend or assign a winner
- Admin override: `/admin pickup-advance [match_id] [winner_team]`

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
All auto-created match voice channels are created under a single dedicated Discord category
(`MATCH_VOICE_CATEGORY_ID`) and are **private** — visible only to the players on the two
matched teams, plus staff. `@everyone` is denied by default.

**Permission overwrites applied at channel creation:**
```
@everyone              → View Channel = Deny, Connect = Deny
@Tournament Organizer  → View Channel = Allow, Connect = Allow, Manage Channels = Allow, Move Members = Allow
@admins                → View Channel = Allow, Connect = Allow, Manage Channels = Allow
[each Discord member on home team]  → View Channel = Allow, Connect = Allow
[each Discord member on away team]  → View Channel = Allow, Connect = Allow
```

**Lookup flow** (same for league matches and pick-up matches):
1. Query `mlbb_player_roster` for all active players on both `sp_team_id`s
2. Resolve Discord IDs → `discord.Member` objects in the guild
3. Apply per-member `PermissionOverwrite(view_channel=True, connect=True)` at creation
4. Players who have not yet run `/player register` (no Discord ID on file) are excluded
   — bot logs a warning but does not block channel creation

**Roster changes after channel creation** (e.g. a substitute added mid-match):
- Captain runs `/team invite` → on accept, bot calls `channel.set_permissions(member, view_channel=True, connect=True)`
  for every active match voice channel that team is currently participating in

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
