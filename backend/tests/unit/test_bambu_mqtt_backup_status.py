"""Auto-refill survives P1 status initialization and print-command replies."""

import pytest

from backend.app.services.bambu_mqtt import BambuMQTTClient, parse_ams_filament_backup_from_status


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        # Live P1S 3-4 capture on 2026-09-23: no cfg field.
        ({"command": "push_status", "home_flag": 6505791}, True),
        ({"home_flag": 6505791 & ~(1 << 10)}, False),
        ({"home_flag": 1 << 10}, True),
        ({"home_flag": 0}, False),
        ({"home_flag": -1}, True),  # signed firmware bitfields
        ({"cfg": "40000", "home_flag": 0}, True),
        ({"cfg": "0", "home_flag": 1024}, False),
        ({"cfg": "malformed", "home_flag": 1024}, True),
        ({"cfg": "40000"}, True),
        ({"command": "project_file", "cfg": "0"}, None),
        ({"command": "project_file", "cfg": "40000", "home_flag": 1024}, None),
        ({"command": "print_option", "home_flag": 0}, None),
        ({"command": "push_status", "mc_percent": 10}, None),
        ({"home_flag": "1024"}, None),
        ({"home_flag": True}, None),
        ({"home_flag": None}, None),
        ({}, None),
    ],
)
def test_status_protocols(status, expected):
    assert parse_ams_filament_backup_from_status(status) is expected


@pytest.fixture
def client():
    return BambuMQTTClient(ip_address="192.0.2.1", serial_number="TEST", access_code="test")


def test_cold_start_then_print_ack_and_incremental_status(client):
    assert client.state.ams_filament_backup is None
    client._process_message({"print": {"command": "push_status", "home_flag": 6505791}})
    assert client.state.ams_filament_backup is True

    client._process_message({"print": {"command": "project_file", "cfg": "0", "result": "success"}})
    client._process_message({"print": {"command": "push_status", "mc_percent": 1}})
    assert client.state.ams_filament_backup is True

    # Real OFF telemetry remains authoritative, including after an ACK.
    client._process_message({"print": {"command": "push_status", "home_flag": 6505791 & ~(1 << 10)}})
    assert client.state.ams_filament_backup is False


def test_ack_does_not_initialize_unknown_backup(client):
    client._process_message({"print": {"command": "project_file", "cfg": "0", "result": "success"}})
    assert client.state.ams_filament_backup is None


def test_recreated_client_restores_backup_from_printer_without_toggle(client):
    client._process_message({"print": {"command": "push_status", "home_flag": 6505791}})
    restarted = BambuMQTTClient(ip_address="192.0.2.1", serial_number="TEST", access_code="test")
    assert restarted.state.ams_filament_backup is None
    restarted._process_message({"print": {"command": "push_status", "home_flag": 6505791}})
    assert restarted.state.ams_filament_backup is True
