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
  create-project, create_project.py   new project scaffold / wizard (writes the --ci bitbucket|github pipeline)
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
  templates/                          scaffold sources (minimal, grouped, shared Makefile), pipelines/ (Bitbucket, GitHub)
  tools/live-probe/                   setup-probe.php: read-only PHP 5.6+ probe uploaded to a LIVE server -> setup settings report
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
  data/                runtime data (DB files, Mailpit, cron history, TLS, profiling); nginx-proxy alone
                       reads ssl/ (Apache is plain HTTP); an empty stage/prod ssl/ gets a self-signed pair
                       generated inside the container, a provisioned or half pair is never replaced
```

## 2. The runner

`make <action>` → `project.py --docker-directory app --project-directory <root> --env <ENV> <action>`.
Before anything else every action except `setup-info` checks `SETUP_MIN_VERSION` (env/common.env)
against `VERSION`. `pipeline-deploy` and `deploy-variables` then run without `Project` (no Compose,
no env/<env>.env needed): they read the committed env files with `project_env_files` and start
`make` steps afresh. Constructing `Project` is the contract every other action relies on:

1. Env files: `setup/docker/.env` (versions) → `env/common.env` → `env/<env>.env`, read by one dotenv parser
   (`project_env_files`; an unreadable line stops with its line number). A key named in
   an env file is dropped from the process environment (`hides_process_value`, shared with the Deployment variables list), so a previous project's exports never select
   this stack. Exception: the pipeline secrets (`PROCESS_SECRETS`: DB/monitor passwords, dbmonitor
   and metrics tokens, PS keys) keep their process value when the env files leave them blank or
   unset, and Compose gives the process environment precedence — that is how Bitbucket Deployment
   variables and GitHub environment secrets (expanded by `pipeline-deploy`) arrive. Setup's own `.env` sets no secret defaults. `APP_ENVIRONMENT` names have no
   blank exception: a server env file must not mention them at all for the process value to arrive.
2. Settings: only the keys in `project-settings.yaml` are read. A new setting must be added there.
   `DOMAIN` may list hostnames and `DOMAIN_<NAME>` variable names (`project_domains`; names, not `${…}`,
   because setup's dotenv parser does not interpolate and Compose does). Right after the settings are read
   `DOMAIN` becomes the first hostname, in the settings and in the process environment (which Compose
   prefers to `--env-file`), so every existing use keeps one hostname; `SETUP_DOMAIN_HOSTS` carries them
   all to nginx-proxy's site `server_name`, a generated network-alias overlay, the default `METRICS_URLS`
   and, locally, `/etc/hosts`, the host Nginx route and the mkcert certificate. Setup writes none of them
   into the database: after-import SQL may use `${DOMAIN_<NAME>}` for the names `DOMAIN` lists.
3. Policy (`project_policy.resolve_policy`): defaults, validation and derived values: cache modes,
   MAIL_MODE (mailpit when the service runs), TZ, resources (`<SERVICE>_MEMORY/_CPUS` → buffer pool,
   FPM pool, memcached/redis memory), PS_SSL, DATABASE_PERFORMANCE_SCHEMA. Results go into the process
   environment used for Compose interpolation.
4. Compose files: the project network and the four core services (`DATABASE_ENGINE` picks mysql or
   mariadb; a project `include` of them is refused), the generated `APP_ENVIRONMENT` overlay
   (`project_app_environment`: `NAME: ${NAME-}` for each listed name in php-fpm and, when enabled,
   cron; references only, content-addressed under `.generated/`, refused for invalid names and names
   setup owns — every setting plus `DATABASE_`, `PS_`, `PHP_`, `CRON_`, … — and loaded before the
   project files so an explicit project definition wins), the project's optional compose sources, every `SERVICES=` catalogue entry
   (`docker/<name>/docker-compose.yml`), env overrides, the generated network aliases of a `DOMAIN` list,
   then a generated overlay that drops optional config mounts whose source is absent (Compose would
   create them root-owned).
5. Guards: stage/prod require explicit core limits; stage/prod tools (pma, mailpit, cron, dbmonitor, metrics)
   require an allowlist (their tokens are optional; a metrics token that is set needs 24+ characters); test must be isolated (own code copy, data and DB).
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
- **Application environment**: php-fpm and cron (the services that run the application code; PHPUnit
  runs in php-fpm) get the same DB connection (`DATABASE_HOST/PORT/NAME/USER/PASSWORD`), `DOMAIN`,
  `PROJECT_NAME`, `SETUP_ENV` and every `APP_ENVIRONMENT` name. Project-defined services get neither.
- **MySQL `init.d`** (`*.sql`, `*.sql.template`, `*.sh`) runs once, when the data directory is created.
- **PrestaShop config** (`parameters.php` / `settings.inc.php`) is generated from env keys at PHP start;
  a missing key is an error. `bootstrap` generates local/test keys only. `PS_VERSION` must match the code.
  An empty or missing web directory (no checkout yet) skips every PrestaShop step and writes nothing there.
- **Target metrics** (`SERVICES=metrics`, `runtime/metrics/`): the tool site `metrics.<domain>` (`sites.metrics.conf`,
  HTTP and HTTPS servers like `cron.`/`backup.`; the main site has no `/metrics`). Prometheus `/metrics` there is
  guarded by the allowlist and the service's optional bearer token; every other page by the allowlist and the optional
  `METRICS_HTPASSWD` Basic-auth, which `location = /metrics` turns off because both use the `Authorization` header.
  Collection is the sampler's (`sampler.php`, started with `php -S` by `start.sh`): its minute loop collects every
  group a few seconds after each minute starts into `snapshot.json` in `data/<env>/metrics` (bind mount
  `/var/lib/setup-metrics`, kept when the container is recreated) and appends the families of up to 16 samples,
  without per-client, per-query, per-table or per-process lists, to `history/<UTC hour>.jsonl`, whole hours kept 48 h.
  `/metrics` only serves the latest snapshot plus `setup_metrics_snapshot_timestamp_seconds`, so scrapes change
  neither the load nor the windows; a snapshot older than three sampler intervals (or none) is served as every
  `setup_metrics_group_up` 0 and nothing else. The website probes are the sampler's second loop (`--probes`, every
  `METRICS_PROBE_INTERVAL` = 300 s, curl `--max-time` 30; a timeout is time 30, status 0) into `probes.json`, which
  the minute's sample reads with `setup_website_probe_timestamp_seconds`; older than three intervals fails the group.
  A loop that stops is restarted by `start.sh`. `PHP_CLI_SERVER_WORKERS=4` keeps a slow page from blocking a scrape.
  The HTTP window defaults to one minute, as often as the sampler samples and Prometheus scrapes.
  Dashboard (`dashboard.php`, the pages other than `/metrics`): PHP rendering plus a few lines of vanilla JS, self-
  contained like the cron and backup UIs, and computing nothing: tabs read the snapshot, the history or the log
  collector's files. Overview (`overview.php`): one card per area from the flat `name{labels}` map of the snapshot,
  coloured by fixed limits (load > 0.7 x cores warns, > cores is bad; I/O wait 5/15 %; RAM and disk 80/90 %; any 5xx
  warns, 10/min is bad; p95 1/3 s; DB connections 60/80 % of max; any container stopped or not healthy; a failing or
  overdue cron job; a page check at 0, >= 400 or over 3 s), the same function giving each point of its 24-hour line
  (the highest value of each 10 minutes). The body refreshes from `?fragment=1` every minute. A card without its
  figures is not shown (p95 until the traffic analysis, cron without the service).
  Names, labels and units are the old monitoring script's, because the Grafana dashboards and alerts
  query them; `tests/fixtures/grafana-metric-names.json` records every name they use and
  `tests/grafana_names.py` assigns each to a group. A group is `runtime/metrics/<group>.php` with
  `metrics_<group>_parts()`, listed in `metrics_groups()` (metrics.php) and in `IMPLEMENTED` (test_metrics). Host numbers come from
  the read-only host `/proc` (network from `/proc/1/net`) and `statfs` of the project data directory; the
  service is on its own network with nginx-proxy, because the project network aliases `DOMAIN` to the proxy.
  HTTP figures come from the log collector's day files (below), its volume mounted read-only at `/host/logs`:
  `nginx-proxy/access-<day>.log` (client IPs after the real-IP handling; lines whose `host=` is a tool site,
  `<tool>.DOMAIN` of setup's tools or `PROXY_TOOLS`, are left out) and `webserver/` and `php-fpm/error-<day>.log`
  (PHP's messages carry their own time because `php-policy/70-errors.ini` sets `error_log` to stderr; without it
  php-fpm passes them untimed). Only today's and yesterday's plain files are read: the collector gzips a day two
  hours after its last line, after the longest window. No collector directory means the group is down; no file
  means no requests or errors. The `<project>-<env>-http-logs` volume holds only nginx-proxy's TCP table snapshot.
  Files are read backwards in chunks only until the window has passed, never loaded whole.
  The database joins the metrics network for the database group, which reads statistics only with the DB monitor
  account; the digest numbers are windowed against the previous scrape (the monitor cannot truncate them).
  Containers: the web-facing metrics service has no Docker access. `metrics-docker` (`docker-collector.php`,
  same image) holds the socket. Its `:ro` mount does not restrict the Docker API (the socket is
  root-equivalent either way); the protection is that the collector has `network_mode: none`, no capabilities,
  a read-only root and code that sends GET requests only for the project's own containers (`com.docker.compose.project`) and writes a snapshot
  (state, health, restart count, last exit/OOM, `oom` events as a persisted counter, OS/Docker versions) into
  the `<project>-<env>-metrics-docker` volume the metrics service mounts read-only; a snapshot older than three
  `METRICS_DOCKER_INTERVAL`s clears the group. With `SERVICES="cron metrics"` (`docker/metrics/cron.yml`) the
  cron group reads the crontab and `data/cron/history.jsonl` read-only and labels jobs by id, never by command.
  The host group sums `/proc/<pid>` per `argv[0]` (arguments can hold secrets) as `ps %cpu/%mem` did.
  Logs: `metrics-logs` (`log-collector.php`, the same guards as `metrics-docker`) follows the stdout and stderr of
  every project container except itself through `GET /containers/{id}/logs?follow=1&timestamps=1` and appends the
  lines as written to the `<project>-<env>-logs` volume: `<service>/<access|output|error>-YYYY-MM-DD.log` (stdout is
  `access` for nginx-proxy, webserver and php-fpm, `output` for the rest; the day is the Docker time in `TZ`). Each
  container's last Docker time (and how many lines shared it) is kept in `.collector-state.json`, so `since=`
  re-reads after a collector restart or for a recreated container skip exactly what was written; lines are never
  dropped for arriving out of time order (stdout and stderr reach the daemon's file side by side), and parts docker
  split over 16 KiB are joined. A finished day is gzipped ten minutes after its last write (a late line becomes
  another gzip member); days past `LOGS_RETENTION_DAYS` go, then the oldest while the files exceed `LOGS_MAX_MB`,
  never today's. `make logs day=` prints a day through `log-collector.php --print` in that container.
- **Container logs**: requests go to stdout, errors to stderr, nothing to files inside a container, so `docker logs`
  keep the two streams apart. nginx-proxy: every access log in the `setup` format (combined, then `rt=` request time,
  `urt=` upstream time, `host=`). Apache: `ErrorLog /proc/self/fd/2`, no access log (`CustomLog /dev/null`; the proxy
  logs the request with the client address). PHP writes its messages to stderr itself, timed
  (`php-policy/70-errors.ini`). php-fpm points its own stdout at /dev/null, so `start.sh` keeps the container's stdout as fd 3 and sets
  `SETUP_FPM_ACCESS_LOG=/proc/self/fd/3` for `zz-setup-global.conf`'s `access.log` (URL, status, `ms=`, `kb=`,
  `cpu=`); php-fpm started any other way gets the image's `/proc/self/fd/2`. The `%{mili}d` spelling is the one
  PHP before 8 accepts.
- **Security defaults**: published ports bind to `PUBLISH_ADDRESS` (127.0.0.1 unless a server publishes on
  its VPN interface); nginx-proxy renders allow/deny per entry point and takes the client IP and scheme
  (`X-Forwarded-For`/`-Proto`) only from trusted peers: on loopback the Docker gateway the host proxy
  arrives through (plus loopback), on any other address nobody (`SETUP_DIRECT_CLIENTS=1`), or
  `TRUSTED_PROXIES` (with direct clients only plus `TRUSTED_PROXIES_DIRECT=on`, else the runner refuses); upstreams get only the resolved `$remote_addr`, never the client-sent chain; Apache hides dot-paths
  (except `.well-known`), `*.sql`, backups and composer files; the application DB user has rights on
  its own database (and `DATABASE_EXTRA_SCHEMAS`) only, root has its own password.

## 5. Database as code

- **Database client** (`database_client.DatabaseClient`, `project.database`): the only way setup reaches a
  project database from the host. It runs the installed client inside the database service (`docker compose
  exec`, so stage/prod reach `DEPLOY_CONTEXT`) and chooses the account: `query` (rows), `extract` (raw bytes),
  `execute` and `dump` as the application user, `load` (a byte stream) as root, because dumps may carry DEFINER
  or GTID statements. `execute` and `load` run their whole input in one session and stop at the first error, so
  a migration and the row recording it fail together. A failure is a `DatabaseError` naming the operation and
  the client's own error line; Compose output never reaches it. The two grant scripts (`database_grants`) are
  self-contained programs and run through `project.run`.
- `database/schema/`: one normalised `CREATE TABLE` per table plus a manifest; the pre-commit hook
  (`make schema-hook-install`) exports it, stops the commit on change and drafts a guarded migration.
  It only warns when the DB is unreachable. With one database the files are flat; with
  `DATABASE_EXTRA_SCHEMAS` every schema has its own directory (`<DATABASE_NAME>/`, `<schema>/`; the
  manifest names an extra schema's table `schema.table`). Only the export picks the layout (a change is a
  move, no migration); every reader follows the manifest by table name.
- `table-registry.txt` (project first, then `database-profile/<profile>/`): every table has
  `group base-rule(full|slice|faker|empty|drop) live-rule(import|skip)`. Unknown tables stop commits,
  imports and data-set builds; `make schema-resolve` classifies them. The backup UI lists every table,
  the profile's too, and saving a profile table with other values writes the project's own line.
- Migrations (`database/migrations/*.sql`) are idempotent: `CREATE TABLE IF NOT EXISTS`, columns and
  keys only after an information_schema check, drops guarded the same way; changed keys and table
  options as `-- TODO:` for a person. A commit that
  changes the snapshot needs a staged migration without TODO for every changed database; a schema's first
  snapshot is its baseline. Below `-- Rollback:` a draft holds the way back (structure, not data) for
  `make db-rollback`. With extra schemas each database's migrations are in its own directory and run in that
  schema. Applied ones are recorded in `setup_migrations` of the main database (outside the snapshot; an extra
  schema's as `<schema>/<file>`). The hook stops while committed migrations are not applied locally.
  `DATABASE_MIGRATIONS=off` leaves migrations to the project (Doctrine); the snapshot and hook stay.
- Load (`database_load`; `db-import`, `db-pull import=1`, `db-load`, `test-prepare`, `db-fixtures-load`):
  `plan_load` checks everything without writing (files readable, hooks rendered, dump intact, its schemas, every
  dump table classified, disk space) and is what `db-import-plan`/`db-fixtures-plan` print; `run_load` holds the
  import lock for the whole run: existing-table check (a data set without `replace=1` stops here), backup, drop
  (data set), data, after-import SQL and fixtures, after-load steps. Fixtures alone skip the after-load steps.
- Load sequence (import, data set, test): data → safe after-import (profile defaults outside prod,
  project scripts — `<group>/*.sql` in the main database, then `<group>/<schema>/*.sql` in that extra
  schema — integration switches unless `INTEGRATIONS_ENABLED`) → pending migrations → shop
  policy → schema report (warning only). `db-import` loads the whole dump (`skip=1` leaves out the data of `skip` tables); the live rule applies
  to exports: `db-backup` in every environment (`full=1` dumps everything) and the deploy backup. The backup
  `db-import`/`db-load backup=1` takes before changing the database is always complete.
- `DATABASE_EXTRA_SCHEMAS`: further project schemas. The first start creates them and grants the
  application user the main database's rights on them. Every database grant, the main one included,
  escapes `_`, which would otherwise match any character (`shop_main` would cover `shopXmain`). Backups dump all schemas in one `mysqldump --databases` run (one transaction,
  `CREATE DATABASE IF NOT EXISTS` + `USE` per schema), so a restore recreates each under its own name;
  an import refuses a dump that `USE`s any other schema; `db-restrict-grants` grants (and creates) them.
  The registry, snapshot, migrations and after-import cover them; the data set, `db-load` and the Doctrine
  diff stay on `DATABASE_NAME`. Without the setting nothing changes. An extra schema's table is named
  `schema.table` (manifest, registry, backups), read only by `database_client.split_name`; each per-schema
  directory stays with its module (`doc/adr/0001-schema-topology-stays-with-its-modules.md`).
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
installed). A release keeps no host path except the read-only `/proc` of the metrics service and the read-only
Docker socket of its network-less collectors (named volumes, such as its HTTP log volume, pass through unchanged).
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
upgrades changed modules, runs setup migrations when the project has them, reports and clears caches,
then reloads php-fpm for every profile (`USR2` to PID 1 via the shell builtin; the CORE image sets
`process_control_timeout = 30s` so workers finish their requests) so OPcache serves the new code
(`DEPLOY_PHP_RELOAD=off` opts out).
Every application-specific step follows `PROFILE`: protected paths are `PRESTASHOP_WRITABLE` (with
`.htaccess`, `robots.txt`, `sitemap*.xml`) only for PrestaShop, plus tool folders and
`DEPLOY_PROTECTED_PATHS` for all; `ps_module`, module upgrades, target-only module protection,
`module-pull` and the PS cache clear are PrestaShop-only; generic projects clear with `CACHE_CLEAR_COMMAND`.
`rollback=1` redeploys the previous completed release (failed ones and redeploys of the current commit are skipped).
The release's files reach the **Deploy target** (`project_target.deploy_target`): this machine, or `DEPLOY_SSH=user@host`
over ssh. Code push, removals, target-only module listing (directories only), `module-pull`, environment-file sync,
runtime directories and the port check all go through it; every ssh connection, rsync's included, runs with
`BatchMode=yes` and `ConnectTimeout=15`, host keys checked as ssh is configured. The containers' Docker daemon is
the separate `DEPLOY_CONTEXT`.

`make pipeline-deploy` (`project_pipeline`) is the CI deploy step of both providers; projects keep only
`bitbucket-pipelines.yml` or `.github/workflows/deploy.yml` (`templates/pipelines/`; `create-project --ci`
writes one and sets `CI_PROVIDER`). The provider comes from the job's environment (`Provider`):
GitHub Actions when `GITHUB_ACTIONS=true` (environment name from `DEPLOY_ENVIRONMENT`, which the workflow
passes because GitHub has no such variable; commit `GITHUB_SHA`), Bitbucket when any `BITBUCKET_*` is
set (`BITBUCKET_DEPLOYMENT_ENVIRONMENT`, `BITBUCKET_COMMIT`). GitHub does not export secrets: the
workflow passes `toJSON(secrets)` / `toJSON(vars)` in `DEPLOY_SECRETS_JSON` / `DEPLOY_VARIABLES_JSON`,
and `expand_json` sets every valid name the process lacks (never `GITHUB_*`, `RUNNER_*`, `ACTIONS_*`;
the job's env wins, a secret wins over a variable); the JSON itself is dropped from the child steps.
It runs in the build root's environment checkout:
ENV from the deployment environment (staging/production); `REF` (aliases `APP_REF`, `SHOP_REF`), `IMAGES`
(`yes`/`no`/`auto`), `CRON` (`auto` by default), `ROLLBACK`, `BACKUP`. Layout decides the ref: one repository (no `app/public/.git`) checks out the deployed commit
(REF, the pipeline commit, or the history's rollback target) and follows the history afterwards; a
separate code repository keeps the environment checkout on the pipeline commit and deploys REF of
`app/public`, which is only fetched. Order: refusals before any change (exit 64; required Deployment
variables included) → fetch → checkout → `env/<env>.env` from the example + the build root's `target.env`
(the deploy target setup-server registers: `DEPLOY_CONTEXT/SSH/PATH`, `APP_UID/GID`; an example naming
them is refused; without `target.env` the example's own target, + the runner's ids on the same host)
→ the same checks with the deployed commit's files (a too old setup on a setup-server worker: `setup_self_update`
checks out the newest release tag of the same MAJOR that is new enough and the step restarts on it) →
`make release` + `make deploy-images` (`transfer=0` on the same host) → `make deploy` → `make cron-reload`.
The `auto` switches are decided before the checkout from `git diff live target` in the environment checkout
(`images_needed`: no `images.lock.json`, its `setup` unlike `git describe` of setup, or `IMAGE_PATHS` +
`env/<env>.env.example` touched; a rollback stays code-only; `cron_needed`: `config/cron/` touched) and
logged with the reason. `make cron-reload` (`project_deploy.reload_cron`) runs `CRON_RELOAD` with `sh -e -c` in
the cron container: `cronCompose()` into `/tmp/crontab.next` (not compose.php as a script, whose start-up step
closes running jobs as interrupted), `supercronic -test`, swap, `SIGUSR2`. A command, not a runtime file,
because stage/prod images carry the runtime they were built with. supercronic reads the crontab only at
start, and compose recreates no container whose mounted file alone changed. A refused crontab fails the
step after the deploy and undoes nothing. `project_release.sync_environment` copies the environment
checkout's bind-mount sources (not code, data or `.generated/`) to `DEPLOY_PATH` in deploy-images and deploy
whenever `DEPLOY_PATH` is not the project root. A failure restores
the previous checkout and env file unless the containers already restarted on new images. Child steps
get `SETUP_DIRECTORY` of this setup and no `MAKEFLAGS`/`DOCKER_HOST`.
The Deployment variables list (`project_env_files.deployment_variables`, beside `PROCESS_SECRETS`) is setup's secrets for the env
(DB passwords, PS keys of `PS_VERSION`; monitor password and metrics/monitor tokens optional) +
`DEPLOY_REQUIRED_VARIABLES` + `DEPLOY_OPTIONAL_VARIABLES` + `APP_ENVIRONMENT` names the env files do not
mention; a name an env file sets is not a Deployment variable, a blank one (other than `PROCESS_SECRETS`)
is refused because it would hide the process value. The same list feeds the stage/prod secret store
(`project_secret_store`): a generated `secrets` service holds the names as `${NAME-}` references in a
sleeping, network-less container, and a stage/prod command outside a pipeline (`CI` not `true`) takes a
name its environment lacks from that container (`docker inspect`, through `DEPLOY_CONTEXT`), so
server-side backups and manual commands need no copy of the secrets. `make deploy-variables` (`project_ci` picks the
provider: `CI_PROVIDER`, else a single `DEPLOY_*_REPOSITORY`, else the origin host, else Bitbucket).
`project_bitbucket` compares the same list with the Bitbucket environment (names and secured flag) and
creates/replaces from a private dotenv file; urllib only (no subprocess, so no value or token on a
command line), no redirects, next links only within the API, token and values files owned by the user
with mode 0600. `project_github` does the same against a GitHub environment: plain names are
environment variables, the rest environment secrets; listing and variable writes use the REST API
(page numbers, not Link headers), secrets go through `gh secret set --env` (gh seals the value with the
environment's public key; libsodium is not in the standard library) with the value on stdin and the
token in `GH_TOKEN`; without `gh` nothing is written.

## 8. Tests

`tests/run` (CI calls it too; it exits with the suite's status) runs the A level (temporary projects, real git, a fake DB: the
`tests/fake_database.FakeDatabase` assigned as `project.database`, which records and never parses SQL; the
pipeline step for both providers against git fixtures and a fake `make`, `deploy-variables` against fake
Bitbucket and GitHub APIs and a fake `gh`). `tests/run --database mysql:8.4` (or `mariadb:11.4`, `mysql:5.7`)
and `tests/run --release` run the database and release B levels as CI does: they pull or build the images and set
the variables. B tests run only with their variable: `SETUP_MYSQL_IMAGE`, `SETUP_MARIADB_IMAGE`, `PHP_TEST_IMAGE`,
`SETUP_DIND_IMAGE`, `SETUP_MYSQL_SCHEMA_INTEGRATION=1`, … (see each test's skip reason).
`SETUP_OLD_BASH_IMAGE=mysql:5.7` runs the DB first-start hooks under bash 4.2: shell hooks that run
inside third-party images must not rely on bash ≥ 4.4 (e.g. `"${array[@]}"` of an empty array under `set -u`).
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
