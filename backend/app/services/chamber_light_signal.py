"""Chamber light blinks while a printer needs a person: paused or failed.

In a farm a stopped printer is hard to spot on the shelf. While its state is
PAUSE or FAILED the chamber light is toggled once per second; when the state
changes, the light returns to what it was before the blinking started.

The firmware's own "flashing" LED mode would be cheaper, but a P1S acknowledges
it with result "success" and keeps the light steady (tested 2026-10-23, both
by camera brightness and by eye), so the toggling happens here.

A blinking printer is never RUNNING, so Obico's detection, which only looks at
running prints, never sees a dark frame because of this.
"""

import asyncio
import logging
import time

from sqlalchemy import select

from backend.app.core.database import async_session
from backend.app.models.settings import Settings

logger = logging.getLogger(__name__)

BLINK_STATES = {"PAUSE", "PAUSED", "FAILED"}
PERIOD = 1.0  # seconds per half cycle: 1 s on, 1 s off
SETTING = "chamber_light_signal_enabled"
SETTINGS_TTL = 15.0


def needs_person(status) -> bool:
    return str(getattr(status, "state", "") or "").upper() in BLINK_STATES


class ChamberLightSignalService:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._task: asyncio.Task | None = None
        # printer id -> light state before blinking started (restored afterwards)
        self._blinking: dict[int, bool] = {}
        self._lit = True
        self._enabled_cache: tuple[float, bool] | None = None

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
            logger.info("Chamber light signal started")

    def stop(self):
        if self._task:
            self._task.cancel()
            self._task = None
        self._restore_all()

    async def _enabled(self) -> bool:
        now = self.clock()
        if self._enabled_cache and now - self._enabled_cache[0] < SETTINGS_TTL:
            return self._enabled_cache[1]
        async with async_session() as db:
            row = (await db.execute(select(Settings).where(Settings.key == SETTING))).scalar_one_or_none()
        enabled = bool(row and (row.value or "").lower() == "true")
        self._enabled_cache = (now, enabled)
        return enabled

    async def _loop(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Chamber light signal: %s", e)
            await asyncio.sleep(PERIOD)

    async def tick(self):
        from backend.app.services.printer_manager import printer_manager

        enabled = await self._enabled()
        statuses = printer_manager.get_all_statuses()
        wanted = (
            {pid for pid, st in statuses.items() if st and printer_manager.is_connected(pid) and needs_person(st)}
            if enabled
            else set()
        )
        for pid in [p for p in self._blinking if p not in wanted]:
            if enabled and statuses.get(pid) is not None and not printer_manager.is_connected(pid):
                continue  # possibly left dark; restore once the printer is reachable again
            self._restore(pid)

        self._lit = not self._lit
        for pid in wanted:
            client = printer_manager.get_client(pid)
            if not client:
                continue
            if pid not in self._blinking:
                self._blinking[pid] = bool(getattr(statuses[pid], "chamber_light", True))
                logger.info("Chamber light signal: printer %s needs attention, blinking", pid)
            client.set_chamber_light(self._lit, quiet=True)

    def _restore(self, pid: int):
        from backend.app.services.printer_manager import printer_manager

        before = self._blinking.pop(pid)
        client = printer_manager.get_client(pid)
        if client:
            client.set_chamber_light(before)
        logger.info("Chamber light signal: printer %s handled, light back %s", pid, "on" if before else "off")

    def _restore_all(self):
        for pid in list(self._blinking):
            try:
                self._restore(pid)
            except Exception as e:
                logger.warning("Chamber light signal: could not restore printer %s: %s", pid, e)


chamber_light_signal_service = ChamberLightSignalService()
