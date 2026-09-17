"""Merge external-spool MQTT deltas without inventing missing material values."""
from copy import deepcopy

# Explicit empty material is an empty slot, not a partial field update.
IDENTITY_FIELDS = {'tray_type', 'tray_info_idx', 'tray_sub_brands', 'tray_id_name',
                   'tray_color', 'nozzle_temp_min', 'nozzle_temp_max', 'k', 'cali_idx',
                   'tag_uid', 'tray_uuid'}


def merge_external_spools(previous, incoming):
    if incoming is None:
        return deepcopy(previous)
    updates = [incoming] if isinstance(incoming, dict) else incoming
    if not isinstance(updates, list):
        return deepcopy(previous)
    if not updates:
        return []
    old = [previous] if isinstance(previous, dict) else previous or []
    result = [deepcopy(row) for row in old if isinstance(row, dict)]
    for update in updates:
        if not isinstance(update, dict):
            continue
        slot = str(update.get('id', '254'))
        index = next((i for i, row in enumerate(result) if str(row.get('id', '254')) == slot), None)
        row = result[index] if index is not None else {'id': slot}
        # Preserve omitted keys; explicit null/empty values must still take effect.
        empty = any(key in update and update[key] in ('', None) for key in ('tray_type', 'tray_info_idx'))
        changed_tag = any(update.get(key) and row.get(key) and update[key] != row[key]
                          for key in ('tag_uid', 'tray_uuid'))
        if empty or changed_tag:
            row = {key: value for key, value in row.items() if key not in IDENTITY_FIELDS}
        row.update(deepcopy(update))
        if index is None:
            result.append(row)
        else:
            result[index] = row
    return result


def external_k_profile_tray_id(ams_id, tray_id):
    """Translate Bambuddy slot coordinates to the global K-profile tray ID."""
    if ams_id == 255:
        return 254 + tray_id
    if ams_id <= 3:
        return ams_id * 4 + tray_id
    return tray_id


def external_cali_sel_wire_ids(external_slot_count, tray_id):
    """Return (ams_id, tray_id) for extrusion_cali_sel on an external spool.

    Measured on a P1S running firmware 01.09.00.00 with a single external
    slot: the firmware refuses this command when it is addressed with
    ams_id=254 and answers result='fail'. It refuses even cali_idx=-1, the
    profile-less default reset, so the rejection is about the slot address and
    not about the calibration profile. The same payload with ams_id=255 and an
    unchanged tray_id=254 is accepted and the slot then reports the selected
    cali_idx. That matches ams_set_filament_setting, which already addresses
    the external slot as 255 and has always worked.

    The dual-slot (H2D) translation is upstream's and stays untouched — no
    dual-external hardware was available to measure it.
    """
    if external_slot_count > 1:
        return 254 + tray_id, 254 + tray_id
    return 255, 254


def external_slot_matches(trays, tray_id, tray_info_idx, tray_type, tray_color):
    """Return whether Ext-L already has the requested filament context."""
    rows = [trays] if isinstance(trays, dict) else trays or []
    expected_id = 254 + tray_id
    expected_color = (tray_color or '').upper()
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            same_slot = int(row.get('id', 254)) == expected_id
        except (TypeError, ValueError):
            same_slot = False
        if (
            same_slot
            and row.get('tray_info_idx') == tray_info_idx
            and (row.get('tray_type') or '').upper() == (tray_type or '').upper()
            and (row.get('tray_color') or '').upper() == expected_color
        ):
            return True
    return False
