# AMS backup status and queue stalls — 2026-09-23

## Incident on printer 3-4

Queue job 1225 required 248.3 g of PLA. The two assigned, compatible spools
contained 212.81 g and 239.68 g (452.49 g together). The plate-clear flag was
already cleared and there were no active HMS errors. The previous FAILED print
state was not the blocker: the scheduler allows a new job after plate clearance.

Bambuddy's cached `ams_filament_backup` was unknown, so the weight check correctly
refused to assume automatic spool switching and only counted the selected spool.
Temporarily enabling backup through the API allowed dispatch, but did not repair
the status parser. At 18:36:08.804 UTC the print command was sent; at
18:36:08.833 UTC the old application logged AMS Filament Backup OFF again.

A read-only MQTT subscription plus a requested full status refresh captured this
subset from the running P1S (credentials and other telemetry omitted):

```json
{"command":"push_status","home_flag":6505791,"gcode_state":"RUNNING"}
```

There was no `cfg` field. Bit 10 of `home_flag` is set: the printer actually
reports backup ON. The old parser only understood hexadecimal `cfg` bit 18.
See BambuStudio's `parse_home_flag` and `SetAutoRefillEnabled` in
[DeviceManager.cpp](https://github.com/bambulab/BambuStudio/blob/master/src/slic3r/GUI/DeviceManager.cpp).

The old parser also accepted `cfg` from every `print` message, including command
replies. `start_print` sends `project_file` with `cfg="0"`; a reply echoing that
field was therefore interpreted as backup OFF. The timing above is consistent
with this second bug; the historical raw reply was not captured. Regression
tests reproduce this path without issuing a print command.

## Fix

- Only status frames (`push_status`, or legacy frames without a command) can
  update the cached backup setting. Command replies cannot change it.
- Read modern hexadecimal `cfg` bit 18, falling back to integer `home_flag`
  bit 10 when `cfg` is absent or invalid.
- Missing fields in incremental updates preserve the last known value.
- Fresh printer telemetry restores the setting after a Bambuddy restart.
- Explicit OFF remains authoritative after the existing short toggle hold.
  Unknown or OFF still prevents pooled weight accounting. The fix does not
  enable auto-switch on printers where the operator has disabled it.

Assigned-spool requirements, compatible-material/color checks, plate clearance
and other queue eligibility checks continue to apply. No database migration is
needed, and already running jobs execute on the printers during deployment.

## Regression coverage

`test_bambu_mqtt_backup_status.py` covers the captured P1 status, modern status,
malformed/missing fields, command replies, cold starts and client recreation.
The existing toggle-hold tests now exercise both protocol formats.
`test_filament_deficit.py` exercises MQTT ingestion through the real deficit
calculation with the incident's weights: ON permits pooling even after a print
reply; OFF and unknown still report the selected spool's deficit.

Live rollout results and the application revision are recorded in
`deployment-20260923-ams-status.json` and `DEPLOYMENT.md`.
