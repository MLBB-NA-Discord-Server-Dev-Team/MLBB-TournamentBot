"""
scripts/season_init.py
Initializes seasons, lore leagues, standings tables, and registration periods
for the MLBB Tournament system.

Season cadence: every 90 days from March 21, 2026.
Registration opens 28 days before play start.
Run this script once to bootstrap all upcoming seasons, then via cron to add new ones.
Idempotent — safe to re-run.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import mysql.connector
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

WP_URL   = os.getenv("WP_PLAY_MLBB_URL", "https://play.mlbb.site").rstrip("/")
WP_USER  = os.getenv("WP_PLAY_MLBB_USER", "admin")
WP_PASS  = os.getenv("WP_PLAY_MLBB", "")
AUTH     = (WP_USER, WP_PASS)
HEADERS  = {"User-Agent": "MLBB-TournamentBot/1.0"}

DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "wpdbuser"),
    password=os.getenv("DB_PASSWORD", "zCszKbVi9xPvFk6i!"),
    database=os.getenv("DB_NAME", "playmlbb_db"),
)

SEASON_ZERO     = date(2026, 3, 21)   # first play start
SEASON_INTERVAL = 90                   # days
REG_LEAD_DAYS   = 30                   # registration opens 30 days before play start
SEASONS_AHEAD   = 5                    # how many seasons to initialize

# All active sp_league taxonomy term IDs.
# Sourced from league_pages.py output. Add new IDs here when new formats are created.
ALL_LEAGUE_IDS = [
    34, 35, 36, 37,     # Draft Pick BO5: Moniyan, Abyss, Northern Vale, Cadia Riverlands
    25, 26, 27, 28,     # Draft Pick BO3: Agelta, Los Pecados, Aberleen, Dragon Altar
    40, 41, 42, 43,     # Brawl: Megalith, Vonetis, Oasis, Swan Castle
]

def season_name(start: date) -> str:
    m = start.month
    if m in (3, 4, 5):   label = "Spring"
    elif m in (6, 7, 8): label = "Summer"
    elif m in (9, 10, 11): label = "Fall"
    else:                label = "Winter"
    return f"{label} {start.year}"


def build_season_schedule() -> list[dict]:
    seasons = []
    for i in range(SEASONS_AHEAD):
        start     = SEASON_ZERO + timedelta(days=SEASON_INTERVAL * i)
        reg_opens = start - timedelta(days=REG_LEAD_DAYS)
        seasons.append({
            "name":       season_name(start),
            "slug":       season_name(start).lower().replace(" ", "-"),
            "play_start": start,
            "reg_opens":  reg_opens,
            "reg_closes": start,
        })
    return seasons


# ── SportsPress REST helpers ──────────────────────────────────────────────────

def sp_get(endpoint: str) -> list:
    r = requests.get(f"{WP_URL}/wp-json/sportspress/v2/{endpoint}",
                     auth=AUTH, headers=HEADERS, params={"per_page": 100})
    r.raise_for_status()
    return r.json()


def sp_post(endpoint: str, data: dict) -> dict:
    r = requests.post(f"{WP_URL}/wp-json/sportspress/v2/{endpoint}",
                      auth=AUTH, headers=HEADERS, json=data)
    r.raise_for_status()
    return r.json()


def fmt_date(d: date) -> str:
    return d.strftime("%B %d, %Y")


def get_or_create_term(endpoint: str, name: str, slug: str, description: str = "") -> int:
    """Return existing term ID or create and return new one."""
    existing = sp_get(endpoint)
    for t in existing:
        if t["slug"] == slug or t["name"].lower() == name.lower():
            print(f"  EXISTS [{endpoint}]: {name} (id={t['id']})")
            return t["id"]
    payload = {"name": name, "slug": slug}
    if description:
        payload["description"] = description
    created = sp_post(endpoint, payload)
    print(f"  CREATED [{endpoint}]: {name} (id={created['id']})")
    return created["id"]


def update_term_description(endpoint: str, term_id: int, description: str):
    requests.post(
        f"{WP_URL}/wp-json/sportspress/v2/{endpoint}/{term_id}",
        auth=AUTH, headers=HEADERS, json={"description": description}
    )


def get_or_create_table(title: str, league_id: int, season_id: int) -> int:
    """Return existing sp_table ID or create and return new one."""
    existing = sp_get("tables")
    for t in existing:
        if t["title"]["rendered"] == title:
            print(f"  EXISTS [table]: {title} (id={t['id']})")
            return t["id"]
    created = sp_post("tables", {
        "title":   title,
        "status":  "publish",
        "leagues": [league_id],
        "seasons": [season_id],
    })
    print(f"  CREATED [table]: {title} (id={created['id']})")
    return created["id"]


# ── DB helpers ────────────────────────────────────────────────────────────────

def upsert_season_schedule(cur, sp_season_id: int, season: dict):
    cur.execute("""
        INSERT INTO mlbb_season_schedule
            (sp_season_id, season_name, play_start, reg_opens, reg_closes)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            season_name=VALUES(season_name),
            play_start=VALUES(play_start),
            reg_opens=VALUES(reg_opens),
            reg_closes=VALUES(reg_closes)
    """, (sp_season_id, season["name"], season["play_start"],
          season["reg_opens"], season["reg_closes"]))


def upsert_registration_period(cur, entity_id: int, sp_season_id: int, season: dict):
    """Create registration period if one doesn't already exist for this table."""
    cur.execute("""
        SELECT id, status FROM mlbb_registration_periods
        WHERE entity_type='league' AND entity_id=%s
    """, (entity_id,))
    row = cur.fetchone()
    if row:
        print(f"  EXISTS [reg_period]: table {entity_id} (id={row[0]}, status={row[1]})")
        return

    today = date.today()
    opens_at  = season["reg_opens"]
    closes_at = season["reg_closes"]

    # If we're bootstrapping past the reg_opens date, open immediately
    if today >= closes_at:
        status = "closed"
    elif today >= opens_at:
        status = "open"
        opens_at = today   # open now
    else:
        status = "scheduled"

    cur.execute("""
        INSERT INTO mlbb_registration_periods
            (entity_type, entity_id, sp_season_id, opens_at, closes_at, status, created_by)
        VALUES ('league', %s, %s, %s, %s, %s, 'system')
    """, (entity_id, sp_season_id,
          datetime.combine(opens_at, datetime.min.time()),
          datetime.combine(closes_at, datetime.min.time()),
          status))
    print(f"  CREATED [reg_period]: table {entity_id} status={status} "
          f"opens={opens_at} closes={closes_at}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    seasons = build_season_schedule()

    print("\n=== Season Schedule ===")
    for s in seasons:
        print(f"  {s['name']:20s}  play={s['play_start']}  "
              f"reg={s['reg_opens']} → {s['reg_closes']}")

    # Fetch all sp_league terms to build id→name map
    print("\n=== Fetching league terms ===")
    all_terms = sp_get("leagues")
    league_map = {t["id"]: t["name"] for t in all_terms}
    active_ids = [lid for lid in ALL_LEAGUE_IDS if lid in league_map]
    print(f"  {len(active_ids)} active league formats found")

    conn = mysql.connector.connect(**DB)
    cur  = conn.cursor()

    for season in seasons:
        print(f"\n=== Season: {season['name']} ===")
        desc = (
            f"Play Start: {fmt_date(season['play_start'])}  |  "
            f"Registration: {fmt_date(season['reg_opens'])} – {fmt_date(season['reg_closes'])}"
        )
        sp_season_id = get_or_create_term("seasons", season["name"], season["slug"], description=desc)
        upsert_season_schedule(cur, sp_season_id, season)

        for league_id in active_ids:
            league_name = league_map[league_id]
            table_title = f"{league_name} — {season['name']}"
            table_id = get_or_create_table(table_title, league_id, sp_season_id)
            upsert_registration_period(cur, table_id, sp_season_id, season)

    conn.commit()
    cur.close()
    conn.close()
    print("\n✓ Season initialization complete.")


if __name__ == "__main__":
    main()
