"""
bot/cogs/match.py — match result submission (captains only, win-claim model)

/match submit   — winning team captain uploads screenshot
/match confirm  — opposing captain confirms
/match dispute  — opposing captain disputes
"""
import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import config
from services import db, admin_log
from services.admin_log import Event
from services.db_helpers import get_captain_team
from services import match_parser
from services.match_parser import MatchParseError, WARN_CONFIDENCE

logger = logging.getLogger(__name__)


def _notifications_channel(bot: commands.Bot) -> discord.TextChannel | None:
    if not config.MATCH_NOTIFICATIONS_CHANNEL_ID:
        return None
    return bot.get_channel(config.MATCH_NOTIFICATIONS_CHANNEL_ID)


class Match(commands.Cog):
    """Match result submission and confirmation"""

    match_group = app_commands.Group(name="match", description="Match results")

    # ── /match submit ─────────────────────────────────────────────────────

    @match_group.command(
        name="submit",
        description="Submit a win — winning team captain only. Attach your end-of-game screenshot.",
    )
    @app_commands.describe(
        screenshot="Screenshot of the VICTORY scoreboard",
        event_id="League sp_event post ID (optional — leave blank for pick-up matches)",
    )
    async def match_submit(
        self,
        interaction: discord.Interaction,
        screenshot: discord.Attachment,
        event_id: int = None,
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # Must be a captain
        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send(
                "❌ Only team captains can submit match results.", ephemeral=True
            )
            return

        # Validate file type
        if not screenshot.content_type or not screenshot.content_type.startswith("image/"):
            await interaction.followup.send(
                "❌ Please attach an image file (PNG, JPG, or JFIF).", ephemeral=True
            )
            return

        if not config.ANTHROPIC_API_KEY:
            await interaction.followup.send(
                "❌ AI parsing is not configured (ANTHROPIC_API_KEY missing). Contact an admin.",
                ephemeral=True,
            )
            return

        # Parse screenshot with Claude
        try:
            result = await match_parser.parse(screenshot.url, config.ANTHROPIC_API_KEY)
        except MatchParseError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            logger.exception("Unexpected error parsing screenshot from %s", discord_id)
            await interaction.followup.send(
                "❌ An unexpected error occurred while parsing the screenshot. Please try again.",
                ephemeral=True,
            )
            return

        # Check for duplicate BattleID
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id FROM mlbb_match_submissions WHERE battle_id=%s AND status != 'rejected'",
                    (result.battle_id,),
                )
                if await cur.fetchone():
                    await interaction.followup.send(
                        f"❌ This match (BattleID `{result.battle_id}`) has already been submitted.",
                        ephemeral=True,
                    )
                    return

        # Parse match timestamp
        match_ts = None
        if result.match_timestamp:
            try:
                match_ts = datetime.strptime(result.match_timestamp, "%m/%d/%Y %H:%M:%S")
            except ValueError:
                pass

        # Store submission
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO mlbb_match_submissions
                        (sp_event_id, submitted_by, winning_team_id, screenshot_url,
                         battle_id, winner_kills, loser_kills, match_duration,
                         match_timestamp, ai_confidence, ai_raw, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                    """,
                    (
                        event_id,
                        discord_id,
                        captain["sp_team_id"],
                        screenshot.url,
                        result.battle_id,
                        result.winner_kills,
                        result.loser_kills,
                        result.duration,
                        match_ts,
                        result.confidence,
                        result.raw_response[:4000],
                    ),
                )
                submission_id = cur.lastrowid

        # Build public result embed
        needs_admin = result.confidence < WARN_CONFIDENCE
        color = 0xF59E0B if needs_admin else 0x2ECC71

        embed = discord.Embed(
            title="📸 Match Result Submitted",
            color=color,
        )
        embed.add_field(name="Winning Team", value=captain["team_name"], inline=True)
        embed.add_field(name="Score", value=f"**{result.winner_kills} – {result.loser_kills}**", inline=True)
        embed.add_field(name="BattleID", value=f"`{result.battle_id}`", inline=True)
        if result.duration:
            embed.add_field(name="Duration", value=result.duration, inline=True)
        if match_ts:
            embed.add_field(name="Played", value=match_ts.strftime("%b %d, %Y %H:%M UTC"), inline=True)
        embed.add_field(name="Confidence", value=f"{result.confidence:.0%}", inline=True)
        embed.add_field(
            name="Submission ID",
            value=f"`#{submission_id}`",
            inline=False,
        )
        embed.set_footer(
            text=(
                "⚠️ Low confidence — admin review required before confirmation."
                if needs_admin
                else "Opposing captain: use /match confirm or /match dispute"
            )
        )
        embed.set_image(url=screenshot.url)
        embed.set_author(
            name=f"Submitted by {interaction.user}",
            icon_url=interaction.user.display_avatar.url,
        )

        # Post to #match-notifications
        notif_channel = _notifications_channel(interaction.client)
        if notif_channel:
            await notif_channel.send(embed=embed)

        # Ping admins if low confidence
        if needs_admin and notif_channel:
            staff_ping = " ".join(
                r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
            )
            if staff_ping:
                await notif_channel.send(
                    f"⚠️ Admin review needed for submission `#{submission_id}` — low confidence parse. {staff_ping}"
                )

        await interaction.followup.send(
            f"✅ Submitted! Submission ID: `#{submission_id}`\n"
            "The result has been posted to #match-notifications for confirmation.",
            ephemeral=True,
        )

        await admin_log.log(interaction.client, Event.MATCH_SUBMITTED, user=interaction.user, fields={
            "Submission ID": f"#{submission_id}",
            "Winning Team": captain["team_name"],
            "Score": f"{result.winner_kills}–{result.loser_kills}",
            "BattleID": result.battle_id,
            "Confidence": f"{result.confidence:.0%}",
            "Event ID": event_id or "—",
        })

    # ── /match confirm ────────────────────────────────────────────────────

    @match_group.command(name="confirm", description="Confirm a pending match result")
    @app_commands.describe(submission_id="Submission ID from the result post")
    async def match_confirm(self, interaction: discord.Interaction, submission_id: int):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        # Must be a captain
        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send(
                "❌ Only team captains can confirm results.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, submitted_by, winning_team_id, battle_id, winner_kills, loser_kills FROM mlbb_match_submissions WHERE id=%s AND status='pending'",
                    (submission_id,),
                )
                row = await cur.fetchone()

        if not row:
            await interaction.followup.send(
                f"❌ Submission `#{submission_id}` not found or already resolved.", ephemeral=True
            )
            return

        sub_id, submitted_by, winning_team_id, battle_id, winner_kills, loser_kills = row

        # Can't confirm your own submission
        if submitted_by == discord_id:
            await interaction.followup.send(
                "❌ You can't confirm your own submission. The opposing captain must confirm.",
                ephemeral=True,
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE mlbb_match_submissions
                    SET status='confirmed', confirmed_by=%s, confirmed_at=NOW()
                    WHERE id=%s
                    """,
                    (discord_id, submission_id),
                )

        embed = discord.Embed(
            title="✅ Result Confirmed",
            color=0x2ECC71,
        )
        embed.add_field(name="Submission", value=f"`#{submission_id}`", inline=True)
        embed.add_field(name="BattleID", value=f"`{battle_id}`", inline=True)
        embed.add_field(name="Score", value=f"**{winner_kills} – {loser_kills}**", inline=True)
        embed.set_footer(text=f"Confirmed by {interaction.user}")

        notif_channel = _notifications_channel(interaction.client)
        if notif_channel:
            await notif_channel.send(embed=embed)

        await interaction.followup.send(
            f"✅ Result `#{submission_id}` confirmed.", ephemeral=True
        )

        await admin_log.log(interaction.client, Event.MATCH_CONFIRMED, user=interaction.user, fields={
            "Submission ID": f"#{submission_id}",
            "BattleID": battle_id,
            "Score": f"{winner_kills}–{loser_kills}",
            "Confirmed by": f"<@{interaction.user.id}>",
        })

    # ── /match dispute ────────────────────────────────────────────────────

    @match_group.command(name="dispute", description="Dispute a pending match result")
    @app_commands.describe(
        submission_id="Submission ID to dispute",
        reason="Reason for the dispute",
    )
    async def match_dispute(
        self,
        interaction: discord.Interaction,
        submission_id: int,
        reason: str,
    ):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)

        captain = await get_captain_team(discord_id)
        if not captain:
            await interaction.followup.send(
                "❌ Only team captains can dispute results.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT id, submitted_by FROM mlbb_match_submissions WHERE id=%s AND status='pending'",
                    (submission_id,),
                )
                row = await cur.fetchone()

        if not row:
            await interaction.followup.send(
                f"❌ Submission `#{submission_id}` not found or already resolved.", ephemeral=True
            )
            return

        if row[1] == discord_id:
            await interaction.followup.send(
                "❌ You can't dispute your own submission.", ephemeral=True
            )
            return

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE mlbb_match_submissions SET status='disputed' WHERE id=%s",
                    (submission_id,),
                )

        embed = discord.Embed(
            title=f"⚠️ Result Disputed — Submission #{submission_id}",
            color=0xE74C3C,
        )
        embed.add_field(name="Disputed by", value=str(interaction.user), inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)

        notif_channel = _notifications_channel(interaction.client)
        if notif_channel:
            # Ping admins
            staff_ping = " ".join(
                r.mention for r in interaction.guild.roles if r.name in config.ADMIN_ROLES
            )
            await notif_channel.send(
                f"{staff_ping}\n" if staff_ping else "", embed=embed
            )

        # DM the submitter
        submitter = interaction.guild.get_member(int(row[1]))
        if submitter:
            try:
                await submitter.send(
                    f"⚠️ Your match submission `#{submission_id}` has been disputed by the opposing captain.\n"
                    f"**Reason:** {reason}\nAn admin will review and resolve this."
                )
            except discord.Forbidden:
                pass

        await interaction.followup.send(
            f"⚠️ Dispute filed for `#{submission_id}`. Admins have been notified.", ephemeral=True
        )

        await admin_log.log(interaction.client, Event.MATCH_DISPUTED, user=interaction.user, fields={
            "Submission ID": f"#{submission_id}",
            "Disputed by": f"<@{interaction.user.id}>",
            "Reason": reason,
        })


async def setup(bot: commands.Bot):
    await bot.add_cog(Match(bot))
