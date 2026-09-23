# Queue filament selection

These changes live in the fork's Python and TypeScript source. Application
startup does not rewrite code or frontend bundles.

## Behavior

For automatically assigned jobs, the scheduler now checks each eligible printer
with that printer's current slot mapping and spool inventory before assigning
the job. A printer with insufficient material is skipped for this job. Another
printer, or a smaller job that fits on the remaining spool, can still run.
If none qualifies, the job remains pending and is checked again automatically.
Fixed-printer jobs also retry automatically after a filament shortage.

Automatic selection evaluates all eligible printers for the candidate model,
then prefers the smallest estimated filament remainder after the print. The
rule uses the job's actual sliced consumption and inventory weights, without
hard-coded gram thresholds. Known sufficient inventory ranks ahead of unknown
weights; equal estimates retain the previous matcher order. Material, required
color, preference-color quality, plate-clear and other eligibility checks still
come first. An explicitly selected fixed printer remains fixed.

Only feeds used by this job count towards its estimate. Repeated uses of one
physical feed are aggregated; with Filament Backup enabled, each relevant
material/extruder pool is counted once. Both internal inventory and Spoolman use
the existing inventory resolution. Estimates reflect those recorded weights
and the slice, rather than a physical weighing of the spool. Unrelated AMS
spools do not inflate the ranking.

Physical AMS slot IDs are recomputed for automatic assignments. Editing a job
to move it to another fixed printer clears the old mapping unless the edit
explicitly supplies a replacement. Explicit mappings on the same fixed printer
are preserved, but checked against loaded material, required color overrides
and nozzle routing. The scheduler rechecks the selected feed and available
weight after file upload, before sending the print command.

External-spool mapping uses Bambuddy's existing external-feed encoding. It no
longer inherits AMS slot 4 from another printer. On the investigated printer
4-6, logs showed an AMS-slot-4 start command while live data listed an external
spool and no AMS trays. No separate unload command was found in those logs.
This corrects the proven start-command problem; a physical unload still needs
observation on the printer if it recurs. Slicer start/end G-code is unchanged.

Explicit manual-start jobs, including jobs already marked manual by the old
shortage behavior, remain manual. There is no database migration that releases
them all. Review those existing jobs before releasing them in the UI.
"Print Anyway" still bypasses the weight warning, while missing or incompatible
physical feeds remain blocked. Plate-clear, schedule, interlock, ownership and
printer compatibility checks continue to apply.

The existing Restock `mechonix_print` link is also implemented in TypeScript,
so source builds retain that feature. It opens the print dialog for the exact
linked file and requires queue-create permission; it does not submit a print.

## Build and validation

Build the application directly from the repository root using the normal
Dockerfile:

```sh
docker build -t mechonix-bambuddy:queue-filament-selection .
```

The historical production snapshot has its own Dockerfile and tag, documented
in `PRODUCTION.md`. Building either image does not deploy it.

Run the regression suite in an isolated container with no printer network or
production data mounts:

```sh
docker compose -f deploy/mechonix/compose.test.yaml build
docker compose -f deploy/mechonix/compose.test.yaml run --rm queue-tests \
  -q -p no:cacheprovider \
  backend/tests/unit/test_scheduler_filament_selection_mechonix.py \
  backend/tests/unit/services/test_filament_leftover.py \
  backend/tests/unit/test_scheduler_filament_deficit.py \
  backend/tests/unit/test_scheduler_ams_mapping.py \
  backend/tests/unit/test_scheduler_clear_plate.py \
  backend/tests/unit/test_ams_mapping_unresolved_2589.py \
  backend/tests/unit/services/test_filament_deficit.py \
  backend/tests/unit/test_scheduler_cross_model_variants.py \
  backend/tests/unit/test_scheduler_class_target_smart_plug_2786.py
```

Validation on 2026-09-17: 247 backend tests passed, including 26 farm-selection
regressions using SQLite, sliced-file metadata and inventory. MQTT payload
tests cover both AMS and external-feed commands. Ten Restock-link frontend
tests passed; ESLint and the full TypeScript/Vite build with Safari baseline
validation passed. No print was sent and the production service was not
restarted during these checks. Hardware confirmation remains a rollout check.

The subsequent leftover-priority update passed 274 tests in its production
image on 2026-09-17, including variable-weight selection, AMS/external feeds,
unknown weights, repeated feed usage and backup-pool accounting. Deployment
health and all 18 active prints were verified after the restart. See
`deployment-20260917-bestfit.json` for the deployed image and source hashes.

## Mandatory spool assignment (2026-09-23)

Every feed actually used by a print must have an active inventory spool assigned
on that printer. A physically detected filament without an inventory binding is
not eligible. The scheduler checks candidate printers, fixed-printer jobs and
manual-start jobs, and checks again after upload before sending the print command.
Missing assignments leave the job pending with an explicit slot-specific reason;
a later assignment lets the scheduler retry automatically. It does not turn the
job into a manual hold. Neither Print Anyway nor disabled weight warnings bypass
this requirement. Unused slots and unused mapping entries remain irrelevant.

The built-in inventory rejects missing/deleted/archived spools. In Spoolman mode
only Spoolman assignments count, and the referenced spool must be reachable and
not archived. Unknown remaining weight remains a separate weight-check concern.
No schema change or changes to existing assignments are required. Commands issued
directly from the printer screen or another application are outside Bambuddy's
dispatch path.
