"""
services/scheduler.py — background task loop

Runs every 60 seconds. Responsible for:
  - Transitioning mlbb_registration_periods: scheduled → open → closed
    based on opens_at / closes_at datetimes.
  - On close: auto-generating round-robin schedules for leagues with
    enough approved teams.
"""
import logging

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

logger = logging.getLogger(__name__)

MIN_TEAMS_FOR_SCHEDULE = 2


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
            logger.error("Scheduler tick error: %s", e)

    @_loop.before_loop
    async def _before_loop(self):
        await self.bot.wait_until_ready()

    # ── Registration transitions ───────────────────────────────────────────

    async def _tick_registrations(self):
        """
        scheduled → open  when opens_at  <= NOW()
        open      → closed when closes_at <= NOW() (if closes_at is set)
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
                # trigger schedule generation for them
                await cur.execute(
                    """
                    SELECT id, entity_type, entity_id, rule
                    FROM mlbb_registration_periods
                    WHERE status = 'open'
                      AND closes_at IS NOT NULL
                      AND closes_at <= NOW()
                    """
                )
                closing_periods = await cur.fetchall()

                # Close open periods whose window has passed
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

    # ── Round-robin schedule generation ────────────────────────────────────

    async def _generate_league_schedule(self, period_id: int, entity_id: int):
        """Generate and create round-robin events when a league registration closes."""
        # Get approved teams — only those with exactly 6 active roster members
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

    # ── Notifications ──────────────────────────────────────────────────────

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
