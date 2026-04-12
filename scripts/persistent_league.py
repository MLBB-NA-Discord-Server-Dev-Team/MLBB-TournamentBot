#!/usr/bin/env python3
"""
scripts/persistent_league.py -- Weekly persistent bot-league state machine.

Runs via cron every 30 minutes. Weekly cadence (UTC):
  Mon-Wed: REGISTRATION  — drip-feed players, form teams, register for league
  Thu-Sat: PLAYING       — simulate matches as events come due
  Sun:     CLEANUP       — delete all artifacts, start fresh next Monday

The persistent league generates its OWN round-robin schedule on Thursday
(bypassing the scheduler's auto-generation for its periods). The scheduler
still handles auto-approve, result-sync, and hub page updates normally.

Usage:
    cd /root/MLBB-TournamentBot
    python scripts/persistent_league.py              # normal tick
    python scripts/persistent_league.py --reset      # wipe state file
    python scripts/persistent_league.py --status     # print current state
    python scripts/persistent_league.py --dry-run    # show what would happen
    python scripts/persistent_league.py --force-init # bypass day-of-week check

Cron:
    */30 * * * * cd /root/MLBB-TournamentBot && venv/bin/python scripts/persistent_league.py >> /var/log/mlbb-persistent-league.log 2>&1
"""
import asyncio
import argparse
import fcntl
import json
import logging
import math
import os
import random
import struct
import subprocess
import sys
import time
import zlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services import db
from services.sportspress import SportsPressAPI
from services import command_services as cmds
from services import league_lifecycle as lifecycle
from services.round_robin import generate_schedule, ScheduleError

# -- Paths ---------------------------------------------------------------------
DATA_DIR = Path(__file__).parent.parent / "data"
STATE_FILE = DATA_DIR / "persistent_league.json"
LOCK_FILE = DATA_DIR / "persistent_league.lock"

# -- Logging -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("persistent_league")

# -- Constants -----------------------------------------------------------------
_FAKE_BASE = 7_770_000_000_000_000
DISCORD_API = "https://discord.com/api/v10"
BOT_LEAGUES_PARENT_ID = 1123  # WP page ID for /bot-leagues/

_ADJ = [
    "Crimson", "Azure", "Void", "Neon", "Shadow", "Cosmic", "Ember",
    "Frost", "Storm", "Phantom", "Iron", "Crystal", "Obsidian", "Lunar",
    "Solar", "Thunder", "Midnight", "Golden", "Silent", "Infernal",
]
_NOUN = [
    "Phoenix", "Serpent", "Falcon", "Titan", "Sentinel", "Raptor",
    "Vanguard", "Eclipse", "Tempest", "Nexus", "Monarch", "Hydra",
    "Specter", "Zenith", "Forge", "Bastion", "Chimera", "Harbinger",
]
_GAMER_PRE = [
    "Shadow", "Dark", "Neon", "Pixel", "Hyper", "Ultra", "Void", "Blitz",
    "Storm", "Frost", "Blaze", "Glitch", "Turbo", "Cosmic", "Toxic",
    "Dread", "Flux", "Omega", "Zero", "Nova", "Razor", "Drift", "Venom",
]
_GAMER_SUF = [
    "Blade", "Wolf", "Hawk", "Storm", "Fire", "Shot", "King", "Fang",
    "Fury", "Strike", "Lux", "Byte", "Core", "Rush", "Edge",
    "Wraith", "Sage", "Bolt", "Vex", "Dusk", "Lynx", "Mace",
]
RULES = ["DPBO1", "DPBO3", "BrawlBO1", "BrawlBO3"]
RULE_LABELS = {
    "DPBO1": "Draft Pick - Best of 1", "DPBO3": "Draft Pick - Best of 3",
    "BrawlBO1": "Brawl - Best of 1", "BrawlBO3": "Brawl - Best of 3",
}

# -- Pixel art (reused from autonomous_sim) ------------------------------------
RGB = Tuple[int, int, int]
_PALETTES = [
    ((15, 15, 25), (255, 60, 60), (255, 200, 50), (60, 200, 255)),
    ((12, 22, 35), (0, 230, 150), (255, 80, 200), (255, 255, 80)),
    ((25, 12, 40), (180, 50, 255), (50, 200, 255), (255, 150, 50)),
    ((10, 30, 20), (50, 255, 100), (200, 255, 50), (255, 80, 80)),
    ((30, 15, 10), (255, 120, 30), (255, 220, 80), (80, 180, 255)),
    ((20, 20, 35), (100, 100, 255), (220, 60, 255), (60, 255, 200)),
]


def _png_encode(pixels: List[List[RGB]], scale: int) -> bytes:
    h_art, w_art = len(pixels), len(pixels[0])
    width, height = w_art * scale, h_art * scale
    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = tag + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)
    rows = []
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


def _gen_avatar() -> bytes:
    pal = random.choice(_PALETTES)
    bg, accents = pal[0], list(pal[1:])
    grid = [[bg] * 8 for _ in range(8)]
    for y in range(8):
        for x in range(4):
            if random.random() < 0.38:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][7 - x] = c
    return _png_encode(grid, scale=16)


def _gen_logo() -> Tuple[bytes, Tuple[RGB, ...]]:
    pal = random.choice(_PALETTES)
    bg, accents = pal[0], list(pal[1:])
    grid = [[bg] * 10 for _ in range(10)]
    for y in range(10):
        for x in range(5):
            if random.random() < 0.42:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][9 - x] = c
    return _png_encode(grid, scale=16), pal


# -- Discord HTTP (minimal) ---------------------------------------------------
class DiscordHTTP:
    def __init__(self, token: str):
        self._auth = {"Authorization": f"Bot {token}", "User-Agent": "MLBB-PersistentLeague/1.0"}

    async def send_embed(self, channel_id: int, embed: dict):
        url = f"{DISCORD_API}/channels/{channel_id}/messages"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers={**self._auth, "Content-Type": "application/json"},
                              data=json.dumps({"embeds": [embed]}).encode()) as resp:
                if resp.status >= 400:
                    log.warning("Discord POST %d: %s", resp.status, (await resp.text())[:200])


def _embed(title: str, color: int, fields: List[Tuple[str, str]], desc: str = "") -> dict:
    e = {"title": title, "color": color,
         "timestamp": datetime.now(timezone.utc).isoformat(),
         "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields],
         "footer": {"text": "Persistent Bot-League | autonomous lifecycle"}}
    if desc:
        e["description"] = desc
    return e


# -- State Machine -------------------------------------------------------------

def _default_state() -> dict:
    return {
        "version": 2,
        "state": "INIT",
        "league_name": None,
        "league_term_id": None,
        "table_id": None,
        "period_id": None,
        "sp_season_id": None,
        "season_name": None,
        "rule": None,
        "target_teams": None,
        "week_start": None,   # Monday of this league's week (YYYY-MM-DD)
        "reg_close_date": None,  # Wed 23:59 UTC
        "play_start": None,      # Thursday
        "play_end": None,        # Saturday
        "cleanup_day": None,     # Sunday
        "teams": [],
        "pending_players": [],
        "event_ids": [],
        "page_id": None,
        "fake_id_offset": 0,
        "created_at": None,
        "state_changed_at": None,
        "last_tick_at": None,
        "cycle": 0,
        "history": [],
    }


def _week_dates(today: datetime) -> dict:
    """Return the Mon/Wed/Thu/Sat/Sun dates for the week containing `today`."""
    # Monday is weekday 0
    monday = today - timedelta(days=today.weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "mon":     monday,
        "wed_end": monday + timedelta(days=2, hours=23, minutes=59, seconds=59),
        "thu":     monday + timedelta(days=3),
        "sat":     monday + timedelta(days=5),
        "sat_end": monday + timedelta(days=5, hours=23, minutes=59, seconds=59),
        "sun":     monday + timedelta(days=6),
    }


class PersistentLeague:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api = SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)
        self.dc = DiscordHTTP(config.DISCORD_TOKEN)
        # Multi-guild broadcast: read data/guilds.json for bot_leagues channels
        self.notify_channels = self._load_notify_channels()
        self.state = self._load_state()

    def _load_notify_channels(self) -> List[int]:
        """Load bot_leagues channel IDs from data/guilds.json (one per guild)."""
        guilds_file = DATA_DIR / "guilds.json"
        if guilds_file.exists():
            try:
                with open(guilds_file) as f:
                    data = json.load(f)
                ids = [int(g["bot_leagues"]) for g in data.get("guilds", []) if g.get("bot_leagues")]
                if ids:
                    return ids
            except Exception as e:
                log.warning("Could not load guilds.json: %s", e)
        # Fallback: legacy single channel
        if config.BOT_LEAGUE_CHANNEL_ID:
            return [config.BOT_LEAGUE_CHANNEL_ID]
        if config.ADMIN_LOG_CHANNEL_ID:
            return [config.ADMIN_LOG_CHANNEL_ID]
        return []

    # -- State I/O -------------------------------------------------------------

    def _load_state(self) -> dict:
        if self.args.reset or not STATE_FILE.exists():
            log.info("Starting fresh (reset=%s, exists=%s)", self.args.reset, STATE_FILE.exists())
            s = _default_state()
            # Preserve offset from old state to avoid ID collisions
            if self.args.reset and STATE_FILE.exists():
                try:
                    with open(STATE_FILE) as f:
                        old = json.load(f)
                    s["fake_id_offset"] = old.get("fake_id_offset", 0) + 100
                    s["history"] = old.get("history", [])
                    s["cycle"] = old.get("cycle", 0)
                except Exception:
                    s["fake_id_offset"] = 50000
            return s
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            if not isinstance(data, dict) or "state" not in data:
                raise ValueError("Invalid state structure")
            return data
        except Exception as e:
            log.error("Corrupt state file: %s — starting fresh with high offset", e)
            s = _default_state()
            s["fake_id_offset"] = 50000  # avoid ID collisions
            return s

    def _save_state(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2, default=str)
        os.replace(str(tmp), str(STATE_FILE))

    # -- Helpers ---------------------------------------------------------------

    def _fake_id(self) -> str:
        fid = str(_FAKE_BASE + self.state["fake_id_offset"])
        self.state["fake_id_offset"] += 1
        return fid

    def _ign(self) -> str:
        return f"{random.choice(_GAMER_PRE)}{random.choice(_GAMER_SUF)}{random.randint(10, 99)}"

    def _transition(self, new_state: str):
        old = self.state["state"]
        self.state["state"] = new_state
        self.state["state_changed_at"] = datetime.now(timezone.utc).isoformat()
        log.info("TRANSITION: %s -> %s (%s)", old, new_state, self.state.get("league_name", "?"))

    async def _notify(self, title: str, color: int, fields: List[Tuple[str, str]], desc: str = ""):
        if self.args.dry_run or not self.notify_channels:
            return
        embed = _embed(title, color, fields, desc)
        for ch_id in self.notify_channels:
            try:
                await self.dc.send_embed(ch_id, embed)
            except Exception as e:
                log.warning("Discord notification to %s failed: %s", ch_id, e)

    # -- Main tick -------------------------------------------------------------

    async def tick(self):
        state = self.state["state"]
        now = datetime.now(timezone.utc)
        weekday = now.weekday()  # Mon=0, Sun=6
        log.info("=== Tick: state=%s weekday=%s league=%s ===",
                 state, now.strftime("%a"), self.state.get("league_name", "N/A"))

        if state == "INIT":
            await self._tick_init(now, weekday)
        elif state == "REGISTRATION":
            await self._tick_registration(now, weekday)
        elif state == "PLAYING":
            await self._tick_playing(now, weekday)
        elif state == "CLEANUP":
            await self._tick_cleanup(now, weekday)
        else:
            log.error("Unknown state '%s', resetting", state)
            self._transition("INIT")

        self.state["last_tick_at"] = now.isoformat()
        if not self.args.dry_run:
            self._save_state()

    # -- INIT ------------------------------------------------------------------

    async def _tick_init(self, now: datetime, weekday: int):
        """
        INIT only creates a league on Monday (weekday 0).
        Other days: idle.
        --force-init bypasses the weekday check (for testing).
        """
        if weekday != 0 and not self.args.force_init:
            log.info("INIT: waiting for Monday (today is %s)", now.strftime("%A"))
            return

        # Get current season
        season = await lifecycle.get_season_status()
        if not season.ok or not season.data.get("current"):
            log.warning("No current season, will retry next tick")
            return

        current = season.data["current"]
        sp_season_id = current["sp_season_id"]
        season_name = current["season_name"]

        # Calculate week dates. If force-init runs past Wednesday, use NEXT week.
        target = now
        if self.args.force_init and weekday >= 3:
            target = now + timedelta(days=7 - weekday)  # jump to next Monday
            log.info("force-init mid-week: using next week starting %s",
                     target.strftime("%a %b %d"))
        dates = _week_dates(target)
        reg_open = dates["mon"]
        reg_close = dates["wed_end"]
        play_start = dates["thu"].date()
        play_end = dates["sat"].date()
        cleanup_day = dates["sun"]

        # Pick parameters (cap at 4 teams for Thu-Sat = 6 matches over 3 days)
        rule = random.choice(RULES)
        target_teams = 4
        league_name = f"Bot-{random.choice(_ADJ)}{random.choice(_NOUN)}"

        log.info("INIT: %s | %s | %d teams | reg %s-%s | play %s-%s",
                 league_name, rule, target_teams,
                 reg_open.strftime("%a %b %d"), reg_close.strftime("%a %b %d"),
                 play_start.strftime("%a"), play_end.strftime("%a"))

        if self.args.dry_run:
            log.info("[DRY] Would create league infrastructure")
            return

        # Create SP league term
        term = await self.api.create_league(league_name, RULE_LABELS.get(rule, rule))
        league_term_id = term["id"]

        # Create SP table
        table = await self.api.create_table(
            f"{league_name} -- {season_name}", RULE_LABELS.get(rule, rule),
            league_ids=[league_term_id],
            season_ids=[sp_season_id],
        )
        table_id = table["id"]

        # Create registration period: opens now (or Monday start), closes Wed 23:59
        # If we're starting mid-week (force-init), opens_at = now
        effective_open = now if now > reg_open else reg_open
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO mlbb_registration_periods
                       (entity_type, entity_id, sp_season_id, opens_at, closes_at,
                        rule, created_by, status)
                       VALUES ('league', %s, %s, %s, %s, %s, 'persistent_league', 'open')""",
                    (table_id, sp_season_id,
                     effective_open.strftime("%Y-%m-%d %H:%M:%S"),
                     reg_close.strftime("%Y-%m-%d %H:%M:%S"),
                     rule),
                )
                period_id = cur.lastrowid

        # Create WP page
        page_result = await lifecycle.generate_league_wp_page(table_id, league_term_id, season_name)
        page_id = page_result.data.get("page_id") if page_result.ok else None

        # Set page parent to /bot-leagues/
        if page_id:
            subprocess.run([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "update", str(page_id), f"--post_parent={BOT_LEAGUES_PARENT_ID}",
            ], capture_output=True)

        # Update state
        self.state.update({
            "league_name": league_name,
            "league_term_id": league_term_id,
            "table_id": table_id,
            "period_id": period_id,
            "sp_season_id": sp_season_id,
            "season_name": season_name,
            "rule": rule,
            "target_teams": target_teams,
            "week_start": reg_open.date().isoformat(),
            "reg_close_date": reg_close.isoformat(),
            "play_start": play_start.isoformat(),
            "play_end": play_end.isoformat(),
            "cleanup_day": cleanup_day.date().isoformat(),
            "teams": [],
            "pending_players": [],
            "event_ids": [],
            "page_id": page_id,
            "created_at": now.isoformat(),
            "cycle": self.state.get("cycle", 0) + 1,
        })
        self._transition("REGISTRATION")

        await self._notify(
            f"Weekly Bot-League Started: {league_name}", 0x9D4EDD,
            [("Rule", RULE_LABELS.get(rule, rule)),
             ("Teams", str(target_teams)),
             ("Reg Closes", reg_close.strftime("Wed %b %d")),
             ("Play", f"Thu-Sat ({play_start.strftime('%b %d')} - {play_end.strftime('%b %d')})"),
             ("Cycle", f"#{self.state['cycle']}")],
        )

    # -- REGISTRATION ----------------------------------------------------------

    async def _tick_registration(self, now: datetime, weekday: int):
        teams_created = len(self.state["teams"])
        target = self.state["target_teams"]
        reg_close = datetime.fromisoformat(self.state["reg_close_date"])
        reg_open = datetime.fromisoformat(self.state["reg_close_date"]) - timedelta(days=3)

        # Wait until registration actually opens (league scheduled for future week)
        if now < reg_open:
            log.info("REG: waiting for registration to open (%s)",
                     reg_open.strftime("%a %b %d %H:%M"))
            return

        # Close registration when reg_close datetime has passed
        if now >= reg_close:
            if teams_created >= 2:
                log.info("Registration closed with %d/%d teams, generating schedule", teams_created, target)
                # Close period if not already
                async with db.get_conn() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE mlbb_registration_periods SET status='closed' WHERE id=%s AND status='open'",
                            (self.state["period_id"],),
                        )
                # Generate schedule
                if not self.args.dry_run:
                    ok = await self._generate_schedule()
                    if not ok:
                        log.error("Schedule generation failed, cleaning up")
                        self._transition("CLEANUP")
                        return
                self._transition("PLAYING")
                return
            else:
                log.warning("Registration closed with only %d teams, cleaning up", teams_created)
                self.state["history"].append({
                    "league_name": self.state["league_name"],
                    "rule": self.state["rule"],
                    "teams": teams_created,
                    "result": "cancelled_insufficient_teams",
                    "completed_at": now.isoformat(),
                })
                self._transition("CLEANUP")
                return

        if teams_created >= target:
            log.info("All %d teams created, waiting for registration window to close", target)
            return

        if self.args.dry_run:
            log.info("[DRY] Would drip-feed players/teams (%d/%d teams)", teams_created, target)
            return

        # Drip-feed pacing (every 30 min = 48 ticks/day)
        hours_remaining = max(0.5, (reg_close - now).total_seconds() / 3600)
        ticks_remaining = max(1, int(hours_remaining * 2))
        teams_needed = target - teams_created
        players_needed = (teams_needed * 5) - len(self.state["pending_players"])
        players_this_tick = max(1, min(3, math.ceil(players_needed / ticks_remaining)))

        # Accelerate if close to deadline
        if hours_remaining < 6.0:
            players_this_tick = min(5, players_needed)

        log.info("REG: %d/%d teams | %d pending | +%d players | %.1fh left",
                 teams_created, target, len(self.state["pending_players"]),
                 players_this_tick, hours_remaining)

        # Register players
        for _ in range(players_this_tick):
            if len(self.state["pending_players"]) >= 5:
                break  # form team first
            ign = self._ign()
            fid = self._fake_id()
            username = f"{ign.lower()}_{fid[-4:]}"
            try:
                r = await cmds.player_register(self.api, fid, username, ign, avatar_png=_gen_avatar())
                if r.ok:
                    self.state["pending_players"].append({
                        "discord_id": fid,
                        "sp_player_id": r.data["sp_player_id"],
                        "ign": ign,
                    })
                    log.info("  Registered player: %s (%s)", ign, fid)
                else:
                    log.warning("  player_register failed: %s", r.error)
            except Exception as e:
                log.error("  player_register error: %s", e)

        # Form a team if we have 5 pending players
        if len(self.state["pending_players"]) >= 5 and teams_created < target:
            await self._form_team()

    async def _form_team(self):
        """Take 5 pending players, create a team, invite/accept, register for league."""
        members = self.state["pending_players"][:5]
        self.state["pending_players"] = self.state["pending_players"][5:]

        captain = members[0]
        team_idx = len(self.state["teams"]) + 1
        team_name = f"Bot-{self.state['league_name'].split('-',1)[1][:6]}-T{team_idx}"

        logo_png, pal = _gen_logo()
        c1 = "#{:02X}{:02X}{:02X}".format(*pal[1])
        c2 = "#{:02X}{:02X}{:02X}".format(*pal[2])

        # Create team
        r = await cmds.team_create(self.api, captain["discord_id"], team_name,
                                    logo_png=logo_png, color_primary=c1, color_secondary=c2)
        if not r.ok:
            log.error("team_create failed: %s", r.error)
            # Put members back
            self.state["pending_players"] = members + self.state["pending_players"]
            return

        sp_team_id = r.data["sp_team_id"]

        # Invite and accept
        for m in members[1:]:
            ri = await cmds.team_invite(captain["discord_id"], m["discord_id"],
                                         sp_team_id=sp_team_id, role="player")
            if not ri.ok:
                log.warning("team_invite failed for %s: %s", m["ign"], ri.error)
                continue
            ra = await cmds.team_accept(self.api, m["discord_id"])
            if not ra.ok:
                log.warning("team_accept failed for %s: %s", m["ign"], ra.error)

        # Register for league
        table_id = self.state["table_id"]
        rl = await cmds.league_register(captain["discord_id"], table_id, sp_team_id=sp_team_id)
        if not rl.ok:
            log.warning("league_register failed: %s", rl.error)

        team_data = {
            "sp_team_id": sp_team_id,
            "name": team_name,
            "captain_discord_id": captain["discord_id"],
            "members": members,
        }
        self.state["teams"].append(team_data)

        log.info("  Team created: %s (ID %d) — %d/%d",
                 team_name, sp_team_id, len(self.state["teams"]), self.state["target_teams"])

        await self._notify(
            f"{self.state['league_name']}: Team Registered", 0x3A86FF,
            [("Team", team_name), ("Roster", f"{len(members)} players"),
             ("Progress", f"{len(self.state['teams'])}/{self.state['target_teams']}")],
        )

    # -- Schedule generation (Thu-Sat) -----------------------------------------

    async def _generate_schedule(self) -> bool:
        """Generate round-robin events for Thu-Sat of this week using round_robin service."""
        from datetime import date as date_type
        play_start = date_type.fromisoformat(self.state["play_start"])
        play_end = date_type.fromisoformat(self.state["play_end"])

        team_ids = [t["sp_team_id"] for t in self.state["teams"]]
        team_names = {t["sp_team_id"]: t["name"] for t in self.state["teams"]}

        try:
            entries = generate_schedule(team_ids, play_start, play_end)
        except ScheduleError as e:
            log.error("Schedule generation failed: %s", e)
            return False

        log.info("Generated %d events for %d teams (%s -> %s)",
                 len(entries), len(team_ids), play_start, play_end)

        # Create sp_events
        event_ids = []
        for entry in entries:
            home_name = team_names[entry["home_team_id"]]
            away_name = team_names[entry["away_team_id"]]
            name = f"{home_name} vs {away_name}"
            date_str = f"{entry['date'].isoformat()}T{entry['time']}"
            try:
                event = await self.api.create_event(
                    name, entry["home_team_id"], entry["away_team_id"], date_str,
                    league_ids=[self.state["league_term_id"]],
                    season_ids=[self.state["sp_season_id"]],
                )
                event_ids.append(event["id"])
            except Exception as e:
                log.error("Failed to create event '%s': %s", name, e)

        self.state["event_ids"] = event_ids

        # Regenerate WP page to include the new schedule
        try:
            await lifecycle.generate_league_wp_page(
                self.state["table_id"], self.state["league_term_id"],
                self.state["season_name"],
            )
        except Exception as e:
            log.warning("WP page regen failed: %s", e)

        return True

    # -- PLAYING ---------------------------------------------------------------

    async def _tick_playing(self, now: datetime, weekday: int):
        # If Sunday, force transition to CLEANUP regardless of match state
        if weekday == 6:
            log.info("PLAYING: Sunday reached, transitioning to CLEANUP")
            self._transition("CLEANUP")
            return

        event_ids = self.state["event_ids"]
        if not event_ids:
            log.warning("PLAYING: no events found in state, transitioning to CLEANUP")
            self._transition("CLEANUP")
            return

        if self.args.dry_run:
            log.info("[DRY] Would check for past-due events to simulate")
            return

        # Find past-due events with no confirmed result
        ph = ",".join(["%s"] * len(event_ids))
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT e.ID, e.post_title, e.post_date, e.post_status
                    FROM wp_posts e
                    LEFT JOIN mlbb_match_submissions ms
                        ON ms.sp_event_id = e.ID AND ms.status = 'confirmed'
                    WHERE e.ID IN ({ph})
                      AND e.post_date <= NOW()
                      AND ms.id IS NULL
                    ORDER BY e.post_date
                    """,
                    tuple(event_ids),
                )
                due_events = await cur.fetchall()

        if not due_events:
            # Check if ALL events have results
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT COUNT(*) FROM wp_posts e
                        LEFT JOIN mlbb_match_submissions ms
                            ON ms.sp_event_id = e.ID AND ms.status = 'confirmed'
                        WHERE e.ID IN ({ph}) AND ms.id IS NULL
                        """,
                        tuple(event_ids),
                    )
                    remaining = (await cur.fetchone())[0]

            if remaining == 0:
                log.info("PLAYING: all %d events have results, posting standings", len(event_ids))
                await self._post_final_standings()
                # Stay in PLAYING until Sunday -- the league is done but visible until cleanup
                return
            else:
                log.info("PLAYING: %d events remaining but none past-due yet", remaining)
                return

        # Simulate 1-2 matches this tick
        matches_this_tick = min(2, len(due_events))
        for event_row in due_events[:matches_this_tick]:
            eid, title, edate, estatus = event_row
            await self._simulate_match(eid, title)

    async def _discover_events(self):
        """Find events created by the scheduler for this league."""
        league_term_id = self.state["league_term_id"]
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT p.ID FROM wp_posts p
                    JOIN wp_term_relationships tr ON tr.object_id = p.ID
                    JOIN wp_term_taxonomy tt ON tt.term_taxonomy_id = tr.term_taxonomy_id
                    WHERE tt.term_id = %s AND p.post_type = 'sp_event'
                    ORDER BY p.post_date
                    """,
                    (league_term_id,),
                )
                rows = await cur.fetchall()
        if rows:
            self.state["event_ids"] = [r[0] for r in rows]
            log.info("Discovered %d events for league %s", len(rows), self.state["league_name"])

    async def _simulate_match(self, event_id: int, title: str):
        """Simulate a single match result via command_services."""
        # Get teams from event meta
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT meta_value FROM wp_postmeta WHERE post_id = %s AND meta_key = 'sp_team' ORDER BY meta_id",
                    (event_id,),
                )
                team_rows = await cur.fetchall()

        if len(team_rows) < 2:
            log.warning("Event %d has < 2 teams, skipping", event_id)
            return

        home_team_id = int(team_rows[0][0])
        away_team_id = int(team_rows[1][0])

        # Find captains from our team list
        home_captain = None
        away_captain = None
        for t in self.state["teams"]:
            if t["sp_team_id"] == home_team_id:
                home_captain = t["captain_discord_id"]
            elif t["sp_team_id"] == away_team_id:
                away_captain = t["captain_discord_id"]

        if not home_captain or not away_captain:
            log.warning("Event %d: could not find captains for teams %d/%d",
                        event_id, home_team_id, away_team_id)
            return

        # Generate result
        winner_captain = home_captain if random.random() < 0.55 else away_captain
        loser_captain = away_captain if winner_captain == home_captain else home_captain
        w_kills = random.randint(8, 22)
        l_kills = random.randint(2, max(2, w_kills - 2))
        battle_id = str(random.randint(10**16, 10**17 - 1))
        dur_s = random.randint(7 * 60, 22 * 60)

        match_data = cmds.MatchData(
            battle_id=battle_id, winner_kills=w_kills, loser_kills=l_kills,
            duration=f"{dur_s // 60:02d}:{dur_s % 60:02d}",
            confidence=0.97, screenshot_url=config.WP_URL,
        )

        r = await cmds.match_submit(winner_captain, match_data, event_id=event_id)
        if not r.ok:
            log.error("match_submit failed for event %d: %s", event_id, r.error)
            return

        sub_id = r.data["submission_id"]
        rc = await cmds.match_confirm(loser_captain, sub_id)
        if not rc.ok:
            log.error("match_confirm failed for sub #%d: %s", sub_id, rc.error)
            return

        winner_name = title.split(" vs ")[0] if winner_captain == home_captain else title.split(" vs ")[-1]
        log.info("  MATCH: %s -> %s wins %d-%d (sub #%d)", title, winner_name, w_kills, l_kills, sub_id)

    # -- CLEANUP ---------------------------------------------------------------

    async def _tick_cleanup(self, now: datetime, weekday: int):
        """
        Delete all artifacts from this week's bot-league.
        Runs on Sunday. After cleanup, waits until next Monday for INIT.
        """
        if not self.state.get("league_name"):
            # Nothing to clean up, just transition to INIT (idle until Monday)
            log.info("CLEANUP: nothing to clean, transitioning to INIT")
            self._transition("INIT")
            return

        if self.args.dry_run:
            log.info("[DRY] Would delete all artifacts for %s", self.state["league_name"])
            return

        league_name = self.state["league_name"]
        log.info("CLEANUP: deleting artifacts for %s", league_name)

        # Post a final summary before tearing down
        try:
            await self._post_final_standings()
        except Exception as e:
            log.warning("Final standings post failed: %s", e)

        errors = 0

        # Delete sp_events
        for eid in self.state.get("event_ids", []):
            try:
                await self.api.delete_event(eid)
            except Exception as e:
                log.warning("event delete %d: %s", eid, e)
                errors += 1

        # Delete sp_teams (this also cleans up mlbb_player_roster etc. via team_delete)
        for team in self.state.get("teams", []):
            try:
                r = await cmds.team_delete(self.api, team["captain_discord_id"],
                                            is_admin=True, sp_team_id=team["sp_team_id"])
                if not r.ok:
                    log.warning("team_delete(%s): %s", team["name"], r.error)
                    errors += 1
            except Exception as e:
                log.warning("team_delete error: %s", e)
                errors += 1

        # Delete sp_players (from teams + pending)
        all_pids = []
        for team in self.state.get("teams", []):
            for m in team.get("members", []):
                if m.get("sp_player_id"):
                    all_pids.append(m["sp_player_id"])
        for m in self.state.get("pending_players", []):
            if m.get("sp_player_id"):
                all_pids.append(m["sp_player_id"])
        for pid in all_pids:
            try:
                await self.api.delete_player(pid)
            except Exception as e:
                log.warning("player delete %d: %s", pid, e)
                errors += 1

        # Delete sp_table
        if self.state.get("table_id"):
            try:
                await self.api.delete_table(self.state["table_id"])
            except Exception as e:
                log.warning("table delete: %s", e)
                errors += 1

        # Delete sp_league term
        if self.state.get("league_term_id"):
            try:
                await self.api.delete_league(self.state["league_term_id"])
            except Exception as e:
                log.warning("league term delete: %s", e)
                errors += 1

        # Delete WP page
        if self.state.get("page_id"):
            subprocess.run([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "delete", str(self.state["page_id"]), "--force",
            ], capture_output=True)

        # Clean DB rows
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                if self.state.get("period_id"):
                    await cur.execute(
                        "DELETE FROM mlbb_team_registrations WHERE period_id=%s",
                        (self.state["period_id"],),
                    )
                    await cur.execute(
                        "DELETE FROM mlbb_registration_periods WHERE id=%s",
                        (self.state["period_id"],),
                    )

        # Archive to history
        self.state["history"].append({
            "league_name": league_name,
            "rule": self.state.get("rule"),
            "teams": len(self.state.get("teams", [])),
            "events": len(self.state.get("event_ids", [])),
            "result": "completed" if errors == 0 else f"completed_with_{errors}_errors",
            "completed_at": now.isoformat(),
            "week_start": self.state.get("week_start"),
        })

        await self._notify(
            f"Cleanup Complete: {league_name}", 0x808080,
            [("Errors", str(errors)),
             ("Next cycle", "Monday 00:00 UTC"),
             ("Cycle", f"#{self.state.get('cycle', 0)}")],
        )
        log.info("Cleanup done, %d errors. Next INIT on Monday.", errors)

        # Preserve offset/history/cycle, clear the rest
        offset = self.state["fake_id_offset"]
        cycle = self.state.get("cycle", 0)
        history = self.state["history"]
        self.state = _default_state()
        self.state["fake_id_offset"] = offset
        self.state["cycle"] = cycle
        self.state["history"] = history
        self._transition("INIT")

    async def _post_final_standings(self):
        """Post final standings to Discord and update WP page."""
        league_name = self.state["league_name"]

        # Build standings from match submissions
        team_wins: Dict[int, int] = {t["sp_team_id"]: 0 for t in self.state["teams"]}
        team_names: Dict[int, str] = {t["sp_team_id"]: t["name"] for t in self.state["teams"]}

        if self.state["event_ids"]:
            ph = ",".join(["%s"] * len(self.state["event_ids"]))
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT winning_team_id, COUNT(*) FROM mlbb_match_submissions
                        WHERE sp_event_id IN ({ph}) AND status = 'confirmed'
                        GROUP BY winning_team_id
                        """,
                        tuple(self.state["event_ids"]),
                    )
                    for tid, wins in await cur.fetchall():
                        if tid in team_wins:
                            team_wins[tid] = wins

        ranked = sorted(team_wins.items(), key=lambda x: x[1], reverse=True)
        medals = ["1st", "2nd", "3rd"]
        lines = []
        for i, (tid, wins) in enumerate(ranked):
            name = team_names.get(tid, f"Team {tid}")
            prefix = medals[i] if i < 3 else f"{i+1}th"
            lines.append(f"**{prefix}** {name} -- {wins} wins")

        # Update WP page
        if self.state.get("table_id") and self.state.get("league_term_id"):
            await lifecycle.generate_league_wp_page(
                self.state["table_id"], self.state["league_term_id"],
                self.state.get("season_name", ""),
            )

        await self._notify(
            f"League Complete: {league_name}", 0xFFD700,
            [("Rule", self.state.get("rule", "?")),
             ("Teams", str(len(self.state["teams"]))),
             ("Events", str(len(self.state["event_ids"])))],
            desc="\n".join(lines) if lines else "No standings available",
        )
        log.info("Posted final standings for %s", league_name)


# -- CLI -----------------------------------------------------------------------

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Weekly persistent bot-league state machine.")
    p.add_argument("--reset", action="store_true", help="Force restart from INIT")
    p.add_argument("--status", action="store_true", help="Print current state and exit")
    p.add_argument("--dry-run", action="store_true", dest="dry_run", help="Preview next tick")
    p.add_argument("--force-init", action="store_true", dest="force_init",
                   help="Bypass day-of-week check (create league immediately)")
    return p.parse_args()


async def main():
    args = _parse()

    if args.status:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                print(json.dumps(json.load(f), indent=2, default=str))
        else:
            print("No state file found. First run will start from INIT.")
        return

    config.validate()

    # File lock
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        log.info("Another instance is running, exiting")
        return

    await db.init()
    try:
        league = PersistentLeague(args)
        await league.tick()
    finally:
        await db.close()
        lock_fd.close()


if __name__ == "__main__":
    asyncio.run(main())
