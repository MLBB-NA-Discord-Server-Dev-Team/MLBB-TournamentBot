#!/usr/bin/env python3
"""
scripts/simulate_league_v2.py — Customer-experience league simulation.

Exercises every service function in services/command_services.py, following
the exact same journey a real player takes through the quickstart guide:

    1. /player register    → player_register()
    2. /team create        → team_create()
    2b. /team invite       → team_invite()
    2c. /team accept       → team_accept()
    3. /league register    → league_register()
    4. admin approval      → admin_approve_registration()
    5. schedule events     → SportsPress API
    6. voice channels      → Discord API
    7. /match submit       → match_submit()
    7b. /match confirm     → match_confirm()
    8. /player profile     → player_profile()
    +  /team roster        → team_roster()
    +  cleanup             → team_delete() (or artifact removal)

Unlike simulate_league.py (which calls raw API/DB), this script routes
ALL business logic through command_services.py — testing the same code
paths that the Discord slash commands use.

Usage:
    cd /root/MLBB-TournamentBot
    python scripts/simulate_league_v2.py
    python scripts/simulate_league_v2.py --teams 6 --rule DPBO3 --round-delay 3
    python scripts/simulate_league_v2.py --no-cleanup
    python scripts/simulate_league_v2.py --dry-run
"""
import asyncio
import argparse
import json
import logging
import random
import struct
import sys
import os
import subprocess
import zlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services import db
from services.sportspress import SportsPressAPI
from services import command_services as cmds

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulate_v2.log")),
    ],
)
log = logging.getLogger("simulate_v2")

DISCORD_API = "https://discord.com/api/v10"

# ── Name pools ─────────────────────────────────────────────────────────────────
_ADJ = [
    "Neon", "Crimson", "Shadow", "Thunder", "Phantom", "Iron", "Void",
    "Storm", "Ember", "Frost", "Savage", "Apex", "Arcane", "Rogue",
    "Solar", "Lunar", "Jade", "Azure", "Onyx", "Cobalt", "Titan",
    "Binary", "Flux", "Prism", "Omega", "Hyper", "Blaze", "Static",
]
_NOUN = [
    "Phoenix", "Dragon", "Serpent", "Wolf", "Viper", "Falcon", "Specter",
    "Golem", "Hydra", "Reaper", "Warden", "Striker", "Nomad", "Enforcer",
    "Tempest", "Cipher", "Wraith", "Raven", "Sentinel", "Paladin",
]
_TEAM_CODES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
]
_GAMER_PRE = [
    "Shadow", "Dark", "Neon", "Pixel", "Hyper", "Ultra", "Void", "Blitz",
    "Storm", "Frost", "Blaze", "Glitch", "Turbo", "Cosmic", "Toxic",
    "Dread", "Flux", "Omega", "Zero", "Nova", "Razor", "Drift", "Venom",
    "Rogue", "Hex", "Cyber", "Crypt", "Ion", "Pulse", "Arc",
]
_GAMER_SUF = [
    "Blade", "Wolf", "Hawk", "Storm", "Fire", "Shot", "King", "Fang",
    "Fury", "Strike", "Lux", "Byte", "Core", "Rush", "Edge",
    "Wraith", "Sage", "Bolt", "Vex", "Dusk", "Lynx", "Mace",
]

RULES = ["DPBO1", "DPBO3", "DPBO5", "BrawlBO1", "BrawlBO3", "BrawlBO5"]
RULE_LABELS = {
    "DPBO1": "Draft Pick · Best of 1",  "DPBO3": "Draft Pick · Best of 3",
    "DPBO5": "Draft Pick · Best of 5",  "BrawlBO1": "Brawl · Best of 1",
    "BrawlBO3": "Brawl · Best of 3",    "BrawlBO5": "Brawl · Best of 5",
}
_FAKE_BASE = 9_990_000_000_000_000

# ── Pixel art PNG generator ────────────────────────────────────────────────────
RGB = Tuple[int, int, int]
_PALETTES: List[Tuple[RGB, ...]] = [
    ((15, 15, 25),   (255, 60, 60),   (255, 200, 50),  (60, 200, 255)),
    ((12, 22, 35),   (0, 230, 150),   (255, 80, 200),  (255, 255, 80)),
    ((25, 12, 40),   (180, 50, 255),  (50, 200, 255),  (255, 150, 50)),
    ((10, 30, 20),   (50, 255, 100),  (200, 255, 50),  (255, 80, 80)),
    ((30, 15, 10),   (255, 120, 30),  (255, 220, 80),  (80, 180, 255)),
    ((20, 20, 35),   (100, 100, 255), (220, 60, 255),  (60, 255, 200)),
    ((35, 10, 10),   (255, 40, 80),   (255, 160, 200), (255, 255, 120)),
    ((10, 10, 30),   (80, 80, 255),   (180, 220, 255), (255, 255, 255)),
    ((25, 25, 10),   (220, 180, 40),  (180, 120, 20),  (255, 240, 200)),
    ((15, 25, 15),   (40, 200, 80),   (120, 255, 160), (200, 255, 220)),
    ((30, 10, 30),   (220, 40, 220),  (140, 80, 255),  (60, 200, 255)),
    ((10, 20, 30),   (255, 100, 40),  (255, 180, 80),  (40, 160, 200)),
]

def _png_encode(pixels: List[List[RGB]], scale: int) -> bytes:
    h_art, w_art = len(pixels), len(pixels[0])
    width, height = w_art * scale, h_art * scale
    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    rows: List[bytes] = []
    for y in range(h_art):
        row_bytes = bytearray()
        for x in range(w_art):
            r, g, b = pixels[y][x]
            row_bytes.extend([r, g, b] * scale)
        raw_row = b"\x00" + bytes(row_bytes)
        rows.extend([raw_row] * scale)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 6))
        + chunk(b"IEND", b"")
    )

def _gen_team_logo() -> Tuple[bytes, Tuple[RGB, ...]]:
    pal = random.choice(_PALETTES)
    bg, accents = pal[0], list(pal[1:])
    grid: List[List[RGB]] = [[bg] * 10 for _ in range(10)]
    for y in range(10):
        for x in range(5):
            if random.random() < 0.42:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][9 - x] = c
    if random.random() < 0.5:
        for y in range(5):
            for x in range(10):
                if grid[y][x] != bg:
                    grid[9 - y][x] = grid[y][x]
    return _png_encode(grid, scale=16), pal

def _gen_player_avatar() -> Tuple[bytes, Tuple[RGB, ...]]:
    pal = random.choice(_PALETTES)
    bg, accents = pal[0], list(pal[1:])
    grid: List[List[RGB]] = [[bg] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(4):
            if random.random() < 0.38:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][7 - x] = c
    return _png_encode(grid, scale=16), pal

def _make_solid_png(r: int, g: int, b: int, w: int = 640, h: int = 360) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    row = b"\x00" + bytes([r, g, b] * w)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * h, 6))
        + chunk(b"IEND", b"")
    )

_VICTORY_PNG = _make_solid_png(12, 64, 38, 640, 360)

# ── Discord HTTP shim ──────────────────────────────────────────────────────────
class DiscordHTTP:
    def __init__(self, token: str):
        self._auth = {"Authorization": f"Bot {token}", "User-Agent": "MLBB-SimBot/2.0"}
    async def _req(self, method: str, path: str, payload: dict = None) -> Optional[dict]:
        url = f"{DISCORD_API}{path}"
        headers = dict(self._auth)
        kwargs = {}
        if payload is not None:
            headers["Content-Type"] = "application/json"
            kwargs["data"] = json.dumps(payload).encode()
        async with aiohttp.ClientSession() as s:
            async with s.request(method, url, headers=headers, **kwargs) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    log.warning("Discord %s %s → %s", method, path, resp.status)
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return None
    async def send_embed(self, channel_id: int, embed: dict):
        return await self._req("POST", f"/channels/{channel_id}/messages", {"embeds": [embed]})
    async def create_vc(self, guild_id: int, name: str, cat_id: int) -> Optional[int]:
        data = await self._req("POST", f"/guilds/{guild_id}/channels",
                               {"name": name[:100], "type": 2, "parent_id": str(cat_id)})
        return int(data["id"]) if data and "id" in data else None
    async def delete_channel(self, ch_id: int):
        await self._req("DELETE", f"/channels/{ch_id}")


def _embed(title: str, color: int, fields: List[Tuple[str, str]], footer: str = "", desc: str = "") -> dict:
    e: dict = {"title": title, "color": color, "timestamp": datetime.now(timezone.utc).isoformat(),
               "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields]}
    if footer: e["footer"] = {"text": footer}
    if desc:   e["description"] = desc
    return e


# ── Assertion helper ───────────────────────────────────────────────────────────

def _assert(result: cmds.Result, step: str):
    """Log and raise on service function failure."""
    if not result.ok:
        log.error("  FAIL [%s]: %s", step, result.error)
        raise RuntimeError(f"Step '{step}' failed: {result.error}")
    log.info("  PASS [%s]", step)
    return result


# ── Simulator ──────────────────────────────────────────────────────────────────

class LeagueSimulatorV2:

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api = SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)
        self.dc = DiscordHTTP(config.DISCORD_TOKEN)
        self.guild_id = config.GUILD_IDS[0] if config.GUILD_IDS else 0
        self.notif_ch = config.MATCH_NOTIFICATIONS_CHANNEL_ID
        self.admin_ch = config.ADMIN_LOG_CHANNEL_ID
        self.voice_cat = config.MATCH_VOICE_CATEGORY_ID

        self.league_name = ""
        self.rule = ""
        self.teams: List[dict] = []     # {sp_team_id, name, captain_id, members[]}
        self.events: List[dict] = []
        self.submission_ids: List[int] = []

        # All created artifact IDs (for cleanup)
        self._player_ids: List[int] = []
        self._team_ids: List[int] = []
        self._event_ids: List[int] = []
        self._table_ids: List[int] = []
        self._league_term_id: Optional[int] = None
        self._season_id: Optional[int] = None
        self._period_id: Optional[int] = None
        self._vc_ids: List[int] = []
        self._page_ids: List[int] = []
        self._fake_ids: List[str] = []
        self._reg_ids: List[int] = []
        self._id_counter = 0

    def _fake_id(self) -> str:
        fid = str(_FAKE_BASE + self._id_counter)
        self._id_counter += 1
        self._fake_ids.append(fid)
        return fid

    def _ign(self) -> str:
        return f"{random.choice(_GAMER_PRE)}{random.choice(_GAMER_SUF)}{random.randint(10, 99)}"

    def _wins_needed(self) -> int:
        if "BO1" in self.rule: return 1
        if "BO3" in self.rule: return 2
        return 3

    async def _notify(self, embed: dict):
        if self.notif_ch and not self.args.dry_run:
            await self.dc.send_embed(self.notif_ch, embed)
            await asyncio.sleep(0.3)

    async def _admin_log(self, title: str, color: int, fields: List[Tuple[str, str]]):
        if self.admin_ch and not self.args.dry_run:
            await self.dc.send_embed(self.admin_ch,
                _embed(title, color, fields, footer="🤖 SimBot v2 · command_services"))
            await asyncio.sleep(0.3)

    # ── Phase 1: Create league infrastructure ─────────────────────────────

    async def phase_league(self) -> bool:
        self.league_name = f"Bot-{random.choice(_ADJ)}{random.choice(_NOUN)}"
        self.rule = self.args.rule or random.choice(RULES)
        label = RULE_LABELS[self.rule]

        log.info("━━━ Phase 1: Create league '%s' (%s) ━━━", self.league_name, label)
        if self.args.dry_run:
            return True

        try:
            term = await self.api.create_league(self.league_name, label)
            self._league_term_id = term["id"]
            season_label = "Simulation"
            if self._season_id:
                try:
                    from services.db_helpers import get_current_season
                    s = await get_current_season()
                    season_label = s["season_name"] if s else season_label
                except Exception:
                    pass
            table = await self.api.create_table(
                f"{self.league_name} — {season_label}", label,
                league_ids=[self._league_term_id],
                season_ids=[self._season_id] if self._season_id else None,
            )
            self._table_ids.append(table["id"])
            entity_id = table["id"]
        except Exception as e:
            log.error("League creation failed: %s", e)
            return False

        # Resolve current season
        try:
            from services.db_helpers import get_current_season
            season = await get_current_season()
            self._season_id = season["sp_season_id"] if season else None
        except Exception:
            self._season_id = None

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO mlbb_registration_periods
                       (entity_type, entity_id, opens_at, closes_at, rule, created_by, status)
                       VALUES ('league', %s, NOW(), DATE_ADD(NOW(), INTERVAL 1 DAY), %s, 'simulation', 'open')""",
                    (entity_id, self.rule))
                self._period_id = cur.lastrowid

        # WP page
        slug = self.league_name.lower().replace(" ", "-")
        try:
            page = await self.api.create_page(self.league_name,
                '<p><em>Simulation in progress...</em></p>', slug)
            self._page_ids.append(page["id"])
            parent_id = subprocess.check_output([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "eval", 'echo get_page_by_path("bot-leagues")->ID;'
            ], text=True, stderr=subprocess.DEVNULL).strip()
            subprocess.run(["wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "update", str(page["id"]), f"--post_parent={parent_id}"],
                capture_output=True)
        except Exception as e:
            log.warning("WP page creation failed (non-fatal): %s", e)

        await self._admin_log("⚙️ Sim v2 Started", 0x9D4EDD, [
            ("League", self.league_name), ("Rule", label),
            ("Teams", str(self.args.teams)), ("Delay", f"{self.args.round_delay}s"),
        ])
        log.info("  League term=%s  table=%s  period=%s", self._league_term_id, entity_id, self._period_id)
        return True

    # ── Phase 2: Register players, create teams, invite + accept ──────────

    async def phase_teams(self) -> bool:
        n = self.args.teams
        log.info("━━━ Phase 2: Create %d teams of 5 via service functions ━━━", n)
        if self.args.dry_run:
            for code in random.sample(_TEAM_CODES, n):
                self.teams.append({"sp_team_id": 0, "name": f"Bot-{code}",
                                   "captain_id": self._fake_id(), "members": []})
            return True

        codes = random.sample(_TEAM_CODES, n)

        for code in codes:
            team_name = f"Bot-{code}"
            log.info("  Team '%s':", team_name)

            # ── Step 1: Register 5 players via player_register() ──────────
            members: List[dict] = []
            for p_idx in range(5):
                ign = self._ign()
                fake_id = self._fake_id()
                username = f"{ign.lower()}_{fake_id[-4:]}"
                avatar_png, _ = _gen_player_avatar()

                r = _assert(
                    await cmds.player_register(self.api, fake_id, username, ign, avatar_png=avatar_png),
                    f"player_register({ign})",
                )
                self._player_ids.append(r.data["sp_player_id"])
                members.append({
                    "discord_id": fake_id, "username": username,
                    "sp_player_id": r.data["sp_player_id"],
                    "ign": ign, "role": "captain" if p_idx == 0 else "player",
                })

            captain = members[0]

            # ── Step 2: Create team via team_create() ─────────────────────
            logo_png, pal = _gen_team_logo()
            c1 = "#{:02X}{:02X}{:02X}".format(*pal[1])
            c2 = "#{:02X}{:02X}{:02X}".format(*pal[2])

            r = _assert(
                await cmds.team_create(self.api, captain["discord_id"], team_name,
                                       logo_png=logo_png, color_primary=c1, color_secondary=c2),
                f"team_create({team_name})",
            )
            sp_team_id = r.data["sp_team_id"]
            self._team_ids.append(sp_team_id)

            # ── Step 3: Invite + accept for remaining 4 players ───────────
            for m in members[1:]:
                r = _assert(
                    await cmds.team_invite(captain["discord_id"], m["discord_id"],
                                           sp_team_id=sp_team_id, role=m["role"]),
                    f"team_invite({m['ign']})",
                )
                r = _assert(
                    await cmds.team_accept(self.api, m["discord_id"]),
                    f"team_accept({m['ign']})",
                )

            # ── Verify roster via team_roster() ───────────────────────────
            r = _assert(
                await cmds.team_roster(sp_team_id=sp_team_id),
                f"team_roster({team_name})",
            )
            assert len(r.data["roster"]) == 5, f"Expected 5 roster members, got {len(r.data['roster'])}"
            log.info("    Roster verified: 5 players")

            self.teams.append({
                "sp_team_id": sp_team_id, "name": team_name,
                "captain_id": captain["discord_id"], "members": members,
            })

            await self._admin_log("🛡️ Team Created (v2)", 0x3A86FF, [
                ("Team", team_name), ("ID", str(sp_team_id)),
                ("Captain", f"{captain['ign']} ({captain['username']})"),
                ("Colours", f"{c1} / {c2}"),
            ])

        return True

    # ── Phase 3: Register teams for the league ────────────────────────────

    async def phase_register(self) -> bool:
        log.info("━━━ Phase 3: League registration via service functions ━━━")
        if self.args.dry_run:
            return True

        entity_id = self._table_ids[0] if self._table_ids else self._league_term_id
        for team in self.teams:
            # league_register() — exercises captain check, open-period, conflict, cap
            r = _assert(
                await cmds.league_register(team["captain_id"], entity_id,
                                            sp_team_id=team["sp_team_id"]),
                f"league_register({team['name']})",
            )
            self._reg_ids.append(r.data["registration_id"])

            # admin_approve_registration()
            r = _assert(
                await cmds.admin_approve_registration(r.data["registration_id"], "simulation"),
                f"admin_approve({team['name']})",
            )

        return True

    # ── Phase 4: Schedule round-robin events ──────────────────────────────

    @staticmethod
    def _schedule_play_dates(n_events: int, start: datetime, direction: int = 1,
                              max_per_day: int = 4) -> List[datetime]:
        """
        Distribute n_events across Thu(3)/Fri(4)/Sat(5)/Sun(6) play-days.
        direction=+1 goes forward in time, -1 goes backward (for past events).
        """
        PLAY_DAYS = {3, 4, 5, 6}
        cursor = start.replace(hour=19, minute=0, second=0, microsecond=0)

        # Move to nearest play day in the given direction
        while cursor.weekday() not in PLAY_DAYS:
            cursor += timedelta(days=direction)

        dates: List[datetime] = []
        remaining = n_events
        weekend_count = 0

        while remaining > 0:
            if cursor.weekday() in PLAY_DAYS:
                slots = min(random.randint(1, max_per_day), remaining)
                # Ensure at least 1 per weekend
                end_of_weekend = cursor.weekday() == (6 if direction == 1 else 3)
                if end_of_weekend and weekend_count == 0:
                    slots = max(slots, 1)
                dates.extend([cursor] * slots)
                remaining -= slots
                weekend_count += slots
                if end_of_weekend:
                    weekend_count = 0
            cursor += timedelta(days=direction)

        # Past dates should be in chronological order
        if direction == -1:
            dates.reverse()
        return dates

    async def phase_schedule(self) -> bool:
        pairs = [(self.teams[i], self.teams[j])
                 for i in range(len(self.teams))
                 for j in range(i + 1, len(self.teams))]
        random.shuffle(pairs)

        # Split: ~60% past (completed with scores), ~40% future (upcoming)
        n_past = max(1, int(len(pairs) * 0.6))
        n_future = len(pairs) - n_past
        past_pairs = pairs[:n_past]
        future_pairs = pairs[n_past:]

        log.info("━━━ Phase 4: Schedule %d events (%d past + %d upcoming, Thu-Sun) ━━━",
                 len(pairs), n_past, n_future)

        now = datetime.now(timezone.utc)
        # Past dates go backward from last Thursday/Friday/etc before today
        past_start = now - timedelta(days=1)
        past_dates = self._schedule_play_dates(n_past, past_start, direction=-1)
        # Future dates go forward from next play day
        future_dates = self._schedule_play_dates(n_future, now + timedelta(days=1), direction=1) if n_future else []

        if self.args.dry_run:
            for (h, a), dt in zip(past_pairs, past_dates):
                self.events.append({"sp_event_id": 0, "name": f"{h['name']} vs {a['name']}",
                                    "home": h, "away": a, "date": dt.strftime("%Y-%m-%d"), "past": True})
                log.info("  [DRY] %s %s PAST  — %s vs %s", dt.strftime("%Y-%m-%d"), dt.strftime("%a"),
                         h["name"], a["name"])
            for (h, a), dt in zip(future_pairs, future_dates):
                self.events.append({"sp_event_id": 0, "name": f"{h['name']} vs {a['name']}",
                                    "home": h, "away": a, "date": dt.strftime("%Y-%m-%d"), "past": False})
                log.info("  [DRY] %s %s FUTURE — %s vs %s", dt.strftime("%Y-%m-%d"), dt.strftime("%a"),
                         h["name"], a["name"])
            return True

        from services.db_helpers import set_event_results

        # ── Create past events (with results) ────────────────────────────
        for (home, away), dt in zip(past_pairs, past_dates):
            date_str = dt.strftime("%Y-%m-%dT19:00:00")
            day_name = dt.strftime("%a")
            name = f"{home['name']} vs {away['name']}"
            event = await self.api.create_event(
                name, home["sp_team_id"], away["sp_team_id"], date_str,
                league_ids=[self._league_term_id] if self._league_term_id else None,
                season_ids=[self._season_id] if self._season_id else None,
            )
            sp_event_id = event["id"]
            self._event_ids.append(sp_event_id)

            # Generate random scores and write SP results
            winner = home if random.random() < 0.55 else away
            loser = away if winner is home else home
            w_score = random.randint(8, 22)
            l_score = random.randint(2, max(2, w_score - 2))
            h_score = w_score if winner is home else l_score
            a_score = l_score if winner is home else w_score

            await set_event_results(sp_event_id, home["sp_team_id"], away["sp_team_id"], h_score, a_score)

            self.events.append({"sp_event_id": sp_event_id, "name": name,
                                "home": home, "away": away, "date": dt.strftime("%Y-%m-%d"),
                                "past": True, "vc_id": None,
                                "home_score": h_score, "away_score": a_score})
            log.info("  %s %s PLAYED '%s' → %d-%d (ID %s)",
                     dt.strftime("%Y-%m-%d"), day_name, name, h_score, a_score, sp_event_id)

        # ── Create future events (no results) ────────────────────────────
        for (home, away), dt in zip(future_pairs, future_dates):
            date_str = dt.strftime("%Y-%m-%dT19:00:00")
            day_name = dt.strftime("%a")
            name = f"{home['name']} vs {away['name']}"
            event = await self.api.create_event(
                name, home["sp_team_id"], away["sp_team_id"], date_str,
                league_ids=[self._league_term_id] if self._league_term_id else None,
                season_ids=[self._season_id] if self._season_id else None,
            )
            self._event_ids.append(event["id"])
            self.events.append({"sp_event_id": event["id"], "name": name,
                                "home": home, "away": away, "date": dt.strftime("%Y-%m-%d"),
                                "past": False, "vc_id": None})
            log.info("  %s %s UPCOMING '%s' (ID %s)", dt.strftime("%Y-%m-%d"), day_name, name, event["id"])

        # Post schedule to notifications
        past_lines = [f"• `{e['date']}` — **{e['name']}** → {e.get('home_score',0)}-{e.get('away_score',0)}"
                      for e in self.events if e.get("past")]
        future_lines = [f"• `{e['date']}` — **{e['name']}**" for e in self.events if not e.get("past")]
        desc = ""
        if past_lines:
            desc += "**Completed**\n" + "\n".join(past_lines) + "\n\n"
        if future_lines:
            desc += "**Upcoming**\n" + "\n".join(future_lines)
        await self._notify(_embed(
            f"📅 {self.league_name} — Schedule", 0xFFB703, [],
            footer=f"🤖 SimBot v2 · {n_past} played, {n_future} upcoming · {self.rule}",
            desc=desc,
        ))
        return True

    # ── Phase 5: Simulate matches via match_submit + match_confirm ────────

    async def phase_matches(self) -> bool:
        future_events = [e for e in self.events if not e.get("past")]
        past_events = [e for e in self.events if e.get("past")]

        # Past events already have SP results — run them through match_submit/confirm
        # to populate mlbb_match_submissions (the bot's internal records)
        log.info("━━━ Phase 5a: Record past results (%d completed events) ━━━", len(past_events))
        for event in past_events:
            if self.args.dry_run:
                continue
            home, away = event["home"], event["away"]
            h_score, a_score = event.get("home_score", 0), event.get("away_score", 0)
            winner = home if h_score > a_score else away
            loser = away if winner is home else home
            w_kills, l_kills = max(h_score, a_score), min(h_score, a_score)

            battle_id = str(random.randint(10**16, 10**17 - 1))
            dur_s = random.randint(7*60, 22*60)
            match_data = cmds.MatchData(
                battle_id=battle_id, winner_kills=w_kills, loser_kills=l_kills,
                duration=f"{dur_s//60:02d}:{dur_s%60:02d}",
                confidence=0.97, screenshot_url=config.WP_URL,
            )
            r = _assert(await cmds.match_submit(winner["captain_id"], match_data,
                                                 event_id=event.get("sp_event_id")),
                         f"past_submit({event['name']})")
            sub_id = r.data["submission_id"]
            self.submission_ids.append(sub_id)
            r = _assert(await cmds.match_confirm(loser["captain_id"], sub_id),
                         f"past_confirm({event['name']})")
            log.info("  PAST %s → %d-%d  (sub #%d)", event["name"], w_kills, l_kills, sub_id)

        log.info("━━━ Phase 5b: Simulate future matches (%d upcoming, %ds delay) ━━━",
                 len(future_events), self.args.round_delay)

        for idx, event in enumerate(future_events):
            home, away = event["home"], event["away"]
            log.info("  [%d/%d] %s", idx + 1, len(future_events), event["name"])

            if self.args.dry_run:
                continue

            # Create voice channel
            if self.voice_cat and self.guild_id:
                vc_id = await self.dc.create_vc(self.guild_id,
                    f"🎮 {home['name']} vs {away['name']}", self.voice_cat)
                if vc_id:
                    event["vc_id"] = vc_id
                    self._vc_ids.append(vc_id)

            # Upload one screenshot per match
            screenshot_url = config.WP_URL
            try:
                media = await self.api.upload_media(
                    _VICTORY_PNG, f"sim-v2-{random.randint(1000,9999)}.png", "image/png")
                screenshot_url = media.get("source_url", config.WP_URL)
            except Exception:
                pass

            # Simulate BO series
            games = self._series_games(home, away)

            for game_num, (winner, loser, w_kills, l_kills) in enumerate(games, 1):
                battle_id = str(random.randint(10**16, 10**17 - 1))
                dur_s = random.randint(7*60, 22*60)
                duration = f"{dur_s//60:02d}:{dur_s%60:02d}"
                ts = (datetime.now(timezone.utc) - timedelta(minutes=random.randint(5, 60)))
                match_ts = ts.strftime("%m/%d/%Y %H:%M:%S")
                game_label = f"Game {game_num}" if len(games) > 1 else "Match"

                # match_submit() — winner captain submits
                match_data = cmds.MatchData(
                    battle_id=battle_id, winner_kills=w_kills, loser_kills=l_kills,
                    duration=duration, match_timestamp=match_ts,
                    confidence=0.97, screenshot_url=screenshot_url,
                )
                r = _assert(
                    await cmds.match_submit(winner["captain_id"], match_data,
                                             event_id=event.get("sp_event_id")),
                    f"match_submit({game_label} {winner['name']})",
                )
                sub_id = r.data["submission_id"]
                self.submission_ids.append(sub_id)

                # match_confirm() — loser captain confirms
                r = _assert(
                    await cmds.match_confirm(loser["captain_id"], sub_id),
                    f"match_confirm({game_label} by {loser['name']})",
                )

                # Post result to notifications
                await self._notify(_embed(
                    f"✅ {game_label} Confirmed — {event['name']}", 0x2ECC71,
                    [("🏆 Winner", winner["name"]), ("Score", f"**{w_kills}–{l_kills}**"),
                     ("BattleID", f"`{battle_id}`"), ("Duration", duration),
                     ("Sub", f"`#{sub_id}`")],
                    footer=f"🤖 SimBot v2 · {self.league_name}",
                ))

                log.info("    %s %s wins %d–%d (sub #%d)", game_label, winner["name"], w_kills, l_kills, sub_id)

            # Delete voice channel
            if event.get("vc_id"):
                await asyncio.sleep(2)
                await self.dc.delete_channel(event["vc_id"])
                if event["vc_id"] in self._vc_ids:
                    self._vc_ids.remove(event["vc_id"])

            if idx < len(self.events) - 1:
                await asyncio.sleep(self.args.round_delay)

        return True

    def _series_games(self, home: dict, away: dict) -> List[Tuple[dict, dict, int, int]]:
        wins_needed = self._wins_needed()
        winner_team = home if random.random() < 0.55 else away
        loser_team = away if winner_team is home else home
        w_wins = l_wins = 0
        games = []
        while w_wins < wins_needed:
            w_k = random.randint(8, 22)
            l_k = random.randint(2, max(2, w_k - 2))
            if w_wins > 0 and l_wins < wins_needed - 1 and random.random() < 0.35:
                games.append((loser_team, winner_team, w_k, l_k))
                l_wins += 1
            else:
                games.append((winner_team, loser_team, w_k, l_k))
                w_wins += 1
        return games

    # ── Phase 6: Final summary + player_profile() verification ────────────

    async def phase_summary(self):
        log.info("━━━ Phase 6: Summary + player_profile verification ━━━")
        if self.args.dry_run or not self.submission_ids:
            return

        # Tally wins
        standings: Dict[int, dict] = {t["sp_team_id"]: {"name": t["name"], "wins": 0} for t in self.teams}
        ph = ",".join(["%s"] * len(self.submission_ids))
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT winning_team_id, COUNT(*) FROM mlbb_match_submissions "
                    f"WHERE id IN ({ph}) AND status='confirmed' GROUP BY winning_team_id",
                    tuple(self.submission_ids))
                for tid, wins in await cur.fetchall():
                    if tid in standings:
                        standings[tid]["wins"] = wins

        ranked = sorted(standings.values(), key=lambda x: x["wins"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'{i+1}.'} **{t['name']}** — {t['wins']} wins"
                 for i, t in enumerate(ranked)]

        await self._notify(_embed(
            f"🏆 {self.league_name} — Complete", 0x9D4EDD, [],
            footer=f"🤖 SimBot v2 · {len(self.submission_ids)} games · {self.rule}",
            desc="**Final Standings**\n\n" + "\n".join(lines),
        ))

        # Verify player_profile() for each captain
        for team in self.teams:
            r = _assert(
                await cmds.player_profile(team["captain_id"]),
                f"player_profile(captain of {team['name']})",
            )
            assert any(t["sp_team_id"] == team["sp_team_id"] for t in r.data["teams"]), \
                f"Captain's profile missing team {team['name']}"

        log.info("Champion: %s (%d wins)", ranked[0]["name"], ranked[0]["wins"])

        # Update WP page with final standings
        await self._update_wp_page(ranked)

    async def _update_wp_page(self, ranked: List[dict]):
        if not self._page_ids:
            return
        label = RULE_LABELS[self.rule]
        team_rows = "".join(
            f'<tr><td><strong>{t["name"]}</strong></td><td>{len(t.get("members",[]))}</td></tr>'
            for t in self.teams
        )
        event_rows = "".join(
            f'<tr><td>{e.get("date","")}</td><td>{e["home"]["name"]}</td><td>{e["away"]["name"]}</td></tr>'
            for e in self.events
        )
        medals = ["🥇", "🥈", "🥉"]
        standing_rows = "".join(
            f'<tr><td>{i+1}</td><td>{medals[i] if i < 3 else ""} <strong>{t["name"]}</strong></td>'
            f'<td>{t["wins"]}</td></tr>'
            for i, t in enumerate(ranked)
        )
        content = (
            f'<hr/><h2>League Rules</h2>'
            f'<table class="league-rules"><tbody>'
            f'<tr><th>Format</th><td>{label}</td></tr>'
            f'<tr><th>Type</th><td>Bot Simulation (v2 — service layer)</td></tr>'
            f'</tbody></table>'
            f'<h2>Teams</h2>'
            f'<table class="sp-data-table"><thead><tr><th>Team</th><th>Roster</th></tr></thead>'
            f'<tbody>{team_rows}</tbody></table>'
            f'<h2>Schedule</h2>'
            f'<table class="sp-data-table"><thead><tr><th>Date</th><th>Home</th><th>Away</th></tr></thead>'
            f'<tbody>{event_rows}</tbody></table>'
            f'<h2>Standings</h2>'
            f'<table class="sp-data-table"><thead><tr><th>#</th><th>Team</th><th>Wins</th></tr></thead>'
            f'<tbody>{standing_rows}</tbody></table>'
            f'<p><a href="/bot-leagues/">← Back to Bot Leagues</a></p>'
        )
        tmp = "/tmp/mlbb_sim_v2_page.html"
        with open(tmp, "w") as f:
            f.write(content)
        subprocess.run([
            "wp", "--path=/var/www/sites/play.mlbb.site",
            "--skip-plugins", "--skip-themes", "--allow-root",
            "eval", f'wp_update_post(["ID"=>{self._page_ids[0]},"post_content"=>file_get_contents("{tmp}")]);'
        ], capture_output=True)

    # ── Cleanup ────────────────────────────────────────────────────────────

    async def cleanup(self):
        log.info("━━━ Cleanup ━━━")
        for eid in self._event_ids:
            try: await self.api.delete_event(eid)
            except: pass
        # Use team_delete() service function for proper teardown
        for team in self.teams:
            if team["sp_team_id"]:
                r = await cmds.team_delete(self.api, team["captain_id"], is_admin=True,
                                            sp_team_id=team["sp_team_id"])
                if r.ok:
                    log.info("  team_delete(%s) OK", team["name"])
                else:
                    log.warning("  team_delete(%s): %s", team["name"], r.error)
        # Delete players
        for pid in self._player_ids:
            try: await self.api.delete_player(pid)
            except: pass
        for tbl_id in self._table_ids:
            try: await self.api.delete_table(tbl_id)
            except: pass
        if self._league_term_id:
            try: await self.api.delete_league(self._league_term_id)
            except: pass
        for vc_id in list(self._vc_ids):
            try: await self.dc.delete_channel(vc_id)
            except: pass
        for pid in self._page_ids:
            subprocess.run(["wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "delete", str(pid), "--force"], capture_output=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                if self.submission_ids:
                    ph = ",".join(["%s"] * len(self.submission_ids))
                    await cur.execute(f"DELETE FROM mlbb_match_submissions WHERE id IN ({ph})",
                                     tuple(self.submission_ids))
                if self._reg_ids:
                    ph = ",".join(["%s"] * len(self._reg_ids))
                    await cur.execute(f"DELETE FROM mlbb_team_registrations WHERE id IN ({ph})",
                                     tuple(self._reg_ids))
                if self._fake_ids:
                    ph = ",".join(["%s"] * len(self._fake_ids))
                    await cur.execute(f"DELETE FROM mlbb_player_roster WHERE discord_id IN ({ph})",
                                     tuple(self._fake_ids))
                    await cur.execute(f"DELETE FROM mlbb_team_invites WHERE inviter_id IN ({ph}) OR invitee_id IN ({ph})",
                                     tuple(self._fake_ids) + tuple(self._fake_ids))
                if self._period_id:
                    await cur.execute("DELETE FROM mlbb_registration_periods WHERE id=%s",
                                     (self._period_id,))
        log.info("  Cleanup complete")

    # ── Run ────────────────────────────────────────────────────────────────

    async def run(self):
        n = self.args.teams
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("MLBB Tournament Bot — League Simulation v2 (service layer)")
        log.info("Teams: %d | Matches: %d | Rule: %s | Delay: %ds",
                 n, n*(n-1)//2, self.args.rule or "random", self.args.round_delay)
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        ok = True
        try:
            ok = ok and await self.phase_league()
            ok = ok and await self.phase_teams()
            ok = ok and await self.phase_register()
            ok = ok and await self.phase_schedule()
            ok = ok and await self.phase_matches()
            await self.phase_summary()
        except (RuntimeError, AssertionError) as e:
            log.error("Simulation assertion failed: %s", e)
            await self._admin_log("💥 Sim v2 Assertion Failed", 0xE74C3C,
                                  [("Error", str(e)[:200]), ("League", self.league_name)])
            ok = False
        except Exception as e:
            log.exception("Simulation error: %s", e)
            ok = False
        finally:
            if not self.args.no_cleanup and not self.args.dry_run:
                await self.cleanup()
            elif self.args.no_cleanup and not self.args.dry_run:
                # Refresh hub page
                subprocess.run(["wp", "--path=/var/www/sites/play.mlbb.site",
                    "--skip-plugins", "--skip-themes", "--allow-root", "eval", """
$hub = get_page_by_path("bot-leagues"); if (!$hub) exit;
$children = get_pages(["parent"=>$hub->ID,"post_status"=>"publish","sort_column"=>"post_date","sort_order"=>"DESC"]);
$list=""; foreach($children as $c) $list.='<li><a href="'.get_permalink($c->ID).'">'.esc_html($c->post_title).'</a></li>';
$bl=$list?'<ul class="wp-block-list">'.$list.'</ul>':'<p><em>No bot leagues running.</em></p>';
wp_update_post(["ID"=>$hub->ID,"post_content"=>"<p>Simulated leagues from the bot testing system.</p><hr/><h2>Active Bot Leagues</h2>".$bl."<hr/><p>Created via <code>simulate_league_v2.py --no-cleanup</code>.</p>"]);
"""], capture_output=True)
                log.info("Artifacts retained. Teams: %s", ", ".join(t["name"] for t in self.teams))

        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("Simulation %s", "PASS" if ok else "FAIL")
        return ok


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Customer-experience league simulation (service layer).")
    p.add_argument("--teams", type=int, default=4, help="Number of teams (default 4, min 2)")
    p.add_argument("--round-delay", type=int, default=8, dest="round_delay", help="Seconds between rounds (default 8)")
    p.add_argument("--no-cleanup", action="store_true", dest="no_cleanup", help="Keep artifacts")
    p.add_argument("--rule", choices=RULES, default=None, help="Force match rule")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="Plan only")
    args = p.parse_args()
    if args.teams < 2: p.error("--teams must be >= 2")
    if args.teams > len(_TEAM_CODES): p.error(f"--teams max is {len(_TEAM_CODES)}")
    return args


async def main():
    args = _parse()
    config.validate()
    if not args.dry_run:
        await db.init()
    try:
        ok = await LeagueSimulatorV2(args).run()
        sys.exit(0 if ok else 1)
    finally:
        if not args.dry_run:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
