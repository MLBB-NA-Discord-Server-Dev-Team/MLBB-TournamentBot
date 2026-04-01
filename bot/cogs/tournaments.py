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
from services.db_helpers import list_tournaments as db_list_tournaments

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
            items = await db_list_tournaments()
        except Exception as e:
            await interaction.followup.send(f"❌ DB error: {e}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send("No tournaments found.", ephemeral=True)
            return

        lines = [f"`{t['id']}` — **{t['title']}**" for t in items]
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


    @tournament.command(name="help", description="List all available bot commands")
    async def tournament_help(self, interaction: discord.Interaction):
        is_staff = config.has_staff_role([r.name for r in interaction.user.roles])

        embed = discord.Embed(
            title="🏆 PLAY.MLBB.SITE — Bot Commands",
            description="All commands are slash commands. Staff-only commands are marked 🔒.",
            color=0x3A86FF,
        )

        embed.add_field(
            name="👤 Player",
            value=(
                "`/player register [ign]` — Link your Discord to an MLBB player profile\n"
                "`/player profile [@user]` — View a player profile"
            ),
            inline=False,
        )

        embed.add_field(
            name="🛡️ Team",
            value=(
                "`/team create [name]` — Create a team (you become captain)\n"
                "`/team invite [@user]` — Invite a player *(captain only)*\n"
                "`/team accept` — Accept a pending team invite\n"
                "`/team kick [@user]` — Remove a player *(captain only)*\n"
                "`/team roster [team_id]` — View a team's roster\n"
                "`/team list` — List all teams"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚔️ Match Results",
            value=(
                "`/match submit [screenshot]` — Submit a win with scoreboard screenshot *(captain only)*\n"
                "`/match confirm [#id]` — Confirm an opposing team's result *(captain only)*\n"
                "`/match dispute [#id] [reason]` — Dispute a result *(captain only)*"
            ),
            inline=False,
        )

        embed.add_field(
            name="🎯 Pick-up Tournaments",
            value=(
                "`/pickup status` — View the pick-up pool and your queue position\n"
                "`/pickup join` — Join the rolling pick-up tournament pool *(captain only — coming soon)*\n"
                "`/pickup leave` — Leave the pool *(coming soon)*\n"
                "`/pickup bracket [#n]` — View a pick-up cup bracket *(coming soon)*"
            ),
            inline=False,
        )

        embed.add_field(
            name="🏆 Tournament",
            value=(
                "`/tournament list` — List all tournaments\n"
                "`/tournament create [name]` — Create a tournament 🔒\n"
                "`/tournament info [name]` — Tournament details\n"
                "`/tournament help` — Show this message"
            ),
            inline=False,
        )

        embed.add_field(
            name="📊 League",
            value=(
                "`/league list` — List all leagues\n"
                "`/league standings [name]` — View standings\n"
                "`/league create [name]` — Create a league 🔒"
            ),
            inline=False,
        )

        if is_staff:
            embed.add_field(
                name="🔒 Admin",
                value=(
                    "`/admin pending` — List pending match submissions\n"
                    "`/admin resolve-dispute [#id] [winner_team_id]` — Override a disputed result\n"
                    "`/tournament create/delete` — Manage tournament posts\n"
                    "`/league create/delete` — Manage league posts"
                ),
                inline=False,
            )

        embed.set_footer(text="play.mlbb.site · Results, standings, and brackets posted automatically.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))
