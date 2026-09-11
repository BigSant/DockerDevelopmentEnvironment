# Project Docker configuration

This repository contains original project configuration. Dockerfiles, service
definitions and commands are maintained once in the sibling `Projects/setup`
checkout. There are no submodules. `Makefile` and `compose.yaml` are identical
across projects; updating `setup` updates the shared commands for all of them.

Required layout:

```text
Projects/setup/
Projects/<project>/docker/       # this repository
Projects/<project>/app/public/   # application checkout
Projects/<project>/app/config/   # SQL, cron and QA configuration
Projects/<project>/data/         # persistent DB, TLS and cache data
```

1. Keep the public project name and optional version overrides in `.env`.
2. Copy `.env.local.example` to `.env.local` and set the domain, unused ports
   and local DB credentials. For an existing project, use `prepare_project.py
   --from-legacy` to copy its existing settings instead.
3. Supply the application, app/config and host/TLS configuration. Preparing
   these files does not provision the host, clone the app or restore a database.
4. Run `make check`, then `make config`. The latter writes a private, ignored
   `.generated/compose.local.yaml`; it is never used as a configuration source.
5. After reviewing the configuration, use `make build` when images need
   building, and `make up` to start with existing local images. `up` does not
   build or pull. Existing local tags can still change; compare image IDs
   before migrating a running environment.

`make down`, `make ps`, `make logs`, `make phpstan`, `make phpcs` and `make e2e`
use the same shared runner. Select `ENV=stage` or `ENV=prod` with a corresponding
private `.env.stage` / `.env.prod`. Override QA commands with `cmd='...'`.
`make check PROFILES=` explicitly selects core services; runtime commands also
accept `PROFILES=mailpit,pma,cron`. `check` validates every declared service.

Customize service definitions in optional `compose.override.yaml` and
`compose.<environment>.override.yaml`, not in a generated Compose file.
Container configuration belongs in `config/<service>/[<environment>/]`.
Use `SETUP_DIRECTORY=/path/to/setup` if the shared checkout is elsewhere.
The old `app/docker` location is also supported by this same Makefile.

Private env files and generated snapshots must stay out of Git. Keep `.env`
public. Migrating configuration does not move the app, import the DB, change
host ports, or restart containers. The legacy interactive DB import is not
exposed by this wrapper. For an explicit plain SQL import, configure
`POST_IMPORT_SQL_DIRECTORY=database/after-import` relative to the project
root, with `common/` and environment subdirectories. Use
`make db-import-plan file=/path/to/dump.sql` to preview and
`make db-import file=/path/to/dump.sql` to import into the running DB, then run
those SQL files in filename order. Failure stops later files but does not
roll back earlier changes. This never runs on ordinary container startup.

Optional project Compose files can relocate baseline files to `qa/baselines`,
Playwright tests to `qa/playwright`, and schema migrations to `database/doctrine`.
See `setup/doc/PROJECT_TEMPLATES.md` for mounts and the `phpstan-baseline`,
`doctrine` and configurable E2E commands.

To track MySQL table structure, configure a project-relative `SCHEMA_DIRECTORY`
inside the application repository. `make schema-export` writes snapshots;
after review and `git add`, `make schema-check` compares the DB to the Git index.
`make schema-hook-install` explicitly enables that check before commits.
It preserves existing hooks and requires a reachable DB for checks. See
`setup/doc/DATABASE_SCHEMA.md` for repository paths and supported schema objects.
