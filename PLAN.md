# MLBB-TournamentBot — Full Implementation Plan

**Created**: March 29, 2026
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
     └─── WP REST API (aiohttp) ─── play.mlbb.site           │
               └── Writes results back to SportsPress         │
                                                              │
     └─── Claude API (vision) ─── Screenshot parsing         │
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

### 2.1 `mlbb_registration_periods`
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

### 2.2 `mlbb_team_registrations`
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

### 2.3 `mlbb_player_roster`
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

### 2.4 `mlbb_match_submissions`
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

### 4.2 `cogs/team.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/team create [name]` | Create sp_team, add creator as captain in `mlbb_player_roster` | Everyone |
| `/team list` | List all teams with roster count | Everyone |
| `/team roster [team]` | Show full roster embed with roles | Everyone |
| `/team invite [@user]` | Invite a registered player to your team (must be captain) | Captain |
| `/team accept [team]` | Accept a pending team invite | Everyone |
| `/team register [team] [tournament\|league]` | Register team for an open competition | Captain |
| `/team withdraw [team] [tournament\|league]` | Withdraw registration | Captain |

### 4.3 `cogs/tournament.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/tournament create [name] [description?]` | Create sp_tournament post | Organizer |
| `/tournament list` | List all tournaments | Everyone |
| `/tournament info [name]` | Details, registered teams, status | Everyone |
| `/tournament bracket [name]` | Link to play.mlbb.site bracket page | Everyone |

### 4.4 `cogs/league.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/league create [name] [description?]` | Create sp_table post | Organizer |
| `/league list` | List all leagues | Everyone |
| `/league standings [name]` | Standings table embed (W/L/Pts) | Everyone |
| `/league remaining [name]` | List unplayed matchups grouped by team | Everyone |
| `/league schedule [name]` | Full match schedule embed | Everyone |

**`/league remaining` output example:**
```
Spring 2026 League — Remaining Matchups (6)
─────────────────────────────────────────
Week 3:  Team A  vs  Team B   (unscheduled)
Week 3:  Team C  vs  Team D   (unscheduled)
Week 4:  Team A  vs  Team C   (unscheduled)
...
Full schedule: play.mlbb.site/league/spring-2026/
```

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
   Event:       Week 2 — Team A vs Team B
   Parsed:      Team A  3 – 1  Team B
   Confidence:  94%
   Submitted by: @p3hndrx

   ✅ Confirm   ❌ Dispute
   (Opposing captain must react within 24h)
   ```
4. On confirm: REST API updates `sp_event` with result; record marked confirmed
5. On dispute: flagged for admin, both captains notified

### 4.6 `cogs/admin.py`

| Command | Description | Roles |
|---------|-------------|-------|
| `/registration open [tournament\|league] [opens] [closes] [max_teams?]` | Open a registration window | Organizer |
| `/registration close [tournament\|league]` | Force-close registration | Organizer |
| `/registration list` | Show all periods and status | Organizer |
| `/admin approve [registration_id]` | Approve a team registration | Organizer |
| `/admin reject [registration_id] [reason]` | Reject a team registration | Organizer |
| `/admin pending` | List all pending team registrations | Organizer |
| `/admin link-player [discord_user] [sp_player_id]` | Manually link Discord → sp_player | Admin |

---

## 5. File Structure

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
    └── registration.py              ← registration period logic
```

---

## 6. Configuration (.env)

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

# Match results channel
MATCH_RESULTS_CHANNEL_ID=
```

---

## 7. Implementation Phases

### Phase 1 — Foundation
- [ ] `db/migrate.py` + `db/schema.sql`
- [ ] `services/db.py` connection pool
- [ ] `services/sportspress.py` core reads (player by discord ID, team, event, standings)
- [ ] `cogs/player.py` — `/player register`, `/player profile`

### Phase 2 — Team & Registration
- [ ] `services/registration.py`
- [ ] `cogs/team.py` — full team management + invites
- [ ] `cogs/admin.py` — registration open/close/approve/reject

### Phase 3 — League & Tournament
- [ ] `cogs/league.py` — standings, remaining matchups, schedule
- [ ] `cogs/tournament.py` — create, info, bracket link

### Phase 4 — Match Results
- [ ] `services/match_parser.py` — Claude vision integration
- [ ] `cogs/match.py` — submit, confirm, dispute flow
- [ ] REST API write-back to SportsPress on confirmation

### Phase 5 — Polish
- [ ] Systemd service definition (`/etc/systemd/system/tournament.service`)
- [ ] Automated registration period status updates (background task)
- [ ] Notification embeds for registration open/close events
- [ ] `/league remaining` scheduled reminder (e.g. Monday morning post)

---

## 8. Key Technical Notes

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

### Remaining Matchups Logic
A matchup is "remaining" when:
- `sp_event` post exists with both teams assigned (`sp_team` meta)
- `sp_results` meta is empty (`a:0:{}`) or null
- `post_status = 'publish'` or `'future'`

### AI Confidence Threshold
- ≥ 0.85: Auto-display parsed result, require captain confirmation
- 0.60–0.84: Display with warning, require admin + captain confirmation
- < 0.60: Reject, ask for clearer screenshot

---

## 9. Dependencies

```
discord.py>=2.3.0
python-dotenv>=1.0.0
aiohttp>=3.9.0
aiomysql>=0.2.0
phpserialize>=1.3
anthropic>=0.25.0
```
