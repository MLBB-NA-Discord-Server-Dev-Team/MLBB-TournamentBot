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


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
