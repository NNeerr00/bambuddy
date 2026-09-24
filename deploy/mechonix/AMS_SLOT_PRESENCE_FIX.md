# AMS slot presence / QR assignment fix — 2026-09-24

## Incident

At 10:47:01 UTC the QR tool queued spool F000067 (inventory id 59, PLA
white, approximately 234 g remaining) for printer 3-5 (id 11), physical
AMS slot 1 (API address AMS 0 / tray 0). Job
`070ff842-1c10-44dd-8cc6-abe64d03493e` repeatedly returned `slot_empty`.

During investigation the printer was connected, in FINISH, with no HMS
errors. Bambuddy exposed tray state 3 and old PETG metadata but
`exists=false`. The QR tool correctly refused assignment on that signal.
A full status refresh changed `exists` to true. The existing background
job then confirmed on attempt 80, applied PLA white / GFL99 / calibration
730 (K 0.016), and assigned spool 59. No rescan, manual database reassignment,
plate-clear command or print start was needed.

## Cause and correction

`BambuMQTTClient._handle_ams_data` returned early for partial AMS messages
without an `ams` tray list, even if `tray_exist_bits` was present. Tray
metadata and physical presence can arrive separately. Updated tray state
and material therefore coexisted with a cached `exists=false` until a
full status response. Presence-only removal messages were also ignored.

Process presence-only messages through the existing merge and empty-slot
cleanup path using the cached trays. Keep the early return for unrelated
status-only messages. Also accept integer zero in the shared presence
helper, consistently with its documented integer support.

The shutdown guard (zero bits with power off) remains intact. A presence
bit does not override a firmware state such as 10 (not fed). The QR tool's
physical-slot checks and spool-assignment requirements remain enforced.
No schema changes or physical printer commands are part of this fix.

The original MQTT insertion frames were not captured. The observed stale
API state and successful refresh establish the immediate cause; regression
tests reproduce the parser defect that can produce this exact stale state.

## Verification

- Six new cases cover insertion with either arrival order, removal with
  string/integer zero, shutdown preservation and not-fed state preservation.
- MQTT service and Mechonix queue selection suites: 518 passed in an
  isolated container with the changed backend mounted read-only.
- Before deployment, run the regression cases against the old parser and
  the rebuilt production image; record outcomes in the deployment manifest.
- Verify health, printer reconnections and spool 59 at printer 11 / AMS 0 /
  tray 0 after the management-service restart.

The incident was recovered through `POST /printers/11/refresh-status` using
the authenticated internal integration. This requests telemetry only.
Use a full status refresh when diagnosing a contradictory cached slot state;
do not force an assignment past an actually empty or unfed slot.
