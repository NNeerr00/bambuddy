"""Chamber light blinks while a printer is paused or failed (Mechonix)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.services.chamber_light_signal import ChamberLightSignalService, needs_person


class Farm:
    """printer_manager stand-in: printer id -> state, light, connected."""

    def __init__(self, **printers):
        self.status = {}
        self.clients = {}
        self.connected = {}
        for name, (state, light) in printers.items():
            pid = int(name[1:])
            self.status[pid] = SimpleNamespace(state=state, chamber_light=light)
            self.clients[pid] = MagicMock()
            self.connected[pid] = True

    def get_all_statuses(self):
        return self.status

    def is_connected(self, pid):
        return self.connected[pid]

    def get_client(self, pid):
        return self.clients[pid]

    def calls(self, pid):
        return [(c.args[0], c.kwargs.get("quiet", False)) for c in self.clients[pid].set_chamber_light.call_args_list]


async def ticks(service, farm, n, enabled=True):
    with (
        patch("backend.app.services.printer_manager.printer_manager", farm),
        patch.object(service, "_enabled", new=AsyncMock(return_value=enabled)),
    ):
        for _ in range(n):
            await service.tick()


def test_needs_person_only_when_paused_or_failed():
    for state in ("PAUSE", "pause", "FAILED"):
        assert needs_person(SimpleNamespace(state=state))
    for state in ("RUNNING", "IDLE", "FINISH", "PREPARE", None):
        assert not needs_person(SimpleNamespace(state=state))


async def test_paused_printer_blinks_others_are_left_alone():
    farm = Farm(p1=("PAUSE", True), p2=("RUNNING", True), p3=("FINISH", True))
    service = ChamberLightSignalService()
    await ticks(service, farm, 4)
    assert [on for on, _ in farm.calls(1)] == [False, True, False, True]
    assert all(quiet for _, quiet in farm.calls(1))
    farm.clients[2].set_chamber_light.assert_not_called()
    farm.clients[3].set_chamber_light.assert_not_called()


async def test_light_returns_to_its_previous_state_when_handled():
    farm = Farm(p1=("FAILED", True), p2=("PAUSE", False))
    service = ChamberLightSignalService()
    await ticks(service, farm, 3)
    farm.status[1].state = "IDLE"
    farm.status[2].state = "RUNNING"
    for pid in (1, 2):
        farm.clients[pid].set_chamber_light.reset_mock()
    await ticks(service, farm, 2)
    assert farm.calls(1) == [(True, False)]
    assert farm.calls(2) == [(False, False)]  # it was off before, so it stays off


async def test_disabled_setting_stops_blinking_and_restores():
    farm = Farm(p1=("PAUSE", True))
    service = ChamberLightSignalService()
    await ticks(service, farm, 1)
    farm.clients[1].set_chamber_light.reset_mock()
    await ticks(service, farm, 2, enabled=False)
    assert farm.calls(1) == [(True, False)]


async def test_disconnected_printer_is_restored_once_it_is_back():
    farm = Farm(p1=("PAUSE", True))
    service = ChamberLightSignalService()
    await ticks(service, farm, 1)  # left dark on this tick
    farm.connected[1] = False
    farm.clients[1].set_chamber_light.reset_mock()
    await ticks(service, farm, 3)
    farm.clients[1].set_chamber_light.assert_not_called()
    farm.connected[1] = True
    farm.status[1].state = "IDLE"
    await ticks(service, farm, 1)
    assert farm.calls(1) == [(True, False)]


async def test_shutdown_restores_every_blinking_printer():
    farm = Farm(p1=("PAUSE", True), p2=("FAILED", True))
    service = ChamberLightSignalService()
    await ticks(service, farm, 1)
    with patch("backend.app.services.printer_manager.printer_manager", farm):
        service.stop()
    assert farm.calls(1)[-1] == (True, False)
    assert farm.calls(2)[-1] == (True, False)


def test_setting_is_part_of_the_settings_schema():
    from backend.app.schemas.settings import AppSettings, AppSettingsUpdate

    assert AppSettings.model_fields["chamber_light_signal_enabled"].default is False
    assert AppSettingsUpdate(chamber_light_signal_enabled=True).chamber_light_signal_enabled is True


@pytest.mark.parametrize("quiet", [False, True])
def test_set_chamber_light_quiet_logs_at_debug(quiet):
    from backend.app.services import bambu_mqtt

    client = bambu_mqtt.BambuMQTTClient.__new__(bambu_mqtt.BambuMQTTClient)
    client._client = MagicMock()
    client.state = SimpleNamespace(connected=True)
    client.serial_number = "SN"
    client._sequence_id = 0
    with patch.object(bambu_mqtt, "logger") as log:
        assert client.set_chamber_light(False, quiet=quiet)
    (log.debug if quiet else log.info).assert_called_once()
    (log.info if quiet else log.debug).assert_not_called()
