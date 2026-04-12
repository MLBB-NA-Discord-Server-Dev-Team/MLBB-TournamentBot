#!/usr/bin/env python3
"""
scripts/post_weekly_standings.py

Weekly standings post. Runs every Monday via /etc/cron.d/mlbb-standings-weekly.
For each active season × league pair, posts an embed to #standings showing
team W/L records and weeks remaining in the season.

Idempotent-ish: running twice just posts twice. Standalone — doesn't require
the bot to be up (uses Discord REST API directly with the bot token).
"""
import os
import sys
from datetime import date
from pathlib import Path

import mysql.connector
import phpserialize
import requests
from dotenv import load_dotenv

BOT_DIR = Path("/root/MLBB-TournamentBot")
load_dotenv(BOT_DIR / ".env")

DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "wpdbuser"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "playmlbb_db"),
)
BOT_TOKEN = os.getenv("DISCORD_TOKEN", "")
STANDINGS_CHANNEL_ID = os.getenv("STANDINGS_CHANNEL_ID", "").strip()
BOT_LEAGUES_CHANNEL_ID = os.getenv("BOT_LEAGUES_CHANNEL_ID", "").strip()

DISCORD_API = "https://discord.com/api/v10"


# ── DB queries ────────────────────────────────────────────────────────────

def get_active_seasons(cur):
    today = date.today()
    cur.execute(
        """
        SELECT sp_season_id, season_name, play_start, play_end
        FROM mlbb_season_schedule
        WHERE play_start <= %s AND play_end >= %s
        ORDER BY play_start
        """,
        (today, today),
    )
    return cur.fetchall()


def get_leagues_for_season(cur, sp_season_id):
    """
    All sp_tables relevant to this season, found via any of:
      1. Taxonomy: sp_table tagged with the sp_season term
      2. Registration periods: mlbb_registration_periods.sp_season_id matches
      3. Event backfill: table's sp_league term has events tagged with this season
    Returns deduped list of (table_id, title) tuples ordered by title.
    """
    tables = {}

    # Path 1: direct sp_season taxonomy on the table
    cur.execute(
        """
        SELECT p.ID, p.post_title
        FROM wp_posts p
        JOIN wp_term_relationships wtr ON wtr.object_id = p.ID
        JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
        WHERE p.post_type = 'sp_table' AND p.post_status = 'publish'
          AND wtt.taxonomy = 'sp_season' AND wtt.term_id = %s
        """,
        (sp_season_id,),
    )
    for row in cur.fetchall():
        tables[row[0]] = row[1]

    # Path 2: registration period linkage
    cur.execute(
        """
        SELECT p.ID, p.post_title
        FROM mlbb_registration_periods rp
        JOIN wp_posts p ON p.ID = rp.entity_id
        WHERE rp.entity_type = 'league' AND rp.sp_season_id = %s
          AND p.post_status = 'publish'
        """,
        (sp_season_id,),
    )
    for row in cur.fetchall():
        tables[row[0]] = row[1]

    # Path 3: any sp_table whose sp_league has events in this season
    cur.execute(
        """
        SELECT DISTINCT p.ID, p.post_title
        FROM wp_posts p
        JOIN wp_term_relationships wtr ON wtr.object_id = p.ID
        JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
            AND wtt.taxonomy = 'sp_league'
        WHERE p.post_type = 'sp_table' AND p.post_status = 'publish'
          AND wtt.term_id IN (
              SELECT wtt2.term_id
              FROM wp_posts ev
              JOIN wp_term_relationships tr1 ON tr1.object_id = ev.ID
              JOIN wp_term_taxonomy tt1 ON tt1.term_taxonomy_id = tr1.term_taxonomy_id
                  AND tt1.taxonomy = 'sp_season' AND tt1.term_id = %s
              JOIN wp_term_relationships tr2 ON tr2.object_id = ev.ID
              JOIN wp_term_taxonomy wtt2 ON wtt2.term_taxonomy_id = tr2.term_taxonomy_id
                  AND wtt2.taxonomy = 'sp_league'
              WHERE ev.post_type = 'sp_event'
          )
        """,
        (sp_season_id,),
    )
    for row in cur.fetchall():
        tables[row[0]] = row[1]

    return sorted(tables.items(), key=lambda t: t[1])


def get_league_events(cur, table_id, sp_season_id):
    """Return (published_ids, future_ids) for events in this league+season."""
    cur.execute(
        """
        SELECT wtt.term_id FROM wp_term_relationships wtr
        JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
        WHERE wtr.object_id = %s AND wtt.taxonomy = 'sp_league' LIMIT 1
        """,
        (table_id,),
    )
    row = cur.fetchone()
    if not row:
        return [], []
    league_term_id = row[0]

    cur.execute(
        """
        SELECT p.ID, p.post_status
        FROM wp_posts p
        JOIN wp_term_relationships tr1 ON tr1.object_id = p.ID
        JOIN wp_term_taxonomy tt1 ON tt1.term_taxonomy_id = tr1.term_taxonomy_id
            AND tt1.taxonomy = 'sp_league' AND tt1.term_id = %s
        JOIN wp_term_relationships tr2 ON tr2.object_id = p.ID
        JOIN wp_term_taxonomy tt2 ON tt2.term_taxonomy_id = tr2.term_taxonomy_id
            AND tt2.taxonomy = 'sp_season' AND tt2.term_id = %s
        WHERE p.post_type = 'sp_event'
        """,
        (league_term_id, sp_season_id),
    )
    rows = cur.fetchall()
    published = [r[0] for r in rows if r[1] == "publish"]
    future = [r[0] for r in rows if r[1] == "future"]
    return published, future


def compute_standings(cur, published_events):
    """Read sp_results from each event and tally W/L/D by team."""
    stats = {}  # team_id -> {wins, losses, draws}
    for eid in published_events:
        cur.execute(
            "SELECT meta_value FROM wp_postmeta WHERE post_id = %s AND meta_key = 'sp_results'",
            (eid,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            continue
        try:
            results = phpserialize.loads(row[0].encode(), decode_strings=True)
        except Exception:
            continue
        for tid_key, data in results.items():
            try:
                tid = int(tid_key)
            except (ValueError, TypeError):
                continue
            if tid not in stats:
                stats[tid] = {"wins": 0, "losses": 0, "draws": 0}
            outcomes = data.get("outcome", [])
            if not isinstance(outcomes, list):
                outcomes = [outcomes]
            for o in outcomes:
                if o == "win":
                    stats[tid]["wins"] += 1
                elif o == "loss":
                    stats[tid]["losses"] += 1
                elif o == "draw":
                    stats[tid]["draws"] += 1
    return stats


def get_table_teams(cur, table_id):
    """All team IDs registered on the table (sp_team postmeta)."""
    cur.execute(
        "SELECT meta_value FROM wp_postmeta WHERE post_id = %s AND meta_key = 'sp_team'",
        (table_id,),
    )
    return [int(r[0]) for r in cur.fetchall() if str(r[0]).isdigit()]


_team_name_cache = {}


def get_all_registered_teams(cur, table_id, sp_season_id):
    """
    Return all team IDs registered for this table+season via any path:
      1. sp_team postmeta on the table (manual/generated)
      2. mlbb_team_registrations approved for the period
      3. Teams appearing in any sp_results of published events for this league+season
    """
    team_ids = set(get_table_teams(cur, table_id))

    # From approved registrations
    cur.execute(
        """
        SELECT tr.sp_team_id
        FROM mlbb_team_registrations tr
        JOIN mlbb_registration_periods rp ON rp.id = tr.period_id
        WHERE rp.entity_id = %s AND rp.sp_season_id = %s
          AND tr.status = 'approved'
        """,
        (table_id, sp_season_id),
    )
    for row in cur.fetchall():
        team_ids.add(row[0])

    return sorted(team_ids)



def team_name(cur, tid):
    if tid in _team_name_cache:
        return _team_name_cache[tid]
    cur.execute("SELECT post_title FROM wp_posts WHERE ID = %s", (tid,))
    row = cur.fetchone()
    name = row[0] if row else f"Team {tid}"
    _team_name_cache[tid] = name
    return name


# ── Embed builder ─────────────────────────────────────────────────────────

def build_embed(cur, league_title, season_name, stats, weeks_left, played, total):
    # Sort by points, then win differential, then name
    rows = []
    for tid, s in stats.items():
        played_count = s["wins"] + s["losses"] + s["draws"]
        pts = s["wins"] * 3 + s["draws"]
        rows.append({
            "tid": tid,
            "name": team_name(cur, tid),
            "wins": s["wins"],
            "losses": s["losses"],
            "draws": s["draws"],
            "played": played_count,
            "pts": pts,
        })
    rows.sort(key=lambda x: (-x["pts"], -(x["wins"] - x["losses"]), x["name"]))

    lines = []
    for i, r in enumerate(rows, 1):
        medal = {1: "\U0001f947", 2: "\U0001f948", 3: "\U0001f949"}.get(i, f"`{i:2d}.`")
        record = f"{r['wins']}W {r['losses']}L"
        if r["draws"]:
            record += f" {r['draws']}D"
        lines.append(f"{medal} **{r['name']}** — {r['pts']} pts ({record})")

    description = "\n".join(lines) if lines else "*No teams registered yet.*"

    return {
        "title": f"\U0001f3c6 {league_title}",
        "description": description,
        "color": 0xFFB703,
        "fields": [
            {"name": "Season", "value": season_name, "inline": True},
            {"name": "Matches", "value": f"{played}/{total}", "inline": True},
            {"name": "Weeks left", "value": str(weeks_left), "inline": True},
        ],
    }


# ── Discord REST ──────────────────────────────────────────────────────────

def post_message(channel_id, content=None, embeds=None):
    if not channel_id:
        return  # channel not configured — silently skip
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "MLBB-TournamentBot-Standings/1.0",
    }
    data = {}
    if content:
        data["content"] = content
    if embeds:
        data["embeds"] = embeds
    r = requests.post(url, headers=headers, json=data, timeout=15)
    if r.status_code >= 400:
        print(f"Discord API error {r.status_code}: {r.text}", file=sys.stderr)
    r.raise_for_status()
    return r


def is_bot_league(title: str) -> bool:
    """Detect test/bot leagues by their title prefix."""
    return title.strip().lower().startswith("bot-") or title.strip().lower().startswith("bot ")



# ── Main ──────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERROR: DISCORD_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    if not STANDINGS_CHANNEL_ID and not BOT_LEAGUES_CHANNEL_ID:
        print("ERROR: neither STANDINGS_CHANNEL_ID nor BOT_LEAGUES_CHANNEL_ID set", file=sys.stderr)
        sys.exit(1)

    conn = mysql.connector.connect(**DB)
    cur = conn.cursor()

    seasons = get_active_seasons(cur)
    if not seasons:
        print("No active seasons today — nothing to post")
        return

    today = date.today()
    posted_seasons = 0
    posted_leagues = 0

    for season in seasons:
        sp_season_id, season_name, play_start, play_end = season
        days_left = max(0, (play_end - today).days)
        weeks_left = (days_left + 6) // 7  # round up

        leagues = get_leagues_for_season(cur, sp_season_id)
        if not leagues:
            continue

        # Collect embeds per destination channel
        standings_embeds = []
        bot_league_embeds = []

        for league in leagues:
            table_id, league_title = league
            published, future = get_league_events(cur, table_id, sp_season_id)
            total = len(published) + len(future)

            stats = compute_standings(cur, published)

            # Include teams that haven't played yet, from multiple sources
            for tid in get_all_registered_teams(cur, table_id, sp_season_id):
                if tid not in stats:
                    stats[tid] = {"wins": 0, "losses": 0, "draws": 0}

            if not stats:
                continue  # no teams and no results — skip

            embed = build_embed(cur, league_title, season_name, stats,
                                weeks_left, len(published), total)

            if is_bot_league(league_title):
                bot_league_embeds.append(embed)
            else:
                standings_embeds.append(embed)
            posted_leagues += 1

        if not standings_embeds and not bot_league_embeds:
            continue

        header = (
            f"\U0001f4c5 **{season_name} Standings — Week of {today.strftime('%b %d, %Y')}**\n"
            f"{weeks_left} week{'s' if weeks_left != 1 else ''} remaining \u2022 "
            f"Play window: {play_start.strftime('%b %d')} \u2013 {play_end.strftime('%b %d, %Y')}"
        )

        # Route to #standings
        if standings_embeds and STANDINGS_CHANNEL_ID:
            post_message(STANDINGS_CHANNEL_ID, content=header)
            for i in range(0, len(standings_embeds), 10):
                post_message(STANDINGS_CHANNEL_ID, embeds=standings_embeds[i:i + 10])

        # Route to #bot-leagues
        if bot_league_embeds and BOT_LEAGUES_CHANNEL_ID:
            bot_header = (
                f"\U0001f916 **{season_name} Bot League Standings — Week of {today.strftime('%b %d, %Y')}**\n"
                f"{weeks_left} week{'s' if weeks_left != 1 else ''} remaining"
            )
            post_message(BOT_LEAGUES_CHANNEL_ID, content=bot_header)
            for i in range(0, len(bot_league_embeds), 10):
                post_message(BOT_LEAGUES_CHANNEL_ID, embeds=bot_league_embeds[i:i + 10])

        posted_seasons += 1

    cur.close()
    conn.close()
    print(f"Posted standings: {posted_leagues} league(s) across {posted_seasons} season(s)")


if __name__ == "__main__":
    main()
