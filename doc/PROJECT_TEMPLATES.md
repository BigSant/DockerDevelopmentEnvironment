# One shared setup, reusable project sources

Use one `Projects/setup` Git checkout on each machine. Every project's Docker
repository contains small original source files and project settings. Shared
Dockerfiles and service logic are neither copied nor registered as submodules.
Updating the shared checkout updates the common behavior for every prepared
project on its next command; running containers change only when explicitly
recreated. Test shared changes on one project before broader use.

## File ownership

| File | Owner and storage |
| --- | --- |
| `setup/docker/docker/*`, `Dockerfile`, `.env` | Shared versioned service definitions/defaults |
| `setup/docker/project.py`, `project.mk` | Shared versioned commands |
| `setup/templates/project/*` | Authoritative original project templates |
| `<project>/docker/Makefile`, `compose.yaml` | Identical versioned bootstrap in every project |
| `<project>/docker/.env` | Versioned public project name and optional overrides |
| `<project>/docker/.env.<env>` | Private per-machine/environment values; ignored |
| `<project>/docker/compose*.override.yaml`, `config/` | Optional versioned project-specific source configuration |
| `<project>/docker/.generated/` | Private rendered output; ignored, never an input |

## Preparing one or many projects

From the shared setup checkout:

```bash
python3 prepare_project.py --check ../shop-one ../shop-two
python3 prepare_project.py ../shop-one ../shop-two
```

The tool preflights all targets, then writes missing files. Repeating it
preserves project settings and is safe after a partially completed run. It
refuses an existing different Makefile, compose.yaml or .gitignore for manual
review. It does not init Git, set remotes, provision hosts or run Docker.
Create or use a separate Bitbucket repo in each `<project>/docker` as needed.

For each new project:

1. Review `.env` (the project name is derived from its root directory).
2. Copy `.env.local.example` to `.env.local`; set actual unused ports, domain
   and local DB credentials. `python3 project_ports.py ../shop-one` can suggest
   a configured-free pair **before** `.env.local` exists. Prepare host port
   assignments sequentially. Example ports of `0` must be replaced.
3. Supply `app/public` and `app/config`, then provision the host/TLS using
   `./new_host.sh shop-one`. Because the directory already exists, the host
   script preserves the prepared files rather than creating the legacy tree.
4. Run `make -C ../shop-one/docker check` and `make -C ../shop-one/docker config`.
5. Review the snapshot locally. Build images explicitly with `make build` when
   needed, then use `make up` to start with local images. Building shared image
   tags can affect other projects at their next recreation; `up` itself never
   builds or pulls.

Project root names must use lowercase letters, digits, `_` and `-`. Standard
checkouts live beside `setup`; for another location pass
`SETUP_DIRECTORY=/absolute/path/to/setup` to Make. The bootstrap supports both
`<project>/docker` and `<project>/app/docker`; `PROJECT_DIRECTORY=...` explicitly
selects the project root for unusual layouts.

## Preparing an existing project for migration

```bash
python3 prepare_project.py --from-legacy --check ../forsena
python3 prepare_project.py --from-legacy ../forsena
make -C ../forsena/docker check
make -C ../forsena/docker config
```

This copies existing environment settings and config files without removing
`app/docker`. Private environment copies get mode 0600. Old generated
`docker-compose.yml` is not copied. Review that snapshot for any manual changes
and encode actual exceptions as original `compose*.override.yaml` sources.
Legacy custom Dockerfiles and environment overlays stop preparation for a
review of the full build context/paths. No application files or DB data move.

Before switching, compare old and new normalized Compose models. Expected
differences are the container config source paths. Keep the project name,
ports, app/DB/TLS mounts and images stable. Check actual image IDs as well as
tags. New `make up` may recreate containers to apply changed config mounts;
preparing or rendering files alone does not migrate a running stack.

Keep the old source directory and image IDs available for rollback until
functional checks pass. Both environment files can coexist while their port
pairs agree; then keep one active configuration, removing the old copy or
replacing the old entry point with a forwarding wrapper. Do not operate two
independent stacks against the same DB directory.

## Commands and validation

For projects with more configuration, the runner supports this grouped layout
without changing the shared Makefile:

```text
docker/
  Dockerfile                   # optional project-specific image extension
  Makefile
  env/common.env               # public settings, e.g. PHP_VERSION
  env/local.env                # private local settings
  env/prod.env                 # private production settings
  compose/base.yaml            # includes shared setup
  compose/common.yaml          # optional common project overrides
  compose/redis.yaml           # example extra service
  compose/local.yaml           # optional local-only overrides
  compose/prod.yaml            # optional prod-only overrides
```

Set `PROJECT_COMPOSE_FILES=compose/redis.yaml` in `common.env` to load the
extra service. Multiple paths are separated by spaces (quote paths containing
spaces). Paths are relative to the project Docker directory and must stay
within it. Merge order: base → common → listed components → selected
environment. Local and prod overrides are never loaded together automatically.
Env order: shared defaults → common.env → selected environment env.

`make ENV=local check` and `make ENV=prod check` validate each variant.
Generated snapshots remain under `.generated/`. Keep real local/prod env files
out of Git, with fictional `.env.example` equivalents for distribution.
Root-level `.env` and `compose.yaml` must be removed from the active config
directory when adopting grouped sources; the runner rejects ambiguous mixes.
`prepare_project.py` continues to create the existing root-level template.

`make check`, `config`, `build`, `up`, `down`, `ps`, `logs`, `phpstan`, `phpcs`,
`phpstan-baseline`, `e2e`, `doctrine`, `db-import-plan` and `db-import` are
defined once in the shared project.mk. `ENV=local|stage|prod`
selects `.env.<env>` or `env/<env>.env` according to the source layout.
`PROFILES=` explicitly means core-only, while absent
`PROFILES` uses the project's setting or the shared environment default.
`check` validates all declared profiles. Use `cmd='...'` to override a QA
command; it is parsed as arguments rather than executed by a host shell.

## Larger projects: application, QA and schema sources

The shared paths can be overridden in project Compose files to group sources:

```text
app/public/                      # application checkout mounted at /var/www/html
docker/config/php/               # common .ini and local/prod .ini subdirectories
docker/config/redis/              # Redis config and environment includes
qa/baselines/                    # reviewed, versioned analysis baselines
qa/phpstan/                      # phpstan.neon including ../baselines/phpstan.neon
qa/php-cs/                       # .php-cs-fixer.php
qa/playwright/tests/             # test sources; config in qa/playwright/
database/doctrine/versions/      # versioned schema migrations
database/sql/after-import/       # common/, local/, prod/ SQL hooks
data/<environment>/              # ignored caches, reports and persistent data
```

This grouping is opt-in; preparation retains existing default mount paths.
Override volumes by container target: `/tmp/phpstan/config`,
`/tmp/phpstan/baselines`, `/tmp/phpstan/cache`, `/tmp/php-cs-fixer/config`,
`/tmp/php-cs-fixer/cache`, `/e2e/config`, `/e2e/tests` and `/e2e/data`.
Precreate writable host cache directories as the user running QA containers.
PHP settings already load from `docker/config/php` plus its selected environment
subdirectory. Redis is project-defined and must explicitly mount its config.

Set `PHPSTAN_BASELINE_FILE=/tmp/phpstan/baselines/phpstan.neon` to enable
`make phpstan-baseline`; mount that folder writable and version its reviewed
output. Set `PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs`
to select a mounted Playwright config. Cache/report files are not baselines.
See [PHPStan baselines](https://phpstan.org/user-guide/baseline) and
[Playwright configuration](https://playwright.dev/docs/test-configuration).

An explicit `compose/doctrine.yaml` can extend the shared
`docker/php-doctrine-migrations/docker-compose.yml` service. Add the `doctrine`
profile, the `doctrine-migrations` entrypoint with `--configuration` and
`--db-configuration`, and mount `database/doctrine` at the configured CLI paths.
`make doctrine cmd=status` reads migration state; `cmd=migrate` applies schema
changes. Build its image explicitly first and start the DB separately.
The base Compose does not enable this optional service. ORM `diff` additionally
needs an application-specific schema provider; migrations alone do not provide
ORM mappings. See [Doctrine Migrations configuration](https://www.doctrine-project.org/projects/doctrine-migrations/en/3.9/reference/configuration.html).

Set `POST_IMPORT_SQL_DIRECTORY=database/sql/after-import` to use:

```bash
make ENV=local db-import-plan file=../data/dumps/shop.sql
make ENV=local db-import file=../data/dumps/shop.sql
```

The second command streams the dump into the running `database` service, then
sorted `common/*.sql`, then sorted `local/*.sql` (or the selected environment).
Any failure stops later files; already executed SQL is not rolled back.
Only explicit `db-import` invokes these hooks, not container startup or other
import tools. The command does not create/drop the DB itself or run Doctrine
migrations. Dump files should stay outside Git. Only plain `.sql` is supported.
Hooks may run again on a later import, so prefer statements safe to repeat.

## Automated validation

Real Compose validation tests run without starting/building any container:

```bash
python3 -m unittest discover -s tests -v
```

The tests cover both layouts, literal/multiline dotenv values, included-service
overrides, explicit empty profiles, concurrent project/same-project renders,
unchanged original files, retry behavior and port reservations across layouts.
The new runner does not use the shared `/tmp` files employed by the legacy
Makefile. Legacy commands and host provisioning still need sequential use.
