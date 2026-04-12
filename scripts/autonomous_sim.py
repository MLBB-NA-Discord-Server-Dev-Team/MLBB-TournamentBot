#!/usr/bin/env python3
"""
scripts/autonomous_sim.py -- Autonomous league lifecycle simulation.

Designed for unattended cron execution. Creates a complete bot-league
simulation that exercises ALL command_services.py functions, validates
the league_lifecycle.py auto-approve and result-sync paths, then
cleans up and reports to Discord.

Usage:
    cd /root/MLBB-TournamentBot
    python scripts/autonomous_sim.py                    # full run + cleanup
    python scripts/autonomous_sim.py --no-cleanup       # retain artifacts
    python scripts/autonomous_sim.py --dry-run          # plan only
    python scripts/autonomous_sim.py --teams 4          # 4 teams (default 2)

Cron:
    0 4 * * * cd /root/MLBB-TournamentBot && venv/bin/python scripts/autonomous_sim.py >> /var/log/mlbb-autosim.log 2>&1
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
import time
import zlib
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from services import db
from services.sportspress import SportsPressAPI
from services import command_services as cmds
from services import league_lifecycle as lifecycle

# -- Logging -------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(__file__)), "autosim.log")),
    ],
)
log = logging.getLogger("autosim")

DISCORD_API = "https://discord.com/api/v10"
_FAKE_BASE = 8_880_000_000_000_000

# -- Name pools ----------------------------------------------------------------
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

# -- Pixel art PNG generator ---------------------------------------------------
RGB = Tuple[int, int, int]
_PALETTES = [
    ((15, 15, 25),   (255, 60, 60),   (255, 200, 50),  (60, 200, 255)),
    ((12, 22, 35),   (0, 230, 150),   (255, 80, 200),  (255, 255, 80)),
    ((25, 12, 40),   (180, 50, 255),  (50, 200, 255),  (255, 150, 50)),
    ((10, 30, 20),   (50, 255, 100),  (200, 255, 50),  (255, 80, 80)),
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


# -- Discord HTTP shim ---------------------------------------------------------
class DiscordHTTP:
    def __init__(self, token: str):
        self._auth = {"Authorization": f"Bot {token}", "User-Agent": "MLBB-AutoSim/1.0"}

    async def send_embed(self, channel_id: int, embed: dict):
        url = f"{DISCORD_API}/channels/{channel_id}/messages"
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers={**self._auth, "Content-Type": "application/json"},
                              data=json.dumps({"embeds": [embed]}).encode()) as resp:
                if resp.status >= 400:
                    log.warning("Discord POST %d: %s", resp.status, await resp.text())


def _embed(title: str, color: int, fields: List[Tuple[str, str]], desc: str = "") -> dict:
    e = {"title": title, "color": color,
         "timestamp": datetime.now(timezone.utc).isoformat(),
         "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields]}
    if desc:
        e["description"] = desc
    e["footer"] = {"text": "AutoSim | league_lifecycle + command_services"}
    return e


# -- Step tracker --------------------------------------------------------------
class StepResult:
    def __init__(self, name: str, ok: bool, detail: str = "", elapsed: float = 0.0):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.elapsed = elapsed


class SimReport:
    def __init__(self):
        self.steps: List[StepResult] = []
        self.start_time = time.time()

    def add(self, step: StepResult):
        self.steps.append(step)
        status = "PASS" if step.ok else "FAIL"
        log.info("  [%s] %s (%.1fs) %s", status, step.name, step.elapsed, step.detail)

    @property
    def all_pass(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def summary(self) -> str:
        passed = sum(1 for s in self.steps if s.ok)
        total = len(self.steps)
        elapsed = time.time() - self.start_time
        lines = [f"**{passed}/{total} steps passed** in {elapsed:.0f}s\n"]
        for s in self.steps:
            icon = "+" if s.ok else "x"
            lines.append(f"`[{icon}]` **{s.name}** ({s.elapsed:.1f}s) {s.detail}")
        return "\n".join(lines)


# -- Simulator -----------------------------------------------------------------
class AutonomousSim:

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api = SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)
        self.dc = DiscordHTTP(config.DISCORD_TOKEN)
        # Multi-guild broadcast: read data/guilds.json for bot_leagues channels
        self.notify_channels = self._load_notify_channels()
        self.report = SimReport()

    def _load_notify_channels(self) -> List[int]:
        import json
        from pathlib import Path
        guilds_file = Path(__file__).parent.parent / "data" / "guilds.json"
        if guilds_file.exists():
            try:
                with open(guilds_file) as f:
                    data = json.load(f)
                ids = [int(g["bot_leagues"]) for g in data.get("guilds", []) if g.get("bot_leagues")]
                if ids:
                    return ids
            except Exception:
                pass
        if config.BOT_LEAGUE_CHANNEL_ID:
            return [config.BOT_LEAGUE_CHANNEL_ID]
        if config.ADMIN_LOG_CHANNEL_ID:
            return [config.ADMIN_LOG_CHANNEL_ID]
        return []

        # Tracking
        self.teams: List[dict] = []
        self._id_counter = 0
        self._player_ids: List[int] = []
        self._team_ids: List[int] = []
        self._event_ids: List[int] = []
        self._table_ids: List[int] = []
        self._league_term_id: Optional[int] = None
        self._period_id: Optional[int] = None
        self._page_ids: List[int] = []
        self._fake_ids: List[str] = []
        self._reg_ids: List[int] = []
        self._sub_ids: List[int] = []

    def _fake_id(self) -> str:
        fid = str(_FAKE_BASE + self._id_counter)
        self._id_counter += 1
        self._fake_ids.append(fid)
        return fid

    def _ign(self) -> str:
        return f"{random.choice(_GAMER_PRE)}{random.choice(_GAMER_SUF)}{random.randint(10, 99)}"

    def _step(self, name: str, result: cmds.Result) -> cmds.Result:
        """Track a service-function call as a step."""
        if not result.ok:
            raise RuntimeError(f"Step '{name}' failed: {result.error}")
        return result

    async def run(self) -> bool:
        n = self.args.teams
        rule = random.choice(RULES)
        league_name = f"AutoSim-{random.randint(1000, 9999)}"

        log.info("=" * 60)
        log.info("AutoSim: %s | %d teams | rule=%s", league_name, n, rule)
        log.info("=" * 60)

        if self.args.dry_run:
            log.info("[DRY RUN] Would create %d teams of 5, simulate matches", n)
            return True

        ok = True
        try:
            # Step 1: Create league infrastructure
            t0 = time.time()
            try:
                term = await self.api.create_league(league_name, rule)
                self._league_term_id = term["id"]
                season = await lifecycle.get_season_status()
                season_name = season.data["current"]["season_name"] if season.ok and season.data.get("current") else "Simulation"
                sp_season_id = season.data["current"]["sp_season_id"] if season.ok and season.data.get("current") else None
                table = await self.api.create_table(
                    f"{league_name} -- {season_name}", rule,
                    league_ids=[self._league_term_id],
                    season_ids=[sp_season_id] if sp_season_id else None,
                )
                self._table_ids.append(table["id"])
                entity_id = table["id"]

                # Create registration period (open now, closes in 2 min)
                async with db.get_conn() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """INSERT INTO mlbb_registration_periods
                               (entity_type, entity_id, sp_season_id, opens_at, closes_at, rule, created_by, status)
                               VALUES ('league', %s, %s, NOW(), DATE_ADD(NOW(), INTERVAL 10 MINUTE), %s, 'autosim', 'open')""",
                            (entity_id, sp_season_id, rule))
                        self._period_id = cur.lastrowid

                self.report.add(StepResult("create_league", True, f"{league_name} (table={entity_id})", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("create_league", False, str(e), time.time() - t0))
                raise

            # Step 2: Register players
            t0 = time.time()
            players_per_team = 5
            all_members: List[List[dict]] = []
            try:
                for ti in range(n):
                    members = []
                    for pi in range(players_per_team):
                        ign = self._ign()
                        fid = self._fake_id()
                        username = f"{ign.lower()}_{fid[-4:]}"
                        r = self._step(f"player_register({ign})",
                                       await cmds.player_register(self.api, fid, username, ign, avatar_png=_gen_avatar()))
                        self._player_ids.append(r.data["sp_player_id"])
                        members.append({"discord_id": fid, "sp_player_id": r.data["sp_player_id"],
                                        "ign": ign, "username": username})
                    all_members.append(members)
                self.report.add(StepResult("player_register", True,
                                           f"{n * players_per_team} players", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("player_register", False, str(e), time.time() - t0))
                raise

            # Step 3: Create teams + invite/accept
            t0 = time.time()
            try:
                for ti, members in enumerate(all_members):
                    team_name = f"AutoSim-T{ti+1}-{random.randint(100,999)}"
                    captain = members[0]
                    logo_png, pal = _gen_logo()
                    c1 = "#{:02X}{:02X}{:02X}".format(*pal[1])
                    c2 = "#{:02X}{:02X}{:02X}".format(*pal[2])

                    r = self._step(f"team_create({team_name})",
                                   await cmds.team_create(self.api, captain["discord_id"], team_name,
                                                           logo_png=logo_png, color_primary=c1, color_secondary=c2))
                    sp_team_id = r.data["sp_team_id"]
                    self._team_ids.append(sp_team_id)

                    for m in members[1:]:
                        self._step(f"team_invite({m['ign']})",
                                   await cmds.team_invite(captain["discord_id"], m["discord_id"],
                                                           sp_team_id=sp_team_id, role="player"))
                        self._step(f"team_accept({m['ign']})",
                                   await cmds.team_accept(self.api, m["discord_id"]))

                    # Verify roster
                    r = self._step(f"team_roster({team_name})",
                                   await cmds.team_roster(sp_team_id=sp_team_id))
                    assert len(r.data["roster"]) == players_per_team

                    self.teams.append({"sp_team_id": sp_team_id, "name": team_name,
                                       "captain_id": captain["discord_id"], "members": members})

                self.report.add(StepResult("team_create_invite_accept", True,
                                           f"{n} teams of {players_per_team}", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("team_create_invite_accept", False, str(e), time.time() - t0))
                raise

            # Step 4: League registration
            t0 = time.time()
            try:
                for team in self.teams:
                    r = self._step(f"league_register({team['name']})",
                                   await cmds.league_register(team["captain_id"], entity_id,
                                                               sp_team_id=team["sp_team_id"]))
                    self._reg_ids.append(r.data["registration_id"])
                self.report.add(StepResult("league_register", True,
                                           f"{n} teams registered", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("league_register", False, str(e), time.time() - t0))
                raise

            # Step 5: Auto-approval (test lifecycle function)
            t0 = time.time()
            try:
                results = await lifecycle.check_pending_approvals()
                approved = [r for r in results if r.ok]
                assert len(approved) == n, f"Expected {n} approvals, got {len(approved)}"
                self.report.add(StepResult("auto_approve", True,
                                           f"{len(approved)} approved", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("auto_approve", False, str(e), time.time() - t0))
                raise

            # Step 6: Create events (simplified -- direct API, not scheduler)
            t0 = time.time()
            try:
                pairs = [(self.teams[i], self.teams[j])
                         for i in range(len(self.teams))
                         for j in range(i + 1, len(self.teams))]
                now = datetime.now(timezone.utc)
                for idx, (home, away) in enumerate(pairs):
                    dt = now + timedelta(days=idx + 1)
                    # Move to next Thu-Sun
                    while dt.weekday() not in (3, 4, 5, 6):
                        dt += timedelta(days=1)
                    date_str = dt.strftime("%Y-%m-%dT19:00:00")
                    name = f"{home['name']} vs {away['name']}"
                    event = await self.api.create_event(
                        name, home["sp_team_id"], away["sp_team_id"], date_str,
                        league_ids=[self._league_term_id] if self._league_term_id else None,
                        season_ids=[sp_season_id] if sp_season_id else None,
                    )
                    self._event_ids.append(event["id"])
                self.report.add(StepResult("create_events", True,
                                           f"{len(pairs)} events", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("create_events", False, str(e), time.time() - t0))
                raise

            # Step 7: Match submit + confirm
            t0 = time.time()
            try:
                for idx, ((home, away), eid) in enumerate(zip(pairs, self._event_ids)):
                    winner = home if random.random() < 0.55 else away
                    loser = away if winner is home else home
                    w_k = random.randint(8, 22)
                    l_k = random.randint(2, max(2, w_k - 2))
                    battle_id = str(random.randint(10**16, 10**17 - 1))
                    dur_s = random.randint(7*60, 22*60)

                    match_data = cmds.MatchData(
                        battle_id=battle_id, winner_kills=w_k, loser_kills=l_k,
                        duration=f"{dur_s//60:02d}:{dur_s%60:02d}",
                        confidence=0.97, screenshot_url=config.WP_URL,
                    )
                    r = self._step(f"match_submit(game {idx+1})",
                                   await cmds.match_submit(winner["captain_id"], match_data, event_id=eid))
                    sub_id = r.data["submission_id"]
                    self._sub_ids.append(sub_id)
                    self._step(f"match_confirm(game {idx+1})",
                               await cmds.match_confirm(loser["captain_id"], sub_id))

                self.report.add(StepResult("match_submit_confirm", True,
                                           f"{len(pairs)} matches", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("match_submit_confirm", False, str(e), time.time() - t0))
                raise

            # Step 8: Result sync (test lifecycle function)
            t0 = time.time()
            try:
                r = await lifecycle.sync_confirmed_results()
                assert r.ok, f"sync failed: {r.error}"
                self.report.add(StepResult("sync_results", True,
                                           f"synced={r.data.get('synced', 0)}", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("sync_results", False, str(e), time.time() - t0))
                raise

            # Step 9: WP page generation (test lifecycle function)
            t0 = time.time()
            try:
                if self._league_term_id:
                    r = await lifecycle.generate_league_wp_page(
                        entity_id, self._league_term_id, season_name)
                    if r.ok and r.data.get("page_id"):
                        self._page_ids.append(r.data["page_id"])
                    self.report.add(StepResult("wp_page_gen", r.ok,
                                               r.data.get("action", r.error) if r.ok else r.error,
                                               time.time() - t0))
                else:
                    self.report.add(StepResult("wp_page_gen", True, "skipped (no term)", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("wp_page_gen", False, str(e), time.time() - t0))
                # Non-fatal

            # Step 10: Player profile verification
            t0 = time.time()
            try:
                for team in self.teams:
                    r = self._step(f"player_profile({team['name']})",
                                   await cmds.player_profile(team["captain_id"]))
                    assert any(t["sp_team_id"] == team["sp_team_id"] for t in r.data["teams"])
                self.report.add(StepResult("player_profile", True,
                                           f"{n} captains verified", time.time() - t0))
            except Exception as e:
                self.report.add(StepResult("player_profile", False, str(e), time.time() - t0))

        except (RuntimeError, AssertionError) as e:
            log.error("Simulation failed: %s", e)
            ok = False
        except Exception as e:
            log.exception("Unexpected error: %s", e)
            ok = False
        finally:
            if not self.args.no_cleanup and not self.args.dry_run:
                await self._cleanup()

        # Post report
        await self._post_report(league_name, ok)
        return ok and self.report.all_pass

    async def _cleanup(self):
        log.info("--- Cleanup ---")
        t0 = time.time()
        errors = 0

        for eid in self._event_ids:
            try:
                await self.api.delete_event(eid)
            except Exception:
                errors += 1

        for team in self.teams:
            if team["sp_team_id"]:
                r = await cmds.team_delete(self.api, team["captain_id"],
                                            is_admin=True, sp_team_id=team["sp_team_id"])
                if not r.ok:
                    errors += 1

        for pid in self._player_ids:
            try:
                await self.api.delete_player(pid)
            except Exception:
                errors += 1

        for tbl_id in self._table_ids:
            try:
                await self.api.delete_table(tbl_id)
            except Exception:
                errors += 1

        if self._league_term_id:
            try:
                await self.api.delete_league(self._league_term_id)
            except Exception:
                errors += 1

        for pid in self._page_ids:
            subprocess.run([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "delete", str(pid), "--force"
            ], capture_output=True)

        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                if self._sub_ids:
                    ph = ",".join(["%s"] * len(self._sub_ids))
                    await cur.execute(f"DELETE FROM mlbb_match_submissions WHERE id IN ({ph})",
                                     tuple(self._sub_ids))
                if self._reg_ids:
                    ph = ",".join(["%s"] * len(self._reg_ids))
                    await cur.execute(f"DELETE FROM mlbb_team_registrations WHERE id IN ({ph})",
                                     tuple(self._reg_ids))
                if self._fake_ids:
                    ph = ",".join(["%s"] * len(self._fake_ids))
                    await cur.execute(f"DELETE FROM mlbb_player_roster WHERE discord_id IN ({ph})",
                                     tuple(self._fake_ids))
                    await cur.execute(
                        f"DELETE FROM mlbb_team_invites WHERE inviter_id IN ({ph}) OR invitee_id IN ({ph})",
                        tuple(self._fake_ids) + tuple(self._fake_ids))
                if self._period_id:
                    await cur.execute("DELETE FROM mlbb_registration_periods WHERE id=%s",
                                     (self._period_id,))

        self.report.add(StepResult("cleanup", errors == 0,
                                   f"{errors} errors" if errors else "clean", time.time() - t0))

    async def _post_report(self, league_name: str, ok: bool):
        if not self.notify_channels:
            return
        color = 0x2ECC71 if ok and self.report.all_pass else 0xE74C3C
        title = f"AutoSim {'PASS' if ok and self.report.all_pass else 'FAIL'}: {league_name}"
        embed = _embed(title, color, [], desc=self.report.summary)
        for ch_id in self.notify_channels:
            try:
                await self.dc.send_embed(ch_id, embed)
            except Exception as e:
                log.warning("Could not post report to %s: %s", ch_id, e)


# -- CLI -----------------------------------------------------------------------
def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Autonomous league lifecycle simulation.")
    p.add_argument("--teams", type=int, default=2, help="Number of teams (default 2, min 2)")
    p.add_argument("--no-cleanup", action="store_true", dest="no_cleanup")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = p.parse_args()
    if args.teams < 2:
        p.error("--teams must be >= 2")
    return args


async def main():
    args = _parse()
    config.validate()
    if not args.dry_run:
        await db.init()
    try:
        ok = await AutonomousSim(args).run()
        sys.exit(0 if ok else 1)
    finally:
        if not args.dry_run:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
