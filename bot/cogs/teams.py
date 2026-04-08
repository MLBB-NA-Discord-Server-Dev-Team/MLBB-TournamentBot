"""
bot/cogs/teams.py — self-service team management

/team create    — anyone (must be registered)
/team invite    — captain only
/team accept    — invited player
/team kick      — captain only
/team roster    — anyone
/team list      — anyone
"""
import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from services import db, admin_log
from services.admin_log import Event
from services.db_helpers import (
    get_player_by_discord_id,
    get_captain_team,
    get_captain_teams,
    get_player_active_team_ids,
    get_player_roster_entry,
    get_roster,
    get_any_pending_invite,
    get_my_teams,
    set_team_colors,
    setup_team_roster_display,
    get_team_url,
    sync_team_roster_list,
)
from services.sportspress import SportsPressAPI

logger = logging.getLogger(__name__)
INVITE_TTL_HOURS = 48


def get_api():
    return SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)


class Teams(commands.Cog):
    """Self-service team management"""

    team = app_commands.Group(name="team", description="Team management")

    # ── /team create ──────────────────────────────────────────────────────

    @team.command(name="create", description="Create a new team and become its captain")
    @app_commands.describe(name="Team name")
    async def team_create(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        player = await get_player_by_discord_id(discord_id)
        if not player:
            await interaction.followup.send(
                "❌ You must register first with `/player register`.", ephemeral=True
            )
            return

        # Players may captain multiple teams (one per league) — no global block here.
        # Per-league conflicts are enforced at registration time.

        api = get_api()
        try:
            team = await api.create_team(name)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create team: {e}", ephemeral=True)
            return

        sp_team_id = team["id"]

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

        # Link captain to all their active teams in SportsPress (accumulate, don't overwrite)
        try:
            all_team_ids = await get_player_active_team_ids(discord_id)
            await api.set_player_teams(player["id"], all_team_ids)
        except Exception as e:
            logger.warning("Could not sync sp_team on captain %s: %s", player["id"], e)

        # Create an sp_list for this team and wire ACF roster fields so the
        # Roster tab on the team page displays players correctly
        try:
            sp_list = await api.create_player_list(f"{name} — Roster")
            await setup_team_roster_display(sp_team_id, sp_list["id"])
            await sync_team_roster_list(sp_team_id)
        except Exception as e:
            logger.warning("Could not create roster list for team %s: %s", sp_team_id, e)

        embed = discord.Embed(title="✅ Team Created", color=0x2ECC71)
        embed.add_field(name="Team", value=name)
        embed.add_field(name="Team ID", value=sp_team_id)
        embed.add_field(name="Your Role", value="Captain")
        embed.set_footer(text="Use /team invite to add players.")
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(interaction.client, Event.TEAM_CREATED, user=interaction.user, fields={
            "Team": name,
            "Team ID": sp_team_id,
            "Captain": f"<@{discord_id}>",
        })

    # ── /team invite ──────────────────────────────────────────────────────

    @team.command(name="invite", description="Invite a registered player to your team")
    @app_commands.describe(
        user="Discord member to invite",
        role="Player role on the team",
        team_id="Your team ID — required if you captain multiple teams",
    )
    @app_commands.choices(role=[
        app_commands.Choice(name="Player", value="player"),
        app_commands.Choice(name="Substitute", value="substitute"),
    ])
    async def team_invite(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: str = "player",
        team_id: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        invitee_id = str(user.id)

        if invitee_id == discord_id:
            await interaction.followup.send("❌ You can't invite yourself.", ephemeral=True)
            return

        captain_teams = await get_captain_teams(discord_id)
        if not captain_teams:
            await interaction.followup.send(
                "❌ You are not a captain. Only captains can invite players.", ephemeral=True
            )
            return

        if team_id is not None:
            captain = next((t for t in captain_teams if t["sp_team_id"] == team_id), None)
            if not captain:
                await interaction.followup.send(
                    f"❌ You are not the captain of team `{team_id}`.", ephemeral=True
                )
                return
        elif len(captain_teams) == 1:
            captain = captain_teams[0]
        else:
            lines = "\n".join(f"`{t['sp_team_id']}` — **{t['team_name']}**" for t in captain_teams)
            await interaction.followup.send(
                f"❌ You captain multiple teams. Re-run with `team_id`:\n{lines}", ephemeral=True
            )
            return

        invitee_player = await get_player_by_discord_id(invitee_id)
        if not invitee_player:
            await interaction.followup.send(
                f"❌ {user.mention} hasn't registered yet (`/player register`).", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM mlbb_player_roster WHERE discord_id=%s AND sp_team_id=%s AND status='active'",
                    (invitee_id, captain["sp_team_id"]),
                )
                if await cur.fetchone():
                    await interaction.followup.send(
                        f"❌ {user.mention} is already on your team.", ephemeral=True
                    )
                    return

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
                        captain["sp_team_id"], discord_id, invitee_id, role, expires,
                        discord_id, role, expires,
                    ),
                )

        try:
            dm_embed = discord.Embed(
                title=f"📩 Team Invite — {captain['team_name']}",
                description=(
                    f"{interaction.user.mention} has invited you to join "
                    f"**{captain['team_name']}** as a **{role}**.\n\n"
                    f"Use `/team accept` in the server to join.\n"
                    f"Invite expires in {INVITE_TTL_HOURS} hours."
                ),
                color=0x3A86FF,
            )
            await user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"✅ Invite sent to {user.mention} as **{role}**.", ephemeral=True
        )

        await admin_log.log(interaction.client, Event.TEAM_INVITE_SENT, user=interaction.user, fields={
            "Team": captain["team_name"],
            "Invitee": f"<@{invitee_id}>",
            "Role": role,
            "Expires": f"{INVITE_TTL_HOURS}h",
        })

    # ── /team accept ─────────────────────────────────────────────────────

    @team.command(name="accept", description="Accept a pending team invite")
    async def team_accept(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        invite = await get_any_pending_invite(discord_id)
        if not invite:
            await interaction.followup.send("❌ You have no pending team invites.", ephemeral=True)
            return

        player = await get_player_by_discord_id(discord_id)
        if not player:
            await interaction.followup.send(
                "❌ You must register first with `/player register`.", ephemeral=True
            )
            return

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
                        discord_id, player["id"], invite["sp_team_id"], invite["role"],
                        invite["role"],
                    ),
                )
                await cur.execute(
                    "UPDATE mlbb_team_invites SET status='accepted' WHERE id=%s",
                    (invite["id"],),
                )

        # Sync all active team associations to SportsPress (accumulate, don't overwrite)
        try:
            all_team_ids = await get_player_active_team_ids(discord_id)
            await get_api().set_player_teams(player["id"], all_team_ids)
        except Exception as e:
            logger.warning("Could not sync sp_team on player %s: %s", player["id"], e)

        try:
            await sync_team_roster_list(invite["sp_team_id"])
        except Exception as e:
            logger.warning("Could not sync roster list for team %s: %s", invite["sp_team_id"], e)

        embed = discord.Embed(
            title=f"✅ Joined {invite['team_name']}",
            description=f"You are now a **{invite['role']}** on **{invite['team_name']}**.",
            color=0x2ECC71,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(interaction.client, Event.TEAM_INVITE_ACCEPTED, user=interaction.user, fields={
            "Team": invite["team_name"],
            "Player": f"<@{discord_id}>",
            "Role": invite["role"],
        })

    # ── /team kick ────────────────────────────────────────────────────────

    @team.command(name="kick", description="Remove a player from your team")
    @app_commands.describe(
        user="The player to remove",
        team_id="Your team ID — required if you captain multiple teams",
    )
    async def team_kick(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        team_id: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        target_id = str(user.id)

        if target_id == discord_id:
            await interaction.followup.send(
                "❌ You can't kick yourself. Contact an admin to transfer captaincy.",
                ephemeral=True,
            )
            return

        captain_teams = await get_captain_teams(discord_id)
        if not captain_teams:
            await interaction.followup.send("❌ You are not a captain.", ephemeral=True)
            return

        if team_id is not None:
            captain = next((t for t in captain_teams if t["sp_team_id"] == team_id), None)
            if not captain:
                await interaction.followup.send(
                    f"❌ You are not the captain of team `{team_id}`.", ephemeral=True
                )
                return
        elif len(captain_teams) == 1:
            captain = captain_teams[0]
        else:
            lines = "\n".join(f"`{t['sp_team_id']}` — **{t['team_name']}**" for t in captain_teams)
            await interaction.followup.send(
                f"❌ You captain multiple teams. Re-run with `team_id`:\n{lines}", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE mlbb_player_roster
                    SET status='inactive'
                    WHERE discord_id=%s AND sp_team_id=%s AND status='active' AND role != 'captain'
                    """,
                    (target_id, captain["sp_team_id"]),
                )
                affected = cur.rowcount

        if not affected:
            await interaction.followup.send(
                f"❌ {user.mention} is not an active non-captain player on your team.",
                ephemeral=True,
            )
            return

        # Update SportsPress: remove this team but keep any other active team associations
        kicked_player = await get_player_by_discord_id(target_id)
        if kicked_player:
            try:
                remaining_ids = await get_player_active_team_ids(target_id)
                await get_api().set_player_teams(kicked_player["id"], remaining_ids)
            except Exception as e:
                logger.warning("Could not update sp_team on player %s: %s", kicked_player["id"], e)

        try:
            await sync_team_roster_list(captain["sp_team_id"])
        except Exception as e:
            logger.warning("Could not sync roster list for team %s: %s", captain["sp_team_id"], e)

        try:
            await user.send(
                f"You have been removed from **{captain['team_name']}** by the team captain."
            )
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"✅ {user.mention} removed from **{captain['team_name']}**.", ephemeral=True
        )

        await admin_log.log(interaction.client, Event.PLAYER_KICKED, user=interaction.user, fields={
            "Team": captain["team_name"],
            "Kicked": f"<@{target_id}>",
            "Captain": f"<@{discord_id}>",
        })

    # ── /team roster ──────────────────────────────────────────────────────

    @team.command(name="roster", description="View a team's roster")
    @app_commands.describe(team_id="Team post ID (leave blank to view your own team)")
    async def team_roster(self, interaction: discord.Interaction, team_id: int = None):
        await interaction.response.defer(ephemeral=True)

        if team_id is None:
            entry = await get_player_roster_entry(str(interaction.user.id))
            if not entry:
                await interaction.followup.send(
                    "❌ You're not on a team. Provide a `team_id` to look up any team.",
                    ephemeral=True,
                )
                return
            team_id = entry["sp_team_id"]

        roster = await get_roster(team_id)
        if not roster:
            await interaction.followup.send(
                f"No roster found for team ID `{team_id}`.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT post_title FROM wp_posts WHERE ID=%s", (team_id,)
                )
                row = await cur.fetchone()
        team_name = row[0] if row else f"Team {team_id}"

        icons = {"captain": "👑", "player": "🎮", "substitute": "🔄"}
        lines = [
            f"{icons.get(p['role'], '•')} **{p['ign']}** — {p['role'].capitalize()}"
            for p in roster
        ]
        embed = discord.Embed(
            title=f"📋 {team_name} — Roster ({len(roster)})",
            description="\n".join(lines),
            color=0x3A86FF,
        )
        team_url = await get_team_url(team_id, config.WP_URL)
        embed.set_footer(text=f"Team ID: {team_id} · {team_url}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /team edit ────────────────────────────────────────────────────────

    @team.command(name="edit", description="Update your team's logo and/or colors (captain only)")
    @app_commands.describe(
        picture="Team logo image (PNG/JPG)",
        color1="Primary color as hex code (e.g. #FF0000)",
        color2="Secondary color as hex code (e.g. #0000FF)",
    )
    async def team_edit(
        self,
        interaction: discord.Interaction,
        picture: discord.Attachment = None,
        color1: str = None,
        color2: str = None,
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        captain = await get_captain_team(discord_id)
        if not captain and not config.has_admin_role([r.name for r in interaction.user.roles]):
            await interaction.followup.send(
                "❌ Only team captains can edit team settings.", ephemeral=True
            )
            return

        if not captain:
            await interaction.followup.send(
                "❌ You are not a captain of any team.", ephemeral=True
            )
            return

        if not picture and not color1 and not color2:
            await interaction.followup.send(
                "Provide at least one of: `picture`, `color1`, `color2`.", ephemeral=True
            )
            return

        # Validate and normalise hex colors
        def normalise_color(raw: str) -> str | None:
            raw = raw.strip().lstrip("#")
            if len(raw) == 3:
                raw = "".join(c * 2 for c in raw)
            if len(raw) == 6 and all(c in "0123456789abcdefABCDEF" for c in raw):
                return f"#{raw.upper()}"
            return None

        color1_hex = normalise_color(color1) if color1 else None
        color2_hex = normalise_color(color2) if color2 else None

        if color1 and color1_hex is None:
            await interaction.followup.send(
                f"❌ Invalid color1 `{color1}` — use a hex code like `#FF0000`.", ephemeral=True
            )
            return
        if color2 and color2_hex is None:
            await interaction.followup.send(
                f"❌ Invalid color2 `{color2}` — use a hex code like `#0000FF`.", ephemeral=True
            )
            return

        api = get_api()
        results = []

        # ── Upload picture ─────────────────────────────────────────────────
        if picture:
            allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
            mime = picture.content_type or "image/png"
            if mime not in allowed_types:
                await interaction.followup.send(
                    f"❌ Unsupported file type `{mime}`. Use PNG, JPG, GIF, or WebP.", ephemeral=True
                )
                return

            try:
                image_bytes = await picture.read()
            except Exception as e:
                await interaction.followup.send(f"❌ Could not read attachment: {e}", ephemeral=True)
                return

            ext = picture.filename.rsplit(".", 1)[-1].lower() if "." in picture.filename else "png"
            safe_name = f"team-{captain['sp_team_id']}-logo.{ext}"

            try:
                media = await api.upload_media(image_bytes, safe_name, mime)
                media_id = media["id"]
                await api.set_team_featured_image(captain["sp_team_id"], media_id)
                results.append(f"🖼️ Logo updated (media ID `{media_id}`)")
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to upload logo: {e}", ephemeral=True)
                return

        # ── Set colors ─────────────────────────────────────────────────────
        if color1_hex or color2_hex:
            try:
                await set_team_colors(
                    captain["sp_team_id"],
                    color_primary=color1_hex,
                    color_secondary=color2_hex,
                )
                if color1_hex:
                    results.append(f"🎨 Primary color set to `{color1_hex}`")
                if color2_hex:
                    results.append(f"🎨 Secondary color set to `{color2_hex}`")
            except Exception as e:
                await interaction.followup.send(f"❌ Failed to update colors: {e}", ephemeral=True)
                return

        embed = discord.Embed(
            title=f"✅ {captain['team_name']} Updated",
            description="\n".join(results),
            color=discord.Color.from_str(color1_hex) if color1_hex else 0x2ECC71,
        )
        team_url = await get_team_url(captain["sp_team_id"], config.WP_URL)
        embed.set_footer(text=f"View team: {team_url}")
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(interaction.client, Event.SYSTEM, user=interaction.user, fields={
            "Action": "Team edited",
            "Team": captain["team_name"],
            "Changes": ", ".join(results),
        })

    # ── /team delete ──────────────────────────────────────────────────────

    @team.command(name="delete", description="Delete a team (captain: own team; admin: any team by ID)")
    @app_commands.describe(team_id="Team post ID — admins only, leave blank to delete your own team")
    async def team_delete(self, interaction: discord.Interaction, team_id: int = None):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        is_admin = config.has_admin_role([r.name for r in interaction.user.roles])

        if team_id is not None and not is_admin:
            await interaction.followup.send(
                "❌ Only admins can delete a team by ID. "
                "Captains can only delete their own team (omit `team_id`).",
                ephemeral=True,
            )
            return

        if team_id is None:
            captain = await get_captain_team(discord_id)
            if not captain:
                await interaction.followup.send(
                    "❌ You are not a captain of any team.", ephemeral=True
                )
                return
            team_id = captain["sp_team_id"]
            team_name = captain["team_name"]
        else:
            from services.db_helpers import get_team_by_id
            team = await get_team_by_id(team_id)
            if not team:
                await interaction.followup.send(
                    f"❌ No team found with ID `{team_id}`.", ephemeral=True
                )
                return
            team_name = team["title"]

        # Fetch all active players so we can clear their SP team links
        roster = await get_roster(team_id)

        # Clear sp_team on all players in SportsPress
        api = get_api()
        for member in roster:
            try:
                await api.set_player_teams(member["sp_player_id"], [])
            except Exception as e:
                logger.warning("Could not clear sp_team on player %s: %s", member["sp_player_id"], e)

        # Clean up custom tables
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE mlbb_player_roster SET status='inactive' WHERE sp_team_id=%s",
                    (team_id,),
                )
                await cur.execute(
                    "UPDATE mlbb_team_invites SET status='expired' WHERE sp_team_id=%s AND status='pending'",
                    (team_id,),
                )
                await cur.execute(
                    """
                    UPDATE mlbb_team_registrations SET status='rejected'
                    WHERE sp_team_id=%s AND status='pending'
                    """,
                    (team_id,),
                )

        # Delete the SportsPress team post via REST API
        try:
            await api.delete_team(team_id)
        except Exception as e:
            await interaction.followup.send(
                f"⚠️ Roster cleaned up but failed to delete SP post: {e}", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Team **{team_name}** (`{team_id}`) has been deleted.", ephemeral=True
        )

        await admin_log.log(interaction.client, Event.SYSTEM, user=interaction.user, fields={
            "Action": "Team deleted",
            "Team": team_name,
            "Team ID": team_id,
            "Deleted by": f"<@{discord_id}>",
        })

    # ── /team list ────────────────────────────────────────────────────────

    @team.command(name="list", description="List your teams and roles")
    async def team_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        try:
            teams = await get_my_teams(discord_id)
        except Exception as e:
            await interaction.followup.send(f"❌ DB error: {e}", ephemeral=True)
            return

        if not teams:
            await interaction.followup.send(
                "You are not on any teams. Use `/team create` to start one.", ephemeral=True
            )
            return

        role_emoji = {"captain": "👑", "player": "🎮", "substitute": "🔄"}
        lines = [
            f"{role_emoji.get(t['role'], '•')} **{t['team_name']}** — {t['role'].capitalize()} "
            f"(`{t['sp_team_id']}`)"
            for t in teams
        ]
        embed = discord.Embed(
            title=f"Your Teams ({len(teams)})",
            description="\n".join(lines),
            color=0x3A86FF,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
