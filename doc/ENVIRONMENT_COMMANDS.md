# Preparing and checking an environment

Run these commands in the project's Docker directory. `ENV=local|stage|prod|test`
and `PROFILES=...` select the same environment and services as `make up`.

| Command | Behavior |
| --- | --- |
| `make init` | Creates a missing private env from its example with mode 0600; creates required bind directories under project data/config. Absent shared optional config directories are omitted. |
| `make check` | Validates Compose sources for all profiles, without requiring a running Engine. |
| `make build` | Builds the shared PHP base, then selected buildable services. Changed shared build sources automatically use new image names. |
| `make pull` | Pulls selected external images; skips locally built images, including reuse by cron. |
| `make doctor` | Checks Docker/Compose, selected local images, explicit available host ports, bind sources, private file permissions/placeholders and expected TLS files. |
| `make up` | Starts/recreates selected services using existing images, waits for container readiness. |
| `make shell` | Opens `sh` in the running PHP container. |
| `make composer [dir=themes/example] [cmd="install"]` | Runs Composer in application sources; defaults to showing its version. |
| `make npm [dir=themes/example/_dev] [cmd="run build"]` | Runs npm in application sources; defaults to showing its version. `dir` is relative to `public/`. |
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

PHP 8.x images include Composer, Node, npm, npx and Webpack; Xdebug is included in local builds only. Version defaults live in `docker/.env` and can be overridden in project `env/common.env` or `env/<environment>.env`. Rebuild and recreate containers after changing build versions. Use `make npm dir=themes/framework/_dev cmd=ci`, then `make npm dir=themes/framework/_dev cmd="run build"`. Paths are relative to the mounted application source. Project dependencies remain locked by Composer/npm lockfiles. See the [PHP and build tools guide](lt/13-php-ir-build-irankiai.md) for configuration and PrestaShop compatibility.

Shared PHP-FPM worker limits are configurable through `PHP_FPM_MAX_CHILDREN` (4), `PHP_FPM_START_SERVERS` (1), `PHP_FPM_MIN_SPARE_SERVERS` (1), `PHP_FPM_MAX_SPARE_SERVERS` (2), and `PHP_FPM_MAX_REQUESTS` (500). The runner validates pool relationships before starting containers. Use project env overrides for measured production capacity.
