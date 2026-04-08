#!/usr/bin/env python3
"""
scripts/simulate_league.py — End-to-end league simulation for MLBB-TournamentBot.

Exercises every core subsystem under production conditions:
  Phase 1  — /league create       : sp_league term, sp_table standings, registration period
  Phase 2  — /player register ×N  : sp_player posts, Discord ID linkage in sp_metrics, DB roster
  Phase 3  — /team create + invite/accept : sp_team posts, DB roster, SportsPress player↔team
  Phase 4  — /league register      : mlbb_team_registrations (auto-approved)
  Phase 5  — /league-admin approve : simulated approval pass
  Phase 6  — match schedule        : sp_event posts (round-robin), schedule posted to #match-notifications
  Phase 7  — /match submit + confirm : VC creation, fake VICTORY PNG upload, DB insert, VC teardown
  Phase 8  — final standings       : win tally posted to #match-notifications + #tournament-admin
  Phase 9  — cleanup               : all Bot-* artifacts deleted unless --no-cleanup

Usage:
    cd /root/MLBB-TournamentBot
    python scripts/simulate_league.py
    python scripts/simulate_league.py --teams 6 --round-delay 5
    python scripts/simulate_league.py --rule BrawlBO3 --no-cleanup
    python scripts/simulate_league.py --dry-run
"""
import asyncio
import argparse
import json
import logging
import random
import struct
import sys
import os
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
        logging.FileHandler(os.path.join(os.path.dirname(os.path.dirname(__file__)), "simulate.log")),
    ],
)
log = logging.getLogger("simulate")

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
    "Colossus", "Nexus", "Forge", "Circuit", "Pulse", "Vector",
]
_TEAM_CODES = [
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
]
# Discord-style gamer tags
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
    "Claw", "Jinx", "Rift", "Haze", "Bane", "Tide", "Ash", "Warp",
]

RULES = ["DPBO1", "DPBO3", "DPBO5", "BrawlBO1", "BrawlBO3", "BrawlBO5"]
RULE_LABELS = {
    "DPBO1": "Draft Pick · Best of 1",
    "DPBO3": "Draft Pick · Best of 3",
    "DPBO5": "Draft Pick · Best of 5",
    "BrawlBO1": "Brawl · Best of 1",
    "BrawlBO3": "Brawl · Best of 3",
    "BrawlBO5": "Brawl · Best of 5",
}

# Fake Discord ID base — outside real snowflake timestamp range; no real users will match
_FAKE_BASE = 9_990_000_000_000_000


# ── Pixel art PNG generator (no PIL/Pillow) ────────────────────────────────────

RGB = Tuple[int, int, int]

# Curated colour palettes — each is (background, accent1, accent2, accent3)
_PALETTES: List[Tuple[RGB, ...]] = [
    ((15, 15, 25),   (255, 60, 60),   (255, 200, 50),  (60, 200, 255)),    # red/gold/cyan
    ((12, 22, 35),   (0, 230, 150),   (255, 80, 200),  (255, 255, 80)),    # green/pink/yellow
    ((25, 12, 40),   (180, 50, 255),  (50, 200, 255),  (255, 150, 50)),    # purple/blue/orange
    ((10, 30, 20),   (50, 255, 100),  (200, 255, 50),  (255, 80, 80)),     # emerald/lime/red
    ((30, 15, 10),   (255, 120, 30),  (255, 220, 80),  (80, 180, 255)),    # fire/gold/sky
    ((20, 20, 35),   (100, 100, 255), (220, 60, 255),  (60, 255, 200)),    # blue/violet/teal
    ((35, 10, 10),   (255, 40, 80),   (255, 160, 200), (255, 255, 120)),   # hot-pink/rose/lemon
    ((10, 10, 30),   (80, 80, 255),   (180, 220, 255), (255, 255, 255)),   # ice/white/blue
    ((25, 25, 10),   (220, 180, 40),  (180, 120, 20),  (255, 240, 200)),   # gold/bronze/cream
    ((15, 25, 15),   (40, 200, 80),   (120, 255, 160), (200, 255, 220)),   # forest/mint/pale
    ((30, 10, 30),   (220, 40, 220),  (140, 80, 255),  (60, 200, 255)),    # magenta/violet/cyan
    ((10, 20, 30),   (255, 100, 40),  (255, 180, 80),  (40, 160, 200)),    # rust/amber/steel
]


def _png_encode(pixels: List[List[RGB]], scale: int) -> bytes:
    """Encode a 2D pixel grid as a scaled-up RGB PNG. No external deps."""
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
    """Generate a symmetric 10x10 pixel-art team logo PNG (160x160).
    Returns (png_bytes, palette) so the caller can extract team colours."""
    pal = random.choice(_PALETTES)
    bg = pal[0]
    accents = list(pal[1:])
    grid: List[List[RGB]] = [[bg] * 10 for _ in range(10)]

    # Fill left half randomly, mirror to right → emblem-like symmetry
    for y in range(10):
        for x in range(5):
            if random.random() < 0.42:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][9 - x] = c

    # Optional: vertical half-mirror for extra structure (50% chance)
    if random.random() < 0.5:
        for y in range(5):
            for x in range(10):
                if grid[y][x] != bg:
                    grid[9 - y][x] = grid[y][x]

    return _png_encode(grid, scale=16), pal


def _gen_player_avatar() -> Tuple[bytes, Tuple[RGB, ...]]:
    """Generate an 8x8 pixel-art player avatar PNG (128x128).
    Horizontally symmetric, slightly sparser than team logos."""
    pal = random.choice(_PALETTES)
    bg = pal[0]
    accents = list(pal[1:])
    grid: List[List[RGB]] = [[bg] * 8 for _ in range(8)]

    for y in range(8):
        for x in range(4):
            if random.random() < 0.38:
                c = random.choice(accents)
                grid[y][x] = c
                grid[y][7 - x] = c

    return _png_encode(grid, scale=16), pal


def _make_solid_png(r: int, g: int, b: int, w: int = 640, h: int = 360) -> bytes:
    """Solid-colour PNG (used for VICTORY screenshot stand-in)."""
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


# ── Discord HTTP shim (no gateway, no intents) ─────────────────────────────────

class DiscordHTTP:
    """Thin async Discord REST client for posting messages and managing channels."""

    def __init__(self, token: str):
        self._auth = {"Authorization": f"Bot {token}", "User-Agent": "MLBB-SimBot/1.0"}

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
                    log.warning("Discord %s %s → %s  %s", method, path, resp.status, text[:300])
                    return None
                try:
                    return json.loads(text)
                except Exception:
                    return None

    async def send_embed(self, channel_id: int, embed: dict, content: str = "") -> Optional[dict]:
        payload: dict = {"embeds": [embed]}
        if content:
            payload["content"] = content
        return await self._req("POST", f"/channels/{channel_id}/messages", payload)

    async def create_voice_channel(self, guild_id: int, name: str, category_id: int) -> Optional[int]:
        data = await self._req("POST", f"/guilds/{guild_id}/channels", {
            "name": name[:100],
            "type": 2,            # GUILD_VOICE
            "parent_id": str(category_id),
        })
        return int(data["id"]) if data and "id" in data else None

    async def delete_channel(self, channel_id: int):
        await self._req("DELETE", f"/channels/{channel_id}")


# ── Embed factory ──────────────────────────────────────────────────────────────

def _embed(title: str, color: int, fields: List[Tuple[str, str]], footer: str = "", desc: str = "") -> dict:
    e: dict = {
        "title": title,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields],
    }
    if footer:
        e["footer"] = {"text": footer}
    if desc:
        e["description"] = desc
    return e


# ── Simulator ──────────────────────────────────────────────────────────────────

class LeagueSimulator:

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.api = SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)
        self.dc = DiscordHTTP(config.DISCORD_TOKEN)
        self.guild_id: int = config.GUILD_IDS[0] if config.GUILD_IDS else 0
        self.notif_ch: int = config.MATCH_NOTIFICATIONS_CHANNEL_ID
        self.admin_ch: int = config.ADMIN_LOG_CHANNEL_ID
        self.voice_cat: int = config.MATCH_VOICE_CATEGORY_ID

        # Runtime state
        self.league_name: str = ""
        self.rule: str = ""
        self.teams: List[dict] = []
        self.events: List[dict] = []

        # Artifact tracking (for cleanup)
        self.player_ids: List[int] = []
        self.team_ids: List[int] = []
        self.event_ids: List[int] = []
        self.table_ids: List[int] = []
        self.league_term_id: Optional[int] = None
        self._season_id: Optional[int] = None
        self.period_id: Optional[int] = None
        self.vc_ids: List[int] = []
        self.registration_ids: List[int] = []
        self.submission_ids: List[int] = []
        self.fake_discord_ids: List[str] = []
        self.page_ids: List[int] = []
        self._fake_id_counter: int = 0

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _update_league_page(self):
        """Rebuild the WP league page with current teams, events, and results."""
        if not self.page_ids or self.args.dry_run:
            return
        label = RULE_LABELS[self.rule]
        mode_labels = {
            "DPBO1": "5v5 Custom Room — Draft Pick",
            "DPBO3": "5v5 Custom Room — Draft Pick",
            "DPBO5": "5v5 Custom Room — Draft Pick",
            "BrawlBO1": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO3": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO5": "5v5 Brawl — Randomly Assigned Heroes",
        }
        # Teams list
        team_rows = ""
        for t in self.teams:
            members = t.get("members", [])
            captain = next((m for m in members if m["role"] == "captain"), None)
            cap_name = captain["ign"] if captain else "—"
            team_rows += f'<tr><td><strong>{t["name"]}</strong></td><td>{cap_name}</td><td>{len(members)}</td></tr>'
        teams_html = (
            f'<!-- wp:html --><table class="sp-data-table"><thead>'
            f'<tr><th>Team</th><th>Captain</th><th>Roster</th></tr></thead>'
            f'<tbody>{team_rows}</tbody></table><!-- /wp:html -->'
            if team_rows else
            '<!-- wp:paragraph --><p><em>No teams yet.</em></p><!-- /wp:paragraph -->'
        )
        # Events + results
        event_rows = ""
        for ev in self.events:
            event_rows += (
                f'<tr><td>{ev["date"]}</td>'
                f'<td>{ev["home"]["name"]}</td>'
                f'<td>{ev["away"]["name"]}</td></tr>'
            )
        events_html = (
            f'<!-- wp:html --><table class="sp-data-table"><thead>'
            f'<tr><th>Date</th><th>Home</th><th>Away</th></tr></thead>'
            f'<tbody>{event_rows}</tbody></table><!-- /wp:html -->'
            if event_rows else
            '<!-- wp:paragraph --><p><em>Schedule not yet generated.</em></p><!-- /wp:paragraph -->'
        )
        # Standings
        standings_html = ""
        if self.submission_ids:
            standings: Dict[int, dict] = {t["sp_team_id"]: {"name": t["name"], "wins": 0} for t in self.teams}
            ph = ",".join(["%s"] * len(self.submission_ids))
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"SELECT winning_team_id, COUNT(*) FROM mlbb_match_submissions "
                        f"WHERE id IN ({ph}) AND status='confirmed' GROUP BY winning_team_id",
                        tuple(self.submission_ids),
                    )
                    for tid, wins in await cur.fetchall():
                        if tid in standings:
                            standings[tid]["wins"] = wins
            ranked = sorted(standings.values(), key=lambda x: x["wins"], reverse=True)
            medals = ["🥇", "🥈", "🥉"]
            s_rows = ""
            for i, t in enumerate(ranked):
                m = medals[i] if i < 3 else ""
                s_rows += f'<tr><td>{i+1}</td><td>{m} <strong>{t["name"]}</strong></td><td>{t["wins"]}</td></tr>'
            standings_html = (
                '<!-- wp:heading --><h2 class="wp-block-heading">Standings</h2><!-- /wp:heading -->'
                f'<!-- wp:html --><table class="sp-data-table"><thead>'
                f'<tr><th>#</th><th>Team</th><th>Wins</th></tr></thead>'
                f'<tbody>{s_rows}</tbody></table><!-- /wp:html -->'
            )
        content = (
            '<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity"/><!-- /wp:separator -->'
            '<!-- wp:heading --><h2 class="wp-block-heading">League Rules</h2><!-- /wp:heading -->'
            f'<!-- wp:html --><table class="league-rules"><tbody>'
            f'<tr><th>Format</th><td>{label}</td></tr>'
            f'<tr><th>Mode</th><td>{mode_labels.get(self.rule, self.rule)}</td></tr>'
            f'<tr><th>Type</th><td>Bot Simulation</td></tr>'
            f'</tbody></table><!-- /wp:html -->'
            '<!-- wp:heading --><h2 class="wp-block-heading">Teams</h2><!-- /wp:heading -->'
            + teams_html +
            '<!-- wp:heading --><h2 class="wp-block-heading">Schedule</h2><!-- /wp:heading -->'
            + events_html
            + standings_html +
            '<!-- wp:paragraph -->'
            '<p><a href="/bot-leagues/">← Back to Bot Leagues</a></p>'
            '<!-- /wp:paragraph -->'
        )
        import subprocess
        tmp = "/tmp/mlbb_sim_page.html"
        with open(tmp, "w") as f:
            f.write(content)
        subprocess.run([
            "wp", "--path=/var/www/sites/play.mlbb.site",
            "--skip-plugins", "--skip-themes", "--allow-root",
            "eval", f'wp_update_post(["ID"=>{self.page_ids[0]},"post_content"=>file_get_contents("{tmp}")]);'
        ], capture_output=True)
        log.info("Updated league page ID %s", self.page_ids[0])

    @staticmethod
    async def _refresh_hub_page():
        """Rebuild /bot-leagues/ hub page listing all child pages."""
        import subprocess
        result = subprocess.run([
            "wp", "--path=/var/www/sites/play.mlbb.site",
            "--skip-plugins", "--skip-themes", "--allow-root",
            "eval", """
$hub = get_page_by_path("bot-leagues");
if (!$hub) exit;
$children = get_pages(["parent" => $hub->ID, "post_status" => "publish", "sort_column" => "post_date", "sort_order" => "DESC"]);
$list = "";
foreach ($children as $child) {
    $url = get_permalink($child->ID);
    $list .= '<li><a href="' . $url . '">' . esc_html($child->post_title) . '</a></li>';
}
if ($list) {
    $league_block = '<!-- wp:list --><ul class="wp-block-list">' . $list . '</ul><!-- /wp:list -->';
} else {
    $league_block = '<!-- wp:paragraph --><p><em>No bot leagues currently running. '
        . 'Run <code>python scripts/simulate_league.py --no-cleanup</code> to create one.</em></p><!-- /wp:paragraph -->';
}
$content = '<!-- wp:paragraph --><p>Simulated leagues created by the tournament bot testing system. '
    . 'These leagues run end-to-end simulations with generated players, teams, round-robin schedules, and match results.</p><!-- /wp:paragraph -->'
    . '<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity"/><!-- /wp:separator -->'
    . '<!-- wp:heading --><h2 class="wp-block-heading">Active Bot Leagues</h2><!-- /wp:heading -->'
    . $league_block
    . '<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity"/><!-- /wp:separator -->'
    . '<!-- wp:paragraph --><p>Bot leagues are created via <code>python scripts/simulate_league.py --no-cleanup</code>. '
    . 'Each run generates a "Bot-*" prefixed league with randomized teams and scores.</p><!-- /wp:paragraph -->';
wp_update_post(["ID" => $hub->ID, "post_content" => $content]);
echo count($children);
"""
        ], capture_output=True, text=True)
        count = result.stdout.strip()
        log.info("Refreshed /bot-leagues/ hub — %s child leagues listed", count or "0")

    def _next_fake_id(self) -> str:
        fid = str(_FAKE_BASE + self._fake_id_counter)
        self._fake_id_counter += 1
        self.fake_discord_ids.append(fid)
        return fid

    def _random_ign(self) -> str:
        return f"{random.choice(_GAMER_PRE)}{random.choice(_GAMER_SUF)}{random.randint(10, 99)}"

    def _wins_needed(self) -> int:
        if "BO1" in self.rule:
            return 1
        if "BO3" in self.rule:
            return 2
        return 3  # BO5

    async def _ensure_channel_access(self):
        """Create webhooks for channels the bot can't POST to via REST API.

        The gateway bot can send to restricted channels through its cached
        permission context, but direct REST calls hit 403 if @everyone is
        denied SEND_MESSAGES and the bot has no explicit member override.
        Webhooks bypass channel permission checks entirely.
        """
        for attr in ("admin_ch", "notif_ch"):
            ch_id = getattr(self, attr)
            if not ch_id:
                continue
            # Test if direct posting works
            test = await self.dc._req(
                "POST", f"/channels/{ch_id}/messages",
                {"content": ""},  # empty content → 400 (not 403) if we have access
            )
            # 403 → fall back to webhook
            if test is None:
                wh = await self.dc._req(
                    "POST", f"/channels/{ch_id}/webhooks",
                    {"name": "SimBot"},
                )
                if wh and "id" in wh:
                    url = f"https://discord.com/api/v10/webhooks/{wh['id']}/{wh['token']}"
                    setattr(self, f"_{attr}_webhook", url)
                    log.info("Created webhook for %s (channel %s)", attr, ch_id)
                else:
                    log.warning("Could not create webhook for %s — Discord posts will be skipped", attr)
                    setattr(self, attr, 0)  # disable channel
            else:
                log.info("Direct REST access OK for %s", attr)
                setattr(self, f"_{attr}_webhook", None)

    async def _post_to_channel(self, attr: str, embed: dict):
        """Post an embed to a channel, using a webhook fallback if available."""
        ch_id = getattr(self, attr, 0)
        if not ch_id or self.args.dry_run:
            return
        wh_url = getattr(self, f"_{attr}_webhook", None)
        if wh_url:
            async with aiohttp.ClientSession() as s:
                await s.post(f"{wh_url}?wait=true", json={"embeds": [embed]},
                             headers={"Content-Type": "application/json"})
        else:
            await self.dc.send_embed(ch_id, embed)
        await asyncio.sleep(0.3)

    async def _admin_log(self, title: str, color: int, fields: List[Tuple[str, str]]):
        await self._post_to_channel(
            "admin_ch",
            _embed(title, color, fields, footer="🤖 SimBot · scripts/simulate_league.py"),
        )

    async def _notif(self, embed: dict):
        await self._post_to_channel("notif_ch", embed)

    # ── Phase 1: League ────────────────────────────────────────────────────────

    async def phase_league(self) -> bool:
        adj, noun = random.choice(_ADJ), random.choice(_NOUN)
        self.league_name = f"Bot-{adj}{noun}"
        self.rule = self.args.rule or random.choice(RULES)
        label = RULE_LABELS[self.rule]

        log.info("━━━ Phase 1: League ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("League name : %s", self.league_name)
        log.info("Rule        : %s (%s)", self.rule, label)

        if self.args.dry_run:
            return True

        # sp_league taxonomy term
        try:
            term = await self.api.create_league(self.league_name, label)
            self.league_term_id = term["id"]
            log.info("sp_league term ID : %s", self.league_term_id)
        except Exception as e:
            log.error("create_league failed: %s", e)
            return False

        # Resolve current season
        try:
            from services.db_helpers import get_current_season
            season = await get_current_season()
            self._season_id = season["sp_season_id"] if season else None
        except Exception:
            self._season_id = None

        # sp_table (standings post)
        try:
            season_name = ""
            try:
                from services.db_helpers import get_current_season
                s = await get_current_season()
                season_name = s["season_name"] if s else "Simulation"
            except Exception:
                season_name = "Simulation"
            table = await self.api.create_table(
                f"{self.league_name} — {season_name}", label,
                league_ids=[self.league_term_id],
                season_ids=[self._season_id] if self._season_id else None,
            )
            self.table_ids.append(table["id"])
            entity_id = table["id"]
            log.info("sp_table ID : %s", entity_id)
        except Exception as e:
            log.warning("create_table failed (non-fatal): %s", e)
            entity_id = self.league_term_id

        # Registration period — open immediately, closes in 24h
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO mlbb_registration_periods
                        (entity_type, entity_id, opens_at, closes_at, rule, created_by, status)
                    VALUES ('league', %s, NOW(), DATE_ADD(NOW(), INTERVAL 1 DAY), %s, 'simulation', 'open')
                    """,
                    (entity_id, self.rule),
                )
                self.period_id = cur.lastrowid
        log.info("Registration period ID : %s", self.period_id)

        # WordPress league page (child of /bot-leagues/)
        slug = self.league_name.lower().replace(" ", "-")
        mode_labels = {
            "DPBO1": "5v5 Custom Room — Draft Pick",
            "DPBO3": "5v5 Custom Room — Draft Pick",
            "DPBO5": "5v5 Custom Room — Draft Pick",
            "BrawlBO1": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO3": "5v5 Brawl — Randomly Assigned Heroes",
            "BrawlBO5": "5v5 Brawl — Randomly Assigned Heroes",
        }
        page_html = (
            '<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity"/><!-- /wp:separator -->'
            '<!-- wp:heading --><h2 class="wp-block-heading">League Rules</h2><!-- /wp:heading -->'
            f'<!-- wp:html --><table class="league-rules"><tbody>'
            f'<tr><th>Format</th><td>{label}</td></tr>'
            f'<tr><th>Mode</th><td>{mode_labels.get(self.rule, self.rule)}</td></tr>'
            f'<tr><th>Type</th><td>Bot Simulation</td></tr>'
            f'</tbody></table><!-- /wp:html -->'
            '<!-- wp:heading --><h2 class="wp-block-heading">Teams</h2><!-- /wp:heading -->'
            '<!-- wp:paragraph --><p><em>Teams populate after Phase 3 completes.</em></p><!-- /wp:paragraph -->'
            '<!-- wp:heading --><h2 class="wp-block-heading">Schedule &amp; Results</h2><!-- /wp:heading -->'
            '<!-- wp:paragraph --><p><em>Events populate after Phase 6 completes.</em></p><!-- /wp:paragraph -->'
            '<!-- wp:paragraph -->'
            '<p><a href="/bot-leagues/">← Back to Bot Leagues</a></p>'
            '<!-- /wp:paragraph -->'
        )
        try:
            # Resolve /bot-leagues/ parent page ID
            import subprocess
            parent_id = subprocess.check_output([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "eval", "echo get_page_by_path(\"bot-leagues\")->ID;"
            ], text=True, stderr=subprocess.DEVNULL).strip()
            page = await self.api.create_page(self.league_name, page_html, slug)
            self.page_ids.append(page["id"])
            # Set parent to /bot-leagues/ via WP-CLI
            subprocess.run([
                "wp", "--path=/var/www/sites/play.mlbb.site",
                "--skip-plugins", "--skip-themes", "--allow-root",
                "post", "update", str(page["id"]),
                f"--post_parent={parent_id}",
            ], capture_output=True)
            log.info("WP page ID : %s  → /bot-leagues/%s/", page["id"], slug)
        except Exception as e:
            log.warning("Could not create WP page (non-fatal): %s", e)

        await self._admin_log("⚙️ Simulation Started", 0x9D4EDD, [
            ("League", self.league_name),
            ("Rule", label),
            ("Teams", str(self.args.teams)),
            ("Round delay", f"{self.args.round_delay}s"),
            ("League term ID", str(self.league_term_id)),
            ("Period ID", str(self.period_id)),
        ])
        return True

    # ── Phase 2 + 3: Players and teams ────────────────────────────────────────

    async def phase_teams(self) -> bool:
        log.info("━━━ Phase 2+3: Players + Teams (%d × 5 players) ━━━━━━━━━━━━━", self.args.teams)

        if self.args.dry_run:
            for code in random.sample(_TEAM_CODES, self.args.teams):
                log.info("[DRY RUN] Would create team Bot-%s with 5 players", code)
                self.teams.append({
                    "sp_team_id": 0,
                    "name": f"Bot-{code}",
                    "captain_id": self._next_fake_id(),
                    "members": [],
                })
            return True

        codes = random.sample(_TEAM_CODES, self.args.teams)

        for t_idx, code in enumerate(codes):
            team_name = f"Bot-{code}"
            log.info("  Creating team '%s' ...", team_name)
            members: List[dict] = []

            # ── create 5 players with avatars ─────────────────────────────────
            for p_idx in range(5):
                role = "captain" if p_idx == 0 else "player"
                ign = self._random_ign()
                fake_id = self._next_fake_id()
                discord_username = f"{ign.lower()}_{fake_id[-4:]}"
                try:
                    player = await self.api.create_player(ign, [])
                    sp_player_id = player["id"]
                    self.player_ids.append(sp_player_id)
                    await self.api.set_player_discord(
                        sp_player_id, fake_id, discord_username
                    )
                except Exception as e:
                    log.error("  create_player '%s' failed: %s", ign, e)
                    return False

                # Upload pixel-art avatar → set all 3 photo fields
                try:
                    avatar_png, _ = _gen_player_avatar()
                    media = await self.api.upload_media(
                        avatar_png, f"sim-avatar-{sp_player_id}.png", "image/png"
                    )
                    from services.db_helpers import set_player_photos
                    await set_player_photos(sp_player_id, media["id"])
                except Exception as e:
                    log.warning("  avatar upload for player %s: %s", sp_player_id, e)

                members.append({
                    "discord_id": fake_id,
                    "discord_username": discord_username,
                    "sp_player_id": sp_player_id,
                    "role": role,
                    "ign": ign,
                })

            # ── create sp_team post ───────────────────────────────────────────
            try:
                team_post = await self.api.create_team(team_name)
                sp_team_id = team_post["id"]
                self.team_ids.append(sp_team_id)
            except Exception as e:
                log.error("  create_team '%s' failed: %s", team_name, e)
                return False

            # ── generate + upload team logo ───────────────────────────────────
            try:
                logo_png, team_palette = _gen_team_logo()
                media = await self.api.upload_media(
                    logo_png, f"sim-logo-{sp_team_id}.png", "image/png"
                )
                await self.api.set_team_featured_image(sp_team_id, media["id"])
                # Set team colours from the palette
                from services.db_helpers import set_team_colors
                c1 = "#{:02X}{:02X}{:02X}".format(*team_palette[1])
                c2 = "#{:02X}{:02X}{:02X}".format(*team_palette[2])
                await set_team_colors(sp_team_id, color_primary=c1, color_secondary=c2)
                log.info("    Logo + colours: %s / %s", c1, c2)
            except Exception as e:
                log.warning("  logo/colors for team %s: %s", sp_team_id, e)

            # ── link each player to team in SportsPress ───────────────────────
            for m in members:
                try:
                    await self.api.set_player_teams(m["sp_player_id"], [sp_team_id])
                except Exception as e:
                    log.warning("  set_player_teams failed for player %s: %s", m["sp_player_id"], e)

            # ── create sp_list roster display (mirrors /team create) ──────────
            try:
                from services.db_helpers import setup_team_roster_display, sync_team_roster_list
                sp_list = await self.api.create_player_list(f"{team_name} — Roster")
                await setup_team_roster_display(sp_team_id, sp_list["id"])
                await sync_team_roster_list(sp_team_id)
                log.info("    Roster list ID: %s", sp_list["id"])
            except Exception as e:
                log.warning("  roster display setup for team %s: %s", sp_team_id, e)

            # ── write DB roster ───────────────────────────────────────────────
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    for m in members:
                        await cur.execute(
                            """
                            INSERT INTO mlbb_player_roster
                                (discord_id, sp_player_id, sp_team_id, role, status)
                            VALUES (%s, %s, %s, %s, 'active')
                            """,
                            (m["discord_id"], m["sp_player_id"], sp_team_id, m["role"]),
                        )

            captain = next(m for m in members if m["role"] == "captain")
            self.teams.append({
                "sp_team_id": sp_team_id,
                "name": team_name,
                "captain_id": captain["discord_id"],
                "members": members,
            })

            await self._admin_log("🛡️ Team Created (Sim)", 0x3A86FF, [
                ("Team", team_name),
                ("Team ID", str(sp_team_id)),
                ("Captain", f"{captain['ign']} ({captain['discord_username']})"),
                ("Roster", "5 players registered"),
            ])
            log.info("    ✓ %s (ID %s) — captain: %s", team_name, sp_team_id, captain["ign"])

        return True

    # ── Phase 4 + 5: Register and approve ─────────────────────────────────────

    async def phase_register(self) -> bool:
        log.info("━━━ Phase 4+5: Register + Approve ━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if self.args.dry_run:
            for t in self.teams:
                log.info("[DRY RUN] Would register + approve: %s", t["name"])
            return True

        for team in self.teams:
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO mlbb_team_registrations
                            (period_id, sp_team_id, registered_by, reviewed_by, reviewed_at, status)
                        VALUES (%s, %s, %s, 'simulation', NOW(), 'approved')
                        """,
                        (self.period_id, team["sp_team_id"], team["captain_id"]),
                    )
                    self.registration_ids.append(cur.lastrowid)

            await self._admin_log("📋 Registration Approved (Sim)", 0x2ECC71, [
                ("Team", team["name"]),
                ("League", self.league_name),
                ("Status", "auto-approved"),
            ])
            log.info("  ✓ Registered + approved: %s", team["name"])

        return True

    # ── Phase 6: Schedule events ───────────────────────────────────────────────

    @staticmethod
    def _schedule_dates(n_events: int, max_per_day: int = 4) -> List[datetime]:
        """Distribute events across Thu/Fri/Sat/Sun, max 4/day, min 1/weekend."""
        PLAY_DAYS = {3, 4, 5, 6}  # Thu-Sun
        cursor = datetime.now(timezone.utc).replace(hour=19, minute=0, second=0, microsecond=0)
        while cursor.weekday() not in PLAY_DAYS:
            cursor += timedelta(days=1)

        dates: List[datetime] = []
        remaining = n_events
        weekend_count = 0

        while remaining > 0:
            if cursor.weekday() in PLAY_DAYS:
                slots = min(random.randint(1, max_per_day), remaining)
                if cursor.weekday() == 6 and weekend_count == 0:
                    slots = max(slots, 1)
                for _ in range(slots):
                    dates.append(cursor)
                remaining -= slots
                weekend_count += slots
                if cursor.weekday() == 6:
                    weekend_count = 0
            else:
                if cursor.weekday() == 3:
                    weekend_count = 0
            cursor += timedelta(days=1)
        return dates

    async def phase_schedule(self) -> bool:
        pairs = [
            (self.teams[i], self.teams[j])
            for i in range(len(self.teams))
            for j in range(i + 1, len(self.teams))
        ]
        log.info("━━━ Phase 6: Schedule (%d matches, Thu-Sun, max 4/day) ━━━━━━━",
                 len(pairs))

        play_dates = self._schedule_dates(len(pairs))
        random.shuffle(pairs)

        if self.args.dry_run:
            for (h, a), dt in zip(pairs, play_dates):
                log.info("[DRY RUN] %s %s — %s vs %s", dt.strftime("%Y-%m-%d"), dt.strftime("%a"),
                         h["name"], a["name"])
                self.events.append({"sp_event_id": 0, "name": f"{h['name']} vs {a['name']}",
                                    "home": h, "away": a, "date": dt.strftime("%Y-%m-%d"), "vc_id": None})
            return True

        schedule_lines: List[str] = []

        for (home, away), dt in zip(pairs, play_dates):
            date_str = dt.strftime("%Y-%m-%dT19:00:00")
            day_name = dt.strftime("%a")
            name = f"{home['name']} vs {away['name']}"
            try:
                event = await self.api.create_event(
                    name, home["sp_team_id"], away["sp_team_id"], date_str,
                    league_ids=[self.league_term_id] if self.league_term_id else None,
                    season_ids=[self._season_id] if self._season_id else None,
                )
                sp_event_id = event["id"]
                self.event_ids.append(sp_event_id)
            except Exception as e:
                log.error("  create_event '%s' failed: %s", name, e)
                return False

            self.events.append({
                "sp_event_id": sp_event_id,
                "name": name,
                "home": home,
                "away": away,
                "date": dt.strftime("%Y-%m-%d"),
                "vc_id": None,
            })
            schedule_lines.append(f"• `{dt.strftime('%Y-%m-%d')}` {day_name} — **{name}**")
            log.info("  %s %s — '%s' (ID %s)", dt.strftime("%Y-%m-%d"), day_name, name, sp_event_id)

        # Post schedule to #match-notifications
        sched_embed = _embed(
            f"📅 {self.league_name} — Simulation Schedule",
            0xFFB703,
            [],
            footer=f"🤖 SimBot · {len(self.events)} matches · {self.rule}",
            desc="\n".join(schedule_lines),
        )
        await self._notif(sched_embed)
        await self._admin_log("📅 Schedule Generated (Sim)", 0xFFB703, [
            ("League", self.league_name),
            ("Matches", str(len(self.events))),
            ("Rule", self.rule),
        ])
        return True

    # ── Phase 7: Simulate match results ───────────────────────────────────────

    async def _upload_screenshot(self) -> str:
        """Upload a fake VICTORY PNG to WP media and return its source URL."""
        try:
            media = await self.api.upload_media(
                _VICTORY_PNG,
                f"sim-victory-{random.randint(1000, 9999)}.png",
                "image/png",
            )
            return media.get("source_url") or media.get("link") or config.WP_URL
        except Exception as e:
            log.warning("Screenshot upload failed (%s) — using placeholder URL", e)
            return f"{config.WP_URL}/wp-content/uploads/sim-placeholder.png"

    def _series_games(self, home: dict, away: dict) -> List[Tuple[dict, dict, int, int]]:
        """Return (winner, loser, w_kills, l_kills) for each game in the series."""
        wins_needed = self._wins_needed()
        # Slight home advantage
        series_winner = home if random.random() < 0.55 else away
        series_loser = away if series_winner is home else home
        w_wins = l_wins = 0
        games: List[Tuple[dict, dict, int, int]] = []

        while w_wins < wins_needed:
            w_k = random.randint(8, 22)
            l_k = random.randint(2, max(2, w_k - 2))
            # Allow loser to steal a game for realism (not if already at max losses)
            if w_wins > 0 and l_wins < wins_needed - 1 and random.random() < 0.35:
                games.append((series_loser, series_winner, w_k, l_k))
                l_wins += 1
            else:
                games.append((series_winner, series_loser, w_k, l_k))
                w_wins += 1

        return games

    async def phase_matches(self) -> bool:
        total = len(self.events)
        log.info("━━━ Phase 7: Match Results (%d events, %ds delay) ━━━━━━━━━━━", total, self.args.round_delay)

        for idx, event in enumerate(self.events):
            home, away = event["home"], event["away"]
            log.info("  [%d/%d] %s", idx + 1, total, event["name"])

            if self.args.dry_run:
                winner = home if random.random() > 0.5 else away
                log.info("[DRY RUN] %s → %s wins", event["name"], winner["name"])
                continue

            # Create voice channel
            if self.voice_cat and self.guild_id:
                vc_name = f"🎮 {home['name']} vs {away['name']}"
                vc_id = await self.dc.create_voice_channel(self.guild_id, vc_name, self.voice_cat)
                if vc_id:
                    event["vc_id"] = vc_id
                    self.vc_ids.append(vc_id)
                    log.info("    VC created: %s (ID %s)", vc_name, vc_id)
                else:
                    log.warning("    VC creation failed (check MATCH_VOICE_CATEGORY_ID / bot permissions)")

            # Upload one screenshot per match (reused across games)
            screenshot_url = await self._upload_screenshot()
            games = self._series_games(home, away)

            for game_num, (winner, loser, w_kills, l_kills) in enumerate(games, 1):
                battle_id = str(random.randint(10 ** 16, 10 ** 17 - 1))
                duration_s = random.randint(7 * 60, 22 * 60)
                duration_str = f"{duration_s // 60:02d}:{duration_s % 60:02d}"
                match_ts = (datetime.now(timezone.utc) - timedelta(minutes=random.randint(5, 60)))
                game_label = f"Game {game_num}" if len(games) > 1 else "Match"

                # Insert submission as 'confirmed' directly (bypasses /match submit + confirm flow;
                # ai_confidence=0.97 represents a clean parse, ai_raw flags it as simulated)
                async with db.get_conn() as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            """
                            INSERT INTO mlbb_match_submissions
                                (sp_event_id, submitted_by, winning_team_id, screenshot_url,
                                 battle_id, winner_kills, loser_kills, match_duration,
                                 match_timestamp, ai_confidence, ai_raw,
                                 confirmed_by, confirmed_at, status)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0.97, %s, %s, NOW(), 'confirmed')
                            """,
                            (
                                event["sp_event_id"],
                                winner["captain_id"],
                                winner["sp_team_id"],
                                screenshot_url,
                                battle_id,
                                w_kills, l_kills,
                                duration_str,
                                match_ts.strftime("%Y-%m-%d %H:%M:%S"),
                                json.dumps({"simulated": True, "event": event["name"], "game": game_num}),
                                loser["captain_id"],
                            ),
                        )
                        sub_id = cur.lastrowid
                        self.submission_ids.append(sub_id)

                # Post to #match-notifications (mirrors /match confirm embed)
                result_embed = _embed(
                    f"✅ {game_label} Confirmed — {event['name']}",
                    0x2ECC71,
                    [
                        ("🏆 Winner", winner["name"]),
                        ("Score", f"**{w_kills} – {l_kills}**"),
                        ("BattleID", f"`{battle_id}`"),
                        ("Duration", duration_str),
                        ("Sub ID", f"`#{sub_id}`"),
                    ],
                    footer=f"🤖 SimBot · {self.league_name} · {self.rule}",
                )
                result_embed["image"] = {"url": screenshot_url}
                await self._notif(result_embed)

                # Log to #tournament-admin (mirrors admin_log.log)
                await self._admin_log("📸 Result Confirmed (Sim)", 0x2ECC71, [
                    ("Match", event["name"]),
                    ("Game", str(game_num)),
                    ("Winner", winner["name"]),
                    ("Score", f"{w_kills}–{l_kills}"),
                    ("BattleID", battle_id),
                    ("Sub", f"#{sub_id}"),
                ])
                log.info("    %s %s wins %d–%d  duration %s  (sub #%d)",
                         game_label, winner["name"], w_kills, l_kills, duration_str, sub_id)

            # Tear down voice channel
            if event.get("vc_id"):
                await asyncio.sleep(2)
                await self.dc.delete_channel(event["vc_id"])
                if event["vc_id"] in self.vc_ids:
                    self.vc_ids.remove(event["vc_id"])
                log.info("    VC %s deleted", event["vc_id"])

            if idx < total - 1:
                log.info("    Waiting %ds ...", self.args.round_delay)
                await asyncio.sleep(self.args.round_delay)

        return True

    # ── Phase 8: Final summary ─────────────────────────────────────────────────

    async def phase_summary(self):
        log.info("━━━ Phase 8: Final Summary ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if self.args.dry_run or not self.submission_ids:
            return

        # Tally wins per team from confirmed submissions
        standings: Dict[int, dict] = {
            t["sp_team_id"]: {"name": t["name"], "wins": 0}
            for t in self.teams
        }
        ph = ",".join(["%s"] * len(self.submission_ids))
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT winning_team_id, COUNT(*) FROM mlbb_match_submissions "
                    f"WHERE id IN ({ph}) AND status='confirmed' GROUP BY winning_team_id",
                    tuple(self.submission_ids),
                )
                for tid, wins in await cur.fetchall():
                    if tid in standings:
                        standings[tid]["wins"] = wins

        ranked = sorted(standings.values(), key=lambda x: x["wins"], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        lines = [
            f"{medals[i] if i < 3 else f'{i+1}.'} **{t['name']}** — {t['wins']} wins"
            for i, t in enumerate(ranked)
        ]
        total_games = len(self.submission_ids)
        champion = ranked[0]["name"] if ranked else "—"

        summary_embed = _embed(
            f"🏆 {self.league_name} — Simulation Complete",
            0x9D4EDD, [],
            footer=f"🤖 SimBot · {total_games} games played · {self.rule}",
            desc="**Final Standings**\n\n" + "\n".join(lines),
        )
        await self._notif(summary_embed)

        await self._admin_log("🏆 Simulation Complete", 0x9D4EDD, [
            ("League", self.league_name),
            ("Rule", self.rule),
            ("Teams", str(len(self.teams))),
            ("Events", str(len(self.events))),
            ("Games played", str(total_games)),
            ("Champion", champion),
        ])
        log.info("Champion: %s (%d wins)", champion, ranked[0]["wins"] if ranked else 0)

    # ── Phase 9: Cleanup ───────────────────────────────────────────────────────

    async def cleanup(self):
        log.info("━━━ Phase 9: Cleanup ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # WordPress / SportsPress posts
        for eid in self.event_ids:
            try:
                await self.api.delete_event(eid)
                log.info("  Deleted sp_event %s", eid)
            except Exception as e:
                log.warning("  delete_event %s: %s", eid, e)

        for tid in self.team_ids:
            try:
                await self.api.delete_team(tid)
                log.info("  Deleted sp_team %s", tid)
            except Exception as e:
                log.warning("  delete_team %s: %s", tid, e)

        for pid in self.player_ids:
            try:
                await self.api.delete_player(pid)
                log.info("  Deleted sp_player %s", pid)
            except Exception as e:
                log.warning("  delete_player %s: %s", pid, e)

        for tbl_id in self.table_ids:
            try:
                await self.api.delete_table(tbl_id)
                log.info("  Deleted sp_table %s", tbl_id)
            except Exception as e:
                log.warning("  delete_table %s: %s", tbl_id, e)

        if self.league_term_id:
            try:
                await self.api.delete_league(self.league_term_id)
                log.info("  Deleted sp_league term %s", self.league_term_id)
            except Exception as e:
                log.warning("  delete_league %s: %s", self.league_term_id, e)

        for pid in self.page_ids:
            try:
                import subprocess
                subprocess.run([
                    "wp", "--path=/var/www/sites/play.mlbb.site",
                    "--skip-plugins", "--skip-themes", "--allow-root",
                    "post", "delete", str(pid), "--force",
                ], capture_output=True)
                log.info("  Deleted WP page %s", pid)
            except Exception as e:
                log.warning("  delete page %s: %s", pid, e)

        # Stray voice channels (if process was interrupted mid-match)
        for vc_id in list(self.vc_ids):
            try:
                await self.dc.delete_channel(vc_id)
                log.info("  Deleted orphaned VC %s", vc_id)
            except Exception as e:
                log.warning("  delete_channel %s: %s", vc_id, e)

        # Custom DB tables
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                if self.submission_ids:
                    ph = ",".join(["%s"] * len(self.submission_ids))
                    await cur.execute(
                        f"DELETE FROM mlbb_match_submissions WHERE id IN ({ph})",
                        tuple(self.submission_ids),
                    )
                if self.registration_ids:
                    ph = ",".join(["%s"] * len(self.registration_ids))
                    await cur.execute(
                        f"DELETE FROM mlbb_team_registrations WHERE id IN ({ph})",
                        tuple(self.registration_ids),
                    )
                if self.fake_discord_ids:
                    ph = ",".join(["%s"] * len(self.fake_discord_ids))
                    await cur.execute(
                        f"DELETE FROM mlbb_player_roster WHERE discord_id IN ({ph})",
                        tuple(self.fake_discord_ids),
                    )
                if self.period_id:
                    await cur.execute(
                        "DELETE FROM mlbb_registration_periods WHERE id=%s",
                        (self.period_id,),
                    )
        log.info("  DB records removed")

        await self._admin_log("🧹 Simulation Cleaned Up", 0x95A5A6, [
            ("League", self.league_name),
            ("Players", str(len(self.player_ids))),
            ("Teams", str(len(self.team_ids))),
            ("Events", str(len(self.event_ids))),
            ("Submissions", str(len(self.submission_ids))),
        ])
        log.info("Cleanup complete.")

    # ── Run ────────────────────────────────────────────────────────────────────

    async def run(self):
        teams = self.args.teams
        matches = teams * (teams - 1) // 2
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("MLBB Tournament Bot — League Simulation")
        log.info("Teams: %d | Matches: %d | Rule: %s | Delay: %ds | Cleanup: %s | Dry run: %s",
                 teams, matches,
                 self.args.rule or "random", self.args.round_delay,
                 "no" if self.args.no_cleanup else "yes",
                 self.args.dry_run)
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        ok = True
        try:
            if not self.args.dry_run:
                await self._ensure_channel_access()
            ok = ok and await self.phase_league()
            ok = ok and await self.phase_teams()
            await self._update_league_page()
            ok = ok and await self.phase_register()
            ok = ok and await self.phase_schedule()
            await self._update_league_page()
            ok = ok and await self.phase_matches()
            await self.phase_summary()
            await self._update_league_page()
        except KeyboardInterrupt:
            log.warning("Interrupted by user.")
            ok = False
        except Exception as e:
            log.exception("Simulation error: %s", e)
            await self._admin_log("💥 Simulation Error", 0xE74C3C, [
                ("Error", str(e)[:200]),
                ("League", self.league_name or "—"),
            ])
            ok = False
        finally:
            if not self.args.no_cleanup and not self.args.dry_run:
                await self.cleanup()
            elif self.args.no_cleanup and not self.args.dry_run:
                await self._refresh_hub_page()
                retained = ", ".join(t["name"] for t in self.teams)
                log.info("--no-cleanup: artifacts retained. Teams: %s", retained)
                await self._admin_log("⚠️ Artifacts Retained", 0xF59E0B, [
                    ("League", self.league_name),
                    ("Teams", retained),
                    ("Note", "Delete manually or re-run without --no-cleanup"),
                ])

        status = "✓ OK" if ok else "✗ FAILED"
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        log.info("Simulation %s", status)


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="End-to-end league simulation for MLBB-TournamentBot."
    )
    p.add_argument("--teams", type=int, default=4,
                   help="Number of teams to simulate (default: 4, min: 2)")
    p.add_argument("--round-delay", type=int, default=8, dest="round_delay",
                   help="Seconds to wait between match rounds (default: 8)")
    p.add_argument("--no-cleanup", action="store_true", dest="no_cleanup",
                   help="Keep all Bot-* artifacts after the run for manual inspection")
    p.add_argument("--rule", choices=RULES, default=None,
                   help="Force a specific match rule (default: random)")
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Print the plan without creating anything")
    args = p.parse_args()
    if args.teams < 2:
        p.error("--teams must be at least 2")
    if args.teams > len(_TEAM_CODES):
        p.error(f"--teams max is {len(_TEAM_CODES)}")
    return args


async def main():
    args = _parse()
    config.validate()

    if not args.dry_run:
        await db.init()

    try:
        await LeagueSimulator(args).run()
    finally:
        if not args.dry_run:
            await db.close()


if __name__ == "__main__":
    asyncio.run(main())
