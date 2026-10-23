# Mechonix source deployment

Application revision: `8b0e8ac5` on `feat/obico-alert-photo`.
Image: `mechonix-bambuddy:obico-telegram-8b0e8ac5` (`Dockerfile.obico-telegram`).

Telegram AI failure alerts carry "⏸ Pausieren" and "⏹ Abbrechen" buttons.
`telegram_actions.py` long-polls `getUpdates` (callback queries only) for each
enabled Telegram provider with AI alerts; only presses from that provider's chat
count, and only while the printer still runs the alerted job (fingerprint in the
button). Stop asks no question (owner's choice) and marks the printer
user-stopped. Do not attach another getUpdates consumer or a webhook to the same
bot token: Telegram allows one, and the buttons would silently stop working.
Layers `telegram_actions.py`, `notification_service.py` and `main.py` onto
`obico-photo-1a4c4e72`. 2802 service tests passed in the built image.

Verified live on 2026-10-23: `/health` OK, 36/36 printers reconnected, queue
unchanged, deployed sources identical to this checkout, test alert with buttons
delivered. Rollback: restore
`/home/ubuntu/diagnostics/bambuddy-telegram-knoepfe-2026-10-23/compose.override.before.yaml`
(image `mechonix-bambuddy:obico-photo-1a4c4e72`).

## Previous release: obico-photo

Application revision: `1a4c4e72` on `feat/obico-alert-photo`.
Image: `mechonix-bambuddy:obico-photo-1a4c4e72` (`Dockerfile.obico-photo`).

This release attaches the camera frame a failure was detected on to the Obico
"Possible Print Failure Detected" alert, so Telegram and Discord show the
picture. It layers only `obico_detection.py` and `obico_actions.py` onto the
deployed `queue-afc41c79` image; scheduler, frontend and dependencies are
unchanged. Tested inside the built image without network: 178 Obico and
notification tests passed; the five new photo checks fail on `queue-afc41c79`.

Verified live on 2026-10-23: `/health` and frontend OK, 36/36 printers
reconnected, queue unchanged (35 printing, 239 pending), deployed Obico and
scheduler sources identical to this checkout. A test alert with a photo reached
Telegram and Discord. Rollback: restore
`/home/ubuntu/diagnostics/bambuddy-obico-foto-2026-10-23/compose.override.before.yaml`
(image `mechonix-bambuddy:queue-afc41c79`) and start Compose as below.

## Previous release

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
