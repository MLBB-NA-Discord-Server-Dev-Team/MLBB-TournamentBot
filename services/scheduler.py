"""
services/scheduler.py -- background task loop

Runs every 60 seconds. Responsible for:
  - Transitioning mlbb_registration_periods: scheduled -> open -> closed
    based on opens_at / closes_at datetimes.
  - On close: auto-generating round-robin schedules for leagues with
    enough approved teams.
  - Auto-approving pending team registrations (roster-validated).
  - Syncing confirmed match results to SportsPress.
  - Managing season lifecycle (finalize, advance).
  - Generating WordPress league pages after schedule creation.
"""
import logging
import time

from discord.ext import tasks

from services import db
from services import admin_log
from services.admin_log import Event
from services.db_helpers import (
    get_approved_teams_for_period,
    get_league_term_for_table,
    get_season_for_period,
    get_play_end_for_season,
)
from services.round_robin import generate_schedule, ScheduleError
from services import league_lifecycle as lifecycle

logger = logging.getLogger(__name__)

MIN_TEAMS_FOR_SCHEDULE = 2

# Interval tracking (seconds since epoch)
_last_auto_approve = 0.0
_last_result_sync = 0.0
_last_season_check = 0.0
_last_hub_update = 0.0

AUTO_APPROVE_INTERVAL = 300      # 5 minutes
RESULT_SYNC_INTERVAL = 600       # 10 minutes
SEASON_CHECK_INTERVAL = 21600    # 6 hours
HUB_UPDATE_INTERVAL = 3600       # 1 hour


class Scheduler:
    """Attach to the bot via Scheduler(bot).start()."""

    def __init__(self, bot):
        self.bot = bot
        self._loop.start()

    def stop(self):
        self._loop.cancel()

    @tasks.loop(seconds=60)
    async def _loop(self):
        try:
            await self._tick_registrations()
        except Exception as e:
            logger.error("Scheduler tick (registrations) error: %s", e)

        try:
            await self._tick_auto_approve()
        except Exception as e:
            logger.error("Scheduler tick (auto-approve) error: %s", e)

        try:
            await self._tick_result_sync()
        except Exception as e:
            logger.error("Scheduler tick (result-sync) error: %s", e)

        try:
            await self._tick_season_lifecycle()
        except Exception as e:
            logger.error("Scheduler tick (season-lifecycle) error: %s", e)

        try:
            await self._tick_hub_update()
        except Exception as e:
            logger.error("Scheduler tick (hub-update) error: %s", e)

    @_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()

    # -- Registration transitions -----------------------------------------------

    async def _tick_registrations(self):
        """
        scheduled -> open  when opens_at  <= NOW()
        open      -> closed when closes_at <= NOW() (if closes_at is set)
        """
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                # Open scheduled periods whose window has arrived
                await cur.execute(
                    """
                    UPDATE mlbb_registration_periods
                    SET status = 'open'
                    WHERE status = 'scheduled'
                      AND opens_at <= NOW()
                    """
                )
                opened = cur.rowcount

                # Find periods about to close (before updating) so we can
                # trigger schedule generation for them.
                # Skip persistent_league periods -- they handle their own lifecycle.
                await cur.execute(
                    """
                    SELECT id, entity_type, entity_id, rule
                    FROM mlbb_registration_periods
                    WHERE status = 'open'
                      AND closes_at IS NOT NULL
                      AND closes_at <= NOW()
                      AND created_by != 'persistent_league'
                    """
                )
                closing_periods = await cur.fetchall()

                # Close open periods whose window has passed
                # (includes persistent_league periods -- they just won't get auto-schedule-gen)
                await cur.execute(
                    """
                    UPDATE mlbb_registration_periods
                    SET status = 'closed'
                    WHERE status = 'open'
                      AND closes_at IS NOT NULL
                      AND closes_at <= NOW()
                    """
                )
                closed = cur.rowcount

        if opened:
            logger.info("Scheduler: opened %d registration period(s)", opened)
            await self._notify_opened(opened)

        if closed:
            logger.info("Scheduler: closed %d registration period(s)", closed)

        # Trigger schedule generation for newly-closed league periods
        for period_row in closing_periods:
            period_id, entity_type, entity_id, rule = period_row
            if entity_type != 'league':
                continue
            try:
                await self._generate_league_schedule(period_id, entity_id)
            except Exception as e:
                logger.error(
                    "Failed to generate schedule for period %d (entity %d): %s",
                    period_id, entity_id, e,
                )

    # -- Auto-approve (every 5 min) --------------------------------------------

    async def _tick_auto_approve(self):
        global _last_auto_approve
        now = time.time()
        if now - _last_auto_approve < AUTO_APPROVE_INTERVAL:
            return
        _last_auto_approve = now

        results = await lifecycle.check_pending_approvals()
        approved = [r for r in results if r.ok]
        if approved:
            logger.info("Auto-approved %d/%d pending registrations", len(approved), len(results))
            for r in approved:
                await self._notify_auto_approved(r.data)

    # -- Result sync (every 10 min) --------------------------------------------

    async def _tick_result_sync(self):
        global _last_result_sync
        now = time.time()
        if now - _last_result_sync < RESULT_SYNC_INTERVAL:
            return
        _last_result_sync = now

        r = await lifecycle.sync_confirmed_results()
        if r.ok and r.data.get("synced", 0) > 0:
            logger.info("Synced %d match results to SportsPress", r.data["synced"])
            try:
                await admin_log.log(
                    self.bot,
                    Event.SYSTEM,
                    fields={
                        "Action": "Match results synced to SportsPress",
                        "Synced": r.data["synced"],
                        "Errors": r.data.get("errors", 0),
                    },
                )
            except Exception:
                pass

    # -- Season lifecycle (every 6 hours) --------------------------------------

    async def _tick_season_lifecycle(self):
        global _last_season_check
        now = time.time()
        if now - _last_season_check < SEASON_CHECK_INTERVAL:
            return
        _last_season_check = now

        # Check season status
        status = await lifecycle.get_season_status()
        if not status.ok:
            return

        current = status.data.get("current")

        # If current season has ended, finalize it
        if current:
            from datetime import date as date_type
            play_end = date_type.fromisoformat(current["play_end"])
            if date_type.today() > play_end:
                r = await lifecycle.finalize_season(current["sp_season_id"])
                if r.ok:
                    logger.info("Finalized season: %s", r.data.get("season_name"))
                    try:
                        await admin_log.log(
                            self.bot,
                            Event.SYSTEM,
                            fields={
                                "Action": "Season finalized",
                                "Season": r.data.get("season_name", "?"),
                                "Periods Closed": r.data.get("periods_closed", 0),
                            },
                        )
                    except Exception:
                        pass

        # Ensure next season infrastructure exists
        r = await lifecycle.ensure_next_season()
        if r.ok and r.data.get("action") == "created":
            logger.info("Created next season: %s", r.data.get("next_season"))
            try:
                await admin_log.log(
                    self.bot,
                    Event.SYSTEM,
                    fields={
                        "Action": "Next season infrastructure created",
                        "Season": r.data.get("next_season", "?"),
                    },
                )
            except Exception:
                pass

    # -- Hub page update (every 1 hour) ----------------------------------------

    async def _tick_hub_update(self):
        global _last_hub_update
        now = time.time()
        if now - _last_hub_update < HUB_UPDATE_INTERVAL:
            return
        _last_hub_update = now

        r = await lifecycle.update_league_hub_page()
        if r.ok and r.data.get("hub_page_id"):
            logger.info("Updated league hub page with %d leagues", r.data.get("leagues", 0))

    # -- Round-robin schedule generation ----------------------------------------


    # ── FreePlay team materialization ─────────────────────────────────────

    async def _materialize_free_play_teams(self, period_id: int, entity_id: int) -> int:
        """
        When a FreePlay registration period closes, shuffle the signup pool and
        create teams of 5-6 players. Each new team is auto-registered as approved
        and its roster populated. Returns the number of teams created.
        """
        import random
        import config as _config
        from services.sportspress import SportsPressAPI
        from services.db_helpers import (
            setup_team_roster_display,
            sync_team_roster_list,
            get_player_active_team_ids,
        )

        # Pull the pool (active players only — filter out any who left Discord)
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT fp.discord_id, fp.sp_player_id
                    FROM mlbb_free_play_pool fp
                    WHERE fp.period_id = %s
                    ORDER BY fp.joined_at
                    """,
                    (period_id,),
                )
                pool = await cur.fetchall()

        pool_size = len(pool)
        if pool_size < 10:
            logger.warning(
                "Period %d (FreePlay): pool has %d players, need at least 10 for 2 teams",
                period_id, pool_size,
            )
            await admin_log.log(
                self.bot, Event.SYSTEM,
                fields={
                    "Action": "FreePlay pool too small",
                    "Period": period_id,
                    "Pool size": pool_size,
                    "Minimum": 10,
                },
            )
            return 0

        # Get the league name (parent of the entity_id sp_table)
        async with db.get_conn() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT post_title FROM wp_posts WHERE ID=%s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        table_title = row[0] if row else f"League {entity_id}"
        # "Eruditio League — Spring 2026" → "Eruditio Spring 2026"
        base_name = table_title.replace(" League", "").strip()

        # Shuffle + split into teams of 5-6
        # Target team size: prefer 5, add extras as 6th players starting from top
        random.shuffle(pool)
        teams = []
        num_teams = pool_size // 5
        if num_teams < 2:
            return 0
        # How many teams get 6 players vs 5
        extras = pool_size - (num_teams * 5)
        # Only keep extras if they fit within existing teams (up to 6 each)
        extras = min(extras, num_teams)

        idx = 0
        for t in range(num_teams):
            size = 6 if t < extras else 5
            teams.append(pool[idx:idx + size])
            idx += size
        # Any leftovers beyond what teams can hold → drop (too few for another team)

        # Squad name bank (rotated per materialization)
        squad_names = [
            "Vanguard", "Sentinels", "Raptors", "Titans", "Phoenix", "Wolves",
            "Dragons", "Valkyries", "Reapers", "Falcons", "Oracles", "Specters",
            "Warhawks", "Crimson", "Obsidian", "Stormriders",
        ]
        random.shuffle(squad_names)

        api = SportsPressAPI(_config.WP_URL, _config.WP_USER, _config.WP_APP_PASSWORD)
        created_count = 0

        for i, members in enumerate(teams):
            squad = squad_names[i % len(squad_names)]
            team_name = f"{base_name} {squad}"

            # Create sp_team via REST API
            try:
                team_obj = await api.create_team(team_name)
                sp_team_id = team_obj["id"]
            except Exception as e:
                logger.error("Failed to create FreePlay team '%s': %s", team_name, e)
                continue

            # Create sp_list + wire ACF roster display (same as /team create)
            try:
                sp_list = await api.create_player_list(f"{team_name} — Roster")
                await setup_team_roster_display(sp_team_id, sp_list["id"])
            except Exception as e:
                logger.warning("Could not set up roster display for team %d: %s", sp_team_id, e)

            # First player is captain, rest are players
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    for j, (discord_id, sp_player_id) in enumerate(members):
                        role = "captain" if j == 0 else "player"
                        await cur.execute(
                            """
                            INSERT INTO mlbb_player_roster
                                (discord_id, sp_player_id, sp_team_id, role, status)
                            VALUES (%s, %s, %s, %s, 'active')
                            ON DUPLICATE KEY UPDATE sp_team_id=%s, role=%s, status='active'
                            """,
                            (discord_id, sp_player_id, sp_team_id, role,
                             sp_team_id, role),
                        )

            # Auto-register the team as approved for this period
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO mlbb_team_registrations
                            (period_id, sp_team_id, registered_by, status, reviewed_at, reviewed_by)
                        VALUES (%s, %s, 'system', 'approved', NOW(), 'system')
                        """,
                        (period_id, sp_team_id),
                    )

            # Sync SP player→team relationships and roster display
            for discord_id, sp_player_id in members:
                try:
                    team_ids_for_player = await get_player_active_team_ids(discord_id)
                    await api.set_player_teams(sp_player_id, team_ids_for_player)
                except Exception as e:
                    logger.warning("set_player_teams failed for %s: %s", sp_player_id, e)
            try:
                await sync_team_roster_list(sp_team_id)
            except Exception as e:
                logger.warning("sync_team_roster_list failed for %d: %s", sp_team_id, e)

            created_count += 1
            logger.info(
                "Materialized FreePlay team '%s' (ID %d) with %d players",
                team_name, sp_team_id, len(members),
            )

        # Notify
        await admin_log.log(
            self.bot, Event.SYSTEM,
            fields={
                "Action": "FreePlay teams materialized",
                "Period": period_id,
                "Pool size": pool_size,
                "Teams created": created_count,
                "League": table_title,
            },
        )
        return created_count

    async def _generate_league_schedule(self, period_id: int, entity_id: int):
        """Generate and create round-robin events when a league registration closes."""
        # Get approved teams -- only those with exactly 5-6 active roster members
        all_teams = await get_approved_teams_for_period(period_id, eligible_only=False)
        teams = [t for t in all_teams if 5 <= t.get("roster_count", 0) <= 6]
        ineligible = [t for t in all_teams if not (5 <= t.get("roster_count", 0) <= 6)]
        if ineligible:
            logger.warning(
                "Period %d: %d ineligible teams (wrong roster size): %s",
                period_id, len(ineligible),
                ", ".join(f"{t['team_name']}({t['roster_count']})" for t in ineligible),
            )
        if len(teams) < MIN_TEAMS_FOR_SCHEDULE:
            logger.info(
                "Period %d: only %d approved teams, skipping schedule generation",
                period_id, len(teams),
            )
            return

        # Get league term ID (for sp_league taxonomy on events)
        league_term_id = await get_league_term_for_table(entity_id)

        # Get season info
        season_info = await get_season_for_period(period_id)
        if not season_info:
            logger.warning("Period %d: no season linked, skipping schedule", period_id)
            return

        play_start = season_info["play_start"]
        play_end = await get_play_end_for_season(season_info["sp_season_id"])
        if not play_end:
            logger.warning("Period %d: cannot determine play_end", period_id)
            return

        # Generate the abstract schedule
        team_ids = [t["sp_team_id"] for t in teams]
        team_names = {t["sp_team_id"]: t["team_name"] for t in teams}

        try:
            entries = generate_schedule(team_ids, play_start, play_end)
        except ScheduleError as e:
            logger.error("Period %d: schedule generation failed: %s", period_id, e)
            async with db.get_conn() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "UPDATE mlbb_registration_periods SET generation_error=%s, generation_attempts=generation_attempts+1 WHERE id=%s",
                        (str(e)[:1000], period_id),
                    )
            await self._notify_schedule_error(period_id, entity_id, str(e))
            return

        # Create sp_events via the SportsPress API
        from services.sportspress import SportsPressAPI
        import config
        api = SportsPressAPI(config.WP_URL, config.WP_USER, config.WP_APP_PASSWORD)

        league_ids = [league_term_id] if league_term_id else None
        season_ids = [season_info["sp_season_id"]]

        created = 0
        for entry in entries:
            home_name = team_names[entry["home_team_id"]]
            away_name = team_names[entry["away_team_id"]]
            name = f"{home_name} vs {away_name}"
            date_str = f"{entry['date'].isoformat()}T{entry['time']}"

            try:
                await api.create_event(
                    name,
                    entry["home_team_id"],
                    entry["away_team_id"],
                    date_str,
                    league_ids=league_ids,
                    season_ids=season_ids,
                )
                created += 1
            except Exception as e:
                logger.error("Failed to create event '%s': %s", name, e)

        logger.info(
            "Period %d: created %d/%d events for %d teams",
            period_id, created, len(entries), len(teams),
        )

        await self._notify_schedule_created(
            period_id, entity_id, len(teams), created, len(entries),
            season_info["season_name"],
        )

        # Generate WordPress page for this league
        if league_term_id:
            try:
                r = await lifecycle.generate_league_wp_page(
                    entity_id, league_term_id, season_info["season_name"]
                )
                if r.ok:
                    logger.info("Generated WP page for league (table %d): %s",
                                entity_id, r.data.get("action"))
            except Exception as e:
                logger.error("WP page generation failed for table %d: %s", entity_id, e)

    # -- Notifications -----------------------------------------------------------

    async def _notify_opened(self, count: int):
        try:
            await admin_log.log(
                self.bot,
                Event.SYSTEM,
                fields={
                    "Action": "Registration auto-opened",
                    "Periods": count,
                    "Trigger": "Season schedule",
                },
            )
        except Exception as e:
            logger.warning("Could not post scheduler notification: %s", e)

    async def _notify_auto_approved(self, data: dict):
        try:
            await admin_log.log(
                self.bot,
                Event.SYSTEM,
                fields={
                    "Action": "Team auto-approved",
                    "Team": data.get("team_name", "?"),
                    "Roster": f"{data.get('roster_count', '?')} players",
                    "Registration": f"#{data.get('registration_id', '?')}",
                },
            )
        except Exception as e:
            logger.warning("Could not post auto-approve notification: %s", e)

    async def _notify_schedule_created(
        self, period_id, entity_id, num_teams, created, total, season_name
    ):
        try:
            await admin_log.log(
                self.bot,
                Event.SYSTEM,
                fields={
                    "Action": "Round-robin schedule generated",
                    "League Entity": entity_id,
                    "Season": season_name,
                    "Teams": num_teams,
                    "Events Created": f"{created}/{total}",
                    "Trigger": f"Registration period {period_id} closed",
                },
            )
        except Exception as e:
            logger.warning("Could not post schedule notification: %s", e)

    async def _notify_schedule_error(self, period_id, entity_id, error_msg):
        try:
            await admin_log.log(
                self.bot,
                Event.SYSTEM,
                fields={
                    "Action": "Schedule generation FAILED",
                    "League Entity": entity_id,
                    "Period": period_id,
                    "Error": error_msg,
                },
            )
        except Exception as e:
            logger.warning("Could not post schedule error notification: %s", e)
