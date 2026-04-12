#!/usr/bin/env python3
"""
scripts/provision_guild.py -- One-shot Discord guild provisioning.

Creates (or finds) the "MLBB Tournaments" category and the 4 text channels
in a specified guild, then records the resulting IDs in data/guilds.json.

The bot must already be invited to the target guild. This script uses the
Discord HTTP API directly (no discord.py), so it doesn't require the bot
process to be running.

Channels created (mirroring bot/main.py bootstrap):
  #match-notifications  -- public read-only, staff write
  #tournament-admin     -- staff-only (view + write)
  #bot-commands         -- admin-only
  #bot-leagues          -- public read-only, staff write

Usage:
    cd /root/MLBB-TournamentBot
    python scripts/provision_guild.py <guild_id> --name DEV
    python scripts/provision_guild.py 999999999 --name PROD
    python scripts/provision_guild.py --list
    python scripts/provision_guild.py <guild_id> --remove
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import aiohttp
from dotenv import load_dotenv

# Load .env from repo root
REPO_ROOT = Path(__file__).parent.parent
load_dotenv(REPO_ROOT / ".env")

DATA_DIR = REPO_ROOT / "data"
GUILDS_FILE = DATA_DIR / "guilds.json"

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/MLBB-NA/TournamentBot, 1.0)"
CATEGORY_NAME = "MLBB Tournaments"

# Channel definitions: (name, topic, permission_preset)
# permission_preset is interpreted below into discord permission overwrites
CHANNELS = [
    ("match-notifications", "Automated match notifications — upcoming events, results, and bracket updates.", "public_read"),
    ("tournament-admin", "Internal bot logs — registrations, submissions, disputes, system events.", "staff_only"),
    ("bot-commands", "Admin-only channel for running tournament management bot commands.", "admin_only"),
    ("bot-leagues", "Autonomous bot-league activity: persistent weekly leagues + daily simulation health checks.", "public_read"),
]

# Discord permission bits (subset used here)
P_VIEW_CHANNEL      = 0x00000400
P_SEND_MESSAGES     = 0x00000800
P_READ_HISTORY      = 0x00010000
P_ADD_REACTIONS     = 0x00000040
P_EMBED_LINKS       = 0x00004000
P_MANAGE_CHANNELS   = 0x00000010


# -- Output helpers ------------------------------------------------------------

def ok(msg: str):    print(f"  [OK]   {msg}")
def fail(msg: str):  print(f"  [FAIL] {msg}")
def info(msg: str):  print(f"  [..]   {msg}")
def skip(msg: str):  print(f"  [SKIP] {msg}")


# -- Guilds file ---------------------------------------------------------------

def load_guilds() -> dict:
    if not GUILDS_FILE.exists():
        return {"guilds": []}
    try:
        with open(GUILDS_FILE) as f:
            return json.load(f)
    except Exception as e:
        fail(f"Could not parse {GUILDS_FILE}: {e}")
        return {"guilds": []}


def save_guilds(data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = GUILDS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(str(tmp), str(GUILDS_FILE))


def upsert_guild(data: dict, entry: dict):
    gid = entry["guild_id"]
    for i, g in enumerate(data["guilds"]):
        if g["guild_id"] == gid:
            data["guilds"][i] = entry
            return
    data["guilds"].append(entry)


def remove_guild(data: dict, guild_id: str) -> bool:
    before = len(data["guilds"])
    data["guilds"] = [g for g in data["guilds"] if g["guild_id"] != guild_id]
    return len(data["guilds"]) < before


# -- Discord HTTP --------------------------------------------------------------

class DiscordAPI:
    def __init__(self, token: str):
        self.token = token
        self.headers = {
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, session: aiohttp.ClientSession,
                       payload: Optional[dict] = None) -> Optional[dict]:
        url = f"{DISCORD_API}{path}"
        kwargs = {"headers": self.headers}
        if payload is not None:
            kwargs["data"] = json.dumps(payload).encode()
        async with session.request(method, url, **kwargs) as resp:
            text = await resp.text()
            if resp.status >= 400:
                fail(f"Discord {method} {path} → {resp.status}: {text[:200]}")
                return None
            return json.loads(text) if text else {}

    async def get_guild(self, session, guild_id: str) -> Optional[dict]:
        return await self._request("GET", f"/guilds/{guild_id}", session)

    async def get_guild_channels(self, session, guild_id: str) -> Optional[list]:
        return await self._request("GET", f"/guilds/{guild_id}/channels", session)

    async def get_guild_roles(self, session, guild_id: str) -> Optional[list]:
        return await self._request("GET", f"/guilds/{guild_id}/roles", session)

    async def create_channel(self, session, guild_id: str, payload: dict) -> Optional[dict]:
        return await self._request("POST", f"/guilds/{guild_id}/channels", session, payload)

    async def get_bot_user(self, session) -> Optional[dict]:
        return await self._request("GET", "/users/@me", session)


# -- Permission builders -------------------------------------------------------

def _staff_role_ids(roles: list, staff_names: list) -> list:
    return [r["id"] for r in roles if r["name"] in staff_names]


def _admin_role_ids(roles: list, admin_names: list) -> list:
    return [r["id"] for r in roles if r["name"] in admin_names]


def _build_overwrites(preset: str, guild_id: str, bot_user_id: str,
                      roles: list, staff_names: list, admin_names: list) -> list:
    """Build Discord permission_overwrites payload for a given preset."""
    # @everyone role_id == guild_id
    everyone_id = guild_id

    staff_ids = _staff_role_ids(roles, staff_names)
    admin_ids = _admin_role_ids(roles, admin_names)

    overwrites = []

    if preset == "public_read":
        # @everyone: view only, no send
        overwrites.append({
            "id": everyone_id, "type": 0,
            "allow": str(P_VIEW_CHANNEL | P_READ_HISTORY | P_ADD_REACTIONS),
            "deny": str(P_SEND_MESSAGES),
        })
        # Bot: full write
        overwrites.append({
            "id": bot_user_id, "type": 1,
            "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_EMBED_LINKS | P_READ_HISTORY),
            "deny": "0",
        })
        # Staff: can send
        for rid in staff_ids:
            overwrites.append({
                "id": rid, "type": 0,
                "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY),
                "deny": "0",
            })

    elif preset == "staff_only":
        # @everyone: hidden
        overwrites.append({
            "id": everyone_id, "type": 0,
            "allow": "0", "deny": str(P_VIEW_CHANNEL),
        })
        overwrites.append({
            "id": bot_user_id, "type": 1,
            "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_EMBED_LINKS | P_READ_HISTORY),
            "deny": "0",
        })
        for rid in staff_ids:
            overwrites.append({
                "id": rid, "type": 0,
                "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY),
                "deny": "0",
            })

    elif preset == "admin_only":
        overwrites.append({
            "id": everyone_id, "type": 0,
            "allow": "0", "deny": str(P_VIEW_CHANNEL),
        })
        overwrites.append({
            "id": bot_user_id, "type": 1,
            "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY),
            "deny": "0",
        })
        for rid in admin_ids:
            overwrites.append({
                "id": rid, "type": 0,
                "allow": str(P_VIEW_CHANNEL | P_SEND_MESSAGES | P_READ_HISTORY),
                "deny": "0",
            })

    return overwrites


# -- Main provisioning logic ---------------------------------------------------

async def provision_guild(api: DiscordAPI, guild_id: str, name: Optional[str]) -> Optional[dict]:
    """Create category + 4 channels in the guild. Returns the guilds.json entry."""
    async with aiohttp.ClientSession() as session:
        # Verify guild access
        guild = await api.get_guild(session, guild_id)
        if not guild:
            fail(f"Cannot access guild {guild_id} — is the bot invited?")
            return None
        guild_name_discord = guild["name"]
        display_name = name or guild_name_discord
        ok(f"Guild: {guild_name_discord} ({guild_id})")

        # Get bot user ID
        me = await api.get_bot_user(session)
        if not me:
            fail("Could not fetch bot identity")
            return None
        bot_user_id = me["id"]

        # Get existing channels and roles
        channels = await api.get_guild_channels(session, guild_id)
        roles = await api.get_guild_roles(session, guild_id)
        if channels is None or roles is None:
            return None

        # Staff/admin role name lists from env
        staff_names = [r.strip() for r in os.getenv("ORGANIZER_ROLES", "").split(",") if r.strip()]
        staff_names += [r.strip() for r in os.getenv("ADMIN_ROLES", "").split(",") if r.strip()]
        staff_names = list(dict.fromkeys(staff_names))  # dedupe, preserve order
        admin_names = [r.strip() for r in os.getenv("ADMIN_ROLES", "").split(",") if r.strip()]
        info(f"Staff roles recognized: {staff_names or '(none — no overwrites applied)'}")
        info(f"Admin roles recognized: {admin_names or '(none)'}")

        # Find or create category. Priority order:
        #  1. Existing category matching MATCH_VOICE_CATEGORY_ID env var (if in this guild)
        #  2. Existing category named "MLBB Tournaments"
        #  3. Create new "MLBB Tournaments" category
        env_cat_id = os.getenv("MATCH_VOICE_CATEGORY_ID", "").strip()
        category = None
        if env_cat_id and env_cat_id != "0":
            category = next((c for c in channels if c["type"] == 4 and c["id"] == env_cat_id), None)
            if category:
                ok(f"Using existing category from MATCH_VOICE_CATEGORY_ID: '{category['name']}' (id {category['id']})")

        if not category:
            category = next(
                (c for c in channels if c["type"] == 4 and c["name"] == CATEGORY_NAME),
                None,
            )
            if category:
                ok(f"Category '{CATEGORY_NAME}' exists (id {category['id']})")

        if not category:
            info(f"Creating category '{CATEGORY_NAME}'...")
            category = await api.create_channel(session, guild_id, {
                "name": CATEGORY_NAME,
                "type": 4,  # 4 = category
            })
            if not category:
                return None
            ok(f"Created category '{CATEGORY_NAME}' (id {category['id']})")
        category_id = category["id"]

        # Create each channel
        result = {
            "guild_id": guild_id,
            "name": display_name,
            "category_id": category_id,
        }

        for ch_name, topic, preset in CHANNELS:
            existing = next(
                (c for c in channels if c["type"] == 0 and c["name"] == ch_name
                 and str(c.get("parent_id", "")) == category_id),
                None,
            )
            if existing:
                ok(f"#{ch_name} exists (id {existing['id']})")
                ch_id = existing["id"]
            else:
                info(f"Creating #{ch_name}...")
                overwrites = _build_overwrites(
                    preset, guild_id, bot_user_id, roles, staff_names, admin_names
                )
                created = await api.create_channel(session, guild_id, {
                    "name": ch_name,
                    "type": 0,
                    "topic": topic,
                    "parent_id": category_id,
                    "permission_overwrites": overwrites,
                })
                if not created:
                    return None
                ch_id = created["id"]
                ok(f"Created #{ch_name} (id {ch_id})")

            # Map channel name to JSON key
            key = ch_name.replace("-", "_")
            if key == "match_notifications":
                result["match_notifications"] = ch_id
            elif key == "tournament_admin":
                result["admin_log"] = ch_id
            elif key == "bot_commands":
                result["bot_commands"] = ch_id
            elif key == "bot_leagues":
                result["bot_leagues"] = ch_id

        return result


# -- CLI -----------------------------------------------------------------------

async def main():
    p = argparse.ArgumentParser(description="Provision a Discord guild for MLBB Tournament Bot.")
    p.add_argument("guild_id", nargs="?", help="Discord guild ID to provision")
    p.add_argument("--name", help="Display name for this guild in guilds.json (e.g., DEV, PROD)")
    p.add_argument("--list", action="store_true", help="List configured guilds")
    p.add_argument("--remove", action="store_true", help="Remove guild from guilds.json (keeps channels)")
    args = p.parse_args()

    if args.list:
        data = load_guilds()
        print(f"\nConfigured guilds ({len(data['guilds'])}):\n")
        for g in data["guilds"]:
            print(f"  {g.get('name', '?')} ({g['guild_id']})")
            for k in ("match_notifications", "admin_log", "bot_commands", "bot_leagues"):
                print(f"    {k}: {g.get(k, '-')}")
            print()
        return 0

    if not args.guild_id:
        p.error("guild_id is required (or use --list)")

    if args.remove:
        data = load_guilds()
        if remove_guild(data, args.guild_id):
            save_guilds(data)
            ok(f"Removed guild {args.guild_id} from guilds.json")
        else:
            skip(f"Guild {args.guild_id} was not in guilds.json")
        return 0

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        fail("DISCORD_TOKEN not set in .env")
        return 1

    api = DiscordAPI(token)
    print(f"\nProvisioning guild {args.guild_id} (name={args.name or '(from Discord)'})\n")

    entry = await provision_guild(api, args.guild_id, args.name)
    if not entry:
        print("\nProvisioning FAILED")
        return 1

    data = load_guilds()
    upsert_guild(data, entry)
    save_guilds(data)

    print(f"\n[OK] Guild provisioned. Updated {GUILDS_FILE}")
    print(f"     Don't forget: add {args.guild_id} to GUILD_IDS in .env, then restart the bot.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
