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
    get_player_roster_entry,
    get_roster,
    get_any_pending_invite,
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

        existing_captain = await get_captain_team(discord_id)
        if existing_captain:
            await interaction.followup.send(
                f"❌ You are already the captain of **{existing_captain['team_name']}**. "
                "A player can only captain one team.",
                ephemeral=True,
            )
            return

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

        embed = discord.Embed(title="✅ Team Created", color=0x2ECC71)
        embed.add_field(name="Team", value=name)
        embed.add_field(name="Team ID", value=sp_team_id)
        embed.add_field(name="Your Role", value="Captain")
        embed.set_footer(text="Use /team invite to add players.")
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(self.bot, Event.TEAM_CREATED, user=interaction.user, fields={
            "Team": name,
            "Team ID": sp_team_id,
            "Captain": f"<@{discord_id}>",
        })

    # ── /team invite ──────────────────────────────────────────────────────

    @team.command(name="invite", description="Invite a registered player to your team")
    @app_commands.describe(user="Discord member to invite", role="Player role on the team")
    @app_commands.choices(role=[
        app_commands.Choice(name="Player", value="player"),
        app_commands.Choice(name="Substitute", value="substitute"),
    ])
    async def team_invite(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        role: str = "player",
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        invitee_id = str(user.id)

        if invitee_id == discord_id:
            await interaction.followup.send("❌ You can't invite yourself.", ephemeral=True)
            return

        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send(
                "❌ You are not a captain. Only captains can invite players.", ephemeral=True
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

        await admin_log.log(self.bot, Event.TEAM_INVITE_SENT, user=interaction.user, fields={
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

        embed = discord.Embed(
            title=f"✅ Joined {invite['team_name']}",
            description=f"You are now a **{invite['role']}** on **{invite['team_name']}**.",
            color=0x2ECC71,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(self.bot, Event.TEAM_INVITE_ACCEPTED, user=interaction.user, fields={
            "Team": invite["team_name"],
            "Player": f"<@{discord_id}>",
            "Role": invite["role"],
        })

    # ── /team kick ────────────────────────────────────────────────────────

    @team.command(name="kick", description="Remove a player from your team")
    @app_commands.describe(user="The player to remove")
    async def team_kick(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        target_id = str(user.id)

        if target_id == discord_id:
            await interaction.followup.send(
                "❌ You can't kick yourself. Contact an admin to transfer captaincy.",
                ephemeral=True,
            )
            return

        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send("❌ You are not a captain.", ephemeral=True)
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

        try:
            await user.send(
                f"You have been removed from **{captain['team_name']}** by the team captain."
            )
        except discord.Forbidden:
            pass

        await interaction.followup.send(
            f"✅ {user.mention} removed from **{captain['team_name']}**.", ephemeral=True
        )

        await admin_log.log(self.bot, Event.PLAYER_KICKED, user=interaction.user, fields={
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
        embed.set_footer(text=f"Team ID: {team_id} · {config.WP_URL}/?p={team_id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ── /team list ────────────────────────────────────────────────────────

    @team.command(name="list", description="List all teams")
    async def team_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            teams = await get_api().list_teams()
        except Exception as e:
            await interaction.followup.send(f"❌ API error: {e}", ephemeral=True)
            return

        if not teams:
            await interaction.followup.send("No teams found.", ephemeral=True)
            return

        lines = [f"`{t['id']}` — **{t['title']['rendered']}**" for t in teams[:25]]
        embed = discord.Embed(
            title=f"Teams ({len(teams)})",
            description="\n".join(lines),
            color=0x3A86FF,
        )
        if len(teams) > 25:
            embed.set_footer(text=f"Showing 25 of {len(teams)} teams")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
