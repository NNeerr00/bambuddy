"""Tests for the short-lived /cloud/settings cache.

The Bambu Cloud preset list is fetched live on every call, which is the slow
"loading all the filaments" spinner in the Configure AMS Slot dialog. The route
caches the parsed response per-account (keyed by token hash + version) with a
TTL, and invalidates it on any preset mutation / logout. These tests pin that
behaviour at the handler level.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.routes import cloud as c

# A minimal but representative cloud response: one custom + one stock filament.
_CLOUD_DATA = {
    "filament": {
        "private": [{"setting_id": "PFUcustom1", "name": "My Workhorse PLA"}],
        "public": [{"setting_id": "GFSL99", "name": "Generic PLA"}],
    },
    "printer": {"private": [], "public": []},
    "print": {"private": [], "public": []},
}


def _cloud_mock() -> MagicMock:
    m = MagicMock()
    m.is_authenticated = True
    m.get_slicer_settings = AsyncMock(return_value=_CLOUD_DATA)
    m.close = AsyncMock()
    return m


@pytest.mark.asyncio
async def test_cache_hit_skips_cloud_call():
    """A second call within TTL reuses the cached response and does NOT hit
    Bambu Cloud again."""
    c._invalidate_slicer_settings_cache()
    cloud_mock = _cloud_mock()
    with (
        patch.object(c, "build_authenticated_cloud", AsyncMock(return_value=cloud_mock)),
        patch.object(c, "get_stored_token", AsyncMock(return_value=("tok", "e@x", "global"))),
    ):
        r1 = await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)
        r2 = await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)

    cloud_mock.get_slicer_settings.assert_awaited_once()
    # Private (custom) presets are shaped before public (stock) ones.
    assert [f.setting_id for f in r1.filament] == ["PFUcustom1", "GFSL99"]
    assert [f.setting_id for f in r2.filament] == ["PFUcustom1", "GFSL99"]


@pytest.mark.asyncio
async def test_invalidate_forces_refetch():
    """After a preset mutation invalidates the cache, the next call hits the
    cloud again."""
    c._invalidate_slicer_settings_cache()
    cloud_mock = _cloud_mock()
    with (
        patch.object(c, "build_authenticated_cloud", AsyncMock(return_value=cloud_mock)),
        patch.object(c, "get_stored_token", AsyncMock(return_value=("tok", "e@x", "global"))),
    ):
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)
        c._invalidate_slicer_settings_cache()
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)

    assert cloud_mock.get_slicer_settings.await_count == 2


@pytest.mark.asyncio
async def test_cache_keyed_by_token_so_relogin_bypasses():
    """A token change (logout + login into a different account) must not serve
    the previous account's cached preset list."""
    c._invalidate_slicer_settings_cache()
    cloud_mock = _cloud_mock()
    with (
        patch.object(c, "build_authenticated_cloud", AsyncMock(return_value=cloud_mock)),
        patch.object(
            c,
            "get_stored_token",
            AsyncMock(side_effect=[("tok-old", None, "global"), ("tok-new", None, "global")]),
        ),
    ):
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)

    assert cloud_mock.get_slicer_settings.await_count == 2


@pytest.mark.asyncio
async def test_ttl_zero_disables_cache():
    """CLOUD_SETTINGS_CACHE_TTL=0 turns the cache off — every call refetches."""
    c._invalidate_slicer_settings_cache()
    cloud_mock = _cloud_mock()
    with (
        patch.object(c, "_SLICER_SETTINGS_TTL", 0),
        patch.object(c, "build_authenticated_cloud", AsyncMock(return_value=cloud_mock)),
        patch.object(c, "get_stored_token", AsyncMock(return_value=("tok", None, "global"))),
    ):
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)
        await c.get_slicer_settings(version="v1", db=MagicMock(), current_user=None)

    assert cloud_mock.get_slicer_settings.await_count == 2
    assert c._slicer_settings_cache == {}
