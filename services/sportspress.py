"""
SportsPress API Service — WordPress REST API client
"""
import logging
import base64
from typing import Optional, List, Dict, Any
import aiohttp

logger = logging.getLogger(__name__)


class SportsPressAPI:
    """Async client for WordPress REST API with SportsPress post types"""

    def __init__(self, base_url: str, username: str, app_password: str):
        self.base_url = base_url.rstrip('/')
        self.api_url = f"{self.base_url}/wp-json/wp/v2"
        token = base64.b64encode(f"{username}:{app_password}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json"
        }

    async def _get(self, endpoint: str, params: Dict = None) -> Any:
        url = f"{self.api_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self.headers, params=params or {}) as r:
                r.raise_for_status()
                return await r.json()

    async def _post(self, endpoint: str, data: Dict) -> Any:
        url = f"{self.api_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=data) as r:
                r.raise_for_status()
                return await r.json()

    async def _delete(self, endpoint: str) -> Any:
        url = f"{self.api_url}/{endpoint}"
        async with aiohttp.ClientSession() as session:
            async with session.delete(url, headers=self.headers, params={"force": True}) as r:
                r.raise_for_status()
                return await r.json()

    # ── Teams ──────────────────────────────────────────────────────────────

    async def list_teams(self) -> List[Dict]:
        return await self._get("sp_team", {"per_page": 100, "status": "publish"})

    async def create_team(self, name: str, description: str = "") -> Dict:
        return await self._post("sp_team", {
            "title": name,
            "content": description,
            "status": "publish"
        })

    async def delete_team(self, post_id: int) -> Dict:
        return await self._delete(f"sp_team/{post_id}")

    # ── Players ────────────────────────────────────────────────────────────

    async def list_players(self, team_id: Optional[int] = None) -> List[Dict]:
        params = {"per_page": 100, "status": "publish"}
        return await self._get("sp_player", params)

    async def create_player(self, name: str, team_ids: List[int], description: str = "") -> Dict:
        return await self._post("sp_player", {
            "title": name,
            "content": description,
            "status": "publish",
            "sp_team": team_ids
        })

    # ── Tournaments ────────────────────────────────────────────────────────

    async def list_tournaments(self) -> List[Dict]:
        return await self._get("sp_tournament", {"per_page": 100, "status": "publish"})

    async def create_tournament(self, name: str, description: str = "") -> Dict:
        return await self._post("sp_tournament", {
            "title": name,
            "content": description,
            "status": "publish"
        })

    async def delete_tournament(self, post_id: int) -> Dict:
        return await self._delete(f"sp_tournament/{post_id}")

    # ── Leagues (Tables) ───────────────────────────────────────────────────

    async def list_leagues(self) -> List[Dict]:
        return await self._get("sp_table", {"per_page": 100, "status": "publish"})

    async def create_league(self, name: str, description: str = "") -> Dict:
        return await self._post("sp_table", {
            "title": name,
            "content": description,
            "status": "publish"
        })

    async def delete_league(self, post_id: int) -> Dict:
        return await self._delete(f"sp_table/{post_id}")

    # ── Events (Matches) ───────────────────────────────────────────────────

    async def list_events(self) -> List[Dict]:
        return await self._get("sp_event", {"per_page": 100, "status": "publish", "orderby": "date", "order": "desc"})

    async def create_event(self, name: str, home_team: int, away_team: int, date: str, description: str = "") -> Dict:
        return await self._post("sp_event", {
            "title": name,
            "content": description,
            "status": "publish",
            "sp_team": [home_team, away_team],
            "meta": {"sp_date": date}
        })
