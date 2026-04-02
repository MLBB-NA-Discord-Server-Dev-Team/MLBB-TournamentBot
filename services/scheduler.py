"""
services/scheduler.py — background task loop

Runs every 60 seconds. Responsible for:
  - Transitioning mlbb_registration_periods: scheduled → open → closed
    based on opens_at / closes_at datetimes derived from mlbb_season_schedule.
"""
import logging

from discord.ext import tasks

from services import db
from services import admin_log
from services.admin_log import Event

logger = logging.getLogger(__name__)


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

    async def _notify_opened(self, count: int):
        """Post a system note to #tournament-admin when registration auto-opens."""
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
