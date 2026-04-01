"""
bot/cogs/pickup.py — rolling pick-up tournament pool (stub — Phase 5b)
"""
import discord
from discord import app_commands
from discord.ext import commands


class Pickup(commands.Cog):
    """Pick-up tournament pool — coming in Phase 5b"""

    pickup = app_commands.Group(name="pickup", description="Pick-up tournaments")

    @pickup.command(name="status", description="View the current pick-up tournament pool")
    async def pickup_status(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "🚧 Pick-up tournaments are coming soon!", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Pickup(bot))
