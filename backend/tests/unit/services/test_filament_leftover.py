"""Waste preference uses only the feeds needed by this particular print."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app.services.filament_deficit import SlotMaterial, remaining_filament_after_print


def material(tray, grams, key="PETG-white", extruder=0):
    return SlotMaterial(0, tray, tray, key, grams, extruder)


@pytest.mark.parametrize(
    "weights,mapping,materials,backup,expected",
    [
        ([200], [0], [material(0, 250), material(1, 1000)], False, 50),
        ([120, 100], [0, 0], [material(0, 250)], False, 30),
        ([120, 100], [0, 1], [material(0, 150), material(1, 150)], False, 80),
        ([120, 100], [0, 1], [material(0, 150)], False, None),
        ([120, 100], [0, 0], [material(0, 200)], False, None),
        ([200], [0], [material(0, 50), material(1, 200)], True, 50),
        ([120, 100], [0, 1], [material(0, 50), material(1, 200)], True, 30),
        ([200], [0], [material(0, 50), material(1, 200, extruder=1)], True, None),
        ([200], [0], [material(0, 50), material(1, 200, key="PLA-white")], True, None),
        ([200], [0], [material(0, float("nan"))], False, None),
        ([200], [0], [material(0, float("inf"))], False, None),
        ([None], [0], [material(0, 250)], False, None),
        ([0], [0], [material(0, 250)], False, None),
    ],
)
async def test_leftovers(tmp_path, weights, mapping, materials, backup, expected):
    source = tmp_path / "part.3mf"
    source.touch()
    item = SimpleNamespace(printer_id=1, plate_id=1, ams_mapping=json.dumps(mapping))
    requirements = [{"slot_id": i + 1, "used_grams": grams} for i, grams in enumerate(weights)]
    prefix = "backend.app.services.filament_deficit."
    with (
        patch(prefix + "_resolve_source_3mf", return_value=source),
        patch(prefix + "extract_filament_requirements", return_value=requirements),
        patch(prefix + "build_slot_materials", AsyncMock(return_value=materials)),
        patch(prefix + "_get_printer_backup_context", AsyncMock(return_value=(backup, {}, False))),
    ):
        assert await remaining_filament_after_print(AsyncMock(), item) == expected
