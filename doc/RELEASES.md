# Shared setup releases

`VERSION` identifies the setup release; `make setup-info` reports version, Git revision and local
modifications. Developer machines run the latest `master` of one shared checkout; the build server
builds stage/prod releases from a release tag. Projects do not pin versions.

## Unreleased

- **Breaking for `SERVICES=metrics`: the metrics move from `<domain>/metrics` to their own tool site
  `metrics.<domain>`** (like `cron.` and `backup.`). The main site no longer has a `/metrics` path: from the first
  deploy with this setup, a request there reaches the application like any other page. Prometheus reads
  `https://metrics.<domain>/metrics` with the same names, the same `METRICS_ALLOWED_IPS` and the same optional
  `METRICS_TOKEN`. Scrapes no longer pass through the site's server, so they are out of its access log by
  construction. New optional **`METRICS_HTPASSWD`** (Basic-auth, a Deployment variable like the other tools'
  passwords) guards every `metrics.<domain>` page except `/metrics`, which keeps its bearer token; for now the only
  other page is a short index, the dashboard comes later. `make deploy-variables` lists it, and `make pipeline-deploy`
  names it among the empty optional variables until it is set.
  Before deploying a project that runs `metrics` with this setup, on its server:
  1. DNS record `metrics.<domain>` (VPN target: the name pointing at the VPN address on the Prometheus server, e.g. `/etc/hosts`);
  2. DirectAdmin: subdomain `metrics.<domain>` proxied to nginx-proxy like the main domain, with a TLS certificate;
  3. Prometheus: target `metrics.<domain>`, `metrics_path: /metrics` (job name unchanged, so the dashboards keep working).
  Locally the host Nginx helper changed: run `./install-host` in the setup checkout once (sudo), as `make bootstrap`
  asks; it then adds the `/etc/hosts` entry and the host Nginx name (the mkcert certificate already covers
  `*.<domain>`).

- **Requests on stdout, errors on stderr, timings on the request lines.** `docker logs` now keep the two streams
  apart for the three web services, the base for keeping logs past a container's life (next releases).
  - nginx-proxy writes every access line as `combined` followed by `rt=` (the request's time), `urt=` (Apache and
    PHP's time, `-` when nothing was proxied) and `host=`. Parsers of `combined` read the lines as before.
  - Apache wrote its site access and error logs to files inside the container, which grew without a limit and were
    lost with the container. Errors now go to stderr (with metrics also to the HTTP log volume, as before) and
    Apache keeps no access log: nginx-proxy logs every request, with the client's address.
  - php-fpm writes one line per request to stdout instead of stderr: the client, the URL the visitor asked for
    (not `index.php`), the status, `ms=` (time), `kb=` (PHP's peak memory) and `cpu=`. PHP's messages stay on
    stderr. php-fpm started without setup's `start.sh` keeps writing them to stderr.
  `make build` rebuilds the three images; nothing to set.

- **Container logs are kept for 14 days, one file per service, stream and day** (`SERVICES=metrics`). docker logs
  lose a container's lines whenever it is recreated (every `make deploy-images`). A new collector container,
  `<project>-<env>-metrics-logs`, follows every project container's stdout and stderr and writes the lines to the
  volume `<project>-<env>-logs`: `php-fpm/access-2026-10-15.log`, `php-fpm/error-…`, `nginx-proxy/access-…`,
  `database/error-…`, `cron/output-…`. A finished day is gzipped; `LOGS_RETENTION_DAYS` (default 14) and
  `LOGS_MAX_MB` (default 2048, oldest first, never today) bound it. It continues where it stopped after its own
  restart and for recreated containers. Like the Docker collector it holds the Docker socket, so it has no network
  and no capabilities and sends GET requests for the project's own containers only.
  New **`make logs day=2026-10-14|today|yesterday [service=php-fpm]`** prints a day from there.
  `make build` and `make deploy-images` start it; history begins then (plus what docker logs still hold).

- **The metrics HTTP group reads the log collector's files; the log copies are gone.** `http_*` and `http_error_type`
  (same names and labels) now come from `nginx-proxy/access-<day>.log` and the `webserver/` and `php-fpm/` error
  files. nginx-proxy no longer writes a second access log, Apache no longer pipes its errors through `rotatelogs`
  and php-fpm no longer sends PHP's messages to Apache (`fastcgi.logging`). The proxy's log holds its tool sites
  too; their lines (`metrics.`, `cron.`, … and `PROXY_TOOLS` names) are left out of the figures.
  PHP now writes its own messages to stderr with their time (`error_log = /proc/self/fd/2` in setup's PHP policy;
  a project's `config/php` can still set another `error_log`), so PHP errors are counted where they happen.
  `METRICS_HTTP_LOG_MB` is gone (an env file that still sets it is ignored); the old copies in the
  `<project>-<env>-http-logs` volume are removed at the proxy's next start, and the volume keeps only its TCP snapshot.

- **`/metrics` serves a snapshot the metrics service takes every minute.** Collection moved from the scrape into a
  sampler in the metrics container: a few seconds after each minute it collects every group into
  `data/<env>/metrics/snapshot.json` (and a 48-hour history of the small groups for the coming dashboard);
  `/metrics` serves the latest one plus `setup_metrics_snapshot_timestamp_seconds`. More scrapes no longer mean more
  load or shifted windows. A snapshot older than three minutes is served as every `setup_metrics_group_up` 0.
  The `METRICS_URLS` probes run in their own loop every 5 minutes with a 30 s limit (was 20 s on every scrape): a
  page over it reports `website_response_time` 30 and `website_response_status` 0, and never delays the other
  groups. New `setup_website_probe_timestamp_seconds`.
  **The HTTP window now defaults to 1 minute** (`METRICS_HTTP_WINDOW_MINUTES`, was 5), matching a 1-minute
  Prometheus `scrape_interval`; set it to your interval if Prometheus scrapes less often, or `sum_over_time` alerts
  count each request several times. `make build` and `make deploy-images`; `data/<env>/metrics` is created by them.

- **Dashboard at `metrics.<domain>/`** (`SERVICES=metrics`): the page that listed `/metrics` is now an Overview of
  cards (load, I/O wait, memory, disk, 5xx per minute, database, containers, cron, page checks), each coloured by
  setup's fixed limits and with a 24-hour line from the sampler's history; it refreshes every minute. It reads only
  the sampler's snapshot, so opening it costs the server nothing. Same access as before: the allowlist and the
  optional `METRICS_HTPASSWD`. Limits: `doc/lt/05-servisai.md`.
  Its **Logs** tab lists the kept log files by service and day, shows the end of one (gzipped days too) filtered by
  level (errors, warnings, 5xx, 4xx) and text, and downloads it as it is.
  Its **Traffic** tab replaces `scan_access_file.sh`: for the last 15 minutes, hour, 24 hours, 7 days or a day, each
  request is an attack (SQL injection as the script found it, path traversal, probes such as `/.env` or `/wp-login`,
  XSS), a bot (the script's list, about 30 more crawlers and the project's `METRICS_BOTS`), a static file or a
  person; with the bots and suspicious and regular IPs as the script counted them, attacks by type with their lines,
  top IPs, agents and URLs, 4xx and 5xx by URL, the slowest URLs and the p95 response time. Closed hours are analysed
  once and cached in `data/<env>/metrics/traffic` (8 days).
  **Database**, **Containers** and **Cron** tabs show the figures `/metrics` already served (slow and table-scan
  statements, table locks and I/O, buffer pool; each container's state, health, restarts and OOM kills; each cron job's
  last run, exit code and duration) as tables, and say why when a group has none.

- **Traffic figures in `/metrics`** (new names only, label sets bounded): `setup_http_class_requests{class}`,
  `setup_http_bot_requests{bot}` (top 20), `setup_http_attack_requests{type}`, `setup_http_url_requests{url}` and
  `setup_http_url_5xx_requests{url}` (top 10), `setup_http_slow_requests` (over `METRICS_SLOW_REQUEST_SECONDS`,
  default 2) and `setup_http_response_time_p50_seconds` / `_p95_seconds`, all over the HTTP window. The dashboard's
  Overview shows the p95 card from them.

- **`metrics.<domain>/feed`**: a text report for people and LLMs with the old monitoring script's results.txt
  sections and headers, filled from the latest snapshot, then the last 24 hours' traffic analysis (classes, bots,
  attacks with their lines, top IPs, agents and URLs, 4xx/5xx, the slowest URLs). Guarded like `/metrics`: the
  allowlist and `METRICS_TOKEN`, no `METRICS_HTPASSWD`. Not byte-for-byte the old commands' output; server_stats_viewer
  does not read it.

- **PHP-FPM and OPcache figures** (`SERVICES=metrics`): workers active and idle, the listen queue, how often every
  worker was busy (`setup_php_fpm_max_children_reached_total`), OPcache's hit rate, memory, scripts and restarts by
  reason, and a "PHP workers" card on the Overview. php-fpm joins the metrics network; the metrics service asks it over
  FastCGI for `pm.status_path` (visitors cannot reach it: Apache passes php-fpm only `.php` files). New optional
  **`PHP_FPM_SLOWLOG_SECONDS`**: requests slower than that write their PHP stack trace to php-fpm's docker logs;
  only then does php-fpm get `SYS_PTRACE`, which the slowlog needs. `make build` (the pool settings are in the image).

## 2.11.1

Nothing changes for projects. A pipeline-deploy test whose fixture clone fails now and then on CI (exit status
128, passing on rerun) now shows git's own message in the log, so the cause can be found the next time it happens.

## 2.11.0

Nothing changes for projects: no setting, command, image or file format. This release is for setup's own
maintenance.

- **`tests/run` runs setup's tests the way CI does** and exits with the suite's status, naming the failing tests
  (whole log in `.test-work/last-run.log`). `tests/run --database mysql:8.4` (or `mariadb:11.4`, `mysql:5.7`)
  and `tests/run --release` run the database and release integration tests: they pull or build the images and
  set every variable. CI calls it too, and now also runs the tests that need setup's own database image on
  every server of its matrix, plus a new `release` job. Three of those tests had never run in CI and failed
  there (the release test assumed `alpine:latest` was present; the import and project-init tests used
  `mysqladmin`/`mysql`, which MariaDB 11 no longer ships); they are fixed.
- **Internal restructuring, same behaviour.** The deploy-variables providers share their checks from
  `project_ci`; `Project.active_model()` replaces eight copies of the Compose model read; the image build
  order (base, core, the rest) is written once (`project_images.build_images`); `make db-pull import=1`
  numbers and runs its import step itself; `make phpstan`/`phpcs`/`e2e` go through
  `project_commands.run_qa`, with its first tests.
- A root `CLAUDE.md` points agents to `doc/ARCHITECTURE.md`, `GLOSSARY.md`, `doc/adr/` and `tests/run`.

## 2.10.0

- **`make pipeline-deploy` decides `IMAGES=auto` itself, and reloads cron (`CRON=auto`).** A project no longer
  needs its own script for when images are due: with `IMAGES=auto` the step rebuilds them when the build root
  has none yet, when setup moved since they were built, or when the deployed commits touch `Dockerfile`,
  `docker/`, `compose/`, `config/` (not `config/cron/`) or the env files; a rollback stays code-only. `IMAGES`
  still defaults to `no`; the pipeline templates now default to `auto`.
  New `CRON` (default `auto`): when the deployed commits touch `config/cron/`, the step runs the new
  **`make cron-reload ENV=…`** after the deploy. supercronic read the crontab only at start, and neither
  `make deploy` nor `make deploy-images` recreated the cron container for a changed mounted file, so a job
  added in git did not run until a restart. `make cron-reload` rebuilds the crontab, checks it with
  `supercronic -test` and sends `SIGUSR2`; running jobs go on, a refused crontab keeps the running schedule and
  fails the step. Both decisions are logged with their reason (`==> CRON=auto: yes (changed since …)`). Add the
  `CRON` variable to a copied pipeline file to choose it by hand (templates/pipelines).
- **PHP 8.5 images build again with the cache extensions**: memcached 3.4.0 and redis 6.3.0 (3.3.0 and 6.2.0
  include `php_smart_string.h`, which PHP 8.5 removed). A PHP 8.5 project's images are rebuilt once.
- **CI is green again** (red since 2026-09-12, test fixtures only): the PHP image builder builds the CORE image
  too, the runtime hook test gives a stock PHP image the writable drop-in directories, the Doctrine test starts
  only its database and drops the core networks, data volume and user it replaces, the metrics test keeps its
  Docker socket under the 107-byte limit, the MariaDB schema test accepts `text DEFAULT NULL`, and the probe test
  no longer reads a 404 page that repeats the URL as the probe answering.
- **Leftovers removed.** Every project rebuilds its PHP, nginx-proxy and PrestaShop database images once, because
  their Dockerfiles or build args changed (no change inside the images apart from PHP 5.6 below).
  - **`MEMCACHED_MEMORY` is a container limit only.** A plain number (the cache size in MB of the never-released
    first memcached version) is refused; the cache size is `MEMCACHED_CACHE_MB`. `MEMCACHED_CONTAINER_MEMORY` is
    gone; the other pre-2.0 resource names still work.
  - **PHP 5.6 local images no longer print `Failed loading xdebug.so`** on every start; Xdebug was never
    installed there.
  - **`make db-dataset` no longer passes `replace=`/`backup=`**, which only `db-load` reads.
  - **Unused build args and arguments are gone:** `APCU_VERSION` on PHP 5.6–7.4, nginx-proxy's `ENV`, php-phpstan's
    `COMPOSER_VERSION`; so are commented-out Dockerfile lines and the empty PrestaShop `my.<env>.cnf` files.
  - **Unused files are gone:** the PhpStorm screenshots in `assets/` (their guide went in 2.0.0) and an unused test
    fixture.
  - **Unused code is gone:** `dump_schemas`, `dump_tables`, `supported_php`, the Bitbucket-only
    `deploy_variables` wrapper and the `BITBUCKET_*` aliases.
- **Shared helpers instead of copies.**
  - SQL quoting (`database_client.literal`/`identifier`): the table registry's column lookup now escapes
    backslashes too.
  - The PrestaShop check (`is_prestashop`), the checkout's origin and CI repository (`project_remote`), the
    `.generated/` Compose overlays (`project_ide.write_overlay`) and the project root
    (`project_env_files.project_root`).
- **Database errors say why.** A failed migration, rollback, import, backup or schema read now names the
  database's own error line (for example `ERROR 1064 (42000) at line 2: …`), a stopped database service, or a
  container started for another `DATABASE_NAME`; before, a failed migration said only that it failed. Compose
  output still never appears, because it may hold secrets. Behind it, every database call goes through one
  Database client (`doc/ARCHITECTURE.md` §5).
- **One lock for a whole load.** `db-import`, `db-load`, `test-prepare` and `db-fixtures-load` hold the import
  lock from the first check to the end of the migrations and shop policy, so a second load can no longer run
  between a data set's drop and its data, or during the first one's migrations; it stops with "already
  running", as before. `db-load` no longer writes a combined copy of the data set to `.generated/`.
- **Env files are read one way.** A line inside a quoted multi-line value (a key in PEM form holding
  `DATABASE_PASSWORD=...`) no longer counts as a setting, so it cannot hide the pipeline's real value. A line
  setup cannot read stops with its line number. Locally, `DATABASE_ROOT_PASSWORD=""` now gets a generated
  password instead of stopping the runner.
- **ssh deploys fail fast.** Every ssh connection of `make deploy`, `deploy-images` and `module-pull` (rsync's
  included) runs with `BatchMode=yes` and `ConnectTimeout=15`: a password prompt or an unreachable server stops
  the step at once instead of holding a pipeline. Host keys are still checked as ssh is configured. A server's
  `modules/` is read as directories only, so PrestaShop's `modules/index.php` no longer warns "module index.php
  exists only on the target".

## 2.9.0

### Cron UI: a running job and its output, live

- **The board and the history show a run while it runs**: a pulsing lamp and "running · elapsed · watch" on the
  board, a row on top of the history. `cron-run.sh` marks the run in `data/cron/running/<job>-<pid>.json` and
  removes the mark once the run is recorded.
- **"watch" follows the output as the job writes it** (`/tail` gives the whole lines added since the last look,
  every 2 s; the view keeps to the bottom while you are there) and shows the exit code when the run ends.
  "Run now" opens it as soon as the run has started instead of waiting for the end.
- **A run cut by a container stop or restart** (a deploy, `make restart`) used to leave its output and no
  record; the next cron start records it as **interrupted** (no exit code, duration up to the last output).
  Metrics count it as failed: `setup_cron_job_last_exit_code` `-1`.

### make db-pull: full, fresh, progress bars and a server asked for once

- **`full=1`** downloads the newest full backup (every table's data; setup-server's weekly series), **`fresh=1`**
  has the server make a new one now (with `full=1` a new full one; keys added with `add-backup-key --allow-fresh`),
  both with `import=1` and `backup=1` as before. setup-server answers `info` (name and size) and `fresh`; an older
  one still hands out the newest registry backup and says it is too old for the rest.
- **Each step shows a progress bar on a terminal**: the backup on the server (fresh), the download, and with
  `import=1` the unpacking and the import; a log gets one line per step. `pv` is no longer used by `db-import`.
- **A dump is read once**: the download checks the gzip stream and reads the dump on the way (size, schemas,
  tables), and `db-import` shares one reading between its checks instead of reading a `.sql.gz` three times
  before unpacking it.
- **The backup server is asked for once per machine**: the first `make db-pull` in a terminal asks for
  `user@host` and the key and keeps them in setup's `docker/.env.backup` (0600, ignored by Git) for every project;
  `configure=1` asks again, a project's `BACKUP_SSH` / `BACKUP_SSH_KEY` still win. The project's directory on the
  server is `BACKUP_SERVER_PROJECT`, else the environment repository's git name, else `PROJECT_NAME`; when the
  server knows neither, the command asks and keeps the answer in `env/local.env`.

## 2.8.1

### A registry backup with an extra schema's skip table works again

- **`make db-backup` (and setup-server's nightly backup) failed** with "Configured database differs from the
  running container" whenever the table registry skipped a table of a `DATABASE_EXTRA_SCHEMAS` schema (asana.local:
  `asana_git.git_files` and the other synced tables): the structure pass of that schema gave the dump client the
  extra schema's name where it checks the main database. The dump client now takes `--schema NAME` after the
  checked main database name.

### Git hooks run the project's bin/pre-commit and bin/pre-push

- **`make setup` (local) and the new `make git-hooks` write `.git/hooks/pre-commit` and `pre-push`** that run the
  project's own `bin/pre-commit` / `bin/pre-push` (arguments and stdin passed on, their status decides), after
  setup's schema check in `pre-commit`. A clone carries no hooks, so a check a project keeps in git used to reach
  only the clones where somebody linked it by hand. The hook finds the script when it runs (no reinstall when it
  changes); a hook with nothing to run is not written; someone else's hook, a symlink or a `core.hooksPath`
  manager is left alone and reported. The schema hook of earlier versions is replaced in place.

## 2.8.0

### nginx-proxy finds recreated containers (nginx 1.30.5)

- **A recreated webserver, pma or mailpit no longer answers 502 until nginx-proxy restarts.** nginx resolved
  those names once, at start, so after a deploy or `make up` recreated the container with a new address every
  request went to the old one. The site's upstream now re-resolves through Docker's DNS (`server webserver
  resolve`, every 10 s; it needs nginx 1.27.3+, hence `NGINX_VERSION=1.30.5`), and pma and mailpit are resolved
  per request like the cron UI. The container name carries the version: `<project>-<env>-nginx-proxy-1.30.5`.
  A project's own `config/nginx-proxy` site that proxies to another container does the same (doc/lt/05).

### DATABASE_TEST_SCHEMAS

- **Schemas an application's own tests create and drop** get the application user's privileges without becoming
  project schemas: `DATABASE_TEST_SCHEMAS="asana*_test"` (names end in `_test`, `*` is a wildcard) is granted on a
  first database start and by `make db-restrict-grants` (after a `--test` marker in its arguments), but never
  created, and the snapshot, backups, imports, the table registry and migrations ignore it. Listing such schemas in
  `DATABASE_EXTRA_SCHEMAS` put every test table into the snapshot. Local and test only: the runner refuses it on
  stage/prod.

### PrestaShop .htaccess

- **Setup writes the shop's `.htaccess`** with PrestaShop's `Tools::generateHtaccess()` in the running php-fpm:
  at `make up`/`setup`/`restart` when it is missing (shop repositories usually leave it out, and friendly URLs
  answer 404 without it), and again after `make db-import`/`db-load` outside prod, whose after-import gave the
  shops their local domains. What is outside PrestaShop's markers stays; a failure only warns.

### Migration rollback, per-database migration directories, DATABASE_MIGRATIONS=off

- **A dropped table, column or key is drafted as a guarded statement** like any other change (it was a
  `-- TODO:`); a rename is a drop and a new one, and a later migration moves data when it must. Changed
  keys and table options stay `-- TODO:`.
- **Rollback**: a draft now has a `-- Rollback:` part below the way forward, written by setup (drop what was
  added, restore a changed column, recreate a removed table or column as structure only; changed keys and
  table options stay `-- TODO:`). `make db-rollback [steps=N|to=NAME] [dry=1]` runs it for the latest applied
  migrations, newest first, and removes them from `setup_migrations`; it checks every one before changing
  anything. `make db-migrate dry=1` prints the SQL without running it. The applied checksum covers the way
  forward only, so completing a rollback later is no change. `make deploy rollback=1` names the migrations
  the rolled-back release applied and the `make db-rollback` command for them.
- **`make migration-draft [name=…]`** exports the schema and drafts the migrations from the last commit to the
  local DB (one per changed database) without a commit, whatever is staged; `make schema-export` alone drafts
  nothing.
- **A dropped table takes its registry line with it**: `make schema-export`, `make migration-draft` and the commit
  hook remove the project line that names exactly a table gone from the snapshot (wildcards and the profile stay).
- **Per-database migration directories**, like the snapshot: flat with one database, `migrations/<DATABASE_NAME>/`
  and `migrations/<schema>/` with extra schemas. The hook drafts one migration per changed database, a commit
  needs one for each, and an extra schema's runs in that schema (setup adds `USE`); it is recorded as
  `<schema>/<file>`, a main database's by its file name, so old flat migrations stay valid.
- **After a pull**: the commit hook stops before exporting while committed migrations are not applied to the
  local database (run `make db-migrate`), so an older DB no longer overwrites a newer snapshot or drafts a
  migration back; a passing commit records its new migrations as applied here (the DB already has them).
- **`DATABASE_MIGRATIONS=off`** leaves migrations to the project (e.g. Doctrine): no drafts, no migration
  required by the hook, nothing applied after a load or deploy; the snapshot, registry and hook stay.

### Extra schemas in the snapshot, migrations and after-import

- **The schema snapshot covers `DATABASE_EXTRA_SCHEMAS`, every schema in its own directory**:
  `database/schema/<DATABASE_NAME>/table-*.sql` and `database/schema/<schema>/table-*.sql` (an extra schema's table
  is `schema.table` in the manifest, like in the registry); a project with one database stays flat. Only
  `make schema-export` chooses the layout and moves the files when it changes (the hook shows `moved:`, no
  migration); the hook, migrations, `db-load` and the report after a load or deploy read the files through the
  manifest, so a server's `DATABASE_NAME` may differ. Run `make schema-export` once and
  commit the new subdirectories: a schema's first snapshot is its baseline (like the main database's first), so
  the commit needs no migration; its later changes do.
- **Guarded migration drafts name the schema**: `CREATE TABLE IF NOT EXISTS \`schema\`.\`table\``, `ALTER TABLE
  \`schema\`.\`table\``, and the `information_schema` checks ask for `TABLE_SCHEMA = 'schema'`; the main database's
  drafts are unchanged.
- **After-import scripts of an extra schema** live in `database/after-import/<group>/<schema>/*.sql` and run in
  that schema (setup prepends `USE`), after the group's main-database scripts. A subdirectory with SQL that is not
  an extra schema stops the import.
- The data set, `make db-load` and the Doctrine diff stay on the main database: they leave the extra schemas'
  snapshot files out.
- **Fixed: a committed snapshot was normalised twice** (`;;` at the end), so a commit stopped for another reason
  drafted an empty migration for every table.

### Search and price-filter indexes stay out of LIVE copies

- **`{p}search_index`, `{p}search_word`, `{p}layered_price_index` are `skip` in the PrestaShop profile** (were
  `import`): rebuildable indexes and usually the biggest tables, so backups and server copies keep only their
  structure. After loading such a copy, rebuild the search index (Shop Parameters → Search) and the faceted
  search price index. A project that wants them keeps an `import` line in its own registry.

### The backup UI lets a project override setup's PrestaShop defaults

- **Standard PrestaShop tables are listed** in the backup UI with the source `setup` (filter „Setup defaults“);
  before they were hidden and a save was refused. Saving one with other values writes the project's own line,
  which wins over the profile default (e.g. `ps_search_index` Live copy `import`); the default's own values write
  nothing, and Reset removes the project line. The profile stays the starting rule for every shop.
- **The stats show the live copy size** beside the database size: data + index of the `import` tables (a table
  without a rule counts by its suggestion); the tooltip gives their data alone, close to a dump's size.

### make up waits for the shop checkout

- **An empty or missing `app/public` no longer stops `make up`** in a PrestaShop project: the preflight and the
  PHP container's start step print `No shop code in … yet` and skip every PrestaShop step (config generation,
  `PS_DEBUG_MODE`, policy, cache clear, `make doctor`'s DB probe). Nothing is written there, so `git clone`
  into the directory still works. A checkout with files but without `config/defines.inc.php` still fails
  when `PS_DEBUG_MODE` is set. Env checks (`PS_VERSION`, keys, compatibility) run as before.

### The registry skip rule applies to exports, not imports

- **`make db-import` loads everything the dump holds.** The registry's live rule `skip` no longer leaves data
  out of an import; `skip=1` still does (structure kept). `full=1` on `db-import` is gone.
- **`make db-backup` follows the registry in every environment** (before: stage/prod only): the structure of
  every table, the data of `import` tables. `full=1` dumps every table's data. The backup `db-import` /
  `db-load backup=1` takes before changing the database stays complete.
- **Migrating:** a project that relied on imports skipping cache tables passes `skip=1`; scripts that passed
  `full=1` to `db-import` drop it.

### Several hostnames in DOMAIN

- **`DOMAIN` may list hostnames and `DOMAIN_<NAME>` variables** separated by spaces, e.g.
  `DOMAIN_LT=demo.lt`, `DOMAIN_LV=demo.lv`, `DOMAIN="DOMAIN_LT DOMAIN_LV"` (names, not `${…}`). The first
  hostname stays `DOMAIN` for everything that used it (service host names, Apache, templates, after-import
  `${DOMAIN}`, the default shop's safe default); all of them reach nginx-proxy's site `server_name` (with
  `www.`), the project network aliases, the default `METRICS_URLS` and, locally, `/etc/hosts`, the host Nginx
  route and the mkcert certificate. Setup writes none of them into the database; after-import SQL may use
  `${DOMAIN_<NAME>}`. A single hostname works as before.
- **Migrating:** nothing for one hostname. With a list: rerun `setup/install-host` once per machine
  (`host_proxy.py` changed), then `make bootstrap`; rebuild nginx-proxy (`make build`, `make release`).

### A failed backup says why

- **The backup error names the dump client's last error line** (`Database backup failed: mysqldump: …`), not only
  "check service/permissions": the statement or table that failed, never row data.

### A failed start no longer leaves a container off its networks

- **`make deploy-images` checks the published ports first** (on its own Docker or over `DEPLOY_SSH`): a port held
  by another process (a host web server, another stack) stops the deploy before anything starts. Started anyway,
  Docker left the container on some of its networks only.
- **It recreates this project's containers that miss one of their networks** (left by such a start; nginx-proxy
  answered `host not found in upstream "webserver"` and later deploys only restarted it, its config unchanged).

### Passwords for the tools on top of their IP allowlist

- **`PMA_HTPASSWD`, `MAILPIT_HTPASSWD`, `CRON_HTPASSWD`, `BACKUP_HTPASSWD`** (htpasswd lines `user:hash`, separated by
  whitespace) add Basic-auth to that tool besides its IP allowlist: a client needs an allowed address and a
  password. Empty keeps the allowlist alone. They are setup's optional Deployment variables when the tool is
  in `SERVICES`. `metrics` and `dbmonitor` keep their bearer token instead, the site its own guard.
- **`PROXY_TOOLS`** names a project's own nginx-proxy sites (`config/nginx-proxy/`); each gets
  `/etc/nginx/setup-access/<name>.conf` from `<NAME>_ALLOWED_IPS` and `<NAME>_HTPASSWD`, closed on stage/prod
  without a list, like setup's tools.

### No pre-deploy backup unless asked for

- **`make deploy` backs up the target database only with `backup=1`**; `BACKUP` in `make pipeline-deploy` and
  the Bitbucket / GitHub templates defaults to `no` (`yes` gives `backup=1`). Choose it for a deploy that changes
  data; the nightly backups of a setup-server worker cover the rest. `backup=0` is no longer needed.

## 2.7.1

### A first deploy starts with writable code and data directories

- **`make deploy-images` creates the containers' code and data directories on `DEPLOY_PATH`** (every bind
  mount inside the code directory or the data directory) as the deploying account before `compose up`. Before,
  Docker created the missing ones as root on a first deploy, while the containers run as `APP_UID`: php-fpm's
  startup hooks could not write the code directory, MySQL could not write its data, and both restarted until
  compose gave up ("container …-php-fpm is unhealthy"). A directory left root-owned by such a deploy stops
  the next one with the `chown` command that fixes it.

### Profiled services start on the target

- **The release model no longer carries `profiles`.** It already holds only the selected services, but
  `compose up` on the target runs without `COMPOSE_PROFILES`, so `SERVICES=cron` and `backup` and a
  project's `COMPOSE_PROFILES` services (graphify) were built and then silently left out of the release.

## 2.7.0

### The backup service can be released

- **The profiles' table registry defaults travel in the PHP image** (`/opt/setup/database-profile`, like the
  runtime). A stage/prod release with `SERVICES=backup` stopped with "backup mounts a setup file
  (…/database-profile); the target has no setup checkout". Local development still mounts the checkout.

### A deploy brings setup up to the version its project needs

- **On a setup-server worker** (`SETUP_SERVER_ROOT` from the runner, the shared checkout of its own account),
  `make pipeline-deploy` no longer stops when the deployed commit needs a newer setup (`SETUP_MIN_VERSION`):
  it returns the build root, checks out the newest release tag of the same MAJOR version that is new enough,
  and starts the step again on it. A new MAJOR version, local changes in the checkout or no fitting tag stop
  the deploy with the reason; a developer's machine is never updated this way.

### The environment files reach DEPLOY_PATH

- **`make deploy-images` and `make deploy` copy the environment repository's mounted files** (`config/`, the
  schema directory, ...: every bind mount from the environment checkout except the code, the data and
  `.generated/`) to `DEPLOY_PATH` when it is not this checkout's project root - another directory or a server
  (`rsync --delete`, over `DEPLOY_SSH` when set). Before, only the code went there, so a target other than the
  build root ran without the project's config. `.generated/` (backups, build state) never leaves.
- Nothing changes for a build root that is its own target (the setup-server default).

## 2.6.0

### The server owns the deploy target (target.env)

- **`target.env` in the build root** (beside `app/`), written by setup-server's `bin/add-project`, names
  where the server deploys: `DEPLOY_CONTEXT`, `DEPLOY_SSH`, `DEPLOY_PATH`, `APP_UID`, `APP_GID`.
  `make pipeline-deploy` appends it to `env/<env>.env`; stage/prod commands (the secret store lookup too)
  read it. `target.<env>.env` beside it sends one environment elsewhere (stage and prod on different
  servers from one build root).
- **The project's `env/<env>.env.example` must not name them** when `target.env` exists: the deploy is
  refused before anything changes. Without `target.env` and without `DEPLOY_PATH` in the example the step
  stops with `No deploy target`.
- **Migrating:** register the target (`sudo bin/add-project …` on the setup-server, setup-server README),
  then remove `DEPLOY_*` and `APP_UID`/`APP_GID` from `env/stage.env.example` / `env/prod.env.example`.

## 2.5.0

### Stage/prod keep their Deployment variables (secrets service)

- **A `secrets` service in every stage/prod model** (`<PROJECT_NAME>-<ENV>-secrets`) lists the
  environment's Deployment variables as references, so each deploy gives it the pipeline's values. It
  only sleeps: no network, no mounts, read-only, 16 MB.
- **Outside a pipeline** (`CI` not `true`) a stage/prod command takes a name its environment lacks from
  that container (through `DEPLOY_CONTEXT` when set): the setup-server nightly backup, "backup now" and
  manual `make` commands on the server need no copy of the secrets. In a pipeline the store is not read.
- **Migrating:** nothing to set; the store appears with the next deploy.

### Metrics and DB monitor tokens are optional

- **The IP allowlist alone guards `/metrics` and the DB monitor:** with `METRICS_TOKEN` or
  `DBMONITOR_TOKEN` empty the endpoint answers every address on `METRICS_ALLOWED_IPS` /
  `DBMONITOR_ALLOWED_IPS` (or `ALLOWED_IPS`) without a bearer token. Stage/prod still refuse to start
  either service without an allowlist.
- **A token that is set works as before** (`401` without it); one shorter than 24 characters answers `503`,
  and stage/prod refuse a short `METRICS_TOKEN`.
- **Deployment variables:** `METRICS_TOKEN` is listed as optional, so `make pipeline-deploy` no longer
  stops without it.

### Database as code is on by default

- **No setting needed:** the schema lives in `app/database/schema` (the registry, migrations and data
  sets beside it), as `SCHEMA_DIRECTORY` was set in Forsena. `make setup` installs the schema commit
  hook, `db-import` stops on an unclassified table and loads apply pending migrations in every project.
- **`DATABASE_AS_CODE=off`** in `env/common.env` turns all of it off (schema commands then stop with an
  error, `make setup` installs no hook, an already installed hook lets commits through with a note).
- **`SCHEMA_DIRECTORY`** now only overrides the default path.
- **`core.hooksPath` naming the repository's own `.git/hooks`** no longer counts as a hook manager:
  the schema hook is installed there as without the setting.
- **Migrating:** a project that has not recorded its schema yet has its commits stopped by the hook
  after the next `make setup` until it runs `make schema-export` and `make schema-resolve` (or sets
  `DATABASE_AS_CODE=off`).

### Newest server backup with make db-pull

- **`make db-pull [from=prod|stage]`** downloads the newest backup the setup-server keeps of the project
  (nightly, backup now or pre-deploy, whichever is newest) over SSH into `.generated/backups` (0600) and
  prints the import command; `import=1` also loads it like `make db-import` (`backup=1` first backs up the
  local DB). A file already downloaded is not fetched again; a cut download is not kept. LIVE is never
  reached.
- Settings: `BACKUP_SSH` (the setup-server account on the VPN, e.g. `setup@10.8.0.14`, in `common.env`),
  `BACKUP_SERVER_PROJECT` (the project's directory on the server; default `PROJECT_NAME`), `BACKUP_SSH_KEY`
  (another private key, in `local.env`).
- The server side is setup-server's `bin/add-backup-key`: the developer's public key works only from the
  VPN network and only for this download (`from=`, `restrict`, a forced command), and every request is
  logged. The backup holds real customer data; the command says so.

### The table registry covers the extra schemas

- A table of `DATABASE_EXTRA_SCHEMAS` is named `schema.table` in `database/table-registry.txt` (`*` works
  in both parts, e.g. `asana_git.*`). A line with a dot is about the extra schemas only, one without about
  the main database only, so `*_log` does not reach `asana_git.commit_log`.
- Every table of every schema must be classified: the commit hook, `make db-import` (and
  `make schema-resolve`) stop on an unclassified one; the backup check and `/metrics` list them as
  `schema.table`.
- `skip` applies to the extra schemas too: `db-import` leaves out their data, and a stage/prod backup
  dumps their structure only, under their own `USE` (restored without dropping the table on the server).
- The schema snapshot, migrations, data set and `db-load` stay on the main database (note 3542 kept
  for those). The backup UI shows one tab per schema.

### Table registry in the browser (`backup` service)

- **`SERVICES="backup"`** serves `https://backup.<DOMAIN>/`, like the cron UI: every table of the project
  database with its rows, size, registry rule and where the rule comes from (project line, profile default,
  none), tables without a line first, with schema-resolve's suggestion; a click on a column header sorts by it. Standard PrestaShop tables that setup's
  profile classifies are not listed and cannot be overridden there (they change in setup, for every project). A plain explanation at
  the top says what each field is for (Group, Dev copy, Live copy) and what every value means; each field has its own column. Local/test set a table's rule, mark
  it `drop`, remove a project line, or accept every suggestion at once; the UI writes
  `app/database/table-registry.txt` (new lines first, so they win over wildcards; `{p}` for PrestaShop
  tables), which the developer commits. Stage/prod only show it, behind `BACKUP_ALLOWED_IPS` / `ALLOWED_IPS`.
  Needs database as code. The `backup.` host name needs `setup/install-host` once, then `make bootstrap`.
- The registry stays mandatory: a table without a line still stops commits, `db-import` and `db-dataset`.
- **Every backup checks the registry** (`make db-backup`, the pre-deploy backup): tables without a line are
  printed and kept in `data/<env>/table-registry/status.json` (names only); a failed check never fails the
  backup. With `metrics`, the new `registry` group exposes `setup_database_unregistered_tables`,
  `setup_database_unregistered_table_info{table}` and `setup_database_registry_check_timestamp_seconds`.

### Env files written in sections

- `create-project`, `make init`, `make bootstrap` and `make test-init` put a key they add under its
  section header (`# Project`, `# Versions`, `# PrestaShop`, `# Services`, `# Address`, `# Access`,
  `# Database`, `# PrestaShop keys`, `# Database as code`, `# Runtime`, `# Tools`), sections in that
  order, instead of appending one long list. Existing lines are still replaced in place; keys setup does
  not know go last. The grouped templates use the same sections. The catalogue is `docker/env_layout.py`.

### PHP 7.2 image builds again

- `php:7.2-fpm` is Debian buster, whose mirrors moved to `archive.debian.org`; the 7.2 Dockerfile now
  switches its sources like 7.0 and 7.1 do.

## 2.4.0

### GitHub Actions as a second CI provider

A project on GitHub deploys the same way as one on Bitbucket: the same `make pipeline-deploy` step, the
same inputs, checks, restore and history. Bitbucket projects need no change.

- **`make pipeline-deploy` runs from a GitHub Actions job** with `environment: staging|production`
  (`GITHUB_ACTIONS=true`; commit `GITHUB_SHA`). GitHub has no variable with the job's environment name,
  so the workflow passes it in `DEPLOY_ENVIRONMENT`. GitHub does not export secrets either: the workflow
  passes `DEPLOY_SECRETS_JSON: ${{ toJSON(secrets) }}` and `DEPLOY_VARIABLES_JSON: ${{ toJSON(vars) }}`,
  and the step sets each name the job's own environment lacks (never `GITHUB_*`, `RUNNER_*`,
  `ACTIONS_*`; a secret wins over a variable of the same name, with a warning). No value is printed and
  the JSON does not reach the `make` steps. Trade-off: the step sees every secret the job can read
  (environment, repository, organisation), as a Bitbucket deploy step sees every variable; the workflow
  only runs by hand (`workflow_dispatch`) with `permissions: contents: read`.
- **`templates/pipelines/github-deploy.yml`**, to be used as `.github/workflows/deploy.yml`: Run
  workflow with `environment` (staging/production), `ref`, `images`, `rollback`, `backup`;
  `runs-on: [self-hosted, linux, setup.build]`; one deploy per environment at a time.
- **`make deploy-variables` checks and fills GitHub environment secrets and variables** when
  `CI_PROVIDER=github` (else a single `DEPLOY_GITHUB_REPOSITORY` / `DEPLOY_BITBUCKET_REPOSITORY`, else
  the origin remote decides). `DEPLOY_PLAIN_VARIABLES` become environment variables, the rest secrets.
  Token: `token=PATH`, `GH_TOKEN` or `GITHUB_TOKEN`, fine-grained with the repository permission
  Environments (Read-only to check, Read and write to apply). Secrets are written with the GitHub CLI
  (`gh secret set --env`, value on stdin), which seals them with the environment's public key; without
  `gh` nothing is written. New settings `CI_PROVIDER`, `DEPLOY_GITHUB_REPOSITORY`.
- **`create-project --ci bitbucket|github`** (also asked; default `bitbucket`) writes
  `bitbucket-pipelines.yml` or `.github/workflows/deploy.yml`, sets `CI_PROVIDER` and prints what the
  provider still needs before the first deploy. Until now a new project got no CI file.

No image changes.

**Moving a project to GitHub Actions** (environment repository):

1. Add `.github/workflows/deploy.yml` from `templates/pipelines/github-deploy.yml`; `CI_PROVIDER=github`
   in `env/common.env` (and `DEPLOY_GITHUB_REPOSITORY` when `origin` is not on github.com).
2. Create the environments `staging` and `production` (Settings → Environments) and fill them:
   `make deploy-variables ENV=prod token=… from=FILE` (needs `gh`).
3. Register a self-hosted runner with the label `setup.build` on the build server and update its setup
   checkout to this release. The build root is `vars.BUILD_ROOT`, else
   `$SETUP_SERVER_ROOT/projects/<repository name in lower case>` (setup-server's layout).

### Cron UI

- The cron UI is a departures board: jobs ordered by their next run (amber flaps with the date and
  "in 12 min"), the last twelve runs as signal lamps, the environment as a station sign. Prod has a red
  sign and rule, and "Run now" asks to confirm there. `CRON_SCHEDULE=off` crosses the board with hazard
  tape. History shows the success rate, average and longest run and a duration bar per run, and after
  "Run now" refreshes itself until the run is recorded; output opens in a numbered terminal pane.
  Self-contained (no fonts, scripts or styles from elsewhere). Routes, access and messages are unchanged.

### LIVE environment probe

- **`tools/live-probe/setup-probe.php`**: one read-only PHP 5.6-8.5 file uploaded to a LIVE server
  for a short time (or run over SSH: `php setup-probe.php --format=md --root=…`). It reports the
  `env/common.env` / `env/prod.env` values, `config/php` and `config/mysql` drop-ins, the project
  Dockerfile extensions, after-import and table-registry drafts and the risks, as `?format=json|env|md`;
  the Markdown report ends with instructions for an LLM. Token (`hash_equals`, no default),
  `REMOTE_ADDR` allowlist and a hard expiry of at most 7 days; SELECT/SHOW only, no shop boot, no
  personal data, secret values never printed; `?action=delete` removes it. Guide (LT):
  `tools/live-probe/README.md`. Its embedded setup tables (`sp_setup_data()`) follow
  `compatibility.json`, `php.ini`, the PHP Dockerfiles and the PrestaShop registry; `tests/test_live_probe.py` fails when they drift.

No image changes; no project needs to change.

### Fixes

- **Schema pre-commit hook in a linked `git worktree`.** The hook is shared by every worktree of the
  repository, but it exported the schema into the main checkout and judged the worktree's commit with
  a mixed-up Git index (every table "not staged", "stage the table registry"). It now keeps the main
  checkout's settings and running DB but exports into, drafts the migration in and compares with the
  committing worktree's schema directory and index (same relative path, same `--git-common-dir`), and
  names that directory in its messages. `schema-check` does the same when a hook manager runs it in a
  worktree; `schema-export` and the post-load schema report keep working on the checkout whose
  settings they load. Commits in the main checkout are unchanged. No project needs to change.

## 2.3.0

### Bitbucket deploy step in setup

Projects stop copying the Bitbucket Pipelines deploy logic: setup runs the step and keeps its lists.
No project has to change; those with a `deploy/pipeline.sh` can switch (below).

- **`make pipeline-deploy [ENV=stage|prod]`** runs a Bitbucket deployment step in the build root
  (`make -C "$BUILD_ROOT/app" pipeline-deploy`): refuses outside a Staging/Production step, checks the
  required Deployment variables before any change, checks out the deployed commit (one repository) or
  the pipeline commit beside the code repository in `app/public` (Forsena's layout), writes
  `env/<env>.env` from its example (plus the runner's `APP_UID`/`APP_GID` for `DEPLOY_CONTEXT=default`),
  runs `IMAGES=yes` → `make release` + `make deploy-images`, then `make deploy` or `ROLLBACK=yes` →
  `make deploy rollback=1`, `BACKUP=no` → `backup=0`, and returns the checkout to the live commit on a
  failure (except after the containers restarted on new images; then it prints the way back). The ref
  variable is `REF`; `APP_REF` and `SHOP_REF` keep working.
- **Deployment variables come from setup's list**: its own secrets for the environment
  (`DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD`, `METRICS_TOKEN` with metrics, the PrestaShop keys of
  `PS_VERSION`; `DATABASE_MONITOR_PASSWORD`/`DBMONITOR_TOKEN` optional), the new settings
  `DEPLOY_REQUIRED_VARIABLES` and `DEPLOY_OPTIONAL_VARIABLES`, and every `APP_ENVIRONMENT` name the env
  files leave to the process. A name an env file leaves blank (other than setup's secrets) is refused:
  it would hide the Deployment variable.
- **`make deploy-variables [ENV=…] [names=1] [from=FILE [replace=1]] [token=PATH]`** compares that list
  with the repository's Bitbucket environment (names and the secured flag only) and creates or replaces
  variables from a private dotenv file. `DEPLOY_PLAIN_VARIABLES` are stored not Secured;
  `DEPLOY_BITBUCKET_REPOSITORY` names the repository when `origin` is not on bitbucket.org.
- **`SETUP_MIN_VERSION`** in `env/common.env`: every runner command stops on an older setup.
- `templates/pipelines/bitbucket-pipelines.yml`: the example pipeline.

No image changes.

**Switching a project** (environment repository):

1. `bitbucket-pipelines.yml`: each deploy step's script becomes
   ```yaml
   script:
     - BUILD_ROOT=${BUILD_ROOT:-${SETUP_SERVER_ROOT:-/srv/setup-server}/projects/$BITBUCKET_REPO_SLUG}
     - make -C "$BUILD_ROOT/app" pipeline-deploy
   ```
   (`unset DOCKER_HOST` is no longer needed for it); rename the `APP_REF`/`SHOP_REF` variable to `REF`
   (optional: the old names still work).
2. `env/common.env`: `DEPLOY_REQUIRED_VARIABLES` / `DEPLOY_OPTIONAL_VARIABLES` /
   `DEPLOY_PLAIN_VARIABLES` with the project's own names, `SETUP_MIN_VERSION` with this release's
   version. Check with `make deploy-variables ENV=prod names=1` that the list matches the Deployment
   variables (an `APP_ENVIRONMENT` name the application defaults itself belongs in `env/common.env`
   with its value).
3. Delete `deploy/pipeline.sh`, `deploy/test-pipeline.sh`, `deploy/deployment-variables.sh`,
   `deploy/test-deployment-variables.sh` and the "From Deployment variables" comment list of
   `env/<env>.env.example`; point the project's documentation to setup `doc/lt/09-aplinkos.md`.
4. Update the build server's setup checkout before the first run (the `pipeline-deploy` target must exist).

### Cron schedule switch

- **`CRON_SCHEDULE=off`** keeps the cron service and its UI but runs nothing on schedule, on any ENV
  (default `on`; other values stop the runner). supercronic does not start; the crontab is still
  checked with `supercronic -test` at start and on UI edits. The UI lists the jobs, shows the schedule
  off instead of the next run, and "Run now" still runs through `cron-run.sh` and records the run.
  With `metrics`, `setup_cron_schedule_enabled` is `0` and no expected run is served, so the
  missing-run alert stays quiet. Apply with `make restart service=cron`. No project needs to change.

## 2.2.0

Application settings reach the containers by name. No project needs to change.

- **`APP_ENVIRONMENT`** in `env/common.env`: space-separated names (never values) of the
  application's own settings. Setup passes each as `NAME: ${NAME-}` to php-fpm and, when enabled,
  cron, on every ENV; values come from the env files or, for names they do not mention, the process
  environment (Bitbucket Deployment variables). The generated overlay holds references only and no
  image is rebuilt. Invalid names and names setup owns (its settings, `DATABASE_*`, `MYSQL_*`, `PS_*`,
  `PHP_*`, `CRON_*`, `ENV`, `PROFILE`, …) stop the runner with the names. A project that still lists
  the variables in its compose files keeps its definitions; the per-service lists can be removed.
- **cron** gets the database connection php-fpm gets (`DATABASE_HOST/PORT/NAME/USER/PASSWORD`) and
  `DOMAIN`, `PROJECT_NAME`, `SETUP_ENV` (so `config/php/*.template` drop-ins may use them). Setup's
  values replace a different cron `DATABASE_*` in `compose/base.yaml`; set one in
  `compose/common.yaml` if cron really needs another database.

No image changes.

## 2.1.0

Servers run without DirectAdmin and generic (non-PrestaShop) projects deploy; both monitoring
endpoints feed the existing Grafana dashboards. No v2.0.0 project needs to change.

- **Generic projects** (empty `PROFILE`) release and deploy: no PrestaShop steps, `.htaccess` deploys as
  code, `DEPLOY_PROTECTED_PATHS`, hooks `config/deploy/build.sh` and `config/deploy/after-rsync.sh`,
  external images (e.g. Meilisearch) in the image lock. Every setup step respects `PROFILE`.
- **Extra database schemas** with `DATABASE_EXTRA_SCHEMAS`: grants, backup, import and grant restriction
  cover all of them; `_` in database names is escaped in every grant.
- **`PUBLISH_ADDRESS`** publishes nginx-proxy on a chosen address (e.g. a VPN interface; default
  127.0.0.1). Forwarded client addresses are trusted only from the real proxy; `TRUSTED_PROXIES` with a
  directly published address needs `TRUSTED_PROXIES_DIRECT=on`. **Check DirectAdmin targets after
  upgrading** (client IPs in allowlists).
- **Target `/metrics`** (`SERVICES=metrics`, token + allowlist): system, website, HTTP, security,
  database, host, containers and cron groups under the names the existing Grafana dashboards use.
- **Deploys** reload php-fpm at the end (`DEPLOY_PHP_RELOAD=off` to skip); rollback skips failed releases.
- **Secrets** from the process environment (pipeline Deployment variables) reach Compose when env
  files leave them blank.
- Stage/prod start without provisioned certificates (nginx-proxy creates a self-signed pair for the
  internal hop); the MySQL first-start hook runs on bash 4.2 (MySQL 5.7 images).

Images change (php-fpm, nginx-proxy, Apache, metrics): run `make build` / `make release` after upgrading.

## 2.0.0

One way to run projects from local development to LIVE. Version 1 projects do not run unchanged:
the last v1-compatible commit is `f9c4da9`.

### What changes for projects

- **Removed**: `docker/Makefile`, `Makefile.local`, `new_host.sh`, `db-init.sh`, the root
  `docker-compose.yml` and root-level project layouts, `templates/project`, `prepare_project.py
  --from-legacy`, and the nginx webserver service. Projects use `env/` + `compose/` only.
- **Services** are switched on with `SERVICES="pma mailpit …"` (per env file), not by including setup
  Compose files. An unknown name is an error. The four core services come from the runner too
  (`DATABASE_ENGINE=mysql|mariadb`); `compose/base.yaml` is optional and holds only project additions.
- **Settings**: per-service limits `<SERVICE>_MEMORY/_CPUS` (old `*_CONTAINER_MEMORY`/`CONTAINER_CPUS`
  still accepted), one `TZ` (default `Europe/Vilnius`), `XDEBUG_MODE`, `ALLOWED_IPS` /
  `<NAME>_ALLOWED_IPS`, `DATABASE_ROOT_PASSWORD` (separate from the application password),
  `PS_VERSION`, PrestaShop keys (`PS_COOKIE_KEY`, …) in the private env file, `PS_RELEASE(_PATHS)`.
- **Stage/prod** refuse to start without the four core limits and without an allowlist for tools.
- **Database**: schema snapshot + table registry + guarded migrations in `database/`; imports skip
  `skip` tables and run safe after-import defaults outside prod; `MAIL_MODE` resolves to Mailpit locally.
- **New commands**: `setup/install-host`, `make setup`, `make image-prune`, `make schema-resolve`,
  `make db-migrate`, `make db-dataset`, `make db-load`, `make test-prepare`, `make phpunit`,
  `make test-sync`, `make release`, `make deploy-images`, `make deploy`, `make module-pull`,
  `make db-monitor-user`, `make db-restrict-grants`; `create-project` asks questions.

### Kept on purpose

- The PrestaShop configuration file is generated at PHP start (only when missing; afterwards only changed
  connection values are written), not in `app-prepare`: stage/prod targets have no `app-prepare` step and
  receive the keys only as container environment. An unchanged env leaves `public/` untouched on restart.
- `make doctrine*` stays for applications that already run Doctrine migrations; guarded SQL migrations
  (`database/migrations`) are the default for shop schema changes.
- `db-fixtures-*` load extra SQL on top of a data set; `make db-load` applies the same fixtures itself.
- `test-init` and `db-prepare` are the single steps `make test-prepare` runs, kept callable alone.
- Mailpit and Memcached are configured through env values (`MEMCACHED_MEMORY`, `MEMCACHED_CACHE_MB`) or a
  `compose/<env>.yaml` `environment:` entry; they read no configuration files, so they have no drop-in
  directory. PMA reads `config/pma/*.php`.

### Migrating a v1 project

1. Update the shared checkout (`master` locally, `v2.0.0` on the build server) and run `setup/install-host`
   once per machine.
2. In the project, keep `env/common.env` and the private `env/<env>.env`; move service selection to
   `SERVICES=`; remove the `include` of setup's core services and the network from `compose/base.yaml`
   (the runner loads them; `DATABASE_ENGINE=mariadb` selects MariaDB) and delete files left empty; keep
   real overrides in `compose/`.
3. Add `PS_VERSION` and `PROFILE=prestashop` for shops. Copy the six PS keys from the current
   `parameters.php` (or the cookie key for 1.6) into `env/local.env`; `make bootstrap` fills missing
   local keys and `DATABASE_ROOT_PASSWORD`.
4. Existing databases: run `make db-restrict-grants` once (root gets its own password, the application
   user keeps rights on its own database only).
5. `make setup` (no sudo after install-host), then `make schema-export`, `make schema-resolve` and
   commit `database/schema`, `database/table-registry.txt`; `make schema-hook-install`.
6. Remove the per-environment after-import copies of domain/mail SQL: setup's safe defaults and
   `MAIL_MODE` cover them; put integration switches in `database/after-import/integrations/<name>.sql`.
7. Servers: fill `APP_UID`, `APP_GID`, `DEPLOY_CONTEXT`, `DEPLOY_SSH`, `DEPLOY_PATH` and the limits in the
   stage/prod env (example files without secrets), then `make release` / `make deploy-images` / `make deploy`.

Forsena is the reference migration (`forsena_docker`, branch `feature/setup-v2-docker-aplinka`,
`doc/v2-acceptance.md`).

## Release procedure

1. Run `python3 -m unittest discover -s tests` and the B tests for the touched areas; verify a fresh
   project and an existing project migration.
2. Update `VERSION`, this file and the guide; record migration steps for anything renamed.
3. The owner tags the tested commit `v<version>`; keep the previous tag and images for rollback.
4. The build server moves its setup checkout to the tag (developer machines follow `master`), then runs
   `make setup-info`, `make doctor` and `make setup` for its projects.
