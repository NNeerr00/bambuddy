"""Mandatory inventory binding for every feed used by a print.

Independent of optional weight warnings / Print Anyway. Physical telemetry
alone cannot prove which inventory spool will be consumed. All print entry
points converge on the scheduler's mapping checks, including the final check
after upload. Unused AMS slots do not need an assignment.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.filament_deficit import _global_to_ams_key
from backend.app.services.inventory_mode import spoolman_owns_assignments


async def spool_assignment_problem(db: AsyncSession, printer_id: int, used_feeds: set[int]) -> str | None:
    if not used_feeds:
        return "No used filament feed could be resolved"
    spoolman = await spoolman_owns_assignments(db)
    if spoolman:
        rows = (
            await db.execute(
                select(
                    SpoolmanSlotAssignment.ams_id,
                    SpoolmanSlotAssignment.tray_id,
                    SpoolmanSlotAssignment.spoolman_spool_id,
                ).where(SpoolmanSlotAssignment.printer_id == printer_id)
            )
        ).all()
        assigned = {(ams, tray): sid for ams, tray, sid in rows if sid and sid > 0}
    else:
        # Select scalars, not cached ORM relationships: re-read removals and
        # archival even if the candidate probe loaded this spool before FTP.
        rows = (
            await db.execute(
                select(
                    SpoolAssignment.ams_id,
                    SpoolAssignment.tray_id,
                    Spool.id,
                )
                .join(Spool, Spool.id == SpoolAssignment.spool_id)
                .where(
                    SpoolAssignment.printer_id == printer_id,
                    Spool.archived_at.is_(None),
                )
            )
        ).all()
        assigned = {(ams, tray): sid for ams, tray, sid in rows}

    checked = set()
    for feed in sorted(used_feeds):
        ams, tray = _global_to_ams_key(feed)
        label = f"external spool {tray + 1}" if ams == 255 else f"AMS {ams}, slot {tray + 1}"
        spool_id = assigned.get((ams, tray))
        if spool_id is None:
            return f"No inventory spool assigned to {label}. Assign a spool to continue"
        if spoolman and spool_id not in checked:
            from backend.app.services.spoolman import get_spoolman_client

            try:
                client = await get_spoolman_client()
                spool = await client.get_spool(spool_id) if client else None
            except Exception:
                return f"Cannot verify assigned Spoolman spool for {label}; retrying automatically"
            if not spool or spool.get("archived"):
                return f"Assigned Spoolman spool for {label} is unavailable. Assign an active spool"
            checked.add(spool_id)
    return None
