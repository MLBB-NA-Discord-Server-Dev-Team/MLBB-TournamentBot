"""
MLBB-TournamentBot — SportsPress Tournament Manager
"""
import logging
import os
import sys

import discord
from discord.ext import commands

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services import db

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

COGS = [
    "bot.cogs.player",
    "bot.cogs.teams",
    "bot.cogs.leagues",
    "bot.cogs.tournaments",
    "bot.cogs.match",
    "bot.cogs.pickup",
    "bot.cogs.admin",
]


class TournamentBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True       # required: per-member VC permission overwrites
        intents.voice_states = True  # required: voice channel lifecycle management
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        await db.init()
        logger.info("DB pool initialised")

        for ext in COGS:
            try:
                await self.load_extension(ext)
                logger.info("Loaded cog: %s", ext)
            except Exception as e:
                logger.warning("Could not load cog %s: %s", ext, e)

        for guild_id in config.GUILD_IDS:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to guild %s", guild_id)

    async def on_ready(self):
        logger.info("TournamentBot ready — logged in as %s (ID: %s)", self.user, self.user.id)
        await self._bootstrap_notifications_channel()
        await self._bootstrap_admin_log_channel()

    async def _bootstrap_notifications_channel(self):
        """
        Ensure #match-notifications exists in MATCH_VOICE_CATEGORY_ID.
        Creates it if missing and persists the resolved channel ID to .env.
        """
        if not config.MATCH_VOICE_CATEGORY_ID:
            logger.warning("MATCH_VOICE_CATEGORY_ID not set — skipping notifications channel bootstrap")
            return

        for guild in self.guilds:
            category = guild.get_channel(config.MATCH_VOICE_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                continue

            existing = discord.utils.get(category.text_channels, name="match-notifications")
            if existing:
                config.MATCH_NOTIFICATIONS_CHANNEL_ID = existing.id
                logger.info("Notifications channel: #%s (%s)", existing.name, existing.id)
                return

            # Read-only for @everyone; staff roles can send
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, add_reactions=False
                )
            }
            for role in guild.roles:
                if role.name in config.STAFF_ROLES:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True
                    )

            channel = await guild.create_text_channel(
                "match-notifications",
                category=category,
                overwrites=overwrites,
                topic="Automated match notifications — upcoming events, results, and bracket updates.",
                reason="TournamentBot bootstrap",
            )
            config.MATCH_NOTIFICATIONS_CHANNEL_ID = channel.id
            logger.info("Created notifications channel: #%s (%s)", channel.name, channel.id)
            _write_env_key("MATCH_NOTIFICATIONS_CHANNEL_ID", str(channel.id))
            return

    async def _bootstrap_admin_log_channel(self):
        """
        Ensure #tournament-admin exists in MATCH_VOICE_CATEGORY_ID.
        Visible only to STAFF_ROLES — @everyone denied.
        """
        if not config.MATCH_VOICE_CATEGORY_ID:
            return

        for guild in self.guilds:
            category = guild.get_channel(config.MATCH_VOICE_CATEGORY_ID)
            if not isinstance(category, discord.CategoryChannel):
                continue

            existing = discord.utils.get(category.text_channels, name="tournament-admin")
            if existing:
                config.ADMIN_LOG_CHANNEL_ID = existing.id
                logger.info("Admin log channel: #%s (%s)", existing.name, existing.id)
                return

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=False
                )
            }
            for role in guild.roles:
                if role.name in config.STAFF_ROLES:
                    overwrites[role] = discord.PermissionOverwrite(
                        view_channel=True, send_messages=True, read_message_history=True
                    )

            channel = await guild.create_text_channel(
                "tournament-admin",
                category=category,
                overwrites=overwrites,
                topic="Internal bot logs — registrations, submissions, disputes, system events.",
                reason="TournamentBot bootstrap",
            )
            config.ADMIN_LOG_CHANNEL_ID = channel.id
            logger.info("Created admin log channel: #%s (%s)", channel.name, channel.id)
            _write_env_key("ADMIN_LOG_CHANNEL_ID", str(channel.id))
            return

    async def close(self):
        await db.close()
        await super().close()


def _write_env_key(key: str, value: str):
    """Update or append a single key=value line in .env."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r") as f:
        lines = f.readlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}\n")
    with open(env_path, "w") as f:
        f.writelines(lines)


def main():
    config.validate()
    bot = TournamentBot()
    bot.run(config.DISCORD_TOKEN)
