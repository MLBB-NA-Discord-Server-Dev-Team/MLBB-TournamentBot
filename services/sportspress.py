"""
SportsPress API Service — REST client for play.mlbb.site

SportsPress registers its post types under /wp-json/sportspress/v2/ (not wp/v2):
  teams, players, events, tables, tournaments, leagues, seasons, calendars ...

wp/v2 is still used for media uploads and generic post meta writes.
"""
import logging
import base64
from typing import List, Dict, Any
import aiohttp

logger = logging.getLogger(__name__)


class SportsPressAPI:
    """Async client for the SportsPress v2 REST API"""

    def __init__(self, base_url: str, username: str, app_password: str):
        self.base_url = base_url.rstrip("/")
        self.sp_url = f"{self.base_url}/wp-json/sportspress/v2"
        self.wp_url = f"{self.base_url}/wp-json/wp/v2"
        token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        }

    # ── Internal helpers ───────────────────────────────────────────────────

    async def _get(self, endpoint: str, params: Dict = None, base: str = None) -> Any:
        url = f"{base or self.sp_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params or {}) as r:
                r.raise_for_status()
                return await r.json()

    async def _post(self, endpoint: str, data: Dict, base: str = None) -> Any:
        url = f"{base or self.sp_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as r:
                r.raise_for_status()
                return await r.json()

    async def _patch(self, endpoint: str, data: Dict, base: str = None) -> Any:
        url = f"{base or self.sp_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as r:
                # WP REST uses POST for updates too (no PATCH needed with _method)
                r.raise_for_status()
                return await r.json()

    async def _delete(self, endpoint: str, base: str = None) -> Any:
        url = f"{base or self.sp_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers, params={"force": True}) as r:
                r.raise_for_status()
                return await r.json()

    # ── Teams ──────────────────────────────────────────────────────────────

    async def create_team(self, name: str, description: str = "") -> Dict:
        return await self._post("teams", {
            "title": name,
            "content": description,
            "status": "publish",
        })

    async def delete_team(self, post_id: int) -> Dict:
        return await self._delete(f"teams/{post_id}")

    # ── Players ────────────────────────────────────────────────────────────

    async def create_player(self, name: str, team_ids: List[int], description: str = "") -> Dict:
        data: Dict = {"title": name, "content": description, "status": "publish"}
        if team_ids:
            data["teams"] = team_ids
        return await self._post("players", data)

    async def delete_player(self, post_id: int) -> Dict:
        return await self._delete(f"players/{post_id}")

    # ── Tournaments ────────────────────────────────────────────────────────

    async def create_tournament(self, name: str, description: str = "") -> Dict:
        return await self._post("tournaments", {
            "title": name,
            "content": description,
            "status": "publish",
        })

    async def delete_tournament(self, post_id: int) -> Dict:
        return await self._delete(f"tournaments/{post_id}")

    # ── Tables (League standings) ──────────────────────────────────────────

    async def create_table(self, name: str, description: str = "") -> Dict:
        return await self._post("tables", {
            "title": name,
            "content": description,
            "status": "publish",
        })

    async def delete_table(self, post_id: int) -> Dict:
        return await self._delete(f"tables/{post_id}")

    # ── Events (Matches) ───────────────────────────────────────────────────

    async def create_event(
        self, name: str, home_team: int, away_team: int, date: str, description: str = ""
    ) -> Dict:
        return await self._post("events", {
            "title": name,
            "content": description,
            "status": "publish",
            "teams": [home_team, away_team],
            "date": date,
        })

    async def delete_event(self, post_id: int) -> Dict:
        return await self._delete(f"events/{post_id}")

    # ── Player metrics (Discord linkage) ───────────────────────────────────

    async def set_player_discord(self, player_id: int, discord_id: str, discord_username: str) -> Dict:
        """Write discordid + discordusername into sp_metrics on an sp_player post."""
        import phpserialize
        metrics = {
            "discordid": discord_id,
            "discordusername": discord_username,
            "discorddiscriminator": "0",
        }
        serialised = phpserialize.dumps(metrics).decode()
        return await self._post(f"players/{player_id}", {"meta": {"sp_metrics": serialised}})
