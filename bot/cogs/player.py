"""
bot/cogs/player.py — /player register, /player profile
"""
import logging
import discord
from discord import app_commands
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from services import db
from services.db_helpers import get_player_by_discord_id, get_roster
from services.sportspress import SportsPressAPI

logger = logging.getLogger(__name__)


def get_api():
    return SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)


class Player(commands.Cog):
    """Player registration and profiles"""

    player = app_commands.Group(name="player", description="Player registration and profiles")

    @player.command(name="register", description="Link your Discord account to an MLBB player profile")
    @app_commands.describe(ign="Your in-game name (IGN)")
    async def player_register(self, interaction: discord.Interaction, ign: str):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # Check already registered
        existing = await get_player_by_discord_id(discord_id)
        if existing:
            await interaction.followup.send(
                f"You're already registered as **{existing['title']}** (ID: `{existing['id']}`).\n"
                f"Profile: {config.WP_URL}/player/{existing['title'].lower().replace(' ','-')}/",
                ephemeral=True,
            )
            return

        # Create sp_player post
        api = get_api()
        try:
            player = await api.create_player(ign, [])
        except Exception as e:
            logger.error("Failed to create sp_player for %s: %s", discord_id, e)
            await interaction.followup.send(f"❌ Failed to create player profile: {e}", ephemeral=True)
            return

        sp_player_id = player["id"]

        # Write Discord ID into sp_metrics via REST
        try:
            await api.set_player_discord(
                sp_player_id,
                discord_id=discord_id,
                discord_username=str(interaction.user),
            )
        except Exception as e:
            logger.warning("Could not set sp_metrics for player %s: %s", sp_player_id, e)

        embed = discord.Embed(
            title="✅ Player Registered",
            description=f"Welcome to the league, **{ign}**!",
            color=0x2ECC71,
        )
        embed.add_field(name="IGN", value=ign)
        embed.add_field(name="Player ID", value=sp_player_id)
        embed.add_field(
            name="Profile",
            value=f"{config.WP_URL}/?p={sp_player_id}",
            inline=False,
        )
        embed.set_footer(text="Use /team create to start a team, or ask a captain to invite you.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @player.command(name="profile", description="View a player profile")
    @app_commands.describe(user="Discord user (leave blank for yourself)")
    async def player_profile(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)
        target = user or interaction.user
        discord_id = str(target.id)

        player = await get_player_by_discord_id(discord_id)
        if not player:
            msg = (
                "You are not registered yet. Use `/player register` to create your profile."
                if target == interaction.user
                else f"{target.mention} has not registered yet."
            )
            await interaction.followup.send(msg, ephemeral=True)
            return

        # Fetch teams
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

        embed = discord.Embed(
            title=f"🎮 {player['title']}",
            color=0x3A86FF,
        )
        embed.set_author(name=str(target), icon_url=target.display_avatar.url)

        if team_rows:
            team_lines = [f"**{row[2]}** — {row[1].capitalize()}" for row in team_rows]
            embed.add_field(name="Teams", value="\n".join(team_lines), inline=False)
        else:
            embed.add_field(name="Teams", value="No team yet", inline=False)

        embed.add_field(
            name="Profile Page",
            value=f"{config.WP_URL}/?p={player['id']}",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Player(bot))
