# Mechonix source deployment

Application revision: `afc41c79` on `fix/queue-filament-selection`.
Image: `mechonix-bambuddy:queue-afc41c79`.

This release prevents a printer freed mid-scan from being assigned to a later
queue item after the head was already checked. It layers the versioned scheduler
onto the deployed `queue-0c164af7` image, retaining its frontend, dependencies
and AMS fixes. 145 directly relevant scheduler tests passed, including the
four PLA/PETG black/white reproductions and automatic shortage retries.
The broader scheduler baseline has 33 existing failures also reproduced without
this patch; the directly relevant suite above is green.

Verified live on 2026-10-15: container and `/health` healthy, the same 16 running
queue items retained, 258 pending items synchronized with Restock, shift-fit
enabled, and no pending shortage jobs incorrectly marked manual. Deployed
scheduler SHA-256: `5e9ed1fc29403409826eaa22257c22903ded8068077f71fab0b7f55cd61b11d3`.

The host's base Compose file continues to own network settings, environment,
restart policy and the existing external data/log volumes. The override here
selects `Dockerfile.scheduler-priority`. No startup or post-build source rewriting is
used. `production-static/` is a historical snapshot, not the updated frontend.

Build:

```sh
docker compose -p bambuddy \
  -f /home/ubuntu/services/bambuddy/docker-compose.yml \
  -f /home/ubuntu/bambuddy/deploy/mechonix/compose.production.yaml \
  build bambuddy
```

Test the resulting image with the isolated suite described in `QUEUE_FIX.md`:
set `BAMBUDDY_TEST_IMAGE=mechonix-bambuddy:queue-afc41c79`, use `run --rm
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

Rollback of the scheduler update uses the retained image
`mechonix-bambuddy:queue-0c164af7` with the
previous Compose override, saved under
`/home/ubuntu/diagnostics/bambuddy-priority-20261015/compose.override.before.yaml`.
Stop the new service gracefully and restore that host override before running
Compose with the base file and restored override. Keep the same data volumes;
this change introduces no database schema migration. Do not overwrite current
production data with the backup as part of an ordinary code rollback.
