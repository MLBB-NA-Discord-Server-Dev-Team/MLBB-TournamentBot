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
from services.db_helpers import (
    list_tournaments as db_list_tournaments,
    get_captain_team, get_captain_teams, check_team_has_event_on_date,
)
from services import db

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
        date="Match date (YYYY-MM-DD)",
        league_id="League term ID (assigns league taxonomy)",
        season_id="Season term ID (assigns season taxonomy)",
    )
    @organizer_check()
    async def tournament_add_event(
        self,
        interaction: discord.Interaction,
        name: str,
        home_team_id: int,
        away_team_id: int,
        date: str,
        league_id: int = None,
        season_id: int = None,
    ):
        await interaction.response.defer(ephemeral=True)

        # Check neither team already has an event on this date
        for team_id, label in [(home_team_id, "Home team"), (away_team_id, "Away team")]:
            conflict = await check_team_has_event_on_date(team_id, date)
            if conflict:
                await interaction.followup.send(
                    f"❌ {label} (`{team_id}`) already has a match on **{date}**: "
                    f"*{conflict['title']}* (ID `{conflict['id']}`). "
                    f"A team cannot play more than one match per day.",
                    ephemeral=True,
                )
                return

        league_ids = [league_id] if league_id else None
        season_ids = [season_id] if season_id else None

        try:
            event = await get_api().create_event(
                name, home_team_id, away_team_id, date,
                league_ids=league_ids, season_ids=season_ids,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create event: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="✅ Event Created", color=0x2ECC71)
        embed.add_field(name="Name", value=event['title']['rendered'])
        embed.add_field(name="ID", value=event['id'])
        embed.add_field(name="Date", value=date)
        if league_id:
            embed.add_field(name="League", value=str(league_id))
        embed.add_field(name="URL", value=event.get('link', ''), inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)


    @tournament.command(name="register", description="Register your team for a tournament")
    @app_commands.describe(
        tournament_id="Tournament post ID (use /tournament list to find it)",
        team_id="Your team ID — required if you captain multiple teams",
    )
    async def tournament_register(
        self, interaction: discord.Interaction, tournament_id: int, team_id: int = None
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        captain_teams = await get_captain_teams(discord_id)
        if not captain_teams:
            await interaction.followup.send(
                "❌ Only team captains can register a team. "
                "Create a team first with `/team create`.", ephemeral=True
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
            await interaction.followup.send(
                f"❌ Registration is not currently open for tournament `{tournament_id}`.\n"
                "Ask an organizer to open registrations with `/league-admin open-registration`.",
                ephemeral=True,
            )
            return

        period_id, max_teams = period

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM mlbb_team_registrations WHERE period_id=%s AND sp_team_id=%s",
                    (period_id, captain["sp_team_id"]),
                )
                if await cur.fetchone():
                    await interaction.followup.send(
                        f"❌ **{captain['team_name']}** is already registered for this tournament.",
                        ephemeral=True,
                    )
                    return

                # Per-tournament conflict: no player may be on two teams in the same tournament
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
                    await interaction.followup.send(
                        f"❌ A player on **{captain['team_name']}** is already rostered on "
                        f"**{conflict[1]}** in this tournament. A player may only play for one team per tournament.",
                        ephemeral=True,
                    )
                    return

                if max_teams:
                    await cur.execute(
                        "SELECT COUNT(*) FROM mlbb_team_registrations WHERE period_id=%s AND status!='rejected'",
                        (period_id,),
                    )
                    count = (await cur.fetchone())[0]
                    if count >= max_teams:
                        await interaction.followup.send(
                            f"❌ This tournament is full ({max_teams} teams). Contact an organizer.",
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
            description=f"**{captain['team_name']}** has been registered for tournament `{tournament_id}`.",
            color=0x9D4EDD,
        )
        embed.set_footer(text="An organizer will review and approve your registration.")
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
                "`/team edit` — Set team logo and/or colors *(captain only)*\n"
                "`/team delete` — Disband your team *(captain or admin)*\n"
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
                "`/tournament register [tournament_id]` — Sign your team up *(captain only)*\n"
                "`/tournament create [name]` — Create a tournament 🔒\n"
                "`/tournament help` — Show this message"
            ),
            inline=False,
        )

        embed.add_field(
            name="📊 League",
            value=(
                "`/league list` — List all leagues\n"
                "`/league register [league_id]` — Sign your team up *(captain only)*\n"
                "`/league standings [name]` — View standings\n"
                "`/league create [name]` — Create a league 🔒"
            ),
            inline=False,
        )

        if is_staff:
            embed.add_field(
                name="🔒 Admin",
                value=(
                    "`/league-admin set-season` — Configure season dates + auto-schedule registration\n"
                    "`/league-admin open-registration` — Manually open a registration window\n"
                    "`/league-admin close-registration` — Close a registration window\n"
                    "`/league-admin registrations` — List team sign-ups\n"
                    "`/league-admin approve-registration [#id]` — Approve a team sign-up\n"
                    "`/league-admin pending` — List pending match submissions\n"
                    "`/league-admin resolve-dispute [#id] [winner_team_id]` — Override a disputed result\n"
                    "`/tournament create/delete` — Manage tournament posts\n"
                    "`/league create/delete` — Manage league posts"
                ),
                inline=False,
            )

        embed.set_footer(text="play.mlbb.site · Results, standings, and brackets posted automatically.")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tournaments(bot))
