"""
services/command_services.py — Standalone service functions mirroring slash-command business logic.

Each function performs the **same validations and state changes** as the
corresponding Discord cog handler, but returns a Result instead of sending
Discord messages.

Used by:
  - scripts/simulate_league.py  (headless test harness)
  - potentially any future non-Discord interface

The cog handlers remain untouched — this is a parallel path, not a refactor.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from services import db
from services.db_helpers import (
    get_player_by_discord_id,
    get_team_by_id,
    get_captain_team,
    get_captain_teams,
    get_player_active_team_ids,
    get_player_roster_entry,
    get_any_pending_invite,
    get_roster,
    get_my_teams,
    setup_team_roster_display,
    sync_team_roster_list,
    set_team_colors,
    set_player_photos,
    set_player_discord_meta,
)
from services.sportspress import SportsPressAPI

logger = logging.getLogger(__name__)

INVITE_TTL_HOURS = 48


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class Result:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""


# ── Player ────────────────────────────────────────────────────────────────────

async def player_register(
    api: SportsPressAPI,
    discord_id: str,
    discord_username: str,
    ign: str,
    avatar_png: bytes = None,
) -> Result:
    """
    Mirror of /player register.
    Creates sp_player, links Discord ID via sp_metrics, optionally sets avatar.
    """
    # 1. Already registered?
    existing = await get_player_by_discord_id(discord_id)
    if existing:
        return Result(ok=False, error=f"Already registered as {existing['title']} (ID {existing['id']})")

    # 2. Create sp_player post
    try:
        player = await api.create_player(ign, [])
    except Exception as e:
        return Result(ok=False, error=f"Failed to create player: {e}")

    sp_player_id = player["id"]

    # 3. Write Discord linkage into sp_metrics (direct MySQL — REST API drops meta)
    try:
        await set_player_discord_meta(sp_player_id, discord_id, discord_username)
    except Exception as e:
        logger.warning("Could not set sp_metrics for player %s: %s", sp_player_id, e)

    # 4. Upload avatar and set all 3 photo fields
    media_id = None
    if avatar_png:
        try:
            media = await api.upload_media(
                avatar_png, f"player-{sp_player_id}-avatar.png", "image/png"
            )
            media_id = media["id"]
            await set_player_photos(sp_player_id, media_id)
        except Exception as e:
            logger.warning("Could not upload avatar for player %s: %s", sp_player_id, e)

    return Result(ok=True, data={
        "sp_player_id": sp_player_id, "ign": ign, "media_id": media_id,
    })


# ── Team ──────────────────────────────────────────────────────────────────────

async def player_profile(
    discord_id: str,
) -> Result:
    """
    Mirror of /player profile.
    Returns player info + team memberships.
    """
    player = await get_player_by_discord_id(discord_id)
    if not player:
        return Result(ok=False, error="Player not registered")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT r.sp_team_id, r.role, tp.post_title
                FROM mlbb_player_roster r
                JOIN wp_posts tp ON tp.ID = r.sp_team_id
                WHERE r.discord_id = %s AND r.status = 'active'
                """,
                (discord_id,),
            )
            team_rows = await cur.fetchall()

    teams = [{"sp_team_id": r[0], "role": r[1], "team_name": r[2]} for r in team_rows]
    return Result(ok=True, data={
        "sp_player_id": player["id"],
        "ign": player["title"],
        "teams": teams,
    })


async def team_create(
    api: SportsPressAPI,
    discord_id: str,
    team_name: str,
    logo_png: bytes = None,
    color_primary: str = None,
    color_secondary: str = None,
) -> Result:
    """
    Mirror of /team create.
    Requires player to be registered.  Creates sp_team, roster entry,
    sp_list, and wires the roster display.
    """
    # 1. Must be registered
    player = await get_player_by_discord_id(discord_id)
    if not player:
        return Result(ok=False, error="Player not registered")

    # 2. Create sp_team
    try:
        team = await api.create_team(team_name)
    except Exception as e:
        return Result(ok=False, error=f"Failed to create team: {e}")

    sp_team_id = team["id"]

    # 3. Insert captain into roster
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO mlbb_player_roster
                    (discord_id, sp_player_id, sp_team_id, role, status)
                VALUES (%s, %s, %s, 'captain', 'active')
                """,
                (discord_id, player["id"], sp_team_id),
            )

    # 4. Sync player → team in SportsPress (accumulate)
    try:
        all_team_ids = await get_player_active_team_ids(discord_id)
        await api.set_player_teams(player["id"], all_team_ids)
    except Exception as e:
        logger.warning("Could not sync sp_team on captain %s: %s", player["id"], e)

    # 5. Create roster list + wire ACF display
    sp_list_id = None
    try:
        sp_list = await api.create_player_list(f"{team_name} — Roster")
        sp_list_id = sp_list["id"]
        await setup_team_roster_display(sp_team_id, sp_list_id)
        await sync_team_roster_list(sp_team_id)
    except Exception as e:
        logger.warning("Could not create roster list for team %s: %s", sp_team_id, e)

    # 6. Upload team logo + set colors (optional)
    media_id = None
    if logo_png:
        try:
            media = await api.upload_media(logo_png, f"team-{sp_team_id}-logo.png", "image/png")
            media_id = media["id"]
            await api.set_team_featured_image(sp_team_id, media_id)
        except Exception as e:
            logger.warning("Could not upload logo for team %s: %s", sp_team_id, e)
    if color_primary or color_secondary:
        try:
            await set_team_colors(sp_team_id, color_primary=color_primary, color_secondary=color_secondary)
        except Exception as e:
            logger.warning("Could not set colors for team %s: %s", sp_team_id, e)

    return Result(ok=True, data={
        "sp_team_id": sp_team_id,
        "sp_player_id": player["id"],
        "sp_list_id": sp_list_id,
        "team_name": team_name,
        "media_id": media_id,
    })


async def team_invite(
    discord_id: str,
    invitee_discord_id: str,
    sp_team_id: int = None,
    role: str = "player",
) -> Result:
    """
    Mirror of /team invite.
    Validates captain status, invitee registration, no-dupe-on-team, then inserts invite.
    """
    if invitee_discord_id == discord_id:
        return Result(ok=False, error="Cannot invite yourself")

    # 1. Resolve captain's team
    captain_teams = await get_captain_teams(discord_id)
    if not captain_teams:
        return Result(ok=False, error="Not a captain")

    if sp_team_id is not None:
        captain = next((t for t in captain_teams if t["sp_team_id"] == sp_team_id), None)
        if not captain:
            return Result(ok=False, error=f"Not captain of team {sp_team_id}")
    elif len(captain_teams) == 1:
        captain = captain_teams[0]
    else:
        return Result(ok=False, error="Captains multiple teams — sp_team_id required")

    # 2. Invitee must be registered
    invitee_player = await get_player_by_discord_id(invitee_discord_id)
    if not invitee_player:
        return Result(ok=False, error="Invitee not registered")

    # 3. Not already on team
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM mlbb_player_roster WHERE discord_id=%s AND sp_team_id=%s AND status='active'",
                (invitee_discord_id, captain["sp_team_id"]),
            )
            if await cur.fetchone():
                return Result(ok=False, error="Invitee already on team")

    # 4. Insert invite
    expires = datetime.utcnow() + timedelta(hours=INVITE_TTL_HOURS)
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO mlbb_team_invites
                    (sp_team_id, inviter_id, invitee_id, role, expires_at, status)
                VALUES (%s, %s, %s, %s, %s, 'pending')
                ON DUPLICATE KEY UPDATE
                    inviter_id=%s, role=%s, expires_at=%s, status='pending'
                """,
                (
                    captain["sp_team_id"], discord_id, invitee_discord_id, role, expires,
                    discord_id, role, expires,
                ),
            )

    return Result(ok=True, data={
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
        "invitee_discord_id": invitee_discord_id,
        "role": role,
    })


async def team_accept(
    api: SportsPressAPI,
    invitee_discord_id: str,
) -> Result:
    """
    Mirror of /team accept.
    Finds first pending invite, adds player to roster, syncs SP.
    """
    # 1. Find pending invite
    invite = await get_any_pending_invite(invitee_discord_id)
    if not invite:
        return Result(ok=False, error="No pending invite")

    # 2. Must be registered
    player = await get_player_by_discord_id(invitee_discord_id)
    if not player:
        return Result(ok=False, error="Player not registered")

    # 3. Upsert roster + accept invite
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO mlbb_player_roster
                    (discord_id, sp_player_id, sp_team_id, role, status)
                VALUES (%s, %s, %s, %s, 'active')
                ON DUPLICATE KEY UPDATE role=%s, status='active'
                """,
                (
                    invitee_discord_id, player["id"], invite["sp_team_id"], invite["role"],
                    invite["role"],
                ),
            )
            await cur.execute(
                "UPDATE mlbb_team_invites SET status='accepted' WHERE id=%s",
                (invite["id"],),
            )

    # 4. Sync player → all active teams in SportsPress
    try:
        all_team_ids = await get_player_active_team_ids(invitee_discord_id)
        await api.set_player_teams(player["id"], all_team_ids)
    except Exception as e:
        logger.warning("Could not sync sp_team on player %s: %s", player["id"], e)

    # 5. Sync roster display list
    try:
        await sync_team_roster_list(invite["sp_team_id"])
    except Exception as e:
        logger.warning("Could not sync roster list for team %s: %s", invite["sp_team_id"], e)

    return Result(ok=True, data={
        "sp_team_id": invite["sp_team_id"],
        "team_name": invite["team_name"],
        "role": invite["role"],
        "sp_player_id": player["id"],
    })


# ── League registration ──────────────────────────────────────────────────────

async def league_register(
    discord_id: str,
    league_id: int,
    sp_team_id: int = None,
) -> Result:
    """
    Mirror of /league register.
    Full validation: captain check, open period, already-registered,
    per-league conflict, max_teams cap.
    """
    # 1. Must be captain
    captain_teams = await get_captain_teams(discord_id)
    if not captain_teams:
        return Result(ok=False, error="Not a captain")

    if sp_team_id is not None:
        captain = next((t for t in captain_teams if t["sp_team_id"] == sp_team_id), None)
        if not captain:
            return Result(ok=False, error=f"Not captain of team {sp_team_id}")
    elif len(captain_teams) == 1:
        captain = captain_teams[0]
    else:
        return Result(ok=False, error="Captains multiple teams — sp_team_id required")

    # 2. Find open registration period
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, max_teams
                FROM mlbb_registration_periods
                WHERE entity_type='league'
                  AND entity_id=%s
                  AND status='open'
                  AND opens_at <= NOW()
                  AND closes_at > NOW()
                LIMIT 1
                """,
                (league_id,),
            )
            period = await cur.fetchone()

    if not period:
        return Result(ok=False, error=f"Registration not open for league {league_id}")

    period_id, max_teams = period

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            # 3. Already registered?
            await cur.execute(
                "SELECT id FROM mlbb_team_registrations WHERE period_id=%s AND sp_team_id=%s",
                (period_id, captain["sp_team_id"]),
            )
            if await cur.fetchone():
                return Result(ok=False, error=f"{captain['team_name']} already registered")

            # 4. Per-league conflict: player on two teams in same period
            await cur.execute(
                """
                SELECT r.discord_id, tp.post_title as other_team
                FROM mlbb_player_roster r
                JOIN mlbb_team_registrations tr ON tr.sp_team_id = r.sp_team_id
                JOIN wp_posts tp ON tp.ID = r.sp_team_id
                WHERE r.sp_team_id != %s
                  AND r.status = 'active'
                  AND tr.period_id = %s
                  AND tr.status != 'rejected'
                  AND r.discord_id IN (
                      SELECT discord_id FROM mlbb_player_roster
                      WHERE sp_team_id = %s AND status = 'active'
                  )
                LIMIT 1
                """,
                (captain["sp_team_id"], period_id, captain["sp_team_id"]),
            )
            conflict = await cur.fetchone()
            if conflict:
                return Result(ok=False,
                              error=f"Player conflict: member already on {conflict[1]} in this league")

            # 5. Max teams cap
            if max_teams:
                await cur.execute(
                    "SELECT COUNT(*) FROM mlbb_team_registrations WHERE period_id=%s AND status!='rejected'",
                    (period_id,),
                )
                count = (await cur.fetchone())[0]
                if count >= max_teams:
                    return Result(ok=False, error=f"League full ({max_teams} teams)")

            # 6. Insert registration
            await cur.execute(
                """
                INSERT INTO mlbb_team_registrations
                    (period_id, sp_team_id, registered_by, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (period_id, captain["sp_team_id"], discord_id),
            )
            registration_id = cur.lastrowid

    return Result(ok=True, data={
        "registration_id": registration_id,
        "period_id": period_id,
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
    })


# ── Admin ─────────────────────────────────────────────────────────────────────

async def admin_approve_registration(
    registration_id: int,
    reviewer_id: str = "system",
) -> Result:
    """
    Mirror of /league-admin approve-registration.
    """
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_team_registrations
                SET status='approved', reviewed_by=%s, reviewed_at=NOW()
                WHERE id=%s AND status='pending'
                """,
                (reviewer_id, registration_id),
            )
            affected = cur.rowcount

    if not affected:
        return Result(ok=False, error=f"Registration #{registration_id} not found or not pending")

    return Result(ok=True, data={"registration_id": registration_id})


# ── Match ─────────────────────────────────────────────────────────────────────

@dataclass
class MatchData:
    """Pre-parsed match data (replaces Claude Vision in headless context)."""
    battle_id: str
    winner_kills: int
    loser_kills: int
    duration: str
    match_timestamp: Optional[str] = None      # "MM/DD/YYYY HH:MM:SS"
    confidence: float = 0.97
    screenshot_url: str = ""


async def match_submit(
    discord_id: str,
    match_data: MatchData,
    event_id: int = None,
) -> Result:
    """
    Mirror of /match submit (post-parse).
    Validates captain, deduplicates BattleID, inserts submission as 'pending'.
    """
    # 1. Must be captain
    captain = await get_captain_team(discord_id)
    if not captain:
        return Result(ok=False, error="Not a captain")

    # 2. Duplicate BattleID?
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM mlbb_match_submissions WHERE battle_id=%s AND status != 'rejected'",
                (match_data.battle_id,),
            )
            if await cur.fetchone():
                return Result(ok=False, error=f"BattleID {match_data.battle_id} already submitted")

    # 3. Parse timestamp
    match_ts = None
    if match_data.match_timestamp:
        try:
            match_ts = datetime.strptime(match_data.match_timestamp, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            pass

    # 4. Insert
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO mlbb_match_submissions
                    (sp_event_id, submitted_by, winning_team_id, screenshot_url,
                     battle_id, winner_kills, loser_kills, match_duration,
                     match_timestamp, ai_confidence, ai_raw, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    event_id,
                    discord_id,
                    captain["sp_team_id"],
                    match_data.screenshot_url,
                    match_data.battle_id,
                    match_data.winner_kills,
                    match_data.loser_kills,
                    match_data.duration,
                    match_ts,
                    match_data.confidence,
                    f'{{"simulated":true}}',
                ),
            )
            submission_id = cur.lastrowid

    return Result(ok=True, data={
        "submission_id": submission_id,
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
        "battle_id": match_data.battle_id,
        "winner_kills": match_data.winner_kills,
        "loser_kills": match_data.loser_kills,
    })


async def match_confirm(
    discord_id: str,
    submission_id: int,
) -> Result:
    """
    Mirror of /match confirm.
    Validates captain, prevents self-confirm, updates status.
    """
    # 1. Must be captain
    captain = await get_captain_team(discord_id)
    if not captain:
        return Result(ok=False, error="Not a captain")

    # 2. Find pending submission
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, submitted_by, winning_team_id, battle_id, winner_kills, loser_kills "
                "FROM mlbb_match_submissions WHERE id=%s AND status='pending'",
                (submission_id,),
            )
            row = await cur.fetchone()

    if not row:
        return Result(ok=False, error=f"Submission #{submission_id} not found or already resolved")

    sub_id, submitted_by, winning_team_id, battle_id, winner_kills, loser_kills = row

    # 3. Can't confirm own submission
    if submitted_by == discord_id:
        return Result(ok=False, error="Cannot confirm your own submission")

    # 4. Confirm
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_match_submissions
                SET status='confirmed', confirmed_by=%s, confirmed_at=NOW()
                WHERE id=%s
                """,
                (discord_id, submission_id),
            )

    return Result(ok=True, data={
        "submission_id": submission_id,
        "battle_id": battle_id,
        "winner_kills": winner_kills,
        "loser_kills": loser_kills,
        "winning_team_id": winning_team_id,
    })


async def match_dispute(
    discord_id: str,
    submission_id: int,
    reason: str,
) -> Result:
    """
    Mirror of /match dispute.
    Validates captain, prevents self-dispute, marks status as 'disputed'.
    """
    captain = await get_captain_team(discord_id)
    if not captain:
        return Result(ok=False, error="Not a captain")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, submitted_by FROM mlbb_match_submissions WHERE id=%s AND status='pending'",
                (submission_id,),
            )
            row = await cur.fetchone()

    if not row:
        return Result(ok=False, error=f"Submission #{submission_id} not found or already resolved")

    if row[1] == discord_id:
        return Result(ok=False, error="Cannot dispute your own submission")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE mlbb_match_submissions SET status='disputed' WHERE id=%s",
                (submission_id,),
            )

    return Result(ok=True, data={
        "submission_id": submission_id,
        "submitted_by": row[1],
        "reason": reason,
    })


# ── Team: kick, edit, delete, roster, list ────────────────────────────────────

async def team_kick(
    api: SportsPressAPI,
    discord_id: str,
    target_discord_id: str,
    sp_team_id: int = None,
) -> Result:
    """
    Mirror of /team kick.
    Validates captain, removes target from roster, syncs SP and roster list.
    """
    if target_discord_id == discord_id:
        return Result(ok=False, error="Cannot kick yourself")

    captain_teams = await get_captain_teams(discord_id)
    if not captain_teams:
        return Result(ok=False, error="Not a captain")

    if sp_team_id is not None:
        captain = next((t for t in captain_teams if t["sp_team_id"] == sp_team_id), None)
        if not captain:
            return Result(ok=False, error=f"Not captain of team {sp_team_id}")
    elif len(captain_teams) == 1:
        captain = captain_teams[0]
    else:
        return Result(ok=False, error="Captains multiple teams — sp_team_id required")

    # Mark inactive (only non-captains)
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                UPDATE mlbb_player_roster
                SET status='inactive'
                WHERE discord_id=%s AND sp_team_id=%s AND status='active' AND role != 'captain'
                """,
                (target_discord_id, captain["sp_team_id"]),
            )
            affected = cur.rowcount

    if not affected:
        return Result(ok=False, error="Target not an active non-captain player on this team")

    # Sync kicked player's remaining teams in SP
    kicked_player = await get_player_by_discord_id(target_discord_id)
    if kicked_player:
        try:
            remaining_ids = await get_player_active_team_ids(target_discord_id)
            await api.set_player_teams(kicked_player["id"], remaining_ids)
        except Exception as e:
            logger.warning("Could not update sp_team on player %s: %s", kicked_player["id"], e)

    try:
        await sync_team_roster_list(captain["sp_team_id"])
    except Exception as e:
        logger.warning("Could not sync roster list for team %s: %s", captain["sp_team_id"], e)

    return Result(ok=True, data={
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
        "kicked_discord_id": target_discord_id,
    })


async def team_edit(
    api: SportsPressAPI,
    discord_id: str,
    sp_team_id: int = None,
    logo_png: bytes = None,
    color_primary: str = None,
    color_secondary: str = None,
) -> Result:
    """
    Mirror of /team edit.
    Validates captain, uploads logo, sets colours.
    """
    if not logo_png and not color_primary and not color_secondary:
        return Result(ok=False, error="Provide at least one of: logo, color_primary, color_secondary")

    captain_teams = await get_captain_teams(discord_id)
    if not captain_teams:
        return Result(ok=False, error="Not a captain")

    if sp_team_id is not None:
        captain = next((t for t in captain_teams if t["sp_team_id"] == sp_team_id), None)
        if not captain:
            return Result(ok=False, error=f"Not captain of team {sp_team_id}")
    elif len(captain_teams) == 1:
        captain = captain_teams[0]
    else:
        return Result(ok=False, error="Captains multiple teams — sp_team_id required")

    changes = []

    if logo_png:
        try:
            safe_name = f"team-{captain['sp_team_id']}-logo.png"
            media = await api.upload_media(logo_png, safe_name, "image/png")
            await api.set_team_featured_image(captain["sp_team_id"], media["id"])
            changes.append(f"Logo updated (media {media['id']})")
        except Exception as e:
            return Result(ok=False, error=f"Failed to upload logo: {e}")

    if color_primary or color_secondary:
        try:
            await set_team_colors(
                captain["sp_team_id"],
                color_primary=color_primary,
                color_secondary=color_secondary,
            )
            if color_primary:
                changes.append(f"Primary color: {color_primary}")
            if color_secondary:
                changes.append(f"Secondary color: {color_secondary}")
        except Exception as e:
            return Result(ok=False, error=f"Failed to set colors: {e}")

    return Result(ok=True, data={
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
        "changes": changes,
    })


async def team_delete(
    api: SportsPressAPI,
    discord_id: str,
    sp_team_id: int = None,
    is_admin: bool = False,
) -> Result:
    """
    Mirror of /team delete.
    Captains delete own team (omit sp_team_id); admins can delete any team by ID.
    Cleans up roster, invites, registrations, SP player links, then deletes post.
    """
    if sp_team_id is not None and not is_admin:
        return Result(ok=False, error="Only admins can delete by team_id")

    if sp_team_id is None:
        captain = await get_captain_team(discord_id)
        if not captain:
            return Result(ok=False, error="Not a captain of any team")
        sp_team_id = captain["sp_team_id"]
        team_name = captain["team_name"]
    else:
        team = await get_team_by_id(sp_team_id)
        if not team:
            return Result(ok=False, error=f"No team found with ID {sp_team_id}")
        team_name = team["title"]

    # Clear SP team links for all roster members
    roster = await get_roster(sp_team_id)
    for member in roster:
        try:
            remaining = await get_player_active_team_ids(member["discord_id"])
            # Remove this team from the list
            remaining = [t for t in remaining if t != sp_team_id]
            await api.set_player_teams(member["sp_player_id"], remaining)
        except Exception as e:
            logger.warning("Could not clear sp_team on player %s: %s", member["sp_player_id"], e)

    # Clean custom tables
    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE mlbb_player_roster SET status='inactive' WHERE sp_team_id=%s",
                (sp_team_id,),
            )
            await cur.execute(
                "UPDATE mlbb_team_invites SET status='expired' WHERE sp_team_id=%s AND status='pending'",
                (sp_team_id,),
            )
            await cur.execute(
                "UPDATE mlbb_team_registrations SET status='rejected' WHERE sp_team_id=%s AND status='pending'",
                (sp_team_id,),
            )

    # Delete SP post
    try:
        await api.delete_team(sp_team_id)
    except Exception as e:
        return Result(ok=False, error=f"Roster cleaned but SP delete failed: {e}")

    return Result(ok=True, data={"sp_team_id": sp_team_id, "team_name": team_name})


async def team_roster(
    discord_id: str = None,
    sp_team_id: int = None,
) -> Result:
    """
    Mirror of /team roster.
    Returns roster for a given team, or the caller's own team.
    """
    if sp_team_id is None:
        if not discord_id:
            return Result(ok=False, error="Provide discord_id or sp_team_id")
        entry = await get_player_roster_entry(discord_id)
        if not entry:
            return Result(ok=False, error="Not on a team")
        sp_team_id = entry["sp_team_id"]

    roster = await get_roster(sp_team_id)
    if not roster:
        return Result(ok=False, error=f"No roster for team {sp_team_id}")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT post_title FROM wp_posts WHERE ID=%s", (sp_team_id,)
            )
            row = await cur.fetchone()
    team_name = row[0] if row else f"Team {sp_team_id}"

    return Result(ok=True, data={
        "sp_team_id": sp_team_id,
        "team_name": team_name,
        "roster": roster,
    })


async def team_list(discord_id: str) -> Result:
    """
    Mirror of /team list.
    Returns all teams where the player is an active member.
    """
    teams = await get_my_teams(discord_id)
    if not teams:
        return Result(ok=False, error="Not on any teams")
    return Result(ok=True, data={"teams": teams})


# ── Tournament registration ──────────────────────────────────────────────────

async def tournament_register(
    discord_id: str,
    tournament_id: int,
    sp_team_id: int = None,
) -> Result:
    """
    Mirror of /tournament register.
    Same structure as league_register but entity_type='tournament'.
    """
    captain_teams = await get_captain_teams(discord_id)
    if not captain_teams:
        return Result(ok=False, error="Not a captain")

    if sp_team_id is not None:
        captain = next((t for t in captain_teams if t["sp_team_id"] == sp_team_id), None)
        if not captain:
            return Result(ok=False, error=f"Not captain of team {sp_team_id}")
    elif len(captain_teams) == 1:
        captain = captain_teams[0]
    else:
        return Result(ok=False, error="Captains multiple teams — sp_team_id required")

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT id, max_teams
                FROM mlbb_registration_periods
                WHERE entity_type='tournament'
                  AND entity_id=%s
                  AND status='open'
                  AND opens_at <= NOW()
                  AND closes_at > NOW()
                LIMIT 1
                """,
                (tournament_id,),
            )
            period = await cur.fetchone()

    if not period:
        return Result(ok=False, error=f"Registration not open for tournament {tournament_id}")

    period_id, max_teams = period

    async with db.get_conn() as conn:
        async with conn.cursor() as cur:
            # Already registered?
            await cur.execute(
                "SELECT id FROM mlbb_team_registrations WHERE period_id=%s AND sp_team_id=%s",
                (period_id, captain["sp_team_id"]),
            )
            if await cur.fetchone():
                return Result(ok=False, error=f"{captain['team_name']} already registered")

            # Per-tournament player conflict
            await cur.execute(
                """
                SELECT r.discord_id, tp.post_title as other_team
                FROM mlbb_player_roster r
                JOIN mlbb_team_registrations tr ON tr.sp_team_id = r.sp_team_id
                JOIN wp_posts tp ON tp.ID = r.sp_team_id
                WHERE r.sp_team_id != %s
                  AND r.status = 'active'
                  AND tr.period_id = %s
                  AND tr.status != 'rejected'
                  AND r.discord_id IN (
                      SELECT discord_id FROM mlbb_player_roster
                      WHERE sp_team_id = %s AND status = 'active'
                  )
                LIMIT 1
                """,
                (captain["sp_team_id"], period_id, captain["sp_team_id"]),
            )
            conflict = await cur.fetchone()
            if conflict:
                return Result(ok=False,
                              error=f"Player conflict: member already on {conflict[1]} in this tournament")

            # Max teams cap
            if max_teams:
                await cur.execute(
                    "SELECT COUNT(*) FROM mlbb_team_registrations WHERE period_id=%s AND status!='rejected'",
                    (period_id,),
                )
                count = (await cur.fetchone())[0]
                if count >= max_teams:
                    return Result(ok=False, error=f"Tournament full ({max_teams} teams)")

            await cur.execute(
                """
                INSERT INTO mlbb_team_registrations
                    (period_id, sp_team_id, registered_by, status)
                VALUES (%s, %s, %s, 'pending')
                """,
                (period_id, captain["sp_team_id"], discord_id),
            )
            registration_id = cur.lastrowid

    return Result(ok=True, data={
        "registration_id": registration_id,
        "period_id": period_id,
        "sp_team_id": captain["sp_team_id"],
        "team_name": captain["team_name"],
    })
