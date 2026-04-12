"""
services/admin_log.py — post structured log embeds to #tournament-admin

Usage in any cog:
    from services import admin_log
    await admin_log.log(bot, admin_log.Event.PLAYER_REGISTERED, interaction=interaction, ign="p3hndrx")

Multi-guild broadcast:
    The bot reads data/guilds.json at log time and posts the same embed to
    every guild's admin_log channel. Falls back to config.ADMIN_LOG_CHANNEL_ID
    if guilds.json is missing or empty (backwards compat for single-guild installs).
"""
import json
import logging
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord

logger = logging.getLogger(__name__)

# Resolve data/guilds.json relative to the repo root
_REPO_ROOT = Path(__file__).parent.parent
_GUILDS_FILE = _REPO_ROOT / "data" / "guilds.json"


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


def _load_admin_channels() -> list[int]:
    """Return list of admin_log channel IDs from data/guilds.json, or [] if missing."""
    if not _GUILDS_FILE.exists():
        return []
    try:
        with open(_GUILDS_FILE) as f:
            data = json.load(f)
        return [int(g["admin_log"]) for g in data.get("guilds", []) if g.get("admin_log")]
    except Exception as e:
        logger.warning("Could not load guilds.json: %s", e)
        return []


async def log(bot: discord.Client, event: Event, fields: dict[str, Any] = None,
              user: discord.User | discord.Member = None) -> None:
    """Post a log embed to every configured guild's #tournament-admin."""
    import config

    channel_ids = _load_admin_channels()

    # Fallback: if guilds.json is empty or missing, use the legacy single channel
    if not channel_ids and config.ADMIN_LOG_CHANNEL_ID:
        channel_ids = [config.ADMIN_LOG_CHANNEL_ID]

    if not channel_ids:
        return

    title, color = event.value
    embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))

    if user:
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

    for name, value in (fields or {}).items():
        embed.add_field(name=name, value=str(value) if value is not None else "—", inline=True)

    for ch_id in channel_ids:
        channel = bot.get_channel(ch_id)
        if not channel:
            continue
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.warning("Failed to post admin log to channel %s: %s", ch_id, e)
