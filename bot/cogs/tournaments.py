"""
Tournaments Cog
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


class Tournaments(commands.Cog):
    """Manage SportsPress tournaments"""

    tournament = app_commands.Group(name="tournament", description="Manage tournaments")

    @tournament.command(name="list", description="List all tournaments")
    @organizer_check()
    async def tournament_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            items = await get_api().list_tournaments()
        except Exception as e:
            await interaction.followup.send(f"❌ API error: {e}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send("No tournaments found.", ephemeral=True)
            return

        lines = [f"**{t['id']}** — {t['title']['rendered']}" for t in items]
        embed = discord.Embed(title=f"Tournaments ({len(items)})", description="\n".join(lines), color=0x9D4EDD)
        embed.set_footer(text=config.WP_URL)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tournament.command(name="create", description="Create a new tournament")
    @app_commands.describe(name="Tournament name", description="Optional description")
    @organizer_check()
    async def tournament_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            item = await get_api().create_tournament(name, description)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create tournament: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ Tournament Created", color=0x2ECC71)
        embed.add_field(name="Name", value=item['title']['rendered'])
        embed.add_field(name="ID", value=item['id'])
        embed.add_field(name="URL", value=item.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @tournament.command(name="delete", description="Delete a tournament by ID")
    @app_commands.describe(tournament_id="The tournament post ID")
    @organizer_check()
    async def tournament_delete(self, interaction: discord.Interaction, tournament_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await get_api().delete_tournament(tournament_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ Tournament `{tournament_id}` deleted.", ephemeral=True)

    @tournament.command(name="add-event", description="Add a match event to a tournament")
    @app_commands.describe(
        name="Event/match name",
        home_team_id="Home team post ID",
        away_team_id="Away team post ID",
        date="Match date (YYYY-MM-DD)"
    )
    @organizer_check()
    async def tournament_add_event(
        self,
        interaction: discord.Interaction,
        name: str,
        home_team_id: int,
        away_team_id: int,
        date: str
    ):
        await interaction.response.defer(ephemeral=True)
        try:
            event = await get_api().create_event(name, home_team_id, away_team_id, date)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create event: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ Event Created", color=0x2ECC71)
        embed.add_field(name="Name", value=event['title']['rendered'])
        embed.add_field(name="ID", value=event['id'])
        embed.add_field(name="Date", value=date)
        embed.add_field(name="URL", value=event.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))
