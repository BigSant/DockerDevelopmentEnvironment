# Preparing and checking an environment

Run these commands in the project's Docker directory. `ENV=local|stage|prod|test`
and `PROFILES=...` select the same environment and services as `make up`.

| Command | Behavior |
| --- | --- |
| `make init` | Creates a missing private env from its example with mode 0600; creates missing bind directories under project data, app/config and docker/config. |
| `make check` | Validates Compose sources for all profiles, without requiring a running Engine. |
| `make build` | Builds the shared PHP base, then selected buildable services. Changed shared build sources automatically use new image names. |
| `make pull` | Pulls selected external images; skips locally built images, including reuse by cron. |
| `make doctor` | Checks Docker/Compose, selected local images, explicit available host ports, bind sources, private file permissions/placeholders and expected TLS files. |
| `make up` | Starts/recreates selected services using existing images, waits for container readiness. |
| `make shell` | Opens `sh` in the running PHP container. |
| `make bootstrap` | Prepares local env placeholders, ports, hostname, TLS and PhpStorm. |
| `make test-init` | Creates independent test env, code and data paths. |
| `make db-prepare ENV=test` | Starts only the test DB before its initial import. |
| `make db-backup` | Creates a private `.generated/backups/*.sql.gz` logical backup. |
| `make doctrine-build` | Builds only the optional Doctrine tool with its own PHP version. |
| `make doctrine-diff [ref=HEAD]` | Generates a reviewable Doctrine migration from committed schema to the local DB using an isolated comparison database. |
| `make setup-info` | Reports shared setup version, API and Git revision. |

`init` preserves existing files, never creates an application checkout, and does
not generate certificates, import SQL or activate Git hooks. File bind mounts
must be supplied separately. It creates directories for declared optional
services too, so later QA runs do not let Docker create root-owned directories.
New grouped projects use `env/local.env`; existing flat projects retain
`.env.local`. Edit the newly created example before using `up`.

`doctor` checks prerequisites and, for PS with running PHP, invokes configuration/DB checks. No application HTTP requests are made. It checks TLS
file presence/readability, not trust, expiry or domain coverage. Mount write
checks use the current host user; deployments using another service UID need
an additional permissions check. Ports already published by this Compose
project are accepted during recreation. Port availability can change after
the check. Compose 2.24.4 or newer is required for the source merge behavior.

Typical local sequence:

```bash
make init
# Edit env/local.env, supply app/public, provision host routing and TLS.
make check
make build
make pull
make doctor
make up
make ps
# Verify the application URL and run the project's tests.
```

Redis is optional: use `PROFILES=mailpit,pma,cron,redis` consistently with
`pull`, `doctor` and `up`. QA tools run on demand with `make phpstan`,
`make phpcs` and `make e2e`; build their selected image first if absent.

Before migrating an existing running stack, preserve exact running image IDs
and a private standalone Compose rollback file. Compare app, database and TLS
mounts and database engine versions before recreation. A Compose snapshot is
not a database backup. Never start two database services over the same data
directory. Keep rollback files and credentials out of Git.

Production examples separate data directories but do not establish deployment
policy, database durability/grants, secret delivery or immutable app images.

See [the complete runtime workflow](ENVIRONMENT_WORKFLOW.md) for bootstrap, test isolation and per-project startup/configuration hooks.

For initial baseline, migration review and deployment, see [automatic Doctrine generation](DATABASE_MIGRATIONS.md).
