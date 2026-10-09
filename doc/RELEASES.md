# Shared setup releases

`VERSION` identifies the setup release; `make setup-info` reports version, Git revision and local
modifications. Developer machines run the latest `master` of one shared checkout; the build server
builds stage/prod releases from a release tag. Projects do not pin versions.

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
