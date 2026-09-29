"""
db/migrate.py — idempotent schema migration for mlbb_* extension tables.
Safe to re-run; uses CREATE TABLE IF NOT EXISTS throughout.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "3306")),
    user=os.getenv("DB_USER", "wpdbuser"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "playmlbb_db"),
)

TABLES = [
    # ── Season calendar ────────────────────────────────────────────────────
    (
        "mlbb_season_schedule",
        """
        CREATE TABLE IF NOT EXISTS mlbb_season_schedule (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            sp_season_id  INT UNSIGNED NOT NULL,
            season_name   VARCHAR(100) NOT NULL,
            play_start    DATE NOT NULL,
            play_end      DATE NOT NULL,
            reg_opens     DATE NOT NULL,
            reg_closes    DATE NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY (sp_season_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Registration periods ───────────────────────────────────────────────
    (
        "mlbb_registration_periods",
        """
        CREATE TABLE IF NOT EXISTS mlbb_registration_periods (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            entity_type          ENUM('tournament','league') NOT NULL,
            entity_id            BIGINT UNSIGNED NOT NULL,
            sp_season_id         INT UNSIGNED DEFAULT NULL,
            opens_at             DATETIME NOT NULL,
            closes_at            DATETIME DEFAULT NULL,
            play_start           DATE DEFAULT NULL,
            play_end             DATE DEFAULT NULL,
            max_teams            SMALLINT UNSIGNED DEFAULT NULL,
            rule                 VARCHAR(20) DEFAULT NULL,
            created_by           VARCHAR(20) NOT NULL DEFAULT 'system',
            status               ENUM('scheduled','open','closed') DEFAULT 'scheduled',
            generation_error     TEXT DEFAULT NULL,
            generation_attempts  TINYINT UNSIGNED DEFAULT 0,
            created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX (entity_type, entity_id),
            INDEX (status),
            INDEX (sp_season_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Team registrations ─────────────────────────────────────────────────
    (
        "mlbb_team_registrations",
        """
        CREATE TABLE IF NOT EXISTS mlbb_team_registrations (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            period_id     INT NOT NULL,
            sp_team_id    BIGINT UNSIGNED NOT NULL,
            registered_by VARCHAR(20) NOT NULL,
            status        ENUM('pending','approved','rejected','withdrawn') DEFAULT 'pending',
            registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at   DATETIME DEFAULT NULL,
            reviewed_by   VARCHAR(20) DEFAULT NULL,
            notes         TEXT DEFAULT NULL,
            UNIQUE KEY (period_id, sp_team_id),
            INDEX (sp_team_id),
            INDEX (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Player roster ──────────────────────────────────────────────────────
    (
        "mlbb_player_roster",
        """
        CREATE TABLE IF NOT EXISTS mlbb_player_roster (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            discord_id    VARCHAR(20) NOT NULL,
            sp_player_id  BIGINT UNSIGNED NOT NULL,
            sp_team_id    BIGINT UNSIGNED NOT NULL,
            role          ENUM('captain','player','substitute') DEFAULT 'player',
            joined_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            status        ENUM('active','inactive') DEFAULT 'active',
            UNIQUE KEY (sp_player_id, sp_team_id),
            INDEX (discord_id),
            INDEX (sp_team_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Team invites (pending) ─────────────────────────────────────────────
    (
        "mlbb_team_invites",
        """
        CREATE TABLE IF NOT EXISTS mlbb_team_invites (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            sp_team_id    BIGINT UNSIGNED NOT NULL,
            inviter_id    VARCHAR(20) NOT NULL,
            invitee_id    VARCHAR(20) NOT NULL,
            role          ENUM('player','substitute') DEFAULT 'player',
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at    DATETIME NOT NULL,
            status        ENUM('pending','accepted','declined','expired') DEFAULT 'pending',
            UNIQUE KEY (sp_team_id, invitee_id, status),
            INDEX (invitee_id),
            INDEX (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Match submissions (screenshot results) ────────────────────────────
    (
        "mlbb_match_submissions",
        """
        CREATE TABLE IF NOT EXISTS mlbb_match_submissions (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            sp_event_id      BIGINT UNSIGNED DEFAULT NULL,
            pickup_match_id  INT DEFAULT NULL,
            submitted_by     VARCHAR(20) NOT NULL,
            winning_team_id  BIGINT UNSIGNED NOT NULL,
            screenshot_url   TEXT NOT NULL,
            battle_id        VARCHAR(30) DEFAULT NULL,
            winner_kills     TINYINT UNSIGNED DEFAULT NULL,
            loser_kills      TINYINT UNSIGNED DEFAULT NULL,
            match_duration   VARCHAR(10) DEFAULT NULL,
            match_timestamp  DATETIME DEFAULT NULL,
            ai_confidence    FLOAT DEFAULT NULL,
            ai_raw           TEXT DEFAULT NULL,
            status           ENUM('pending','confirmed','disputed','rejected') DEFAULT 'pending',
            confirmed_by     VARCHAR(20) DEFAULT NULL,
            submitted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            confirmed_at     DATETIME DEFAULT NULL,
            INDEX (sp_event_id),
            INDEX (pickup_match_id),
            INDEX (battle_id),
            INDEX (status),
            INDEX (submitted_by)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Match schedule (league play windows) ──────────────────────────────
    (
        "mlbb_match_schedule",
        """
        CREATE TABLE IF NOT EXISTS mlbb_match_schedule (
            id              INT AUTO_INCREMENT PRIMARY KEY,
            sp_event_id     BIGINT UNSIGNED NOT NULL,
            sp_league_id    BIGINT UNSIGNED NOT NULL,
            round_number    TINYINT UNSIGNED NOT NULL,
            window_start    DATETIME NOT NULL,
            window_end      DATETIME NOT NULL,
            status          ENUM('scheduled','active','completed','cancelled') DEFAULT 'scheduled',
            created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX (sp_event_id),
            INDEX (sp_league_id),
            INDEX (window_start)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Discord scheduled events ───────────────────────────────────────────
    (
        "mlbb_discord_events",
        """
        CREATE TABLE IF NOT EXISTS mlbb_discord_events (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            match_schedule_id     INT DEFAULT NULL,
            pickup_match_id       INT DEFAULT NULL,
            discord_event_id      VARCHAR(20) NOT NULL,
            guild_id              VARCHAR(20) NOT NULL,
            channel_id            VARCHAR(20) DEFAULT NULL,
            event_name            TEXT NOT NULL,
            scheduled_start       DATETIME NOT NULL,
            scheduled_end         DATETIME NOT NULL,
            status                ENUM('scheduled','active','completed','cancelled') DEFAULT 'scheduled',
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY (discord_event_id),
            INDEX (match_schedule_id),
            INDEX (pickup_match_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Voice channels (auto-created / auto-deleted) ───────────────────────
    (
        "mlbb_voice_channels",
        """
        CREATE TABLE IF NOT EXISTS mlbb_voice_channels (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            match_schedule_id INT DEFAULT NULL,
            pickup_match_id   INT DEFAULT NULL,
            channel_id        VARCHAR(20) NOT NULL,
            channel_name      VARCHAR(100) NOT NULL,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            deleted_at        DATETIME DEFAULT NULL,
            INDEX (match_schedule_id),
            INDEX (pickup_match_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Pick-up tournament pool ────────────────────────────────────────────
    (
        "mlbb_pickup_pool",
        """
        CREATE TABLE IF NOT EXISTS mlbb_pickup_pool (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            sp_team_id  BIGINT UNSIGNED NOT NULL UNIQUE,
            joined_by   VARCHAR(20) NOT NULL,
            joined_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX (joined_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Pick-up tournaments (one per 8-team bracket) ───────────────────────
    (
        "mlbb_pickup_tournaments",
        """
        CREATE TABLE IF NOT EXISTS mlbb_pickup_tournaments (
            id                INT AUTO_INCREMENT PRIMARY KEY,
            sp_tournament_id  BIGINT UNSIGNED NOT NULL,
            tournament_name   VARCHAR(100) NOT NULL,
            total_teams       TINYINT UNSIGNED DEFAULT 8,
            current_round     TINYINT UNSIGNED DEFAULT 1,
            status            ENUM('active','completed','cancelled') DEFAULT 'active',
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at      DATETIME DEFAULT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
    # ── Pick-up individual matches ─────────────────────────────────────────
    (
        "mlbb_pickup_matches",
        """
        CREATE TABLE IF NOT EXISTS mlbb_pickup_matches (
            id                    INT AUTO_INCREMENT PRIMARY KEY,
            pickup_tournament_id  INT NOT NULL,
            sp_event_id           BIGINT UNSIGNED DEFAULT NULL,
            round                 TINYINT UNSIGNED NOT NULL,
            match_number          TINYINT UNSIGNED NOT NULL,
            home_team_id          BIGINT UNSIGNED NOT NULL,
            away_team_id          BIGINT UNSIGNED NOT NULL,
            winner_team_id        BIGINT UNSIGNED DEFAULT NULL,
            discord_channel_id    VARCHAR(20) DEFAULT NULL,
            deadline              DATETIME NOT NULL,
            status                ENUM('pending','active','completed','forfeited','admin_review') DEFAULT 'pending',
            created_at            DATETIME DEFAULT CURRENT_TIMESTAMP,
            completed_at          DATETIME DEFAULT NULL,
            INDEX (pickup_tournament_id),
            INDEX (round),
            INDEX (deadline)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    ),
]


# ALTER TABLE statements for columns added after initial deployment.
# Each entry is (table, column, definition). Safe to re-run — skipped if column exists.
ALTERATIONS = [
    ("mlbb_registration_periods", "rule",
     "ALTER TABLE mlbb_registration_periods ADD COLUMN rule VARCHAR(20) DEFAULT NULL AFTER closes_at"),
    ("mlbb_registration_periods", "play_start",
     "ALTER TABLE mlbb_registration_periods ADD COLUMN play_start DATE DEFAULT NULL AFTER rule"),
    ("mlbb_registration_periods", "play_end",
     "ALTER TABLE mlbb_registration_periods ADD COLUMN play_end DATE DEFAULT NULL AFTER play_start"),
    ("mlbb_registration_periods", "generation_error",
     "ALTER TABLE mlbb_registration_periods ADD COLUMN generation_error TEXT DEFAULT NULL AFTER status"),
    ("mlbb_registration_periods", "generation_attempts",
     "ALTER TABLE mlbb_registration_periods ADD COLUMN generation_attempts TINYINT UNSIGNED DEFAULT 0 AFTER generation_error"),
]


def run():
    conn = mysql.connector.connect(**DB)
    cur = conn.cursor()

    for name, sql in TABLES:
        cur.execute(sql)
        print(f"  OK: {name}")

    # Apply alterations only if the column doesn't already exist
    db_name = DB["database"]
    for table, column, alter_sql in ALTERATIONS:
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s
            """,
            (db_name, table, column),
        )
        (exists,) = cur.fetchone()
        if exists:
            print(f"  SKIP: {table}.{column} already exists")
        else:
            cur.execute(alter_sql)
            print(f"  ADDED: {table}.{column}")

    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
