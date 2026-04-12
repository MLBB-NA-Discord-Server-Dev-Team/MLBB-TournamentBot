"""
services/league_lifecycle.py -- Autonomous league lifecycle management.

Pure async functions (no Discord imports) that manage the full seasonal
league lifecycle:
  - Auto-approve team registrations (roster-size validated)
  - Sync confirmed match results to SportsPress
  - Generate/update WordPress league pages with SP shortcodes
  - Manage season transitions (finalize, advance)

All functions return command_services.Result for consistency.
Called by the scheduler (services/scheduler.py) on timed intervals.
"""
import logging
import subprocess
import tempfile
from datetime import date
from typing import List, Optional

from services import db
from services.db_helpers import (
    get_roster,
    get_current_season,
    set_event_results,
    get_league_term_for_table,
    MIN_ROSTER_SIZE,
    MAX_ROSTER_SIZE,
)
from services.command_services import Result

logger = logging.getLogger(__name__)

WP_PATH = "/var/www/sites/play.mlbb.site"
WP_CLI_BASE = ["wp", f"--path={WP_PATH}", "--skip-plugins", "--skip-themes", "--allow-root"]


# -- Auto-approval ------------------------------------------------------------

async def auto_approve_registration(reg_id: int) -> Result:
    """
    Auto-approve a single pending registration if the team has a valid roster.
    Checks: roster size 5-6, registration still pending.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT tr.sp_team_id, tr.period_id, p.post_title
                FROM mlbb_team_registrations tr
                JOIN wp_posts p ON p.ID = tr.sp_team_id
                WHERE tr.id = %s AND tr.status = 'pending'
                """,
                (reg_id,),
            )
            row = await cur.fetchone()

    if not row:
        return Result(ok=False, error=f"Registration #{reg_id} not found or not pending")

    sp_team_id, period_id, team_name = row

    roster = await get_roster(sp_team_id)
    roster_count = len(roster)

    if roster_count < MIN_ROSTER_SIZE:
        return Result(ok=False, error=f"{team_name}: roster too small ({roster_count}/{MIN_ROSTER_SIZE})")
    if roster_count > MAX_ROSTER_SIZE:
        return Result(ok=False, error=f"{team_name}: roster too large ({roster_count}/{MAX_ROSTER_SIZE})")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_team_registrations
                SET status = 'approved', reviewed_by = 'auto', reviewed_at = NOW()
                WHERE id = %s AND status = 'pending'
                """,
                (reg_id,),
            )
            affected = cur.rowcount

    if not affected:
        return Result(ok=False, error=f"Registration #{reg_id} was modified concurrently")

    logger.info("Auto-approved registration #%d for %s (%d players)", reg_id, team_name, roster_count)
    return Result(ok=True, data={
        "registration_id": reg_id,
        "sp_team_id": sp_team_id,
        "team_name": team_name,
        "roster_count": roster_count,
    })


async def check_pending_approvals() -> List[Result]:
    """
    Batch-process all pending registrations for open/closed periods.
    Returns list of Results (one per registration attempted).
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT tr.id
                FROM mlbb_team_registrations tr
                JOIN mlbb_registration_periods rp ON rp.id = tr.period_id
                WHERE tr.status = 'pending'
                  AND rp.status IN ('open', 'closed')
                ORDER BY tr.registered_at
                """
            )
            rows = await cur.fetchall()

    results = []
    for (reg_id,) in rows:
        try:
            r = await auto_approve_registration(reg_id)
            results.append(r)
        except Exception as e:
            logger.error("Auto-approve error for reg #%d: %s", reg_id, e)
            results.append(Result(ok=False, error=f"Registration #{reg_id}: {e}"))
    return results


# -- Match result sync ---------------------------------------------------------

async def sync_confirmed_results() -> Result:
    """
    Find confirmed match submissions not yet synced to SportsPress
    and write their results via set_event_results().
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ms.id, ms.sp_event_id, ms.winning_team_id,
                       ms.winner_kills, ms.loser_kills
                FROM mlbb_match_submissions ms
                JOIN wp_posts ev ON ev.ID = ms.sp_event_id
                WHERE ms.status = 'confirmed'
                  AND ms.sp_event_id IS NOT NULL
                  AND ev.post_status = 'future'
                ORDER BY ms.confirmed_at
                """
            )
            rows = await cur.fetchall()

    if not rows:
        return Result(ok=True, data={"synced": 0})

    synced = 0
    errors = 0
    for sub_id, sp_event_id, winning_team_id, w_kills, l_kills in rows:
        try:
            # Determine home/away from event sp_team meta
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT meta_value FROM wp_postmeta
                        WHERE post_id = %s AND meta_key = 'sp_team'
                        ORDER BY meta_id
                        """,
                        (sp_event_id,),
                    )
                    team_rows = await cur.fetchall()

            if len(team_rows) < 2:
                logger.warning("Event %d has < 2 teams, skipping", sp_event_id)
                errors += 1
                continue

            home_team_id = int(team_rows[0][0])
            away_team_id = int(team_rows[1][0])

            if winning_team_id == home_team_id:
                home_score, away_score = w_kills or 1, l_kills or 0
            else:
                home_score, away_score = l_kills or 0, w_kills or 1

            await set_event_results(sp_event_id, home_team_id, away_team_id, home_score, away_score)
            synced += 1
            logger.info("Synced result for event %d (sub #%d): %d-%d",
                        sp_event_id, sub_id, home_score, away_score)
        except Exception as e:
            logger.error("Failed to sync sub #%d (event %d): %s", sub_id, sp_event_id, e)
            errors += 1

    return Result(ok=True, data={"synced": synced, "errors": errors, "total": len(rows)})


# -- WordPress page generation -------------------------------------------------

def _wp_cli(*args: str) -> Optional[str]:
    """Run a WP-CLI command and return stdout, or None on failure."""
    cmd = WP_CLI_BASE + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            logger.warning("WP-CLI failed: %s stderr=%s", " ".join(cmd[:6]), r.stderr.strip()[:200])
            return None
        return r.stdout.strip()
    except Exception as e:
        logger.error("WP-CLI error: %s", e)
        return None


def _wp_eval(php_code: str) -> Optional[str]:
    """Run a PHP expression via wp eval and return the output."""
    return _wp_cli("eval", php_code)


async def generate_league_wp_page(
    table_post_id: int,
    league_term_id: int,
    season_name: str,
) -> Result:
    """
    Create or update a WordPress page for a league+season with SportsPress shortcodes.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT post_title FROM wp_posts WHERE ID = %s", (table_post_id,)
            )
            row = await cur.fetchone()
    if not row:
        return Result(ok=False, error=f"Table post {table_post_id} not found")

    table_title = row[0]

    content = (
        '<h2>Standings</h2>\n'
        f'[league_table id="{table_post_id}" /]\n\n'
        '<h2>Schedule</h2>\n'
        f'[event_list league="{league_term_id}" '
        'title="" show_title="no" columns="event,time,results" /]\n\n'
        '<h2>Teams</h2>\n'
        f'[team_gallery league="{league_term_id}" '
        'columns="logo,team,w,l,pct" /]\n'
    )

    # Write content to temp file (avoids shell escaping issues)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, dir='/tmp') as f:
        f.write(content)
        tmp = f.name

    # Check if page already exists by mlbb_table_id meta
    existing_id = _wp_eval(
        "$pages = get_posts(['post_type'=>'page','meta_key'=>'mlbb_table_id',"
        f"'meta_value'=>'{table_post_id}','posts_per_page'=>1]);"
        "echo $pages ? $pages[0]->ID : '';"
    )

    if existing_id:
        _wp_eval(
            f"wp_update_post(['ID'=>{existing_id},"
            f"'post_content'=>file_get_contents('{tmp}')]);"
        )
        subprocess.run(["rm", "-f", tmp], capture_output=True)
        logger.info("Updated WP page %s for table %d", existing_id, table_post_id)
        return Result(ok=True, data={"page_id": int(existing_id), "action": "updated"})
    else:
        slug = table_title.lower().replace("\u2014", "").replace("  ", " ").replace(" ", "-")
        page_id = _wp_eval(
            "$id = wp_insert_post(['post_type'=>'page',"
            f"'post_title'=>'{table_title}',"
            f"'post_name'=>'{slug}',"
            "'post_status'=>'publish',"
            f"'post_content'=>file_get_contents('{tmp}')]);"
            f"update_post_meta($id, 'mlbb_table_id', '{table_post_id}');"
            "echo $id;"
        )
        subprocess.run(["rm", "-f", tmp], capture_output=True)

        if not page_id or page_id == '0':
            return Result(ok=False, error=f"Failed to create WP page for {table_title}")

        # Set parent to league format hub page
        league_slug = _wp_eval(
            f"$t = get_term({league_term_id}, 'sp_league'); echo $t ? $t->slug : '';"
        )
        if league_slug:
            parent = _wp_eval(
                f"$p = get_page_by_path('{league_slug}'); echo $p ? $p->ID : '';"
            )
            if parent:
                _wp_cli("post", "update", page_id, f"--post_parent={parent}")

        logger.info("Created WP page %s for table %d (%s)", page_id, table_post_id, table_title)
        return Result(ok=True, data={"page_id": int(page_id), "action": "created"})


async def update_league_hub_page() -> Result:
    """Refresh the /custom-leagues/ hub page with links to all active league pages."""
    season = await get_current_season()
    if not season:
        return Result(ok=False, error="No current season found")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT rp.entity_id, p.post_title, rp.rule, rp.status
                FROM mlbb_registration_periods rp
                JOIN wp_posts p ON p.ID = rp.entity_id
                WHERE rp.sp_season_id = %s
                  AND rp.entity_type = 'league'
                ORDER BY p.post_title
                """,
                (season["sp_season_id"],),
            )
            rows = await cur.fetchall()

    if not rows:
        return Result(ok=True, data={"updated": False, "reason": "No leagues for current season"})

    lines = []
    for entity_id, title, rule, status in rows:
        page_url = _wp_eval(
            "$pages = get_posts(['post_type'=>'page','meta_key'=>'mlbb_table_id',"
            f"'meta_value'=>'{entity_id}','posts_per_page'=>1]);"
            "echo $pages ? get_permalink($pages[0]->ID) : '';"
        )
        link = page_url if page_url else f"/?p={entity_id}"
        badge = "open" if status == "open" else ("closed" if status == "closed" else "scheduled")
        lines.append(f'<li><a href="{link}">{title}</a> <small>({rule} | {badge})</small></li>')

    content = (
        f'<h2>{season["season_name"]} Leagues</h2>\n'
        '<ul class="wp-block-list">\n'
        + "\n".join(lines)
        + '\n</ul>\n'
        '<p><em>Registration: open / closed / scheduled</em></p>'
    )

    hub_id = _wp_eval("$p = get_page_by_path('custom-leagues'); echo $p ? $p->ID : '';")
    if hub_id:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False, dir='/tmp') as f:
            f.write(content)
            tmp = f.name
        _wp_eval(
            f"wp_update_post(['ID'=>{hub_id},"
            f"'post_content'=>file_get_contents('{tmp}')]);"
        )
        subprocess.run(["rm", "-f", tmp], capture_output=True)
        logger.info("Updated league hub page (%s)", hub_id)
        return Result(ok=True, data={"hub_page_id": int(hub_id), "leagues": len(rows)})

    return Result(ok=False, error="Hub page /custom-leagues/ not found")


# -- Season lifecycle ----------------------------------------------------------

async def get_season_status() -> Result:
    """Return the current season status and any needed transitions."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, sp_season_id, season_name, play_start, play_end, reg_opens, reg_closes
                FROM mlbb_season_schedule
                ORDER BY play_start
                """
            )
            rows = await cur.fetchall()

    if not rows:
        return Result(ok=False, error="No seasons defined")

    today = date.today()
    current = None
    next_season = None
    for r in rows:
        sid, sp_id, name, pstart, pend, ropen, rclose = r
        if pstart <= today <= pend:
            current = {"id": sid, "sp_season_id": sp_id, "season_name": name,
                        "play_start": pstart.isoformat(), "play_end": pend.isoformat()}
        elif pstart > today and next_season is None:
            next_season = {"id": sid, "sp_season_id": sp_id, "season_name": name,
                           "play_start": pstart.isoformat(), "play_end": pend.isoformat(),
                           "reg_opens": ropen.isoformat()}

    return Result(ok=True, data={
        "current": current,
        "next": next_season,
        "today": today.isoformat(),
    })


async def ensure_next_season() -> Result:
    """
    Verify the season buffer is maintained: require at least MIN_SEASONS_AHEAD
    future seasons to exist. If fewer, run season_init.py to extend the buffer.
    """
    MIN_SEASONS_AHEAD = 3  # always keep at least 3 future seasons configured

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT COUNT(*) FROM mlbb_season_schedule
                WHERE play_start > CURDATE()
                """
            )
            future_count = (await cur.fetchone())[0]

    if future_count >= MIN_SEASONS_AHEAD:
        return Result(ok=True, data={
            "action": "buffer_ok",
            "future_seasons": future_count,
        })

    logger.info(
        "Season buffer low (%d future seasons, need %d) — running season_init.py",
        future_count, MIN_SEASONS_AHEAD,
    )

    logger.info("Next season %s has no registration periods, running season_init.py",
                next_season["season_name"])
    try:
        r = subprocess.run(
            ["/root/MLBB-TournamentBot/venv/bin/python",
             "/root/MLBB-TournamentBot/scripts/season_init.py"],
            capture_output=True, text=True, timeout=120,
            cwd="/root/MLBB-TournamentBot",
        )
        if r.returncode != 0:
            return Result(ok=False, error=f"season_init.py failed: {r.stderr[:500]}")
    except Exception as e:
        return Result(ok=False, error=f"season_init.py error: {e}")

    return Result(ok=True, data={"action": "created", "next_season": next_season["season_name"]})


async def finalize_season(sp_season_id: int) -> Result:
    """Mark a season's registration periods as closed. Called when play_end has passed."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_registration_periods
                SET status = 'closed'
                WHERE sp_season_id = %s AND status IN ('scheduled', 'open')
                """,
                (sp_season_id,),
            )
            closed = cur.rowcount

            await cur.execute(
                "SELECT season_name FROM mlbb_season_schedule WHERE sp_season_id = %s",
                (sp_season_id,),
            )
            row = await cur.fetchone()
            season_name = row[0] if row else f"Season {sp_season_id}"

    logger.info("Finalized season %s: closed %d registration period(s)", season_name, closed)
    return Result(ok=True, data={
        "sp_season_id": sp_season_id,
        "season_name": season_name,
        "periods_closed": closed,
    })
