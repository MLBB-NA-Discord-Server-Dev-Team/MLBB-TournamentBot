#!/usr/bin/env python3
"""
scripts/deploy.py -- Idempotent production deployment for MLBB-TournamentBot.

Runs 7 phases in order:
  1. Pre-flight checks (env, MySQL, WP API, Discord token, WP-CLI)
  2. Database setup (migrate.py + schema verification)
  3. WordPress infrastructure (league_pages.py + season_init.py)
  4. Systemd service (install, enable, restart, verify Scheduler started)
  5. Cron jobs (persistent_league + autonomous_sim)
  6. Log rotation (/etc/logrotate.d/mlbb-tournament-bot)
  7. Post-install verification

Usage:
    python scripts/deploy.py              # full deploy
    python scripts/deploy.py --check      # preflight only
    python scripts/deploy.py --skip-wp    # skip WordPress infrastructure
    python scripts/deploy.py --force      # no confirmation prompts
    python scripts/deploy.py --path /opt/mlbb-tournament-bot
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, Tuple

# -- Output helpers ------------------------------------------------------------

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _color(text: str, color: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{RESET}"


def ok(msg: str):
    print(f"  {_color('[OK]', GREEN)}   {msg}")


def fail(msg: str):
    print(f"  {_color('[FAIL]', RED)} {msg}")


def skip(msg: str):
    print(f"  {_color('[SKIP]', YELLOW)} {msg}")


def info(msg: str):
    print(f"  {_color('[..]', BLUE)}   {msg}")


def phase(num: int, title: str):
    print()
    print(_color(f"━━━ Phase {num}: {title} ━━━", BOLD))


def banner(text: str):
    print()
    print(_color("═" * 60, BOLD))
    print(_color(text.center(60), BOLD))
    print(_color("═" * 60, BOLD))
    print()


# -- Deployer ------------------------------------------------------------------

class Deployer:
    REQUIRED_ENV_KEYS = [
        "DISCORD_TOKEN", "GUILD_IDS", "WP_PLAY_MLBB_URL", "WP_PLAY_MLBB_USER",
        "WP_PLAY_MLBB", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD",
        "MATCH_VOICE_CATEGORY_ID",
    ]
    REQUIRED_TABLES = [
        "mlbb_season_schedule", "mlbb_registration_periods", "mlbb_team_registrations",
        "mlbb_player_roster", "mlbb_team_invites", "mlbb_match_submissions",
        "mlbb_match_schedule", "mlbb_discord_events", "mlbb_voice_channels",
        "mlbb_pickup_pool", "mlbb_pickup_tournaments", "mlbb_pickup_matches",
    ]

    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.install_path = Path(args.path).resolve()
        self.venv_python = self.install_path / "venv" / "bin" / "python"
        self.env_path = self.install_path / ".env"
        self.env_values: dict = {}
        self.errors: list = []
        self.warnings: list = []

    # -- Env loader ---------------------------------------------------------

    def _load_env(self) -> dict:
        if not self.env_path.exists():
            return {}
        values = {}
        with open(self.env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                values[k.strip()] = v.strip().strip('"').strip("'")
        return values

    def _confirm(self, prompt: str) -> bool:
        if self.args.force:
            return True
        answer = input(f"  {_color('[?]', YELLOW)}    {prompt} [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    # -- Phase 1: Pre-flight checks -----------------------------------------

    def phase1_preflight(self) -> bool:
        phase(1, "Pre-flight checks")
        all_ok = True

        # .env exists
        if not self.env_path.exists():
            fail(f".env not found at {self.env_path}")
            self.errors.append("Missing .env file")
            return False
        ok(f".env found at {self.env_path}")

        # Load env values
        self.env_values = self._load_env()

        # Required keys present
        missing = [k for k in self.REQUIRED_ENV_KEYS if not self.env_values.get(k)]
        if missing:
            fail(f"Missing required env keys: {', '.join(missing)}")
            self.errors.append(f"Missing env: {missing}")
            all_ok = False
        else:
            ok(f"All {len(self.REQUIRED_ENV_KEYS)} required env keys set")

        # Install path + venv
        if not self.install_path.exists():
            fail(f"Install path does not exist: {self.install_path}")
            self.errors.append("Install path missing")
            return False
        ok(f"Install path: {self.install_path}")

        if not self.venv_python.exists():
            fail(f"venv Python not found at {self.venv_python}")
            self.errors.append("venv missing — run deploy.sh first")
            all_ok = False
        else:
            ok(f"venv Python: {self.venv_python}")

        # MySQL connectivity
        if not missing and self._check_mysql():
            ok("MySQL connection successful")
        else:
            all_ok = False

        # WP REST API
        if not missing and self._check_wp_api():
            ok("WordPress REST API reachable (SportsPress v2)")
        else:
            all_ok = False

        # Discord token
        if not missing and self._check_discord_token():
            ok("Discord bot token valid")
        else:
            all_ok = False

        # WP-CLI
        if self._check_wp_cli():
            ok("WP-CLI available")
        else:
            self.warnings.append("WP-CLI missing — WP infrastructure phase will fail")

        return all_ok

    def _check_mysql(self) -> bool:
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=self.env_values.get("DB_HOST", "localhost"),
                port=int(self.env_values.get("DB_PORT", "3306")),
                user=self.env_values["DB_USER"],
                password=self.env_values["DB_PASSWORD"],
                database=self.env_values["DB_NAME"],
                connection_timeout=5,
            )
            conn.close()
            return True
        except ImportError:
            fail("mysql.connector not installed (run deploy.sh first)")
            return False
        except Exception as e:
            fail(f"MySQL connection failed: {e}")
            self.errors.append(f"MySQL: {e}")
            return False

    def _check_wp_api(self) -> bool:
        try:
            url = self.env_values["WP_PLAY_MLBB_URL"].rstrip("/") + "/wp-json/sportspress/v2/seasons?per_page=1"
            auth = base64.b64encode(
                f"{self.env_values['WP_PLAY_MLBB_USER']}:{self.env_values['WP_PLAY_MLBB']}".encode()
            ).decode()
            req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status == 200
        except Exception as e:
            fail(f"WP REST API check failed: {e}")
            self.errors.append(f"WP API: {e}")
            return False

    def _check_discord_token(self) -> bool:
        try:
            req = urllib.request.Request(
                "https://discord.com/api/v10/users/@me",
                headers={
                    "Authorization": f"Bot {self.env_values['DISCORD_TOKEN']}",
                    "User-Agent": "DiscordBot (https://github.com/MLBB-NA/TournamentBot, 1.0)",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                info(f"Bot identity: {data.get('username', '?')} (ID {data.get('id', '?')})")
                return True
        except Exception as e:
            fail(f"Discord token invalid: {e}")
            self.errors.append(f"Discord: {e}")
            return False

    def _check_wp_cli(self) -> bool:
        try:
            r = subprocess.run(
                ["wp", "--path=/var/www/sites/play.mlbb.site",
                 "--skip-plugins", "--skip-themes", "--allow-root", "core", "version"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                info(f"WordPress version: {r.stdout.strip()}")
                return True
            return False
        except Exception as e:
            fail(f"WP-CLI check failed: {e}")
            return False

    # -- Phase 2: Database --------------------------------------------------

    def phase2_database(self) -> bool:
        phase(2, "Database setup")
        if self.args.check:
            skip("--check mode, not running migrations")
            return True

        # Run migrate.py
        info("Running db/migrate.py...")
        r = subprocess.run(
            [str(self.venv_python), "db/migrate.py"],
            cwd=self.install_path, capture_output=True, text=True,
        )
        if r.returncode != 0:
            fail(f"migrate.py failed: {r.stderr.strip()[:300]}")
            self.errors.append("Migration failed")
            return False
        ok("Migration completed")

        # Verify tables
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=self.env_values.get("DB_HOST", "localhost"),
                user=self.env_values["DB_USER"],
                password=self.env_values["DB_PASSWORD"],
                database=self.env_values["DB_NAME"],
            )
            cur = conn.cursor()
            cur.execute("SHOW TABLES LIKE 'mlbb_%'")
            existing = {r[0] for r in cur.fetchall()}
            missing = [t for t in self.REQUIRED_TABLES if t not in existing]
            if missing:
                fail(f"Missing tables: {', '.join(missing)}")
                self.errors.append(f"Missing tables: {missing}")
                return False
            ok(f"All {len(self.REQUIRED_TABLES)} mlbb_* tables exist")

            # Schema drift: mlbb_season_schedule.play_end
            cur.execute("SHOW COLUMNS FROM mlbb_season_schedule LIKE 'play_end'")
            if not cur.fetchone():
                fail("mlbb_season_schedule.play_end column missing")
                self.warnings.append("Run: ALTER TABLE mlbb_season_schedule ADD COLUMN play_end DATE")
            else:
                ok("mlbb_season_schedule.play_end present")
            cur.close()
            conn.close()
        except Exception as e:
            fail(f"Schema verification failed: {e}")
            return False

        return True

    # -- Phase 3: WordPress infrastructure ----------------------------------

    def phase3_wordpress(self) -> bool:
        phase(3, "WordPress infrastructure")
        if self.args.check:
            skip("--check mode")
            return True
        if self.args.skip_wp:
            skip("--skip-wp flag")
            return True

        # league_pages.py
        info("Running scripts/league_pages.py (this may take a minute)...")
        r = subprocess.run(
            [str(self.venv_python), "scripts/league_pages.py"],
            cwd=self.install_path, capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            fail(f"league_pages.py failed: {r.stderr.strip()[:300]}")
            self.errors.append("league_pages failed")
            return False
        ok("league_pages.py completed (sp_league terms, format hubs, /custom-leagues/, /bot-leagues/)")

        # season_init.py
        info("Running scripts/season_init.py...")
        r = subprocess.run(
            [str(self.venv_python), "scripts/season_init.py"],
            cwd=self.install_path, capture_output=True, text=True, timeout=300,
        )
        if r.returncode != 0:
            fail(f"season_init.py failed: {r.stderr.strip()[:300]}")
            self.errors.append("season_init failed")
            return False
        ok("season_init.py completed (seasons, tables, registration periods)")

        return True

    # -- Phase 4: Systemd service -------------------------------------------

    def phase4_systemd(self) -> bool:
        phase(4, "Systemd service")
        if self.args.check:
            skip("--check mode")
            return True

        service_path = Path("/etc/systemd/system/mlbb-tournament-bot.service")
        template = f"""[Unit]
Description=MLBB Tournament Bot
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory={self.install_path}
ExecStart={self.venv_python} run.py
Restart=on-failure
RestartSec=10
StandardOutput=append:{self.install_path}/bot.log
StandardError=append:{self.install_path}/bot.log

[Install]
WantedBy=multi-user.target
"""
        # Write service file
        current = service_path.read_text() if service_path.exists() else ""
        if current == template:
            ok(f"{service_path} up to date")
        else:
            try:
                service_path.write_text(template)
                ok(f"Wrote {service_path}")
            except PermissionError:
                fail("Permission denied writing systemd service (need root)")
                return False

        # daemon-reload + enable
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        ok("systemctl daemon-reload")

        subprocess.run(["systemctl", "enable", "mlbb-tournament-bot"], capture_output=True)
        ok("systemctl enable mlbb-tournament-bot")

        # Restart
        info("Restarting mlbb-tournament-bot...")
        subprocess.run(["systemctl", "restart", "mlbb-tournament-bot"], check=True)

        # Wait for Scheduler started in log
        bot_log = self.install_path / "bot.log"
        deadline = time.time() + 30
        scheduler_started = False
        while time.time() < deadline:
            if bot_log.exists():
                content = bot_log.read_text()[-5000:]
                if "Scheduler started" in content:
                    scheduler_started = True
                    break
            time.sleep(1)

        if scheduler_started:
            ok("Scheduler started — bot bootstrapped channels + loaded cogs")
        else:
            fail("Timeout waiting for 'Scheduler started' in bot.log")
            self.errors.append("Bot failed to start cleanly")
            return False

        return True

    # -- Phase 5: Cron jobs -------------------------------------------------

    def phase5_cron(self) -> bool:
        phase(5, "Cron jobs")
        if self.args.check:
            skip("--check mode")
            return True

        persistent_line = (
            f"*/30 * * * * cd {self.install_path} && "
            f"venv/bin/python scripts/persistent_league.py >> /var/log/mlbb-persistent-league.log 2>&1"
        )
        autosim_line = (
            f"0 4 * * * cd {self.install_path} && "
            f"venv/bin/python scripts/autonomous_sim.py >> /var/log/mlbb-autosim.log 2>&1"
        )

        r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        current = r.stdout if r.returncode == 0 else ""

        added = []
        new_content = current.rstrip() + "\n"
        if "persistent_league.py" not in current:
            new_content += "\n# MLBB Tournament: persistent bot-league (every 30 min)\n"
            new_content += persistent_line + "\n"
            added.append("persistent_league")
        else:
            ok("persistent_league cron already present")

        if "autonomous_sim.py" not in current:
            new_content += "\n# MLBB Tournament: daily simulation health check (04:00 UTC)\n"
            new_content += autosim_line + "\n"
            added.append("autonomous_sim")
        else:
            ok("autonomous_sim cron already present")

        if added:
            proc = subprocess.run(["crontab", "-"], input=new_content, text=True,
                                  capture_output=True)
            if proc.returncode != 0:
                fail(f"crontab install failed: {proc.stderr}")
                return False
            for name in added:
                ok(f"Added cron: {name}")

        return True

    # -- Phase 6: Log rotation ----------------------------------------------

    def phase6_logrotate(self) -> bool:
        phase(6, "Log rotation")
        if self.args.check:
            skip("--check mode")
            return True

        conf_path = Path("/etc/logrotate.d/mlbb-tournament-bot")
        template = f"""/var/log/mlbb-persistent-league.log /var/log/mlbb-autosim.log {self.install_path}/bot.log {{
    weekly
    rotate 4
    compress
    missingok
    notifempty
    copytruncate
}}
"""
        current = conf_path.read_text() if conf_path.exists() else ""
        if current == template:
            ok(f"{conf_path} up to date")
        else:
            try:
                conf_path.write_text(template)
                ok(f"Wrote {conf_path}")
            except PermissionError:
                fail("Permission denied writing logrotate config")
                return False

        return True

    # -- Phase 7: Post-install verification ---------------------------------

    def phase7_verify(self) -> bool:
        phase(7, "Post-install verification")

        # Bot service active
        r = subprocess.run(["systemctl", "is-active", "mlbb-tournament-bot"],
                           capture_output=True, text=True)
        if r.stdout.strip() == "active":
            ok("mlbb-tournament-bot.service is active (running)")
        else:
            fail(f"Service state: {r.stdout.strip()}")
            self.errors.append("Bot service not running")

        # Reload .env (bot may have auto-populated channel IDs)
        env = self._load_env()
        channel_keys = ["MATCH_NOTIFICATIONS_CHANNEL_ID", "ADMIN_LOG_CHANNEL_ID",
                        "BOT_COMMANDS_CHANNEL_ID", "BOT_LEAGUE_CHANNEL_ID"]
        populated = [k for k in channel_keys if env.get(k)]
        if len(populated) == 4:
            ok(f"All 4 Discord channels bootstrapped: {', '.join(populated)}")
        else:
            missing_chs = [k for k in channel_keys if not env.get(k)]
            fail(f"Some channels not bootstrapped: {', '.join(missing_chs)}")
            self.warnings.append("Bot may need more time to connect to Discord")

        # DB checks
        try:
            import mysql.connector
            conn = mysql.connector.connect(
                host=env.get("DB_HOST", "localhost"),
                user=env["DB_USER"], password=env["DB_PASSWORD"],
                database=env["DB_NAME"],
            )
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM mlbb_season_schedule WHERE play_start <= NOW()")
            if cur.fetchone()[0] > 0:
                ok("Current season exists in mlbb_season_schedule")
            else:
                fail("No current season found")
                self.warnings.append("Run scripts/season_init.py manually")

            cur.execute("SELECT COUNT(*) FROM mlbb_registration_periods")
            count = cur.fetchone()[0]
            ok(f"Registration periods: {count} rows")
            cur.close()
            conn.close()
        except Exception as e:
            fail(f"DB verification failed: {e}")

        return len(self.errors) == 0

    # -- Main ---------------------------------------------------------------

    def run(self) -> int:
        banner("MLBB Tournament Bot — Deployment")
        print(f"  Install path: {self.install_path}")
        print(f"  Mode: {'CHECK (no changes)' if self.args.check else 'FULL DEPLOY'}")
        if self.args.skip_wp:
            print("  --skip-wp: WordPress infrastructure will be skipped")
        print()

        if not self.args.force and not self.args.check:
            if not self._confirm("Proceed with deployment?"):
                print("  Aborted.")
                return 1

        # Phase 1
        if not self.phase1_preflight():
            banner("DEPLOYMENT ABORTED — pre-flight checks failed")
            for e in self.errors:
                print(f"  - {e}")
            return 1

        if self.args.check:
            banner("CHECK MODE — all pre-flight checks passed")
            return 0

        # Phases 2-7
        phases = [
            (self.phase2_database, True),
            (self.phase3_wordpress, False),
            (self.phase4_systemd, True),
            (self.phase5_cron, False),
            (self.phase6_logrotate, False),
            (self.phase7_verify, False),
        ]
        for fn, critical in phases:
            try:
                if not fn() and critical:
                    banner("DEPLOYMENT FAILED — critical phase aborted")
                    return 1
            except Exception as e:
                fail(f"Exception in {fn.__name__}: {e}")
                if critical:
                    return 1

        # Final summary
        self._print_summary()
        return 0 if not self.errors else 1

    def _print_summary(self):
        banner("DEPLOYMENT SUMMARY")
        env = self._load_env()
        wp_url = env.get("WP_PLAY_MLBB_URL", "").rstrip("/")

        print("  Bot service:       systemctl status mlbb-tournament-bot")
        print("  Bot log:           tail -f {0}/bot.log".format(self.install_path))
        print("  Persistent league: python scripts/persistent_league.py --status")
        print("  Autosim log:       /var/log/mlbb-autosim.log")
        print()
        print("  WordPress pages:")
        print(f"    - {wp_url}/custom-leagues/")
        print(f"    - {wp_url}/bot-leagues/")
        print()
        if self.warnings:
            print(_color("  Warnings:", YELLOW))
            for w in self.warnings:
                print(f"    - {w}")
            print()
        if self.errors:
            print(_color("  Errors:", RED))
            for e in self.errors:
                print(f"    - {e}")
            print()
        else:
            print(_color("  All phases completed successfully.", GREEN))
            print()


# -- CLI -----------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="MLBB-TournamentBot deployment script.")
    p.add_argument("--check", action="store_true", help="Preflight checks only, no changes")
    p.add_argument("--skip-wp", action="store_true", dest="skip_wp",
                   help="Skip WordPress infrastructure phase")
    p.add_argument("--force", action="store_true", help="Skip confirmation prompts")
    p.add_argument("--path", default="/root/MLBB-TournamentBot",
                   help="Install path (default: /root/MLBB-TournamentBot)")
    args = p.parse_args()

    deployer = Deployer(args)
    sys.exit(deployer.run())


if __name__ == "__main__":
    main()
