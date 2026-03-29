"""
MLBB-TournamentBot — SportsPress Tournament Manager
"""
import logging
import discord
from discord.ext import commands
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler('bot.log'), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


class TournamentBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        for ext in ['bot.cogs.teams', 'bot.cogs.tournaments', 'bot.cogs.leagues']:
            await self.load_extension(ext)
        for guild_id in config.GUILD_IDS:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Synced commands to guild {guild_id}")

    async def on_ready(self):
        logger.info(f"TournamentBot ready as {self.user}")


def main():
    config.validate()
    bot = TournamentBot()
    bot.run(config.DISCORD_TOKEN)
