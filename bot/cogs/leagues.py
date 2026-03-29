"""
Leagues Cog
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


class Leagues(commands.Cog):
    """Manage SportsPress league tables"""

    league = app_commands.Group(name="league", description="Manage league tables")

    @league.command(name="list", description="List all leagues")
    @organizer_check()
    async def league_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            items = await get_api().list_leagues()
        except Exception as e:
            await interaction.followup.send(f"❌ API error: {e}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send("No leagues found.", ephemeral=True)
            return

        lines = [f"**{t['id']}** — {t['title']['rendered']}" for t in items]
        embed = discord.Embed(title=f"Leagues ({len(items)})", description="\n".join(lines), color=0xFFB703)
        embed.set_footer(text=config.WP_URL)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="create", description="Create a new league table")
    @app_commands.describe(name="League name", description="Optional description")
    @organizer_check()
    async def league_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            item = await get_api().create_league(name, description)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create league: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ League Created", color=0x2ECC71)
        embed.add_field(name="Name", value=item['title']['rendered'])
        embed.add_field(name="ID", value=item['id'])
        embed.add_field(name="URL", value=item.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="delete", description="Delete a league by ID")
    @app_commands.describe(league_id="The league post ID")
    @organizer_check()
    async def league_delete(self, interaction: discord.Interaction, league_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await get_api().delete_league(league_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ League `{league_id}` deleted.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leagues(bot))
