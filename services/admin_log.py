"""
services/admin_log.py — post structured log embeds to #tournament-admin

Usage in any cog:
    from services import admin_log
    await admin_log.log(bot, admin_log.Event.PLAYER_REGISTERED, interaction=interaction, ign="p3hndrx")
"""
import logging
from enum import Enum
from datetime import datetime, timezone
from typing import Any

import discord

logger = logging.getLogger(__name__)


class Event(Enum):
    # Player
    PLAYER_REGISTERED   = ("🆕 Player Registered",    0x2ECC71)
    # Team
    TEAM_CREATED        = ("🛡️ Team Created",          0x3A86FF)
    TEAM_INVITE_SENT    = ("📩 Team Invite Sent",      0x3A86FF)
    TEAM_INVITE_ACCEPTED= ("✅ Player Joined Team",    0x2ECC71)
    PLAYER_KICKED       = ("🚫 Player Kicked",         0xE74C3C)
    # Match
    MATCH_SUBMITTED     = ("📸 Result Submitted",      0xF59E0B)
    MATCH_CONFIRMED     = ("✅ Result Confirmed",      0x2ECC71)
    MATCH_DISPUTED      = ("⚠️ Result Disputed",       0xE74C3C)
    DISPUTE_RESOLVED    = ("🔨 Dispute Resolved",      0x9D4EDD)
    # Pick-up
    PICKUP_JOINED       = ("🎯 Joined Pick-up Pool",   0x9D4EDD)
    PICKUP_LEFT         = ("🚪 Left Pick-up Pool",     0x95A5A6)
    PICKUP_BRACKET      = ("🏆 Bracket Fired",         0xFFB703)
    # System
    SYSTEM              = ("⚙️ System",                0x95A5A6)


async def log(bot: discord.Client, event: Event, fields: dict[str, Any] = None, user: discord.User | discord.Member = None) -> None:
    """Post a log embed to #tournament-admin."""
    import config
    if not config.ADMIN_LOG_CHANNEL_ID:
        return

    channel = bot.get_channel(config.ADMIN_LOG_CHANNEL_ID)
    if not channel:
        return

    title, color = event.value
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))

    if user:
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

    for name, value in (fields or {}).items():
        embed.add_field(name=name, value=str(value) if value is not None else "—", inline=True)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.warning("Failed to post admin log: %s", e)
