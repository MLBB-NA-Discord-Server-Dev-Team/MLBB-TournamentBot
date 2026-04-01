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
    """Return {id, title} for an sp_team post."""
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ID, post_title FROM wp_posts WHERE ID=%s AND post_type='sp_team' AND post_status='publish'",
                (sp_team_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "title": row[1]}


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


async def get_captain_team(discord_id: str) -> Optional[dict]:
    """
    Return the team where this Discord user is captain, or None.
    Also returns the sp_player_id for convenience.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.sp_team_id, r.sp_player_id, p.post_title
                FROM mlbb_player_roster r
                JOIN wp_posts p ON p.ID = r.sp_team_id
                WHERE r.discord_id = %s
                  AND r.role = 'captain'
                  AND r.status = 'active'
                LIMIT 1
                """,
                (discord_id,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    return {"sp_team_id": row[0], "sp_player_id": row[1], "team_name": row[2]}


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
