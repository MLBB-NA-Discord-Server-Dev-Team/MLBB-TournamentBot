"""
Teams Cog
"""
import logging
import discord
from discord import app_commands
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from services.sportspress import SportsPressAPI

logger = logging.getLogger(__name__)


def organizer_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        member_roles = [r.name for r in interaction.user.roles]
        allowed = config.ORGANIZER_ROLES + config.ADMIN_ROLES
        if not any(r in member_roles for r in allowed):
            await interaction.response.send_message(
                "❌ You need the **Tournament Organizer** role to use this command.", ephemeral=True
            )
            return False
        return True
    return app_commands.check(predicate)


def get_api():
    return SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)


class Teams(commands.Cog):
    """Manage SportsPress teams"""

    team = app_commands.Group(name="team", description="Manage tournament teams")

    @team.command(name="list", description="List all teams")
    @organizer_check()
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

        lines = [f"**{t['id']}** — {t['title']['rendered']}" for t in teams]
        embed = discord.Embed(title=f"Teams ({len(teams)})", description="\n".join(lines), color=0x3A86FF)
        embed.set_footer(text=config.WP_URL)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @team.command(name="create", description="Create a new team")
    @app_commands.describe(name="Team name", description="Optional description")
    @organizer_check()
    async def team_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            team = await get_api().create_team(name, description)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create team: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ Team Created", color=0x2ECC71)
        embed.add_field(name="Name", value=team['title']['rendered'])
        embed.add_field(name="ID", value=team['id'])
        embed.add_field(name="URL", value=team.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @team.command(name="delete", description="Delete a team by ID")
    @app_commands.describe(team_id="The team post ID")
    @organizer_check()
    async def team_delete(self, interaction: discord.Interaction, team_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await get_api().delete_team(team_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete team: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Team `{team_id}` deleted.", ephemeral=True)

    @team.command(name="add-player", description="Add a player to a team")
    @app_commands.describe(
        player_name="Player name (IGN)",
        team_id="Team post ID to assign the player to"
    )
    @organizer_check()
    async def team_add_player(self, interaction: discord.Interaction, player_name: str, team_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            player = await get_api().create_player(player_name, [team_id])
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to add player: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ Player Added", color=0x2ECC71)
        embed.add_field(name="Player", value=player['title']['rendered'])
        embed.add_field(name="Team ID", value=team_id)
        embed.add_field(name="Player ID", value=player['id'])
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Teams(bot))
