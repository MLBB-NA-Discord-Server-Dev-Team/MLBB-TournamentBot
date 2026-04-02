"""
bot/cogs/admin.py — admin and organizer commands (stub — Phase 2/3)
"""
import discord
from discord import app_commands
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import config
from services import db, admin_log
from services.admin_log import Event
from services.db_helpers import list_tables, list_tournaments


def admin_check():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        member_roles = [r.name for r in interaction.user.roles]
        if config.has_admin_role(member_roles):
            return True
        await interaction.response.send_message(
            "❌ You need an admin role to use this command.", ephemeral=True
        )
        return False
    return app_commands.check(predicate)


class Admin(commands.Cog):
    """Admin and organizer commands"""

    admin = app_commands.Group(name="admin", description="Admin commands")

    @admin.command(name="resolve-dispute", description="Override a disputed match result")
    @app_commands.describe(
        submission_id="Submission ID to resolve",
        winner_team_id="sp_team post ID of the winning team",
        notes="Optional notes for the record",
    )
    @admin_check()
    async def resolve_dispute(
        self,
        interaction: discord.Interaction,
        submission_id: int,
        winner_team_id: int,
        notes: str = "",
    ):
        await interaction.response.defer(ephemeral=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, status FROM mlbb_match_submissions WHERE id=%s",
                    (submission_id,),
                )
                row = await cur.fetchone()

        if not row:
            await interaction.followup.send(
                f"❌ Submission `#{submission_id}` not found.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE mlbb_match_submissions
                    SET status='confirmed',
                        winning_team_id=%s,
                        confirmed_by=%s,
                        confirmed_at=NOW()
                    WHERE id=%s
                    """,
                    (winner_team_id, str(interaction.user.id), submission_id),
                )

        embed = discord.Embed(
            title=f"🔨 Dispute Resolved — Submission #{submission_id}",
            color=0x9D4EDD,
        )
        embed.add_field(name="Winner (admin override)", value=f"Team ID `{winner_team_id}`")
        embed.add_field(name="Resolved by", value=str(interaction.user))
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)

        if config.MATCH_NOTIFICATIONS_CHANNEL_ID:
            channel = interaction.guild.get_channel(config.MATCH_NOTIFICATIONS_CHANNEL_ID)
            if channel:
                await channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ Submission `#{submission_id}` resolved. Winner set to team `{winner_team_id}`.",
            ephemeral=True,
        )

        await admin_log.log(self.bot, Event.DISPUTE_RESOLVED, user=interaction.user, fields={
            "Submission ID": f"#{submission_id}",
            "Winner Team ID": winner_team_id,
            "Resolved by": f"<@{interaction.user.id}>",
            "Notes": notes or "—",
        })

    @admin.command(name="pending", description="List all pending match submissions")
    @admin_check()
    async def pending(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, submitted_by, winning_team_id, battle_id, ai_confidence, submitted_at
                    FROM mlbb_match_submissions
                    WHERE status IN ('pending','disputed')
                    ORDER BY submitted_at DESC
                    LIMIT 20
                    """,
                )
                rows = await cur.fetchall()

        if not rows:
            await interaction.followup.send("No pending submissions.", ephemeral=True)
            return

        lines = []
        for row in rows:
            sub_id, submitted_by, winning_team, battle_id, conf, ts = row
            conf_str = f"{conf:.0%}" if conf else "?"
            lines.append(
                f"`#{sub_id}` — Team `{winning_team}` · BattleID `{battle_id}` · "
                f"Confidence {conf_str} · <@{submitted_by}>"
            )

        embed = discord.Embed(
            title=f"Pending Submissions ({len(rows)})",
            description="\n".join(lines),
            color=0xF59E0B,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


    @admin.command(name="open-registration", description="Open registration for a league or tournament")
    @app_commands.describe(
        entity_type="league or tournament",
        entity_id="The post ID (use /league list or /tournament list)",
        closes_in_days="How many days until registration closes (default 7)",
        max_teams="Maximum teams allowed (leave blank for unlimited)",
    )
    @app_commands.choices(entity_type=[
        app_commands.Choice(name="League", value="league"),
        app_commands.Choice(name="Tournament", value="tournament"),
    ])
    @admin_check()
    async def open_registration(
        self,
        interaction: discord.Interaction,
        entity_type: str,
        entity_id: int,
        closes_in_days: int = 7,
        max_teams: int = None,
    ):
        await interaction.response.defer(ephemeral=True)

        # Verify entity exists
        if entity_type == "league":
            items = await list_tables()
        else:
            items = await list_tournaments()

        entity = next((i for i in items if i["id"] == entity_id), None)
        if not entity:
            await interaction.followup.send(
                f"❌ No {entity_type} found with ID `{entity_id}`.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                # Close any existing open period for this entity
                await cur.execute(
                    """
                    UPDATE mlbb_registration_periods
                    SET status='closed'
                    WHERE entity_type=%s AND entity_id=%s AND status='open'
                    """,
                    (entity_type, entity_id),
                )
                await cur.execute(
                    """
                    INSERT INTO mlbb_registration_periods
                        (entity_type, entity_id, opens_at, closes_at, max_teams, created_by, status)
                    VALUES (%s, %s, NOW(), DATE_ADD(NOW(), INTERVAL %s DAY), %s, %s, 'open')
                    """,
                    (entity_type, entity_id, closes_in_days, max_teams, str(interaction.user.id)),
                )
                period_id = cur.lastrowid

        cap_text = str(max_teams) if max_teams else "Unlimited"
        embed = discord.Embed(
            title=f"✅ Registration Opened — {entity['title']}",
            color=0x2ECC71,
        )
        embed.add_field(name="Type", value=entity_type.capitalize())
        embed.add_field(name="Entity ID", value=entity_id)
        embed.add_field(name="Period ID", value=period_id)
        embed.add_field(name="Closes in", value=f"{closes_in_days} days")
        embed.add_field(name="Max teams", value=cap_text)
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(self.bot, Event.SYSTEM, user=interaction.user, fields={
            "Action": "Registration opened",
            "Entity": f"{entity_type.capitalize()} #{entity_id} — {entity['title']}",
            "Closes in": f"{closes_in_days} days",
            "Max teams": cap_text,
        })

    @admin.command(name="close-registration", description="Close registration for a league or tournament")
    @app_commands.describe(
        entity_type="league or tournament",
        entity_id="The post ID",
    )
    @app_commands.choices(entity_type=[
        app_commands.Choice(name="League", value="league"),
        app_commands.Choice(name="Tournament", value="tournament"),
    ])
    @admin_check()
    async def close_registration(
        self,
        interaction: discord.Interaction,
        entity_type: str,
        entity_id: int,
    ):
        await interaction.response.defer(ephemeral=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE mlbb_registration_periods
                    SET status='closed'
                    WHERE entity_type=%s AND entity_id=%s AND status='open'
                    """,
                    (entity_type, entity_id),
                )
                affected = cur.rowcount

        if not affected:
            await interaction.followup.send(
                f"❌ No open registration period found for {entity_type} `{entity_id}`.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Registration closed for {entity_type} `{entity_id}`.", ephemeral=True
        )

    @admin.command(name="registrations", description="List team registrations for a league or tournament")
    @app_commands.describe(
        entity_type="league or tournament",
        entity_id="The post ID",
    )
    @app_commands.choices(entity_type=[
        app_commands.Choice(name="League", value="league"),
        app_commands.Choice(name="Tournament", value="tournament"),
    ])
    @admin_check()
    async def registrations(
        self,
        interaction: discord.Interaction,
        entity_type: str,
        entity_id: int,
    ):
        await interaction.response.defer(ephemeral=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT tr.id, tr.sp_team_id, tp.post_title, tr.registered_by, tr.status, tr.registered_at
                    FROM mlbb_team_registrations tr
                    JOIN mlbb_registration_periods rp ON rp.id = tr.period_id
                    JOIN wp_posts tp ON tp.ID = tr.sp_team_id
                    WHERE rp.entity_type=%s AND rp.entity_id=%s
                    ORDER BY tr.registered_at ASC
                    """,
                    (entity_type, entity_id),
                )
                rows = await cur.fetchall()

        if not rows:
            await interaction.followup.send(
                f"No registrations found for {entity_type} `{entity_id}`.", ephemeral=True
            )
            return

        status_icons = {"pending": "🕐", "approved": "✅", "rejected": "❌"}
        lines = [
            f"{status_icons.get(r[4], '•')} `#{r[0]}` **{r[2]}** (Team `{r[1]}`) — <@{r[3]}> — {r[4]}"
            for r in rows
        ]
        embed = discord.Embed(
            title=f"Registrations — {entity_type.capitalize()} {entity_id} ({len(rows)})",
            description="\n".join(lines),
            color=0xF59E0B,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @admin.command(name="approve-registration", description="Approve a team registration")
    @app_commands.describe(registration_id="Registration ID from /admin registrations")
    @admin_check()
    async def approve_registration(self, interaction: discord.Interaction, registration_id: int):
        await interaction.response.defer(ephemeral=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE mlbb_team_registrations
                    SET status='approved', reviewed_by=%s, reviewed_at=NOW()
                    WHERE id=%s AND status='pending'
                    """,
                    (str(interaction.user.id), registration_id),
                )
                affected = cur.rowcount

        if not affected:
            await interaction.followup.send(
                f"❌ Registration `#{registration_id}` not found or not pending.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Registration `#{registration_id}` approved.", ephemeral=True
        )


    @admin.command(name="set-season", description="Define season dates and auto-schedule registration for leagues/tournaments")
    @app_commands.describe(
        season_name="Display name for this season (e.g. 'Season 3')",
        sp_season_id="SportsPress season term ID",
        reg_opens="Registration opens date (YYYY-MM-DD)",
        reg_closes="Registration closes date (YYYY-MM-DD)",
        play_start="Season play start date (YYYY-MM-DD)",
        play_end="Season play end date (YYYY-MM-DD)",
        league_ids="Comma-separated league IDs to auto-schedule (e.g. 12,34,56)",
        tournament_ids="Comma-separated tournament IDs to auto-schedule (optional)",
        max_teams="Team cap per registration period (leave blank for unlimited)",
    )
    @admin_check()
    async def set_season(
        self,
        interaction: discord.Interaction,
        season_name: str,
        sp_season_id: int,
        reg_opens: str,
        reg_closes: str,
        play_start: str,
        play_end: str,
        league_ids: str = "",
        tournament_ids: str = "",
        max_teams: int = None,
    ):
        await interaction.response.defer(ephemeral=True)

        from datetime import date as _date

        # Validate dates
        try:
            _date.fromisoformat(reg_opens)
            _date.fromisoformat(reg_closes)
            _date.fromisoformat(play_start)
            _date.fromisoformat(play_end)
        except ValueError as e:
            await interaction.followup.send(f"❌ Invalid date format: {e}", ephemeral=True)
            return

        # Parse entity ID lists
        def _parse_ids(raw: str) -> list[int]:
            return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]

        leagues = _parse_ids(league_ids)
        tournaments = _parse_ids(tournament_ids)

        if not leagues and not tournaments:
            await interaction.followup.send(
                "❌ Provide at least one league_id or tournament_id.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                # Upsert season schedule
                await cur.execute(
                    """
                    INSERT INTO mlbb_season_schedule
                        (sp_season_id, season_name, play_start, play_end, reg_opens, reg_closes)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        season_name=%s, play_start=%s, play_end=%s,
                        reg_opens=%s, reg_closes=%s
                    """,
                    (
                        sp_season_id, season_name, play_start, play_end, reg_opens, reg_closes,
                        season_name, play_start, play_end, reg_opens, reg_closes,
                    ),
                )

                # Create scheduled registration periods for each entity
                created = 0
                skipped = 0
                for entity_type, ids in [("league", leagues), ("tournament", tournaments)]:
                    for eid in ids:
                        # Skip if an open/scheduled period already exists
                        await cur.execute(
                            """
                            SELECT id FROM mlbb_registration_periods
                            WHERE entity_type=%s AND entity_id=%s AND status IN ('scheduled','open')
                            """,
                            (entity_type, eid),
                        )
                        if await cur.fetchone():
                            skipped += 1
                            continue

                        await cur.execute(
                            """
                            INSERT INTO mlbb_registration_periods
                                (entity_type, entity_id, sp_season_id, opens_at, closes_at,
                                 max_teams, created_by, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, 'scheduled')
                            """,
                            (
                                entity_type, eid, sp_season_id,
                                reg_opens, reg_closes,
                                max_teams, str(interaction.user.id),
                            ),
                        )
                        created += 1

        embed = discord.Embed(
            title=f"📅 Season Configured — {season_name}",
            color=0x2ECC71,
        )
        embed.add_field(name="Season ID", value=sp_season_id)
        embed.add_field(name="Registration", value=f"{reg_opens} → {reg_closes}")
        embed.add_field(name="Play window", value=f"{play_start} → {play_end}")
        embed.add_field(name="Leagues", value=", ".join(map(str, leagues)) or "—")
        embed.add_field(name="Tournaments", value=", ".join(map(str, tournaments)) or "—")
        embed.add_field(name="Periods created", value=f"{created} scheduled, {skipped} skipped (already active)")
        embed.add_field(name="Max teams", value=str(max_teams) if max_teams else "Unlimited")
        embed.set_footer(text="Registration will open automatically on the opens date.")
        await interaction.followup.send(embed=embed, ephemeral=True)

        await admin_log.log(self.bot, Event.SYSTEM, user=interaction.user, fields={
            "Action": "Season configured",
            "Season": f"{season_name} (ID {sp_season_id})",
            "Registration": f"{reg_opens} → {reg_closes}",
            "Entities": f"{len(leagues)} leagues, {len(tournaments)} tournaments",
            "Periods created": created,
        })


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
