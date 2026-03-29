"""
Configuration for MLBB-TournamentBot
"""
import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
GUILD_IDS: List[int] = [int(g) for g in os.getenv("GUILD_IDS", "").split(",") if g.strip()]
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# Role allowed to manage tournaments
ORGANIZER_ROLES: List[str] = [r.strip() for r in os.getenv("ORGANIZER_ROLES", "Tournament Organizer").split(",") if r.strip()]
ADMIN_ROLES: List[str] = [r.strip() for r in os.getenv("ADMIN_ROLES", "admins").split(",") if r.strip()]

# WordPress / SportsPress
WP_URL: str = os.getenv("WP_PLAY_MLBB_URL", "https://play.mlbb.site")
WP_USER: str = os.getenv("WP_PLAY_MLBB_USER", "")
WP_APP_PASSWORD: str = os.getenv("WP_PLAY_MLBB", "")

def validate():
    if not DISCORD_TOKEN:
        raise ValueError("DISCORD_TOKEN is required")
    if not WP_USER or not WP_APP_PASSWORD:
        raise ValueError("WP_PLAY_MLBB_USER and WP_PLAY_MLBB are required")
