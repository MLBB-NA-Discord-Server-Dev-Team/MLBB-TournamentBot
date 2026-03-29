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
    user=os.getenv("DB_USER", "wpdbuser"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "playmlbb_db"),
)

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS mlbb_registration_periods (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        entity_type   ENUM('tournament','league') NOT NULL,
        entity_id     BIGINT UNSIGNED NOT NULL,
        sp_season_id  INT UNSIGNED DEFAULT NULL,
        opens_at      DATETIME NOT NULL,
        closes_at     DATETIME DEFAULT NULL,
        max_teams     SMALLINT UNSIGNED DEFAULT NULL,
        created_by    VARCHAR(20) NOT NULL DEFAULT 'system',
        status        ENUM('scheduled','open','closed') DEFAULT 'scheduled',
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        INDEX (entity_type, entity_id),
        INDEX (status),
        INDEX (sp_season_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
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
    """,
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
    """,
    """
    CREATE TABLE IF NOT EXISTS mlbb_match_submissions (
        id               INT AUTO_INCREMENT PRIMARY KEY,
        sp_event_id      BIGINT UNSIGNED NOT NULL,
        submitted_by     VARCHAR(20) NOT NULL,
        screenshot_url   TEXT NOT NULL,
        ai_home_team     VARCHAR(100) DEFAULT NULL,
        ai_away_team     VARCHAR(100) DEFAULT NULL,
        ai_home_score    TINYINT UNSIGNED DEFAULT NULL,
        ai_away_score    TINYINT UNSIGNED DEFAULT NULL,
        ai_confidence    FLOAT DEFAULT NULL,
        ai_raw           TEXT DEFAULT NULL,
        status           ENUM('pending','confirmed','disputed','rejected') DEFAULT 'pending',
        confirmed_by     VARCHAR(20) DEFAULT NULL,
        submitted_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
        confirmed_at     DATETIME DEFAULT NULL,
        INDEX (sp_event_id),
        INDEX (status),
        INDEX (submitted_by)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS mlbb_season_schedule (
        id            INT AUTO_INCREMENT PRIMARY KEY,
        sp_season_id  INT UNSIGNED NOT NULL,
        season_name   VARCHAR(100) NOT NULL,
        play_start    DATE NOT NULL,
        reg_opens     DATE NOT NULL,
        reg_closes    DATE NOT NULL,
        created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY (sp_season_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def run():
    conn = mysql.connector.connect(**DB)
    cur = conn.cursor()
    for sql in TABLES:
        cur.execute(sql)
        print(f"  OK: {sql.strip().split()[5]}")
    conn.commit()
    cur.close()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    run()
