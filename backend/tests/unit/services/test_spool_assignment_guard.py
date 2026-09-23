from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.models.settings import Settings
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment
from backend.app.services.spool_assignment_guard import spool_assignment_problem


@pytest.mark.asyncio
async def test_only_used_feeds_need_assignments(db_session, printer_factory):
    printer = await printer_factory()
    spool = Spool(material="PETG", rgba="000000FF", label_weight=0)
    db_session.add(spool)
    await db_session.flush()
    db_session.add(SpoolAssignment(printer_id=printer.id, spool_id=spool.id, ams_id=0, tray_id=2))
    await db_session.commit()
    # Unknown weight is a separate concern, not evidence of a missing assignment.
    assert await spool_assignment_problem(db_session, printer.id, {2}) is None
    assert "slot 1" in await spool_assignment_problem(db_session, printer.id, {0, 2})
    assert "external spool 1" in await spool_assignment_problem(db_session, printer.id, {254})
    assert await spool_assignment_problem(db_session, printer.id, set())
    spool.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    assert await spool_assignment_problem(db_session, printer.id, {2})


@pytest.mark.asyncio
async def test_dual_external_and_ht_slot_addresses(db_session, printer_factory):
    printer = await printer_factory()
    for ams, tray in [(255, 1), (128, 0)]:
        spool = Spool(material="PETG", label_weight=1000)
        db_session.add(spool)
        await db_session.flush()
        db_session.add(SpoolAssignment(printer_id=printer.id, spool_id=spool.id, ams_id=ams, tray_id=tray))
    await db_session.commit()
    assert await spool_assignment_problem(db_session, printer.id, {128, 255}) is None
    assert await spool_assignment_problem(db_session, printer.id, {254})


@pytest.mark.asyncio
@pytest.mark.parametrize("remote", [{"id": 17}, None, {"id": 17, "archived": True}, RuntimeError("offline")])
async def test_spoolman_requires_active_verified_assignment(db_session, printer_factory, remote):
    printer = await printer_factory()
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    await db_session.commit()
    assert await spool_assignment_problem(db_session, printer.id, {0})
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=17))
    await db_session.commit()
    client = AsyncMock()
    if isinstance(remote, Exception):
        client.get_spool.side_effect = remote
    else:
        client.get_spool.return_value = remote
    with patch("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client)):
        problem = await spool_assignment_problem(db_session, printer.id, {0})
    assert (problem is None) == (remote == {"id": 17})


@pytest.mark.asyncio
async def test_inactive_inventory_mode_cannot_satisfy_guard(db_session, printer_factory):
    printer = await printer_factory()
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=0, tray_id=0, spoolman_spool_id=17))
    await db_session.commit()
    assert await spool_assignment_problem(db_session, printer.id, {0})
