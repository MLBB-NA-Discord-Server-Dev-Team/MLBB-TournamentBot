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
from services.db_helpers import list_tables
from services import db
from services.db_helpers import get_captain_team

logger = logging.getLogger(__name__)


def organizer_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if config.has_organizer_role([r.name for r in interaction.user.roles]):
            return True
        await interaction.response.send_message(
            "❌ You need the **Tournament Organizer** role to use this command.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def get_api():
    return SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)


class Leagues(commands.Cog):
    """Manage SportsPress league standings tables"""

    league = app_commands.Group(name="league", description="Manage league tables")

    @league.command(name="list", description="List all league standing tables")
    async def league_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            items = await list_tables()
        except Exception as e:
            await interaction.followup.send(f"❌ DB error: {e}", ephemeral=True)
            return

        if not items:
            await interaction.followup.send("No leagues found.", ephemeral=True)
            return

        lines = [f"`{t['id']}` — **{t['title']}**" for t in items]
        embed = discord.Embed(title=f"Leagues ({len(items)})", description="\n".join(lines), color=0xFFB703)
        embed.set_footer(text=config.WP_URL)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="create", description="Create a new league standings table")
    @app_commands.describe(name="League name", description="Optional description")
    @organizer_check()
    async def league_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        await interaction.response.defer(ephemeral=True)
        try:
            item = await get_api().create_table(name, description)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create league: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ League Created", color=0x2ECC71)
        embed.add_field(name="Name", value=item['title']['rendered'])
        embed.add_field(name="ID", value=item['id'])
        embed.add_field(name="URL", value=item.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="delete", description="Delete a league standings table by ID")
    @app_commands.describe(league_id="The table post ID")
    @organizer_check()
    async def league_delete(self, interaction: discord.Interaction, league_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await get_api().delete_table(league_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ League `{league_id}` deleted.", ephemeral=True)

    @league.command(name="register", description="Register your team for a league")
    @app_commands.describe(league_id="League post ID (use /league list to find it)")
    async def league_register(self, interaction: discord.Interaction, league_id: int):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send(
                "❌ Only team captains can register a team. "
                "Create a team first with `/team create`.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                # Find an open registration period for this league
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
            await interaction.followup.send(
                f"❌ Registration is not currently open for league `{league_id}`.\n"
                "Ask an organizer to open registrations with `/admin open-registration`.",
                ephemeral=True,
            )
            return

        period_id, max_teams = period

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                # Check already registered
                await cur.execute(
                    "SELECT id FROM mlbb_team_registrations WHERE period_id=%s AND sp_team_id=%s",
                    (period_id, captain["sp_team_id"]),
                )
                if await cur.fetchone():
                    await interaction.followup.send(
                        f"❌ **{captain['team_name']}** is already registered for this league.",
                        ephemeral=True,
                    )
                    return

                # Enforce max_teams cap
                if max_teams:
                    await cur.execute(
                        "SELECT COUNT(*) FROM mlbb_team_registrations WHERE period_id=%s AND status!='rejected'",
                        (period_id,),
                    )
                    count = (await cur.fetchone())[0]
                    if count >= max_teams:
                        await interaction.followup.send(
                            f"❌ This league is full ({max_teams} teams). Contact an organizer.",
                            ephemeral=True,
                        )
                        return

                await cur.execute(
                    """
                    INSERT INTO mlbb_team_registrations
                        (period_id, sp_team_id, registered_by, status)
                    VALUES (%s, %s, %s, 'pending')
                    """,
                    (period_id, captain["sp_team_id"], discord_id),
                )

        embed = discord.Embed(
            title="📋 Registration Submitted",
            description=f"**{captain['team_name']}** has been registered for league `{league_id}`.",
            color=0x3A86FF,
        )
        embed.set_footer(text="An organizer will review and approve your registration.")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leagues(bot))
