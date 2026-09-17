# Production snapshot — 2026-09-17

This branch records the application actually running as
`mechonix-bambuddy:restock-links-v1`, Bambuddy 1.2.5.5. It is based on upstream
tag `v1.2.5.5`, not the fork's older 0.2.4.3 main branch.

The backend files are copied byte-for-byte from the running container, excluding
Python bytecode. The existing external-spool merge, calibration addressing,
profile-coefficient and slot-configuration fixes are ordinary source changes here.

`production-static/` preserves the exact deployed frontend assets, including the
Restock direct-link behavior. These generated assets are intentionally retained
for this historical production snapshot. The upstream TypeScript sources alone
do not yet contain that previously bundle-injected Restock feature; porting it
to TypeScript belongs to the subsequent source-build change, not to a claim that
a newly rebuilt frontend is byte-identical to the running service.

`production-manifest.json` records SHA-256 hashes of the deployed backend source,
frontend assets and requirements, and the running image ID. It contains no
database, printer credentials, environment file, print files or logs.

To rebuild this snapshot from the repository root without post-build patches:

```sh
docker build -f deploy/mechonix/Dockerfile.production-snapshot \
  -t mechonix-bambuddy:production-20260917 .
```

The queued-filament fixes are deliberately absent from this snapshot. They are
developed on a separate branch. Creating this snapshot does not restart or change
the running service.
