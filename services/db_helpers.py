"""
services/db_helpers.py — common DB reads used across cogs

All functions use the aiomysql pool from services/db.py.
Reads go direct to MySQL; writes go through WP REST API.
"""
import logging
from typing import Optional

import phpserialize

from services import db

logger = logging.getLogger(__name__)


# ── Player ─────────────────────────────────────────────────────────────────

async def get_player_by_discord_id(discord_id: str) -> Optional[dict]:
    """Return sp_player row {id, post_title} for a Discord ID, or None."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID, p.post_title
                FROM wp_posts p
                JOIN wp_postmeta pm ON pm.post_id = p.ID
                WHERE p.post_type = 'sp_player'
                  AND p.post_status = 'publish'
                  AND pm.meta_key = 'sp_metrics'
                """,
            )
            rows = await cur.fetchall()

    for row in rows:
        post_id, title = row
        # wp_postmeta stores sp_metrics as a PHP-serialised string
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT meta_value FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_metrics'",
                    (post_id,),
                )
                meta_row = await cur.fetchone()
        if not meta_row:
            continue
        try:
            metrics = phpserialize.loads(meta_row[0].encode(), decode_strings=True)
        except Exception:
            continue
        if str(metrics.get("discordid", "")) == str(discord_id):
            return {"id": post_id, "title": title}
    return None


# ── Team ───────────────────────────────────────────────────────────────────

async def get_team_by_id(sp_team_id: int) -> Optional[dict]:
    """Return {id, title, slug} for an sp_team post."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ID, post_title, post_name FROM wp_posts WHERE ID=%s AND post_type='sp_team' AND post_status='publish'",
                (sp_team_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1], "slug": row[2]}


async def get_team_url(sp_team_id: int, base_url: str) -> str:
    """Return the canonical team permalink using post_name slug, e.g. https://play.mlbb.site/team/chicken/"""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT post_name FROM wp_posts WHERE ID=%s AND post_type='sp_team' AND post_status='publish'",
                (sp_team_id,),
            )
            row = await cur.fetchone()
    if row and row[0]:
        return f"{base_url.rstrip('/')}/team/{row[0]}/"
    return f"{base_url.rstrip('/')}/?p={sp_team_id}"


async def get_roster(sp_team_id: int) -> list[dict]:
    """Return all active roster rows for a team."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.id, r.discord_id, r.sp_player_id, r.role, r.joined_at,
                       p.post_title
                FROM mlbb_player_roster r
                JOIN wp_posts p ON p.ID = r.sp_player_id
                WHERE r.sp_team_id = %s AND r.status = 'active'
                ORDER BY FIELD(r.role,'captain','player','substitute'), r.joined_at
                """,
                (sp_team_id,),
            )
            rows = await cur.fetchall()
    return [
        {
            "id": r[0],
            "discord_id": r[1],
            "sp_player_id": r[2],
            "role": r[3],
            "joined_at": r[4],
            "ign": r[5],
        }
        for r in rows
    ]


async def get_captain_team(discord_id: str, sp_team_id: int = None) -> Optional[dict]:
    """
    Return the captain entry for this Discord user.
    If sp_team_id is given, checks that specific team.
    If omitted, returns the first captain entry (use only when player has exactly one team).
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            if sp_team_id is not None:
                await cur.execute(
                    """
                    SELECT r.sp_team_id, r.sp_player_id, p.post_title
                    FROM mlbb_player_roster r
                    JOIN wp_posts p ON p.ID = r.sp_team_id
                    WHERE r.discord_id = %s AND r.sp_team_id = %s
                      AND r.role = 'captain' AND r.status = 'active'
                    LIMIT 1
                    """,
                    (discord_id, sp_team_id),
                )
            else:
                await cur.execute(
                    """
                    SELECT r.sp_team_id, r.sp_player_id, p.post_title
                    FROM mlbb_player_roster r
                    JOIN wp_posts p ON p.ID = r.sp_team_id
                    WHERE r.discord_id = %s
                      AND r.role = 'captain' AND r.status = 'active'
                    LIMIT 1
                    """,
                    (discord_id,),
                )
            row = await cur.fetchone()
    if not row:
        return None
    return {"sp_team_id": row[0], "sp_player_id": row[1], "team_name": row[2]}


async def get_captain_teams(discord_id: str) -> list[dict]:
    """Return all teams where this Discord user is an active captain."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.sp_team_id, r.sp_player_id, p.post_title
                FROM mlbb_player_roster r
                JOIN wp_posts p ON p.ID = r.sp_team_id
                WHERE r.discord_id = %s AND r.role = 'captain' AND r.status = 'active'
                ORDER BY r.joined_at
                """,
                (discord_id,),
            )
            rows = await cur.fetchall()
    return [{"sp_team_id": r[0], "sp_player_id": r[1], "team_name": r[2]} for r in rows]


async def get_player_active_team_ids(discord_id: str) -> list[int]:
    """Return all sp_team_ids where this player has an active roster entry."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT sp_team_id FROM mlbb_player_roster WHERE discord_id=%s AND status='active'",
                (discord_id,),
            )
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_player_roster_entry(discord_id: str) -> Optional[dict]:
    """Return the active roster entry for this Discord user (any team/role)."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.id, r.sp_player_id, r.sp_team_id, r.role,
                       tp.post_title as team_name
                FROM mlbb_player_roster r
                JOIN wp_posts tp ON tp.ID = r.sp_team_id
                WHERE r.discord_id = %s AND r.status = 'active'
                LIMIT 1
                """,
                (discord_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "roster_id": row[0],
        "sp_player_id": row[1],
        "sp_team_id": row[2],
        "role": row[3],
        "team_name": row[4],
    }


# ── Pending invites ────────────────────────────────────────────────────────

async def get_pending_invite(invitee_discord_id: str, sp_team_id: int) -> Optional[dict]:
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, sp_team_id, inviter_id, role
                FROM mlbb_team_invites
                WHERE invitee_id = %s
                  AND sp_team_id = %s
                  AND status = 'pending'
                  AND expires_at > NOW()
                LIMIT 1
                """,
                (invitee_discord_id, sp_team_id),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "sp_team_id": row[1], "inviter_id": row[2], "role": row[3]}


async def get_any_pending_invite(invitee_discord_id: str) -> Optional[dict]:
    """Return the first pending invite for this Discord user across all teams."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT i.id, i.sp_team_id, i.inviter_id, i.role, p.post_title
                FROM mlbb_team_invites i
                JOIN wp_posts p ON p.ID = i.sp_team_id
                WHERE i.invitee_id = %s
                  AND i.status = 'pending'
                  AND i.expires_at > NOW()
                ORDER BY i.created_at DESC
                LIMIT 1
                """,
                (invitee_discord_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "sp_team_id": row[1],
        "inviter_id": row[2],
        "role": row[3],
        "team_name": row[4],
    }


# ── Team appearance (colors stored as ACF postmeta) ───────────────────────

# ACF field keys as registered in the Alchemists theme
_ACF_COLOR_PRIMARY   = "field_5d5d65131416b"
_ACF_COLOR_SECONDARY = "field_5d5d729675d1b"


async def setup_team_roster_display(sp_team_id: int, sp_list_id: int) -> None:
    """
    Wire a newly created sp_list to its team so the Alchemists roster tab renders.

    Two things must happen:
      1. Set sp_team postmeta on the sp_list post → SP_Player_List filters by this team.
      2. Set 4 ACF fields on the sp_team post → content-roster.php uses them to find
         and render the list.

    ACF field keys (from alchemists/inc/acf-fields.php):
      gallery_roster_show  field_58fca7bbfbeb5  (bool 1)
      gallery_roster       field_58fca8b1aadab  (int: list ID)
      list_roster_show     field_58fcafde0456f  (bool 1)
      list_roster          field_58fcb01004570  (serialised array of list IDs)
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            # 1. Point sp_list → team and configure columns.
            #    sp_select='manual' bypasses the auto query that filters by sp_number
            #    (bot-created players have no jersey number, so auto returns nothing).
            #    sp_columns controls which stats appear in the list tab.
            _sp_columns = (
                'a:5:{i:0;s:15:"discordusername";'
                'i:1;s:8:"avgkills";'
                'i:2;s:9:"avgdeaths";'
                'i:3;s:10:"avgassists";'
                'i:4;s:11:"avgkdaratio";}'
            )
            # wp_postmeta has no unique(post_id, meta_key) — must DELETE then INSERT
            for meta_key, meta_value in [
                ("sp_team",    str(sp_team_id)),
                ("sp_select",  "manual"),
                ("sp_columns", _sp_columns),
            ]:
                await cur.execute(
                    "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                    (sp_list_id, meta_key),
                )
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                    (sp_list_id, meta_key, meta_value),
                )

            # 2. ACF fields on the team post
            acf_pairs = [
                ("gallery_roster_show", "_gallery_roster_show", "1",               "field_58fca7bbfbeb5"),
                ("gallery_roster",      "_gallery_roster",      sp_list_id,         "field_58fca8b1aadab"),
                ("list_roster_show",    "_list_roster_show",    "1",                "field_58fcafde0456f"),
                # ACF stores multiple post_object as PHP serialised array
                ("list_roster",         "_list_roster",         f'a:1:{{i:0;i:{sp_list_id};}}', "field_58fcb01004570"),
            ]
            for value_key, ref_key, value, acf_key in acf_pairs:
                for post_id, key, val in [
                    (sp_team_id, value_key, value),
                    (sp_team_id, ref_key, acf_key),
                ]:
                    await cur.execute(
                        "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                        (post_id, key),
                    )
                    await cur.execute(
                        "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                        (post_id, key, val),
                    )


async def get_team_sp_list_id(sp_team_id: int) -> Optional[int]:
    """Return the sp_list post ID wired to this team (gallery_roster ACF field), or None."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT meta_value FROM wp_postmeta WHERE post_id=%s AND meta_key='gallery_roster' LIMIT 1",
                (sp_team_id,),
            )
            row = await cur.fetchone()
    if row and row[0]:
        try:
            return int(row[0])
        except (ValueError, TypeError):
            return None
    return None


async def sync_sp_list_roster(sp_list_id: int, sp_player_ids: list[int]) -> None:
    """
    Replace the explicit sp_player rows on an sp_list post with the current roster.
    SP_Player_List reads these when sp_select != 'auto'.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_player'",
                (sp_list_id,),
            )
            for player_id in sp_player_ids:
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, 'sp_player', %s)",
                    (sp_list_id, player_id),
                )


async def sync_team_roster_list(sp_team_id: int) -> None:
    """
    Convenience wrapper: look up the sp_list for this team and sync active roster players into it.
    Call this after any roster change (join, kick, leave).
    """
    sp_list_id = await get_team_sp_list_id(sp_team_id)
    if not sp_list_id:
        return
    roster = await get_roster(sp_team_id)
    player_ids = [r["sp_player_id"] for r in roster]
    await sync_sp_list_roster(sp_list_id, player_ids)


async def set_team_colors(
    sp_team_id: int,
    color_primary: Optional[str] = None,
    color_secondary: Optional[str] = None,
) -> None:
    """
    UPSERT team_color_primary / team_color_secondary postmeta.
    Also ensures the ACF field-reference keys (_team_color_*) exist so
    get_field() works on bot-created posts that lack them.
    """
    pairs = []
    if color_primary is not None:
        pairs.append(("team_color_primary",   "_team_color_primary",   color_primary,   _ACF_COLOR_PRIMARY))
    if color_secondary is not None:
        pairs.append(("team_color_secondary", "_team_color_secondary", color_secondary, _ACF_COLOR_SECONDARY))

    if not pairs:
        return

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            for value_key, ref_key, value, acf_field_key in pairs:
                for post_id, key, val in [
                    (sp_team_id, value_key, value),
                    (sp_team_id, ref_key, acf_field_key),
                ]:
                    await cur.execute(
                        "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                        (post_id, key),
                    )
                    await cur.execute(
                        "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                        (post_id, key, val),
                    )


# ── Player Discord linkage (direct MySQL — REST API drops meta silently) ──────

async def set_player_discord_meta(sp_player_id: int, discord_id: str, discord_username: str) -> None:
    """
    Write discordid + discordusername into sp_metrics postmeta via direct MySQL.

    The SportsPress REST API silently ignores the 'meta' payload, so the
    api.set_player_discord() call doesn't actually persist sp_metrics.
    This function writes it directly to wp_postmeta instead.
    """
    import phpserialize
    metrics = {
        "discordid": discord_id,
        "discordusername": discord_username,
        "discorddiscriminator": "0",
    }
    serialised = phpserialize.dumps(metrics).decode()

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_metrics'",
                (sp_player_id,),
            )
            await cur.execute(
                "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, 'sp_metrics', %s)",
                (sp_player_id, serialised),
            )


# ACF field keys for player photos (Alchemists theme)
_ACF_HEADING_PLAYER_PHOTO = "field_58fcc00499d46"
_ACF_PLAYER_IMAGE         = "field_58f3f4ac21e0c"


async def set_player_photos(sp_player_id: int, media_id: int) -> None:
    """
    Set all three photo fields that the Alchemists theme uses for a player:
      - _thumbnail_id        (core WP featured image)
      - heading_player_photo (page heading photo)
      - player_image         (widget / roster card image)
    Each ACF field also needs its underscore-prefixed reference key.
    """
    entries = [
        ("_thumbnail_id",           str(media_id), None),
        ("heading_player_photo",    str(media_id), _ACF_HEADING_PLAYER_PHOTO),
        ("player_image",            str(media_id), _ACF_PLAYER_IMAGE),
    ]

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            for value_key, value, acf_field_key in entries:
                await cur.execute(
                    "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                    (sp_player_id, value_key),
                )
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                    (sp_player_id, value_key, value),
                )
                if acf_field_key:
                    ref_key = f"_{value_key}"
                    await cur.execute(
                        "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                        (sp_player_id, ref_key),
                    )
                    await cur.execute(
                        "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                        (sp_player_id, ref_key, acf_field_key),
                    )


# ── Event results ─────────────────────────────────────────────────────────

async def set_event_results(
    sp_event_id: int,
    home_team_id: int,
    away_team_id: int,
    home_score: int,
    away_score: int,
) -> None:
    """
    Write SportsPress match results onto an sp_event post.

    Sets sp_results (serialized PHP), sp_main_result, and changes
    post_status from 'future' to 'publish' so SportsPress recalculates
    league table standings.
    """
    import phpserialize

    home_outcome = "win" if home_score > away_score else ("loss" if home_score < away_score else "draw")
    away_outcome = "win" if away_score > home_score else ("loss" if away_score < home_score else "draw")

    # Resolve the primary result column slug from sp_result posts.
    # Defaults to 'kills' — the standard MLBB result column.
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT post_name FROM wp_posts WHERE post_type='sp_result' AND post_status='publish' ORDER BY menu_order LIMIT 1"
            )
            row = await cur.fetchone()
    result_key = row[0] if row else "kills"

    results = {
        home_team_id: {
            b"outcome": [home_outcome.encode()],
            result_key.encode(): str(home_score).encode(),
        },
        away_team_id: {
            b"outcome": [away_outcome.encode()],
            result_key.encode(): str(away_score).encode(),
        },
    }
    serialised = phpserialize.dumps(results).decode()

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            for key, val in [("sp_results", serialised), ("sp_main_result", result_key)]:
                await cur.execute(
                    "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                    (sp_event_id, key),
                )
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                    (sp_event_id, key, val),
                )
            await cur.execute(
                "UPDATE wp_posts SET post_status='publish' WHERE ID=%s AND post_status='future'",
                (sp_event_id,),
            )


# ── Event scheduling ──────────────────────────────────────────────────────

async def check_team_has_event_on_date(team_id: int, date: str) -> Optional[dict]:
    """
    Check if a team already has an event scheduled on a given date (YYYY-MM-DD).
    Returns {id, title, opponent_id} if a conflict exists, None otherwise.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID, p.post_title
                FROM wp_posts p
                JOIN wp_postmeta pm ON pm.post_id = p.ID AND pm.meta_key = 'sp_team'
                WHERE p.post_type = 'sp_event'
                  AND p.post_status IN ('publish','future')
                  AND DATE(p.post_date) = %s
                  AND pm.meta_value = %s
                LIMIT 1
                """,
                (date, str(team_id)),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1]}


MIN_ROSTER_SIZE = 5   # 5 players minimum
MAX_ROSTER_SIZE = 6   # 5 players + 1 substitute maximum


async def get_approved_teams_for_period(period_id: int, eligible_only: bool = True) -> list[dict]:
    """
    Return approved team registrations for a registration period.
    If eligible_only=True, only include teams with 5-6 active roster members.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT tr.sp_team_id, p.post_title,
                       (SELECT COUNT(*) FROM mlbb_player_roster r
                        WHERE r.sp_team_id = tr.sp_team_id AND r.status = 'active') AS roster_count
                FROM mlbb_team_registrations tr
                JOIN wp_posts p ON p.ID = tr.sp_team_id
                WHERE tr.period_id = %s AND tr.status = 'approved'
                ORDER BY tr.registered_at
                """,
                (period_id,),
            )
            rows = await cur.fetchall()
    teams = [{"sp_team_id": r[0], "team_name": r[1], "roster_count": r[2]} for r in rows]
    if eligible_only:
        teams = [t for t in teams if MIN_ROSTER_SIZE <= t["roster_count"] <= MAX_ROSTER_SIZE]
    return teams


async def get_league_term_for_table(table_post_id: int) -> Optional[int]:
    """Return the sp_league term_id assigned to an sp_table post."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT wtt.term_id
                FROM wp_term_relationships wtr
                JOIN wp_term_taxonomy wtt
                    ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                    AND wtt.taxonomy = 'sp_league'
                WHERE wtr.object_id = %s
                LIMIT 1
                """,
                (table_post_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def get_season_for_period(period_id: int) -> Optional[dict]:
    """Return season info for a registration period (via sp_season_id or season_schedule)."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT rp.sp_season_id, ss.season_name, ss.play_start
                FROM mlbb_registration_periods rp
                LEFT JOIN mlbb_season_schedule ss ON ss.sp_season_id = rp.sp_season_id
                WHERE rp.id = %s
                """,
                (period_id,),
            )
            row = await cur.fetchone()
    if not row or not row[0]:
        return None
    return {"sp_season_id": row[0], "season_name": row[1], "play_start": row[2]}


async def get_play_end_for_season(sp_season_id: int) -> Optional:
    """Return play_end date for a season from mlbb_season_schedule."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT play_end FROM mlbb_season_schedule WHERE sp_season_id = %s",
                (sp_season_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


# ── SportsPress post listings (direct MySQL — no HTTP overhead) ────────────

async def list_posts(post_type: str, limit: int = 100) -> list[dict]:
    """Generic list of published wp_posts by post_type. Returns [{id, title, link}]."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ID, post_title, guid
                FROM wp_posts
                WHERE post_type = %s AND post_status = 'publish'
                ORDER BY post_date DESC
                LIMIT %s
                """,
                (post_type, limit),
            )
            rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "link": r[2]} for r in rows]


async def get_my_teams(discord_id: str) -> list[dict]:
    """Return all active roster entries for a Discord user with team name and role."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.sp_team_id, r.role, p.post_title, p.post_name
                FROM mlbb_player_roster r
                JOIN wp_posts p ON p.ID = r.sp_team_id
                WHERE r.discord_id = %s AND r.status = 'active'
                ORDER BY r.role, r.joined_at
                """,
                (discord_id,),
            )
            rows = await cur.fetchall()
    return [{"sp_team_id": r[0], "role": r[1], "team_name": r[2], "slug": r[3]} for r in rows]


async def list_leagues(search: str = None, limit: int = 25) -> tuple[list[dict], int]:
    """
    Return leagues (sp_table posts) that have registration periods configured.
    Deduplicates by entity_id, keeping the most recent period per league.
    Returns (rows, total_count).
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            where = "rp.entity_type = 'league' AND p.post_status = 'publish' AND rp.status = 'open'"
            params: list = []
            if search:
                where += " AND p.post_title LIKE %s"
                params.append(f"%{search}%")
            await cur.execute(
                f"""
                SELECT p.ID, p.post_title,
                       rp.status,
                       rp.opens_at,
                       rp.closes_at,
                       rp.rule
                FROM mlbb_registration_periods rp
                JOIN wp_posts p ON p.ID = rp.entity_id
                WHERE {where}
                GROUP BY p.ID
                ORDER BY rp.opens_at DESC, p.post_title
                LIMIT %s
                """,
                params + [limit],
            )
            rows = await cur.fetchall()
            await cur.execute(
                f"""
                SELECT COUNT(DISTINCT p.ID)
                FROM mlbb_registration_periods rp
                JOIN wp_posts p ON p.ID = rp.entity_id
                WHERE {where}
                """,
                params,
            )
            total = (await cur.fetchone())[0]
    return (
        [{"id": r[0], "title": r[1], "status": r[2], "opens_at": r[3], "closes_at": r[4], "rule": r[5]} for r in rows],
        total,
    )


async def list_teams(limit: int = 100) -> list[dict]:
    return await list_posts("sp_team", limit)


async def list_players(limit: int = 100) -> list[dict]:
    return await list_posts("sp_player", limit)


async def list_tables(limit: int = 100) -> list[dict]:
    return await list_posts("sp_table", limit)


async def list_tournaments(limit: int = 100) -> list[dict]:
    return await list_posts("sp_tournament", limit)


async def list_events(limit: int = 100) -> list[dict]:
    """List published sp_event posts, newest first."""
    return await list_posts("sp_event", limit)


async def set_league_termmeta(term_id: int, meta_key: str, meta_value: str) -> None:
    """Insert or update a single wp_termmeta row for an sp_league term."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM wp_termmeta WHERE term_id=%s AND meta_key=%s",
                (term_id, meta_key),
            )
            await cur.execute(
                "INSERT INTO wp_termmeta (term_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                (term_id, meta_key, meta_value),
            )


async def get_rule_for_table(table_post_id: int) -> str | None:
    """Return mlbb_rule termmeta for the sp_league term assigned to an sp_table post."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT tm.meta_value
                FROM wp_term_relationships wtr
                JOIN wp_term_taxonomy wtt
                    ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                    AND wtt.taxonomy = 'sp_league'
                JOIN wp_termmeta tm
                    ON tm.term_id = wtt.term_id AND tm.meta_key = 'mlbb_rule'
                WHERE wtr.object_id = %s
                LIMIT 1
                """,
                (table_post_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def get_existing_table_for_league(league_term_id: int) -> int | None:
    """Return the sp_table post ID already assigned to this sp_league term, or None."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID FROM wp_posts p
                JOIN wp_term_relationships wtr ON wtr.object_id = p.ID
                JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                WHERE wtt.term_id = %s AND p.post_type = 'sp_table' AND p.post_status = 'publish'
                ORDER BY p.ID DESC LIMIT 1
                """,
                (league_term_id,),
            )
            row = await cur.fetchone()
            return row[0] if row else None


async def get_current_season() -> dict | None:
    """Return the most recently started season from mlbb_season_schedule."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT sp_season_id, season_name
                FROM mlbb_season_schedule
                WHERE play_start <= NOW()
                ORDER BY play_start DESC
                LIMIT 1
                """
            )
            row = await cur.fetchone()
            return {"sp_season_id": row[0], "season_name": row[1]} if row else None


# ── Roster lock ──────────────────────────────────────────────────────────

async def get_team_roster_locks(sp_team_id: int) -> list[dict]:
    """
    Return closed registration periods where this team is approved.
    A non-empty result means the team's roster is locked.
    Returns [{period_id, entity_id, league_name, rule}].
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT rp.id, rp.entity_id, p.post_title, rp.rule
                FROM mlbb_team_registrations tr
                JOIN mlbb_registration_periods rp ON rp.id = tr.period_id
                JOIN wp_posts p ON p.ID = rp.entity_id
                WHERE tr.sp_team_id = %s
                  AND tr.status = 'approved'
                  AND rp.status = 'closed'
                """,
                (sp_team_id,),
            )
            rows = await cur.fetchall()
    return [
        {"period_id": r[0], "entity_id": r[1], "league_name": r[2], "rule": r[3]}
        for r in rows
    ]


async def get_event_team_ids(sp_event_id: int) -> list[int]:
    """Return the team IDs assigned to an sp_event post (via sp_team postmeta)."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT meta_value FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_team'",
                (sp_event_id,),
            )
            rows = await cur.fetchall()
    return [int(r[0]) for r in rows if r[0]]


async def get_future_events_for_team(sp_team_id: int) -> list[dict]:
    """Return future sp_event posts where this team is a participant."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID, p.post_title, p.post_date
                FROM wp_posts p
                JOIN wp_postmeta pm ON pm.post_id = p.ID AND pm.meta_key = 'sp_team'
                WHERE p.post_type = 'sp_event'
                  AND p.post_status = 'future'
                  AND pm.meta_value = %s
                ORDER BY p.post_date ASC
                """,
                (str(sp_team_id),),
            )
            rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "date": r[2]} for r in rows]


async def get_all_league_events(league_term_id: int, season_id: int = None) -> list[dict]:
    """Return all sp_event posts for a league (optionally filtered by season)."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            query = """
                SELECT p.ID, p.post_title, p.post_date, p.post_status
                FROM wp_posts p
                JOIN wp_term_relationships wtr ON wtr.object_id = p.ID
                JOIN wp_term_taxonomy wtt ON wtt.term_taxonomy_id = wtr.term_taxonomy_id
                WHERE p.post_type = 'sp_event'
                  AND wtt.taxonomy = 'sp_league'
                  AND wtt.term_id = %s
            """
            params = [league_term_id]
            if season_id:
                query += """
                  AND EXISTS (
                      SELECT 1 FROM wp_term_relationships wtr2
                      JOIN wp_term_taxonomy wtt2 ON wtt2.term_taxonomy_id = wtr2.term_taxonomy_id
                      WHERE wtr2.object_id = p.ID AND wtt2.taxonomy = 'sp_season' AND wtt2.term_id = %s
                  )
                """
                params.append(season_id)
            query += " ORDER BY p.post_date ASC"
            await cur.execute(query, params)
            rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "date": r[2], "status": r[3]} for r in rows]


async def get_league_standings(league_term_id: int, season_id: int = None) -> list[dict]:
    """
    Calculate W/L/D standings for a league by reading sp_results from published events.
    Returns sorted list of [{team_id, team_name, wins, losses, draws, points, played}].
    """
    import phpserialize
    events = await get_all_league_events(league_term_id, season_id)
    published = [e for e in events if e["status"] == "publish"]

    team_stats = {}  # team_id -> {wins, losses, draws}

    for event in published:
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT meta_value FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_results'",
                    (event["id"],),
                )
                row = await cur.fetchone()
        if not row or not row[0]:
            continue
        try:
            results = phpserialize.loads(row[0].encode(), decode_strings=True)
        except Exception:
            continue

        for team_id_str, data in results.items():
            team_id = int(team_id_str) if isinstance(team_id_str, str) else team_id_str
            if team_id not in team_stats:
                team_stats[team_id] = {"wins": 0, "losses": 0, "draws": 0}
            outcomes = data.get("outcome", [])
            if isinstance(outcomes, list):
                for o in outcomes:
                    if o == "win":
                        team_stats[team_id]["wins"] += 1
                    elif o == "loss":
                        team_stats[team_id]["losses"] += 1
                    elif o == "draw":
                        team_stats[team_id]["draws"] += 1

    # Get team names
    standings = []
    for team_id, stats in team_stats.items():
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT post_title FROM wp_posts WHERE ID=%s", (team_id,)
                )
                row = await cur.fetchone()
        team_name = row[0] if row else f"Team {team_id}"
        played = stats["wins"] + stats["losses"] + stats["draws"]
        points = stats["wins"] * 3 + stats["draws"]
        standings.append({
            "team_id": team_id,
            "team_name": team_name,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "draws": stats["draws"],
            "played": played,
            "points": points,
        })

    standings.sort(key=lambda x: (-x["points"], -(x["wins"] - x["losses"]), x["team_name"]))
    return standings


async def get_overdue_events(hours_past: int = 48) -> list[dict]:
    """
    Return future sp_events whose scheduled date is more than hours_past hours ago
    and still have status='future' (no result submitted).
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID, p.post_title, p.post_date
                FROM wp_posts p
                WHERE p.post_type = 'sp_event'
                  AND p.post_status = 'future'
                  AND p.post_date < DATE_SUB(NOW(), INTERVAL %s HOUR)
                ORDER BY p.post_date ASC
                """,
                (hours_past,),
            )
            rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "date": r[2]} for r in rows]


async def get_upcoming_events(hours_ahead: int = 24) -> list[dict]:
    """Return future sp_events within the next hours_ahead hours."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT p.ID, p.post_title, p.post_date
                FROM wp_posts p
                WHERE p.post_type = 'sp_event'
                  AND p.post_status = 'future'
                  AND p.post_date BETWEEN NOW() AND DATE_ADD(NOW(), INTERVAL %s HOUR)
                ORDER BY p.post_date ASC
                """,
                (hours_ahead,),
            )
            rows = await cur.fetchall()
    return [{"id": r[0], "title": r[1], "date": r[2]} for r in rows]


async def get_leagues_with_closed_periods() -> list[dict]:
    """Return leagues (term_id, table_id, name) that have closed registration periods."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT DISTINCT rp.entity_id, p.post_title, rp.sp_season_id
                FROM mlbb_registration_periods rp
                JOIN wp_posts p ON p.ID = rp.entity_id
                WHERE rp.entity_type = 'league'
                  AND rp.status = 'closed'
                  AND rp.sp_season_id IS NOT NULL
                """
            )
            rows = await cur.fetchall()
    return [{"table_id": r[0], "league_name": r[1], "sp_season_id": r[2]} for r in rows]


async def forfeit_team_remaining_events(sp_team_id: int) -> int:
    """
    Forfeit all future events for a team (set result to 0-3 loss).
    Returns count of forfeited events.
    """
    events = await get_future_events_for_team(sp_team_id)
    forfeited = 0
    for event in events:
        team_ids = await get_event_team_ids(event["id"])
        if len(team_ids) != 2:
            continue
        opponent_id = [t for t in team_ids if t != sp_team_id]
        if not opponent_id:
            continue
        opponent_id = opponent_id[0]
        try:
            # Forfeit: opponent wins 3-0
            await set_event_results(event["id"], opponent_id, sp_team_id, 3, 0)
            forfeited += 1
        except Exception:
            pass
    return forfeited


async def get_teams_below_minimum_roster(min_size: int = 5) -> list[dict]:
    """
    Return teams that are registered in active (closed) leagues but have
    fewer than min_size active roster members.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT DISTINCT tr.sp_team_id, p.post_title,
                    (SELECT COUNT(*) FROM mlbb_player_roster r
                     WHERE r.sp_team_id = tr.sp_team_id AND r.status = 'active') AS roster_count
                FROM mlbb_team_registrations tr
                JOIN mlbb_registration_periods rp ON rp.id = tr.period_id
                JOIN wp_posts p ON p.ID = tr.sp_team_id
                WHERE tr.status = 'approved'
                  AND rp.status = 'closed'
                HAVING roster_count < %s
                """,
                (min_size,),
            )
            rows = await cur.fetchall()
    return [{"sp_team_id": r[0], "team_name": r[1], "roster_count": r[2]} for r in rows]


async def withdraw_team_from_leagues(sp_team_id: int) -> int:
    """
    Mark all approved registrations for a team as 'withdrawn'.
    Returns count of withdrawals.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_team_registrations
                SET status = 'withdrawn', reviewed_at = NOW(), reviewed_by = 'system'
                WHERE sp_team_id = %s AND status = 'approved'
                """,
                (sp_team_id,),
            )
            return cur.rowcount


async def create_voice_channel_record(sp_event_id: int, channel_id: str, channel_name: str) -> int:
    """Record a created voice channel in mlbb_voice_channels."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO mlbb_voice_channels (match_schedule_id, channel_id, channel_name)
                VALUES (%s, %s, %s)
                """,
                (sp_event_id, channel_id, channel_name),
            )
            return cur.lastrowid


async def get_active_voice_channels() -> list[dict]:
    """Return voice channels that haven't been deleted yet."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT vc.id, vc.match_schedule_id, vc.channel_id, vc.channel_name, vc.created_at
                FROM mlbb_voice_channels vc
                WHERE vc.deleted_at IS NULL
                """
            )
            rows = await cur.fetchall()
    return [
        {"id": r[0], "sp_event_id": r[1], "channel_id": r[2],
         "channel_name": r[3], "created_at": r[4]}
        for r in rows
    ]


async def mark_voice_channel_deleted(record_id: int) -> None:
    """Mark a voice channel record as deleted."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE mlbb_voice_channels SET deleted_at = NOW() WHERE id = %s",
                (record_id,),
            )


async def apply_standings_table_meta(sp_table_id: int) -> None:
    """
    Set the standard SportsPress postmeta on an sp_table post so it renders
    as a full standings table with Pos | Team | Wins | Losses | Win %.

    This must be called after api.create_table() because the REST API does
    not accept these meta fields directly.
    """
    import phpserialize

    # Serialized values
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
        ("sp_select", "manual"),
        ("sp_orderby", "wins"),
        ("sp_order", "DESC"),
        ("sp_event_status", sp_event_status),
        ("sp_highlight", "0"),
        ("sp_columns", sp_columns),
        ("sp_adjustments", empty_array),
        ("sp_teams", empty_array),
        ("sp_highlight_places", "NULL"),
    ]

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            for meta_key, meta_value in meta_pairs:
                await cur.execute(
                    "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key=%s",
                    (sp_table_id, meta_key),
                )
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, %s, %s)",
                    (sp_table_id, meta_key, meta_value),
                )


async def populate_table_teams(sp_table_id: int, team_ids: list[int]) -> None:
    """
    Write sp_team postmeta rows on an sp_table post so SportsPress can render
    the standings. Call this whenever teams are added to a league.
    sp_select must be 'manual' for SP to read these rows.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            # Clear existing sp_team rows for this table
            await cur.execute(
                "DELETE FROM wp_postmeta WHERE post_id=%s AND meta_key='sp_team'",
                (sp_table_id,),
            )
            # Insert one row per team
            for tid in team_ids:
                await cur.execute(
                    "INSERT INTO wp_postmeta (post_id, meta_key, meta_value) VALUES (%s, 'sp_team', %s)",
                    (sp_table_id, str(tid)),
                )
