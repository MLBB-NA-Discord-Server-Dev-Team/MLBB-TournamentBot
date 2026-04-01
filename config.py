"""
Configuration for MLBB-TournamentBot
"""
import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

# ── Discord ────────────────────────────────────────────────────────────────
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
DISCORD_CLIENT_ID: int = int(os.getenv("DISCORD_CLIENT_ID", "1192925452550017024"))
GUILD_IDS: List[int] = [int(g) for g in os.getenv("GUILD_IDS", "").split(",") if g.strip()]
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Roles ──────────────────────────────────────────────────────────────────
# Roles that can run organizer-level commands (tournament management)
ORGANIZER_ROLES: List[str] = [
    r.strip()
    for r in os.getenv("ORGANIZER_ROLES", "Tournament Organizer,DEV").split(",")
    if r.strip()
]
# Roles that can run admin-level commands (overrides, dispute resolution)
ADMIN_ROLES: List[str] = [
    r.strip()
    for r in os.getenv("ADMIN_ROLES", "admins,DEV").split(",")
    if r.strip()
]
# Union of both — any elevated access
STAFF_ROLES: List[str] = list(dict.fromkeys(ORGANIZER_ROLES + ADMIN_ROLES))

# ── WordPress / SportsPress ────────────────────────────────────────────────
WP_URL: str = os.getenv("WP_PLAY_MLBB_URL", "https://play.mlbb.site").rstrip("/")
WP_USER: str = os.getenv("WP_PLAY_MLBB_USER", "")
WP_APP_PASSWORD: str = os.getenv("WP_PLAY_MLBB", "")

# ── Database ───────────────────────────────────────────────────────────────
DB_HOST: str = os.getenv("DB_HOST", "localhost")
DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
DB_NAME: str = os.getenv("DB_NAME", "playmlbb_db")
DB_USER: str = os.getenv("DB_USER", "wpdbuser")
DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

# ── Claude API ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# ── Voice channels & notifications ────────────────────────────────────────
MATCH_VOICE_CATEGORY_ID: int = int(os.getenv("MATCH_VOICE_CATEGORY_ID", "0") or "0")
MATCH_NOTIFICATIONS_CHANNEL_ID: int = int(os.getenv("MATCH_NOTIFICATIONS_CHANNEL_ID", "0") or "0")
ADMIN_LOG_CHANNEL_ID: int = int(os.getenv("ADMIN_LOG_CHANNEL_ID", "0") or "0")

# Match play window: Thu–Sun, 7 PM – 11 PM PST
MATCH_WINDOW_START_HOUR: int = int(os.getenv("MATCH_WINDOW_START_HOUR", "19"))
MATCH_WINDOW_END_HOUR: int = int(os.getenv("MATCH_WINDOW_END_HOUR", "23"))
MATCH_WINDOW_TZ: str = os.getenv("MATCH_WINDOW_TZ", "America/Los_Angeles")

# Pick-up tournament: 48-hour deadline per round
PICKUP_ROUND_HOURS: int = int(os.getenv("PICKUP_ROUND_HOURS", "48"))


def validate():
    errors = []
    if not DISCORD_TOKEN:
        errors.append("DISCORD_TOKEN is required")
    if not GUILD_IDS:
        errors.append("GUILD_IDS is required")
    if not WP_USER or not WP_APP_PASSWORD:
        errors.append("WP_PLAY_MLBB_USER and WP_PLAY_MLBB are required")
    if not DB_PASSWORD:
        errors.append("DB_PASSWORD is required")
    if errors:
        raise ValueError("Configuration errors:\n  " + "\n  ".join(errors))


def has_staff_role(member_roles: List[str]) -> bool:
    return any(r in member_roles for r in STAFF_ROLES)


def has_organizer_role(member_roles: List[str]) -> bool:
    return any(r in member_roles for r in ORGANIZER_ROLES)


def has_admin_role(member_roles: List[str]) -> bool:
    return any(r in member_roles for r in ADMIN_ROLES)
