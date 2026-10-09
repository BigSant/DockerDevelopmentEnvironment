# Architecture (setup v2)

Reference for agents and maintainers: structure, contracts and the rules that keep projects safe.
The user guide is Lithuanian (`doc/lt/`); this file is the only English document.

## 1. What this repository is

One shared checkout (`~/Projects/setup`) builds and runs an isolated Docker stack per PHP project
(PrestaShop 1.6–9, Akeneo or generic PHP). Projects keep only their own settings and sources; every
recipe, image and command lives here. Nothing is copied into projects except what `create-project`
scaffolds once.

```
setup/
  create-project, create_project.py   new project scaffold / wizard
  install-host, install_host.py       once per machine: packages, root-owned host helpers, sudoers rule
  docker/
    project.py, project.mk            the runner and the Make front end every project includes
    project-settings.yaml             the list of project settings (only these reach the runner)
    project_*.py, database_*.py       runner modules (policy, bootstrap, IDE, testing, release, deploy, DB)
    docker/<service>/                 Dockerfile + docker-compose.yml per service (catalogue)
    profile/<profile>/                image-baked per-profile config (prestashop, akeneo)
    database-profile/<profile>/       NOT baked: default table registry, data-set recipe, safe after-import SQL
    runtime/                          scripts used inside containers (start, templates, PS adapters, cron, dbmonitor, metrics)
    compatibility.json                PrestaShop line -> supported PHP / MySQL / MariaDB
  templates/                          scaffold sources (minimal, grouped, shared Makefile)
  tests/                              unittest suite (A: no Docker; B: opt-in disposable containers)
```

A project (example `~/Projects/forsena`):

```
forsena/
  app/                 environment repository (forsena_docker)
    Makefile           includes setup/docker/project.mk
    env/common.env     shared settings (git);  env/<env>.env private settings (not in git)
    compose/           optional Compose overrides (base, per env)
    config/<service>/  optional drop-ins per container (php, apache, nginx-proxy, mysql, cron, …)
    database/          schema/ (snapshot), migrations/, table-registry.txt, after-import/, data/<set>/, fixtures/
    public/            the shop's own repository (never managed by setup)
  data/                runtime data (DB files, Mailpit, cron history, TLS, profiling)
```

## 2. The runner

`make <action>` → `project.py --docker-directory app --project-directory <root> --env <ENV> <action>`.
Constructing `Project` is the contract every action relies on:

1. Env files: `setup/docker/.env` (versions) → `env/common.env` → `env/<env>.env`. Shell environment
   wins over files (Compose precedence), which is how pipeline secrets arrive.
2. Settings: only the keys in `project-settings.yaml` are read. A new setting must be added there.
3. Policy (`project_policy.resolve_policy`): defaults, validation and derived values: cache modes,
   MAIL_MODE (mailpit when the service runs), TZ, resources (`<SERVICE>_MEMORY/_CPUS` → buffer pool,
   FPM pool, memcached/redis memory), PS_SSL, DATABASE_PERFORMANCE_SCHEMA. Results go into the process
   environment used for Compose interpolation.
4. Compose files: the project network and the four core services (`DATABASE_ENGINE` picks mysql or
   mariadb; a project `include` of them is refused), the project's optional compose sources, every `SERVICES=` catalogue entry
   (`docker/<name>/docker-compose.yml`), env overrides, then a generated overlay that drops optional
   config mounts whose source is absent (Compose would create them root-owned).
5. Guards: stage/prod require explicit core limits; stage/prod tools (pma, mailpit, cron, dbmonitor, metrics)
   require an allowlist, and metrics a 24+ character `METRICS_TOKEN`; test must be isolated (own code copy, data and DB).
6. Per-image fingerprints (`setup_release.image_suffixes`): each built image's tag ends in a hash of
   only its own inputs (Dockerfile, COPY sources, build args, profile, parent fingerprint).
7. Stage/prod: `APP_UID/APP_GID` become the container user; with `DEPLOY_CONTEXT`, exec/ps/logs go
   to that Docker context while build/config stay local, and up/down/run are refused (use the release).

## 3. Images

```
php-fpm-base-<env>-<php>-…      setup/docker/docker/php-fpm/<php>/Dockerfile (extensions incl. memcached/redis, not enabled)
  └─ <project>-php-fpm-core      setup/docker/Dockerfile: profile overlay, optional PrestaShop release, setup runtime
       └─ <project>-php-fpm      project Dockerfile, must start FROM ${SETUP_PHP_IMAGE} (pass-through by default)
cron, dbmonitor, metrics         reuse the project PHP image
mysql / mariadb, webserver (apache), nginx-proxy, pma, mailpit, redis, memcached   one image each
```

The setup runtime is baked into the PHP image (targets need no setup checkout) and bind-mounted over
it locally. `make image-prune` lists setup images (labels `setup.*`) that nothing uses or whose
fingerprint is outdated; it removes only with `apply=1`.

## 4. Container contracts

- **Drop-ins**: each container reads project files at a fixed level (PHP ini, Apache global/vhost,
  nginx http/server/location, MySQL cnf + `init.d`, redis). Files ending `.template` are rendered at
  start by `runtime/render-templates.sh` with strict `${VAR}` substitution from the container env.
  An unknown variable is a start error. CORE files are never edited by projects.
- **MySQL `init.d`** (`*.sql`, `*.sql.template`, `*.sh`) runs once, when the data directory is created.
- **PrestaShop config** (`parameters.php` / `settings.inc.php`) is generated from env keys at PHP start;
  a missing key is an error. `bootstrap` generates local/test keys only. `PS_VERSION` must match the code.
- **Target metrics** (`SERVICES=metrics`, `runtime/metrics/`): Prometheus `/metrics` on the main domain
  (`location = /metrics` rendered into both site servers), token in the service, allowlist in nginx-proxy.
  Names, labels and units are the old monitoring script's, because the Grafana dashboards and alerts
  query them; `tests/fixtures/grafana-metric-names.json` records every name they use and
  `tests/grafana_names.py` assigns each to a group. A group is `runtime/metrics/<group>.php` with
  `metrics_<group>_parts()`, listed in `index.php` and in `IMPLEMENTED` (test_metrics). Host numbers come from
  the read-only host `/proc` (network from `/proc/1/net`) and `statfs` of the project data directory; the
  service is on its own network with nginx-proxy, because the project network aliases `DOMAIN` to the proxy.
  HTTP figures come from the named volume `<project>-<env>-http-logs`, mounted read-only: nginx-proxy writes a
  second copy of the site's access log there (client IPs after the real-IP handling; `/metrics` excluded) and a
  TCP table snapshot, Apache its site error log through `rotatelogs`, and php-fpm sends PHP messages to Apache
  (`fastcgi.logging=1`). `docker logs` keep everything; each log holds two files of `METRICS_HTTP_LOG_MB`.
  Logs are read backwards in chunks only until the window has passed, never loaded whole.
- **Security defaults**: published ports bind to `PUBLISH_ADDRESS` (127.0.0.1 unless a server publishes on
  its VPN interface); nginx-proxy renders allow/deny per entry point and takes the client IP and scheme
  (`X-Forwarded-For`/`-Proto`) only from trusted peers: on loopback the Docker gateway the host proxy
  arrives through (plus loopback), on any other address nobody (`SETUP_DIRECT_CLIENTS=1`), or
  `TRUSTED_PROXIES`; upstreams get only the resolved `$remote_addr`, never the client-sent chain; Apache hides dot-paths
  (except `.well-known`), `*.sql`, backups and composer files; the application DB user has rights on
  its own database (and `DATABASE_EXTRA_SCHEMAS`) only, root has its own password.

## 5. Database as code

- `database/schema/`: one normalised `CREATE TABLE` per table plus a manifest; the pre-commit hook
  (`make schema-hook-install`) exports it, stops the commit on change and drafts a guarded migration.
  It only warns when the DB is unreachable.
- `table-registry.txt` (project first, then `database-profile/<profile>/`): every table has
  `group base-rule(full|slice|faker|empty|drop) live-rule(import|skip)`. Unknown tables stop commits,
  imports and data-set builds; `make schema-resolve` classifies them.
- Migrations (`database/migrations/*.sql`) are idempotent: `CREATE TABLE IF NOT EXISTS`, columns and
  keys only after an information_schema check, DROP/RENAME as `-- TODO:` for a person. A commit that
  changes the snapshot needs a staged migration without TODO. Applied ones are recorded in
  `setup_migrations` (outside the snapshot).
- Load sequence (import, data set, test): data → safe after-import (profile defaults outside prod,
  project scripts, integration switches unless `INTEGRATIONS_ENABLED`) → pending migrations → shop
  policy → schema report (warning only). `db-import` skips the data of `skip` tables unless `full=1`.
- `DATABASE_EXTRA_SCHEMAS`: further project schemas. The first start creates them and grants the
  application user the main database's rights on them (grant patterns escape `_`, which would otherwise
  match any character). Backups dump all schemas in one `mysqldump --databases` run (one transaction,
  `CREATE DATABASE IF NOT EXISTS` + `USE` per schema), so a restore recreates each under its own name;
  an import refuses a dump that `USE`s any other schema; `db-restrict-grants` grants (and creates) them.
  Snapshot, registry, migrations and data sets stay on `DATABASE_NAME`. Without the setting nothing changes.
- Base data set (`make db-dataset`, local only): registry rules + recipe (product sample, slice
  conditions), personal columns faked, credentials blanked, rows sorted by key, one row per line.
  The output is deterministic. `make db-load set=dev` loads snapshot + set + fixtures.

## 6. Environments

| ENV | Code | Notes |
|---|---|---|
| local | `app/public` | developer stack; tools open without allowlist |
| test | `.generated/test/app` copy | own DB/containers; `make test-prepare`, `make phpunit`, e2e only here |
| stage / prod | release on the target | limits, allowlists and `APP_UID` required; started from the release model |

## 7. Release and deploy (D model)

`make release ENV=stage|prod` builds the images here from committed setup and writes
`.generated/release/<env>/images.lock.json` (setup version, image ids, base digests; Trivy warns when
installed). A release keeps no host path except the read-only `/proc` of the metrics service (named
volumes, such as its HTTP log volume, pass through unchanged).
`make deploy-images` streams the images with `docker save` into `DEPLOY_CONTEXT` and starts
the release model there. The model is rendered at deploy time and piped, never stored, because it
holds env secrets; it maps project paths to `DEPLOY_PATH` and contains no setup path. External images of
active services (no `build`, e.g. `getmeili/meilisearch` from a project compose file) are pulled when
missing and locked (`images` + `external` digests) so `docker save` ships them; a release with an image
that is not on the build machine stops before writing the lock.
`make deploy ref=TAG` builds the code release (git archive, composer `--no-dev`, `PS_RELEASE_PATHS`,
`config/deploy/build.sh` with `HOME`/npm/Composer caches in `/tmp`), backs up the target DB by registry,
rsyncs without `--delete`, removes only files the previous release shipped and never a protected path,
runs `config/deploy/after-rsync.sh` in the target's php-fpm (`sh -e`; failure stops the deploy and records
it as `failed`, so later removals and `rollback=1` follow the code the target really holds),
upgrades changed modules, runs setup migrations when the project has them, reports and clears caches.
Every application-specific step follows `PROFILE`: protected paths are `PRESTASHOP_WRITABLE` (with
`.htaccess`, `robots.txt`, `sitemap*.xml`) only for PrestaShop, plus tool folders and
`DEPLOY_PROTECTED_PATHS` for all; `ps_module`, module upgrades, target-only module protection,
`module-pull` and the PS cache clear are PrestaShop-only; generic projects clear with `CACHE_CLEAR_COMMAND`.
`rollback=1` redeploys the previous completed release (failed ones and redeploys of the current commit are skipped).

## 8. Tests

`python3 -m unittest discover -s tests` runs the A level (temporary projects, real git, fake DB). B tests
run only with their variable: `SETUP_MYSQL_IMAGE`, `SETUP_MARIADB_IMAGE`, `PHP_TEST_IMAGE`,
`SETUP_DIND_IMAGE`, `SETUP_MYSQL_SCHEMA_INTEGRATION=1`, … (see each test's skip reason).
The metrics exposition is checked with `promtool` (`SETUP_PROMTOOL_IMAGE=prom/prometheus:latest` when it
is not installed); `SETUP_GRAFANA_SOURCE=<server_stats_viewer checkout>` checks the recorded names are current.

## 9. Rules — do not break these

1. Never write to a project's `public/` (the shop repository) except the documented `app-prepare` paths.
2. Never touch a project database implicitly: `setup`, `up`, `bootstrap` and tests leave it alone; data
   changes only through explicit db-* commands, and destructive ones need `replace=1` / a backup.
3. Secrets stay in private env files, the process environment, or private (0600) generated files that local
   tools need (the IDE Compose file under `.generated/`); never in committed files, server-side files or
   command lines. Server deploys pipe the model and never store it.
4. A new setting goes into `project-settings.yaml`; a new image input changes only that image's fingerprint.
5. Optional project directories are never created empty; missing optional mounts are dropped.
6. Stage/prod refuse to start without limits, allowlists for tools, and (for releases) `APP_UID`.
7. Tests run only against ENV=test; deploys never delete files the code does not track, nor protected paths.
8. Every setup step that reads or changes application-specific things follows `PROFILE`; an empty
   (generic) profile never reads or touches PrestaShop things (`ps_module`, module upgrades, PS paths).
   Checked per step (`project_prestashop.is_prestashop`): keys, PHP start/`check`/`doctor` adapter,
   `PS_RELEASE*`/`PS_ADMIN_DIR` (refused), `app-prepare`, safe after-import SQL, `${DB_PREFIX}`/`${PS_SSL}`
   and the registry/recipe `{p}` (refused), shop policy after a load, PS 1.6 data-set key, cache-clear,
   test-copy cache folders, `modules/*` PHPUnit suites and IDE excludes. `tests/test_generic_profile.py`
   runs a generic project through setup, db-import, test-prepare and ide-init and records every Docker call.
9. Forwarded client headers are trusted only from a proxy that really sits in front: never from clients
   that reach a non-loopback `PUBLISH_ADDRESS` directly, never from the whole private ranges; upstreams get
   only nginx-proxy's resolved client address.
