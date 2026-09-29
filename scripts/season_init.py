"""
scripts/season_init.py
Initializes seasons, lore leagues, standings tables, and registration periods
for the MLBB Tournament system.

Seasons run every SEASON_INTERVAL days from SEASON_ZERO. Within a season,
registration is staggered by FORMAT_GROUPS (BO5, then BO3, Brawl, FreePlay one
week apart); each group's play starts when its registration window closes.
Run via cron to keep the registration buffer fresh. Idempotent — safe to re-run.
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

SEASON_ZERO     = date(2026, 3, 21)   # first season play start
SEASON_INTERVAL = 90                   # days per season
SEASONS_AHEAD   = 2                    # seasons to pre-create (current + next)
REG_LEAD_DAYS   = 30                   # days before season play_start that BO5 reg opens
REG_WINDOW_DAYS = 14                   # registration window length per format group

# Format groups in stagger order.
# week_offset: how many weeks after the base reg-open date this format's window starts.
# BO5 opens first, BO3 one week later, Brawl one week after that, FreePlay one after Brawl.
# All leagues within a group open/close on the same dates each season.
FORMAT_GROUPS = [
    {"week_offset": 0, "league_ids": [34, 35, 36, 37]},  # Draft Pick BO5
    {"week_offset": 1, "league_ids": [25, 26, 27, 28]},  # Draft Pick BO3
    {"week_offset": 2, "league_ids": [40, 41, 42, 43]},  # Brawl
    {"week_offset": 3, "league_ids": [49]},               # FreePlay
]

ALL_LEAGUE_IDS = [lid for grp in FORMAT_GROUPS for lid in grp["league_ids"]]


def season_name(start: date) -> str:
    m = start.month
    if m in (3, 4, 5):     label = "Spring"
    elif m in (6, 7, 8):   label = "Summer"
    elif m in (9, 10, 11): label = "Fall"
    else:                  label = "Winter"
    return f"{label} {start.year}"


def build_season_schedule() -> list[dict]:
    """
    Return the current season plus the next SEASONS_AHEAD-1 upcoming seasons.
    Each dict: play_start, play_end, name, slug.
    """
    seasons = []
    today   = date.today()
    i = 0
    while len(seasons) < SEASONS_AHEAD:
        play_start = SEASON_ZERO + timedelta(days=SEASON_INTERVAL * i)
        play_end   = play_start + timedelta(days=SEASON_INTERVAL)
        if play_end >= today:
            name = season_name(play_start)
            seasons.append({
                "play_start": play_start,
                "play_end":   play_end,
                "name":       name,
                "slug":       name.lower().replace(" ", "-"),
            })
        i += 1
        if i > 200:
            break
    return seasons


def format_windows(season: dict) -> list[dict]:
    """
    For a given season, return one window dict per format group with the
    staggered registration dates.

      base_opens  = season.play_start - REG_LEAD_DAYS
      group_opens = base_opens + week_offset * 7
      group_closes= group_opens + REG_WINDOW_DAYS
      play_start  = group_closes   (leagues start play when reg closes)
      play_end    = season.play_end (all formats share the season end date)
    """
    base = season["play_start"] - timedelta(days=REG_LEAD_DAYS)
    windows = []
    for grp in FORMAT_GROUPS:
        opens_at   = base + timedelta(days=grp["week_offset"] * 7)
        closes_at  = opens_at + timedelta(days=REG_WINDOW_DAYS)
        windows.append({
            "league_ids": grp["league_ids"],
            "opens_at":   opens_at,
            "closes_at":  closes_at,
            "play_start": closes_at,
            "play_end":   season["play_end"],
        })
    return windows


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


def _apply_standings_meta_sync(cur, sp_table_id: int) -> None:
    """
    Sync version of apply_standings_table_meta for season_init.py (which uses
    mysql.connector, not aiomysql). Sets standard SportsPress standings metadata.
    """
    import phpserialize

    sp_event_status = phpserialize.dumps({0: b"publish", 1: b"future"}).decode()
    sp_columns = phpserialize.dumps({
        0: b"wins", 1: b"losses", 2: b"winrate"
    }).decode()
    empty_array = phpserialize.dumps({}).decode()

    meta_pairs = [
        ("sp_mode", "team"),
        ("sp_format", "standings"),
        ("sp_caption", ""),
        ("sp_date", "0"),
        ("sp_date_from", "2024-01-14"),
        ("sp_date_to", "2024-01-14"),
        ("sp_date_past", "7"),
        ("sp_date_relative", "0"),
        ("sp_main_league", ""),
        ("sp_current_season", ""),
        ("sp_select", "auto"),
        ("sp_orderby", "wins"),
        ("sp_order", "DESC"),
        ("sp_event_status", sp_event_status),
        ("sp_highlight", "0"),
        ("sp_columns", sp_columns),
        ("sp_adjustments", empty_array),
        ("sp_teams", empty_array),
        ("sp_highlight_places", "NULL"),
    ]
    for meta_key, meta_value in meta_pairs:
        cur.execute(
            "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
            (sp_table_id, meta_key),
        )
        cur.execute(
            "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
            (sp_table_id, meta_key, meta_value),
        )


def get_or_create_table(title: str, league_id: int, season_id: int) -> int:
    """Return existing sp_table ID or create and return new one."""
    # Query DB directly — REST API only returns newest 100, misses older tables
    _conn = mysql.connector.connect(**DB)
    _cur  = _conn.cursor()
    _cur.execute(
        "SELECT ID FROM wp_posts WHERE post_type='sp_table' AND post_status='publish' AND post_title=%s LIMIT 1",
        (title,),
    )
    _row = _cur.fetchone()
    _cur.close()
    _conn.close()
    if _row:
        print(f"  EXISTS [table]: {title} (id={_row[0]})")
        return _row[0]
    created = sp_post("tables", {
        "title":   title,
        "status":  "publish",
        "leagues": [league_id],
        "seasons": [season_id],
    })
    print(f"  CREATED [table]: {title} (id={created['id']})")
    # Apply standings metadata directly via MySQL (REST API drops meta)
    try:
        import mysql.connector as _mc
        from config import DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME
        _conn = _mc.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                            password=DB_PASSWORD, database=DB_NAME)
        _cur = _conn.cursor()
        _apply_standings_meta_sync(_cur, created["id"])
        _conn.commit()
        _cur.close()
        _conn.close()
        print(f"    Applied standings metadata to table {created['id']}")
    except Exception as e:
        print(f"    WARNING: could not apply standings meta: {e}")
    return created["id"]


# ── DB helpers ────────────────────────────────────────────────────────────────

def upsert_season_schedule(cur, sp_season_id: int, season: dict):
    """
    Write or update a season schedule row. When a season contains staggered
    leagues, the season-level play_start/play_end is the earliest/latest window
    across all leagues in that season — used only for display, not scheduling.
    """
    cur.execute("""
        INSERT INTO mlbb_season_schedule
            (sp_season_id, season_name, play_start, play_end, reg_opens, reg_closes)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            season_name=VALUES(season_name),
            play_start=LEAST(play_start, VALUES(play_start)),
            play_end=GREATEST(play_end, VALUES(play_end)),
            reg_opens=LEAST(reg_opens, VALUES(reg_opens)),
            reg_closes=GREATEST(reg_closes, VALUES(reg_closes))
    """, (sp_season_id, season["name"], season["play_start"],
          season["play_end"], season["reg_opens"], season["reg_closes"]))


def get_league_rule(cur, table_id: int) -> str | None:
    """Look up mlbb_rule termmeta for the sp_league term assigned to a given sp_table post."""
    cur.execute("""
        SELECT tm.meta_value
        FROM wp_term_relationships wtr
        JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                                  AND wtt.taxonomy = 'sp_league'
        JOIN wp_termmeta tm ON tm.term_id = wtt.term_id AND tm.meta_key = 'mlbb_rule'
        WHERE wtr.object_id = %s
        LIMIT 1
    """, (table_id,))
    row = cur.fetchone()
    return row[0] if row else None


def upsert_registration_period(
    cur,
    entity_id: int,
    sp_season_id: int,
    rule: str,
    opens_at: date,
    closes_at: date,
    play_start: date,
    play_end: date,
) -> None:
    """
    Create or update a registration period for a league table.

    For new periods: inserts with status derived from current date.
    For existing 'scheduled' periods: updates the dates if they differ from
    the staggered schedule (allows re-running this script to correct old periods).
    Closed/open periods are left untouched.
    """
    today = date.today()

    cur.execute("""
        SELECT id, status, opens_at, play_start
        FROM mlbb_registration_periods
        WHERE entity_type='league' AND entity_id=%s
        ORDER BY id DESC LIMIT 1
    """, (entity_id,))
    row = cur.fetchone()

    if row:
        period_id, status, existing_opens, existing_play_start = row
        # Update stale scheduled periods so dates reflect the stagger
        if status == 'scheduled' and (existing_opens != opens_at or existing_play_start != play_start):
            cur.execute("""
                UPDATE mlbb_registration_periods
                SET opens_at=%s, closes_at=%s, play_start=%s, play_end=%s, sp_season_id=%s
                WHERE id=%s
            """, (
                datetime.combine(opens_at, datetime.min.time()),
                datetime.combine(closes_at, datetime.min.time()),
                play_start, play_end, sp_season_id, period_id,
            ))
            print(f"  UPDATED [reg_period]: table {entity_id} (id={period_id}) "
                  f"play_start={play_start} opens={opens_at}")
        else:
            print(f"  EXISTS  [reg_period]: table {entity_id} (id={period_id}, status={status})")
        return

    # Determine initial status from current date
    if today >= closes_at:
        status = "closed"
    elif today >= opens_at:
        status = "open"
        opens_at = today   # opened mid-window; start from today
    else:
        status = "scheduled"

    cur.execute("""
        INSERT INTO mlbb_registration_periods
            (entity_type, entity_id, sp_season_id, opens_at, closes_at,
             play_start, play_end, rule, status, created_by)
        VALUES ('league', %s, %s, %s, %s, %s, %s, %s, %s, 'system')
    """, (
        entity_id, sp_season_id,
        datetime.combine(opens_at, datetime.min.time()),
        datetime.combine(closes_at, datetime.min.time()),
        play_start, play_end,
        rule, status,
    ))
    print(f"  CREATED [reg_period]: table {entity_id} rule={rule} status={status} "
          f"play_start={play_start} opens={opens_at} closes={closes_at}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n=== Fetching league terms ===")
    all_terms  = sp_get("leagues")
    league_map = {t["id"]: t["name"] for t in all_terms}
    active_ids = set(lid for lid in ALL_LEAGUE_IDS if lid in league_map)
    print(f"  {len(active_ids)} active league formats found")

    seasons = build_season_schedule()

    print("\n=== Staggered Schedule (this run) ===")
    for season in seasons:
        print(f"\n  {season['name']}  (play {season['play_start']} → {season['play_end']})")
        for win in format_windows(season):
            label = league_map.get(win["league_ids"][0], "?").replace(" League", "")
            league_names = ", ".join(
                league_map.get(lid, str(lid)).replace(" League", "")
                for lid in win["league_ids"] if lid in active_ids
            )
            print(f"    reg {win['opens_at']} → {win['closes_at']}  "
                  f"play {win['play_start']} → {win['play_end']}  [{league_names}]")

    conn = mysql.connector.connect(**DB)
    cur  = conn.cursor()

    season_id_cache: dict[str, int] = {}

    for season in seasons:
        s_name = season["name"]
        s_slug = season["slug"]

        if s_name not in season_id_cache:
            sp_season_id = get_or_create_term("seasons", s_name, s_slug)
            season_id_cache[s_name] = sp_season_id
        sp_season_id = season_id_cache[s_name]

        print(f"\n=== Season: {s_name} (sp_season_id={sp_season_id}) ===")

        for win in format_windows(season):
            opens_at   = win["opens_at"]
            closes_at  = win["closes_at"]
            play_start = win["play_start"]
            play_end   = win["play_end"]

            # Season schedule row spans the full range across all format windows
            upsert_season_schedule(cur, sp_season_id, {
                "name":       s_name,
                "play_start": play_start,
                "play_end":   play_end,
                "reg_opens":  opens_at,
                "reg_closes": closes_at,
            })

            for league_id in win["league_ids"]:
                if league_id not in active_ids:
                    continue
                league_name = league_map[league_id]
                table_title = f"{league_name} — {s_name}"
                table_id    = get_or_create_table(table_title, league_id, sp_season_id)

                cur.execute(
                    "SELECT meta_value FROM wp_termmeta WHERE term_id=%s AND meta_key='mlbb_rule'",
                    (league_id,),
                )
                rule_row = cur.fetchone()
                rule = rule_row[0] if rule_row else None

                upsert_registration_period(
                    cur, table_id, sp_season_id, rule,
                    opens_at, closes_at, play_start, play_end,
                )

    conn.commit()
    cur.close()
    conn.close()
    print("\n✓ Season initialization complete.")


if __name__ == "__main__":
    main()
