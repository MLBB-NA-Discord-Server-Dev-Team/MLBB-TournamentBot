"""
Leagues Cog
"""
import logging
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from services.sportspress import SportsPressAPI
from services import db
from services.db_helpers import (
    list_leagues, get_captain_team, get_captain_teams,
    set_league_termmeta, get_current_season, get_existing_table_for_league,
)

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


def admin_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if config.has_admin_role([r.name for r in interaction.user.roles]):
            return True
        await interaction.response.send_message(
            "❌ You need an **Admin** role to use this command.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def get_api():
    return SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)


class Leagues(commands.Cog):
    """Manage SportsPress league standings tables"""

    league = app_commands.Group(name="league", description="Manage league tables")

    @league.command(name="list", description="List active and upcoming leagues")
    @app_commands.describe(search="Filter by league name")
    async def league_list(self, interaction: discord.Interaction, search: str = None):
        await interaction.response.defer(ephemeral=True)
        try:
            items, total = await list_leagues(search=search)
        except Exception as e:
            await interaction.followup.send(f"❌ DB error: {e}", ephemeral=True)
            return

        if not items:
            msg = "No leagues found." if search else "No leagues are currently configured."
            await interaction.followup.send(msg, ephemeral=True)
            return

        status_emoji = {"open": "🟢", "scheduled": "🕐", "closed": "🔴"}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lines = []
        for t in items:
            closes = t.get("closes_at")
            if closes:
                days_left = (closes - now).days
                deadline = f" — closes {closes.strftime('%b %d')} ({days_left}d)"
            else:
                deadline = ""
            rule_tag = f" `{t['rule']}`" if t.get("rule") else ""
            title = t['title'].replace(" \u2014 ", " - ")
            lines.append(
                f"{status_emoji.get(t['status'], '•')} **{title}**{rule_tag} (`{t['id']}`){deadline}"
            )
        title = f"Leagues ({total})" if not search else f"Leagues matching '{search}' ({total})"
        embed = discord.Embed(title=title, description="\n".join(lines), color=0xFFB703)
        footer = "Use /league register [league_id] to sign up"
        if total > 25:
            footer += f" · Showing 25 of {total} — use search to narrow results"
        embed.set_footer(text=footer)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="create", description="Create a new league and open registration")
    @app_commands.describe(
        name="League name (e.g. 'Moniyan League — Spring 2026')",
        rule="Match format",
        closes_in_days="Days until registration closes",
        opens_in_days="Days until registration opens (default 0 = immediately)",
        max_teams="Max teams allowed (leave blank for unlimited)",
        description="Optional description",
    )
    @app_commands.choices(rule=[
        app_commands.Choice(name="Draft Pick BO1", value="DPBO1"),
        app_commands.Choice(name="Draft Pick BO3", value="DPBO3"),
        app_commands.Choice(name="Draft Pick BO5", value="DPBO5"),
        app_commands.Choice(name="Brawl BO1", value="BrawlBO1"),
        app_commands.Choice(name="Brawl BO3", value="BrawlBO3"),
        app_commands.Choice(name="Brawl BO5", value="BrawlBO5"),
    ])
    @admin_check()
    async def league_create(
        self,
        interaction: discord.Interaction,
        name: str,
        rule: str,
        closes_in_days: int,
        opens_in_days: int = 0,
        max_teams: int = None,
        description: str = "",
    ):
        await interaction.response.defer(ephemeral=True)
        # Enforce 16-team hard cap for round-robin scheduling
        if max_teams is not None and max_teams > 16:
            max_teams = 16
        elif max_teams is None:
            max_teams = 16
        rule_labels = {
            "DPBO1": "Draft Pick · Best of 1", "DPBO3": "Draft Pick · Best of 3",
            "DPBO5": "Draft Pick · Best of 5", "BrawlBO1": "Brawl · Best of 1",
            "BrawlBO3": "Brawl · Best of 3", "BrawlBO5": "Brawl · Best of 5",
        }
        mode_labels = {
            "DPBO1": "5v5 Custom Room — Draft Pick",
            "DPBO3": "5v5 Custom Room — Draft Pick",
            "DPBO5": "5v5 Custom Room — Draft Pick",
            "BrawlBO1": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO3": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO5": "5v5 Brawl — Randomly Assigned Heroes",
        }
        wp_description = description or rule_labels.get(rule, rule)
        api = get_api()

        # 1. Create sp_league taxonomy term (idempotent — reuse if slug already exists)
        try:
            league_term = await api.create_league(name, wp_description)
            league_term_id = league_term['id']
        except Exception as e:
            body = str(e)
            if 'term_exists' in body:
                import re
                m = re.search(r'"term_id"\s*:\s*(\d+)', body)
                if m:
                    league_term_id = int(m.group(1))
                else:
                    await interaction.followup.send(f"❌ League already exists but could not recover term ID: {e}", ephemeral=True)
                    return
            else:
                await interaction.followup.send(f"❌ Failed to create league: {e}", ephemeral=True)
                return

        # 2. Get current season — create sp_table for it (idempotent — skip if already exists)
        season = await get_current_season()
        table_id = await get_existing_table_for_league(league_term_id)
        if not table_id and season:
            table_title = f"{name} — {season['season_name']}"
            try:
                table = await api.create_table(
                    table_title, wp_description,
                    league_ids=[league_term_id],
                    season_ids=[season['sp_season_id']],
                )
                table_id = table['id']
            except Exception as e:
                logger.warning("Could not create sp_table for custom league: %s", e)

        # 3. Store rule + custom flag as termmeta (permanent, authoritative)
        await set_league_termmeta(league_term_id, 'mlbb_rule', rule)
        await set_league_termmeta(league_term_id, 'mlbb_is_custom', '1')

        # 4. Create registration period (idempotent — skip if one already exists)
        entity_id = table_id or league_term_id
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM mlbb_registration_periods WHERE entity_type='league' AND entity_id=%s LIMIT 1",
                    (entity_id,),
                )
                if not await cur.fetchone():
                    await cur.execute(
                        """
                        INSERT INTO mlbb_registration_periods
                            (entity_type, entity_id, opens_at, closes_at, max_teams, rule, created_by, status)
                        VALUES (
                            'league', %s,
                            DATE_ADD(NOW(), INTERVAL %s DAY),
                            DATE_ADD(NOW(), INTERVAL %s DAY),
                            %s, %s, %s,
                            IF(%s = 0, 'open', 'scheduled')
                        )
                        """,
                        (entity_id, opens_in_days, closes_in_days, max_teams, rule,
                         str(interaction.user.id), opens_in_days),
                    )

        # 5. Create WP franchise page (idempotent — skip if slug already exists)
        league_slug = name.lower().replace(' ', '-').replace('—', '').replace('–', '').strip('-')
        fmt_label = rule_labels.get(rule, rule)
        mode_label = mode_labels.get(rule, rule)
        page_content = (
            '<hr/>'
            '<h2>League Rules</h2>'
            f'<table class="league-rules"><tbody>'
            f'<tr><th>Format</th><td>{fmt_label}</td></tr>'
            f'<tr><th>Mode</th><td>{mode_label}</td></tr>'
            f'</tbody></table>'
            '<p>See the <a href="/general-rules/">General Rules</a> for '
            'sportsmanship guidelines, disconnect policies, and scheduling definitions.</p>'
            '<h2>Current Season Registration</h2>'
            f'[mlbb_league_list search="{name}"]'
            f'\n[mlbb_league_register_help search="{name}"]'
        )
        page_url = ''
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT ID FROM wp_posts WHERE post_name=%s AND post_type='page' AND post_status='publish' LIMIT 1",
                    (league_slug,),
                )
                existing_page = await cur.fetchone()
        if existing_page:
            page_url = f"{config.WP_URL}/{league_slug}/"
        else:
            try:
                page = await api.create_page(name, page_content, league_slug)
                page_url = page.get('link', '')
            except Exception as e:
                logger.warning("Could not create WP page for custom league: %s", e)
                page_url = ''

        cap_text = str(max_teams) if max_teams else "Unlimited"
        reg_status = "Open" if opens_in_days == 0 else f"Opens in {opens_in_days}d"
        embed = discord.Embed(title="✅ Custom League Created", color=0x2ECC71)
        embed.add_field(name="Name", value=name)
        embed.add_field(name="Rule", value=rule)
        embed.add_field(name="Term ID", value=league_term_id)
        if table_id:
            embed.add_field(name="Table ID", value=table_id)
        embed.add_field(name="Registration", value=reg_status)
        embed.add_field(name="Closes in", value=f"{closes_in_days} days")
        embed.add_field(name="Max teams", value=cap_text)
        if page_url:
            embed.add_field(name="Page", value=page_url, inline=False)
        embed.set_footer(text="Appears on /custom-leagues/ automatically.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @league.command(name="delete", description="Delete a league by ID")
    @app_commands.describe(league_id="The league post ID")
    @admin_check()
    async def league_delete(self, interaction: discord.Interaction, league_id: int):
        await interaction.response.defer(ephemeral=True)
        try:
            await get_api().delete_league(league_id)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to delete: {e}", ephemeral=True)
            return
        await interaction.followup.send(f"✅ League `{league_id}` deleted.", ephemeral=True)

    @league.command(name="register", description="Register your team for a league")
    @app_commands.describe(
        league_id="League post ID (use /league list to find it)",
        team_id="Your team ID — required if you captain multiple teams",
    )
    async def league_register(
        self, interaction: discord.Interaction, league_id: int, team_id: int = None
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

        # Enforce roster size: 5-6 active players (5 minimum + 1 optional sub)
        roster = await get_roster(captain["sp_team_id"])
        if len(roster) < 5 or len(roster) > 6:
            await interaction.followup.send(
                f"❌ **{captain['team_name']}** has {len(roster)} active player(s). "
                f"You need **5 or 6** (5 players + 1 optional substitute) to register.",
                ephemeral=True,
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
                "Ask an organizer to open registrations with `/league-admin open-registration`.",
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

                # Per-league conflict: check if any player on this team is already
                # on another team registered (non-rejected) in the same period
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
                        f"**{conflict[1]}** in this league. A player may only play for one team per league.",
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


    @league.command(
        name="quickstart",
        description="Step-by-step guide to registering, joining a league, and playing your first match",
    )
    async def league_quickstart(self, interaction: discord.Interaction):
        wp = config.WP_URL  # https://play.mlbb.site

        embed = discord.Embed(
            title="🚀 Getting Started — Play MLBB Leagues",
            description=(
                "Everything you need from zero to your first match result, in order.\n"
                f"Full guide with screenshots: **{wp}/quickstart/**"
            ),
            color=0x3A86FF,
        )

        embed.add_field(
            name="① Register Your Account",
            value=(
                "`/player register [your-IGN]`\n"
                "Links your Discord to an MLBB player profile on the site.\n"
                f"→ [{wp}/](https://play.mlbb.site)"
            ),
            inline=False,
        )

        embed.add_field(
            name="② Build Your Team",
            value=(
                "`/team create [team-name]` — you become captain\n"
                "`/team invite @teammate` — send an invite *(captain only)*\n"
                "`/team accept` — each player accepts their invite\n"
                "Tip: you need at least **5 active players** to compete.\n"
                f"→ `/team roster` · your page: `{wp}/team/[slug]/`"
            ),
            inline=False,
        )

        embed.add_field(
            name="③ Sign Up for a League",
            value=(
                "`/league list` — browse open leagues and copy your league ID\n"
                "`/league register [league_id]` — enter your team *(captain only)*\n"
                "Formats: **Draft Pick BO3/BO5**, **Brawl BO3/BO5**, **Free Play**\n"
                f"→ [{wp}/leagues/](https://play.mlbb.site/leagues/) · [{wp}/sign-ups/](https://play.mlbb.site/sign-ups/)"
            ),
            inline=False,
        )

        embed.add_field(
            name="④ Wait for the Season to Open",
            value=(
                "After registration closes, admins finalize rosters and schedule your round-robin matches.\n"
                "You'll be notified in **#match-notifications** when the season goes live.\n"
                f"→ [{wp}/leagues/](https://play.mlbb.site/leagues/)"
            ),
            inline=False,
        )

        embed.add_field(
            name="⑤ Check the Events Table for Your Next Game",
            value=(
                "Go to your league page and look at the **Events** table.\n"
                "Each row shows your opponent, date, and match window *(Thu–Sun, 7–11 PM PST)*.\n"
                f"→ e.g. [{wp}/moniyan-league/](https://play.mlbb.site/moniyan-league/)"
            ),
            inline=False,
        )

        embed.add_field(
            name="⑥ Find Your Voice Channel & Play",
            value=(
                "Head to your team's voice channel in Discord *(under the Matches category)*.\n"
                "Coordinate with your teammates and opponents, then launch MLBB.\n"
                "**Screenshot the VICTORY screen** the moment the match ends — you'll need it!"
            ),
            inline=False,
        )

        embed.add_field(
            name="⑦ Upload Your Results",
            value=(
                "`/match submit [screenshot]` — **winning captain** attaches the VICTORY screenshot\n"
                "The bot reads the screenshot automatically *(AI-powered)*.\n"
                "`/match confirm [submission_id]` — **opposing captain** verifies the result\n"
                "`/match dispute [submission_id] [reason]` — disagree? Flag it for review\n"
                "→ Results auto-post to **#match-notifications**"
            ),
            inline=False,
        )

        embed.add_field(
            name="⑧ Check the Standings",
            value=(
                "Head back to your league page and scroll to the **Standings** table.\n"
                "`/player profile` — see your own stats and team affiliations anytime\n"
                f"→ e.g. [{wp}/moniyan-league/](https://play.mlbb.site/moniyan-league/)\n"
                "Keep winning — top teams at season's end earn **trophies and bragging rights**. 🏆"
            ),
            inline=False,
        )

        embed.set_footer(
            text="play.mlbb.site · /tournament help for a full command reference · /player register to begin"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leagues(bot))
