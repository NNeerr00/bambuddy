# Mechonix source deployment

Application revision: `0c164af7` on `fix/queue-filament-selection`.
Image: `mechonix-bambuddy:queue-0c164af7`.

This release fixes AMS presence-only updates that left QR assignments blocked
by stale empty-slot status. See `AMS_SLOT_PRESENCE_FIX.md` for the incident
and regression coverage. It retains the previous AMS backup status fixes.

The host's base Compose file continues to own network settings, environment,
restart policy and the existing external data/log volumes. The override here
selects the fork's source build. No startup or post-build source rewriting is
used. `production-static/` is a historical snapshot, not the updated frontend.

Build:

```sh
docker compose -p bambuddy \
  -f /home/ubuntu/services/bambuddy/docker-compose.yml \
  -f /home/ubuntu/bambuddy/deploy/mechonix/compose.production.yaml \
  build bambuddy
```

Test the resulting image with the isolated suite described in `QUEUE_FIX.md`:
set `BAMBUDDY_TEST_IMAGE=mechonix-bambuddy:queue-0c164af7`, use `run --rm
--entrypoint python queue-tests -m pytest -o asyncio_mode=auto`,
then append the listed test paths. The test service has no network or production
volume mounts. This also exercises the rebuilt runtime's dependencies.

Before switching, check for in-flight dispatches and wait for uploads to finish.
Create a consistent SQLite backup using its backup API. Stop Bambuddy gracefully,
then update the host's `compose.override.yaml` from `compose.production.yaml` and
start with the new override:

```sh
docker compose -p bambuddy \
  -f /home/ubuntu/services/bambuddy/docker-compose.yml \
  -f /home/ubuntu/bambuddy/deploy/mechonix/compose.production.yaml \
  up -d --no-build --pull never bambuddy
```

Check `/health`, the served frontend, printer reconnections, queue processing
and startup logs. Compare deployed source hashes with this checkout. Already
running prints execute on the printers during the brief management restart.
Existing manual queue holds are not automatically released.

Rollback of the AMS presence update uses the retained image
`mechonix-bambuddy:queue-f7144f34` with the
previous Compose override, saved under
`/home/ubuntu/diagnostics/bambuddy-slot-presence-20260924/compose.override.before.yaml`.
Stop the new service gracefully and restore that host override before running
Compose with the base file and restored override. Keep the same data volumes;
this change introduces no database schema migration. Do not overwrite current
production data with the backup as part of an ordinary code rollback.
