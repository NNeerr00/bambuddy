"""Exercise scheduler selection with real SQLite, 3MF parsing and inventory.

The isolated source-test container has no production volumes or network.
The upload launcher is mocked; printer commands can never leave this container.
"""

import json
import zipfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.models  # noqa: F401
from backend.app.core.database import Base
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem, PrintQueueVariant
from backend.app.models.printer import Printer
from backend.app.models.spool import Spool
from backend.app.models.spool_assignment import SpoolAssignment
from backend.app.services.print_scheduler import PrintScheduler


def status(tray=254, material="PETG", color="FFFFFFFF", state="IDLE"):
    slot = {"id": tray if tray >= 254 else tray % 4, "tray_type": material, "tray_color": color}
    raw = (
        {"vt_tray": [slot], "ams": []}
        if tray >= 254
        else {
            "ams": [{"id": tray // 4, "tray": [slot]}],
            "vt_tray": [],
        }
    )
    return SimpleNamespace(
        raw_data=raw,
        state=state,
        ams_filament_backup=False,
        nozzles=[],
        fila_switch=None,
        ams_extruder_map={},
        connected=True,
    )


@pytest.fixture
async def farm(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    path = tmp_path / "part.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Metadata/slice_info.config",
            '<config><filament id="1" type="PETG" color="#FFFFFF" used_g="400"/></config>',
        )
    async with sessions() as db:
        db.add(
            LibraryFile(
                id=1,
                filename="part.3mf",
                file_path=str(path),
                file_type="3mf",
                file_size=path.stat().st_size,
                file_metadata={"sliced_for_model": "P1S"},
            )
        )
        for pid in range(1, 6):
            db.add(
                Printer(
                    id=pid,
                    name=f"Printer-{pid}",
                    serial_number=f"TEST{pid}",
                    ip_address="127.0.0.1",
                    access_code="test",
                    model="P1S",
                    is_active=True,
                )
            )
            db.add(Spool(id=pid, material="PETG", rgba="FFFFFFFF", label_weight=1000, weight_used=0))
            db.add(SpoolAssignment(spool_id=pid, printer_id=pid, ams_id=255, tray_id=0))
        await db.commit()
    yield SimpleNamespace(sessions=sessions, states={i: status() for i in range(1, 6)}, held=set())
    await engine.dispose()


async def add_item(farm, **overrides):
    values = {
        "target_model": "P1S",
        "library_file_id": 1,
        "position": 1,
        "required_filament_types": '["PETG"]',
        "status": "pending",
        "manual_start": False,
        "filament_overrides": '[{"slot_id":1,"type":"PETG","color":"#FFFFFF","force_color_match":true}]',
    }
    values.update(overrides)
    async with farm.sessions() as db:
        item = PrintQueueItem(**values)
        db.add(item)
        await db.commit()
        return item.id


async def items(farm):
    async with farm.sessions() as db:
        return list((await db.scalars(select(PrintQueueItem).order_by(PrintQueueItem.id))).all())


async def remaining(farm, pid, grams):
    async with farm.sessions() as db:
        spool = await db.get(Spool, pid)
        spool.weight_used = 1000 - grams
        await db.commit()


async def change_feed(farm, pid, tray):
    farm.states[pid] = status(tray)
    async with farm.sessions() as db:
        assignment = (await db.scalars(select(SpoolAssignment).where(SpoolAssignment.printer_id == pid))).one()
        assignment.ams_id = 255 if tray >= 254 else tray // 4
        assignment.tray_id = tray - 254 if tray >= 254 else tray % 4
        await db.commit()


def mocked_farm(farm, scheduler):
    stack = ExitStack()
    pm = "backend.app.services.print_scheduler.printer_manager."
    for target, value in [
        ("backend.app.services.print_scheduler.async_session", farm.sessions),
        ("backend.app.core.database.async_session", farm.sessions),
        (pm + "get_status", MagicMock(side_effect=farm.states.get)),
        (pm + "is_connected", MagicMock(side_effect=lambda pid: pid in farm.states)),
        (pm + "is_awaiting_plate_clear", MagicMock(side_effect=lambda pid: pid in farm.held)),
        (
            "backend.app.services.print_scheduler.ha_sensor_manager.blocked_printers",
            AsyncMock(return_value={}),
        ),
        (
            "backend.app.services.print_scheduler.notification_service.on_queue_job_waiting",
            AsyncMock(),
        ),
        (
            "backend.app.services.print_scheduler.notification_service.on_queue_job_assigned",
            AsyncMock(),
        ),
    ]:
        stack.enter_context(patch(target, value))
    for name in ["_check_auto_drying", "_check_scheduled_dryings", "_apply_keep_warm"]:
        stack.enter_context(patch.object(scheduler, name, AsyncMock()))
    stack.enter_context(
        patch.object(
            scheduler,
            "_get_bool_setting",
            AsyncMock(side_effect=lambda db, key, default=False: True if key == "require_plate_clear" else default),
        )
    )
    return stack


async def run(farm, scheduler=None):
    scheduler = scheduler or PrintScheduler()
    launched = MagicMock()
    with mocked_farm(farm, scheduler), patch.object(scheduler, "_launch_uploads", launched):
        await scheduler.check_queue()
    return launched


async def test_five_printers_first_short_does_not_poison_batch(farm):
    await remaining(farm, 1, 10)
    for _ in range(7):
        await add_item(farm)
    launch = await run(farm)
    rows = await items(farm)
    assert launch.call_count == 1
    assert len(launch.call_args.args[0]) == 4
    assert {r.printer_id for r in rows if r.printer_id} == {2, 3, 4, 5}
    assert all(not r.manual_start for r in rows)
    assert all(r.ams_mapping == "[254]" for r in rows if r.printer_id)
    assert all(r.printer_id is None for r in rows[4:])


async def test_all_short_retries_after_spool_change(farm):
    for pid in farm.states:
        await remaining(farm, pid, 10)
    await add_item(farm)
    assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.filament_short and not row.manual_start and row.printer_id is None
    assert "insufficient filament" in row.waiting_reason.lower()
    await remaining(farm, 3, 800)
    assert (await run(farm)).called
    row = (await items(farm))[0]
    assert row.printer_id == 3 and not row.filament_short and row.waiting_reason is None


@pytest.mark.parametrize("tray", [0, 1, 2, 254])
async def test_model_reassignment_discards_slot_four(farm, tray):
    farm.states = {1: status(tray)}
    await change_feed(farm, 1, tray)
    await add_item(farm, printer_id=1, ams_mapping="[3]")
    assert (await run(farm)).called
    row = (await items(farm))[0]
    assert json.loads(row.ams_mapping) == [tray]


async def test_fixed_manual_mapping_is_preserved(farm):
    await change_feed(farm, 1, 3)
    farm.states[1].raw_data["ams"][0]["tray"].append({"id": 0, "tray_type": "PETG", "tray_color": "FFFFFFFF"})
    await add_item(farm, target_model=None, printer_id=1, ams_mapping="[3]")
    assert (await run(farm)).called
    assert (await items(farm))[0].ams_mapping == "[3]"


@pytest.mark.parametrize("manual,short", [(True, False), (True, True)])
async def test_user_and_legacy_manual_stops_stay_stopped(farm, manual, short):
    await add_item(farm, manual_start=manual, filament_short=short, printer_id=1, ams_mapping="[3]")
    assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.manual_start and row.printer_id == 1 and row.ams_mapping == "[3]"


async def test_plate_clear_and_future_schedule_still_gate(farm):
    farm.held = set(farm.states)
    await add_item(farm)
    await add_item(farm, scheduled_time=datetime.now(timezone.utc) + timedelta(days=1))
    assert not (await run(farm)).called
    farm.held.remove(2)
    launch = await run(farm)
    assert launch.call_args.args[0] == [1]
    assert (await items(farm))[1].printer_id is None


async def test_fixed_printer_shortage_does_not_set_manual_stop(farm):
    await remaining(farm, 1, 10)
    await add_item(farm, target_model=None, printer_id=1, ams_mapping="[254]")
    assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.filament_short and not row.manual_start and row.printer_id == 1
    await remaining(farm, 1, 900)
    assert (await run(farm)).called


@pytest.mark.parametrize("state", [status(), status(3, "PLA"), status(3, color="000000FF")])
async def test_invalid_manual_mapping_waits_instead_of_sending(farm, state):
    farm.states[1] = state
    await add_item(farm, target_model=None, printer_id=1, ams_mapping="[3]")
    assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.status == "pending" and row.waiting_reason and not row.manual_start


async def test_post_upload_validation_rejects_removed_feed(farm):
    iid = await add_item(farm, printer_id=1, ams_mapping="[3]")
    scheduler = PrintScheduler()
    with mocked_farm(farm, scheduler):
        async with farm.sessions() as db:
            row = await db.get(PrintQueueItem, iid)
            assert await scheduler._recheck_filament_before_send(db, row)
            assert row.printer_id is None and row.ams_mapping is None and not row.manual_start


async def test_probe_does_not_commit_rejected_assignment(farm):
    await remaining(farm, 1, 1)
    farm.states = {1: status()}
    await add_item(farm)
    assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.archive_id is None and row.printer_id is None and row.ams_mapping is None


async def test_variants_check_the_selected_file_and_mapping(farm):
    iid = await add_item(farm)
    async with farm.sessions() as db:
        db.add(
            PrintQueueVariant(
                queue_item_id=iid,
                library_file_id=1,
                target_model="P1S",
                position=0,
                ams_mapping="[3]",
                required_filament_types='["PETG"]',
            )
        )
        await db.commit()
    await remaining(farm, 1, 1)
    assert (await run(farm)).called
    row = (await items(farm))[0]
    assert row.printer_id == 2 and row.ams_mapping == "[254]"


async def test_print_anyway_does_not_bypass_invalid_slot(farm):
    await add_item(farm, target_model=None, printer_id=1, ams_mapping="[3]", skip_filament_check=True)
    assert not (await run(farm)).called


async def test_print_anyway_still_bypasses_weight(farm):
    farm.states = {1: status()}
    await remaining(farm, 1, 1)
    await add_item(farm, skip_filament_check=True)
    assert (await run(farm)).called
    assert (await items(farm))[0].printer_id == 1


@pytest.mark.parametrize("explicit_mapping", [None, [1]])
async def test_editing_fixed_printer_discards_only_inherited_mapping(farm, explicit_mapping):
    from backend.app.api.routes.print_queue import update_queue_item
    from backend.app.schemas.print_queue import PrintQueueItemUpdate

    iid = await add_item(farm, target_model=None, printer_id=1, ams_mapping="[3]")
    values = {"printer_id": 2}
    if explicit_mapping is not None:
        values["ams_mapping"] = explicit_mapping
    with (
        patch("backend.app.api.routes.print_queue._trusted_item_estimated_cost", AsyncMock(return_value=None)),
        patch("backend.app.api.routes.print_queue.validate_print_budget", AsyncMock()),
    ):
        async with farm.sessions() as db:
            await update_queue_item(iid, PrintQueueItemUpdate(**values), db, (None, True))
    row = (await items(farm))[0]
    assert row.printer_id == 2
    assert row.ams_mapping == (json.dumps(explicit_mapping) if explicit_mapping else None)


async def test_editing_to_model_mode_clears_physical_mapping(farm):
    from backend.app.api.routes.print_queue import update_queue_item
    from backend.app.schemas.print_queue import PrintQueueItemUpdate

    iid = await add_item(farm, target_model=None, printer_id=1, ams_mapping="[3]")
    with (
        patch("backend.app.api.routes.print_queue._trusted_item_estimated_cost", AsyncMock(return_value=None)),
        patch("backend.app.api.routes.print_queue.validate_print_budget", AsyncMock()),
    ):
        async with farm.sessions() as db:
            await update_queue_item(
                iid, PrintQueueItemUpdate(printer_id=None, target_model="P1S", ams_mapping=[3]), db, (None, True)
            )
    row = (await items(farm))[0]
    assert row.printer_id is None and row.ams_mapping is None


async def test_post_upload_deficit_leaves_job_retryable(farm):
    iid = await add_item(farm, printer_id=1, ams_mapping="[254]")
    await remaining(farm, 1, 5)
    scheduler = PrintScheduler()
    with mocked_farm(farm, scheduler):
        async with farm.sessions() as db:
            row = await db.get(PrintQueueItem, iid)
            assert await scheduler._recheck_filament_before_send(db, row)
    row = (await items(farm))[0]
    assert row.status == "pending" and row.filament_short and not row.manual_start
    assert row.printer_id is None and row.ams_mapping is None


async def test_selection_error_defers_without_manual_stop(farm):
    await add_item(farm)
    with patch(
        "backend.app.services.print_scheduler.compute_deficit_for_queue_item",
        AsyncMock(side_effect=RuntimeError("unavailable")),
    ):
        assert not (await run(farm)).called
    row = (await items(farm))[0]
    assert row.printer_id is None and not row.manual_start
    assert "check unavailable" in row.waiting_reason


@pytest.mark.parametrize("tray,wire,enabled", [(254, -1, False), (0, 0, True)])
async def test_selected_feed_produces_correct_p1s_wire_command(farm, tray, wire, enabled):
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    farm.states = {1: status(tray)}
    await change_feed(farm, 1, tray)
    await add_item(farm, ams_mapping="[3]")
    assert (await run(farm)).called
    row = (await items(farm))[0]
    client = BambuMQTTClient(ip_address="127.0.0.1", serial_number="TEST", access_code="test")
    client.model = "P1S"
    client._client = MagicMock()
    client.state.connected = True
    assert client.start_print("part.3mf", ams_mapping=json.loads(row.ams_mapping), use_ams=True)
    command = json.loads(client._client.publish.call_args.args[1])["print"]
    assert command["ams_mapping"] == [wire]
    assert command["use_ams"] is enabled
    assert command["ams_mapping2"] == [{"ams_id": 255 if tray == 254 else 0, "slot_id": 0}]


async def set_job_weight(farm, grams):
    async with farm.sessions() as db:
        library = await db.get(LibraryFile, 1)
        with zipfile.ZipFile(library.file_path, "w") as archive:
            archive.writestr(
                "Metadata/slice_info.config",
                f'<config><filament id="1" type="PETG" color="#FFFFFF" used_g="{grams}"/></config>',
            )


@pytest.mark.parametrize("required,smaller,larger", [(200, 250, 500), (735, 800, 1000), (42.5, 42.5, 85)])
@pytest.mark.parametrize("tray", [0, 3, 254])
async def test_automatic_selection_prefers_smallest_sufficient_spool(farm, required, smaller, larger, tray):
    farm.states = {1: status(), 4: status()}
    for pid in farm.states:
        await change_feed(farm, pid, tray)
    await set_job_weight(farm, required)
    await remaining(farm, 1, larger)
    await remaining(farm, 4, smaller)
    await add_item(farm)
    assert (await run(farm)).called
    row = (await items(farm))[0]
    assert row.printer_id == 4
    assert json.loads(row.ams_mapping) == [tray]


@pytest.mark.parametrize("skip_filament_check", [False, True])
async def test_short_spool_is_not_preferred_to_sufficient_one(farm, skip_filament_check):
    farm.states = {1: status(), 4: status()}
    await remaining(farm, 1, 399)
    await remaining(farm, 4, 450)
    await add_item(farm, skip_filament_check=skip_filament_check)
    await run(farm)
    assert (await items(farm))[0].printer_id == 4


async def test_unknown_weight_is_not_treated_as_empty(farm):
    farm.states = {1: status(), 4: status()}
    async with farm.sessions() as db:
        (await db.get(Spool, 1)).label_weight = 0
        await db.commit()
    await remaining(farm, 4, 450)
    await add_item(farm)
    await run(farm)
    assert (await items(farm))[0].printer_id == 4


async def test_best_fit_keeps_plate_gate_and_fixed_printer_choice(farm):
    farm.states = {1: status(), 4: status()}
    await remaining(farm, 1, 900)
    await remaining(farm, 4, 450)
    farm.held.add(4)
    await add_item(farm)
    await run(farm)
    assert (await items(farm))[0].printer_id == 1
    farm.held.clear()
    async with farm.sessions() as db:
        await db.delete(await db.get(PrintQueueItem, 1))
        await db.commit()
    await add_item(farm, target_model=None, printer_id=1, ams_mapping="[254]")
    await run(farm)
    assert (await items(farm))[0].printer_id == 1


async def test_batch_consumes_smallest_suitable_printers_first(farm):
    farm.states = {1: status(), 3: status(), 4: status()}
    for pid, grams in [(1, 900), (3, 650), (4, 450)]:
        await remaining(farm, pid, grams)
    for _ in range(3):
        await add_item(farm)
    await run(farm)
    assert [row.printer_id for row in await items(farm)] == [4, 3, 1]
