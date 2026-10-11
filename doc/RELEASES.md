# Shared setup releases

`VERSION` identifies the setup release; `make setup-info` reports version, Git revision and local
modifications. Developer machines run the latest `master` of one shared checkout; the build server
builds stage/prod releases from a release tag. Projects do not pin versions.

## Unreleased

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
  none), tables without a line first, with schema-resolve's suggestion; a click on a column header sorts by it. Local/test set a table's rule, mark
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
