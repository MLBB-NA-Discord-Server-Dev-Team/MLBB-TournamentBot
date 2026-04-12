"""
bot/cogs/member_events.py — react to Discord server membership changes.

When a registered player leaves the server we:
  1. Mark all their mlbb_player_roster rows as inactive
  2. Log the departure to the admin channel
  3. Let the scheduler's roster-minimum check pick up any teams that dropped below 5
"""
import logging
import discord
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import db, admin_log
from services.admin_log import Event
from services.db_helpers import get_player_by_discord_id, get_player_active_team_ids

logger = logging.getLogger(__name__)


class MemberEvents(commands.Cog):
    """Handle Discord server membership changes."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        discord_id = str(member.id)
        player = await get_player_by_discord_id(discord_id)
        if not player:
            return  # Not a registered MLBB player

        # Find all teams this player was on before deactivation
        team_ids = await get_player_active_team_ids(discord_id)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE mlbb_player_roster SET status='inactive' WHERE discord_id=%s AND status='active'",
                    (discord_id,),
                )
                affected = cur.rowcount

                # Also cancel any pending invites to this player
                await cur.execute(
                    "UPDATE mlbb_team_invites SET status='expired' WHERE invitee_id=%s AND status='pending'",
                    (discord_id,),
                )

        if affected:
            logger.info(
                "Player %s (discord %s) left the server — deactivated %d roster entries",
                player["title"], discord_id, affected,
            )
            await admin_log.log(
                self.bot, Event.SYSTEM,
                fields={
                    "Action": "Player left server",
                    "Player": player["title"],
                    "Discord": f"<@{discord_id}>",
                    "Roster entries deactivated": affected,
                    "Teams affected": ", ".join(str(t) for t in team_ids) or "—",
                },
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberEvents(bot))
