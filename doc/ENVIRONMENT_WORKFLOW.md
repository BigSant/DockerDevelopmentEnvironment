# Runtime readiness, application preparation and isolated tests

The project `PROFILE` selects application behavior. Use `PROFILE=prestashop`
for PrestaShop integration. Other application profiles (including empty) never
run PrestaShop configuration or DB probes. The old `ps` spelling remains accepted
for existing configurations. Generic PHP is the shared default. Choose `PROFILE=prestashop` explicitly
for a PrestaShop project.

## Startup and health

`make up` uses Compose `--wait --wait-timeout 90` and existing images. It waits for
Docker healthchecks without making application HTTP requests. Check the website
in your browser or use the project's own tests. `make doctor` additionally checks
PrestaShop configuration and DB credentials when that profile is selected and PHP
is running. There is no shared smoke command or URL/expected-text configuration.

See [Compose startup readiness](https://docs.docker.com/reference/cli/docker/compose/up/).

## Per-project application configuration

The shared PHP service mounts `docker/runtime` read-only and invokes `start.sh`
before PHP-FPM. It uses the selected profile, then runs project
`config/startup/*.sh` hooks in filename order. A failing hook prevents PHP-FPM
from starting. Hooks are explicit project code and receive container env; pass
additional values through the project's Compose `environment`. Other applications
can use these hooks to prepare their own files without any PS assumptions.

PS config detection chooses the existing installed format:

- PS 1.6 uses `config/settings.inc.php`, with `_DB_SERVER_`, `_DB_NAME_`,
  `_DB_USER_` and `_DB_PASSWD_` constants. The updater edits their values using
  PHP tokens; unrelated definitions, comments, prefix and encryption keys survive.
- Newer installations use `app/config/parameters.php`. The updater merges the
  existing `parameters` array and preserves unrelated keys. Symfony percent
  escaping is applied to managed string values.
- The shop's config must exist. Installation keys are never invented. Early
  YAML-only installations require conversion to `parameters.php` using their
  application tooling before adopting automatic preparation.

Optional project files return an array of additional settings:

```text
config/prestashop/parameters.override.php   # modern parameter names
config/prestashop/settings.override.php     # legacy constant names
```

```php
<?php
return ['feature_enabled' => true, 'integration_url' => getenv('INTEGRATION_URL')];
```

For PS 1.6, use constants such as `return array('_CUSTOM_FEATURE_' => true);`.
The DB connection keys are reserved for env settings, preventing a shared override
from bypassing test database isolation. Other parameters/constants may be replaced
or added. Modern dev/prod cache and legacy class/Smarty caches are invalidated
only if configuration changes. Legacy cache protection files are retained.

The format distinction follows [PrestaShop's configuration paths](https://devdocs.prestashop-project.org/8/basics/keeping-up-to-date/backup/).

## Local bootstrap

`make bootstrap` initializes a missing private env from its example, replaces only
known example placeholders, selects unused ports, prepares mount directories,
prepares local DNS and host Nginx routing, creates/renews mkcert certificates, and initializes
PhpStorm. Existing credentials, custom settings and shop keys are preserved.
Generated passwords go only to the private mode-0600 env file. The command does
not clone an unknown application, start containers, build images or import data.
Supply the app checkout first, then run `make build` if images are missing and
`make up` to start it.

Prerequisites: Python 3.10+, Docker Compose 2.24.4+, OpenSSL, mkcert and rsync
(for test copies). New default domains use `<project>.local`. If a hostname
needs an `/etc/hosts` entry, a small validated helper attempts `sudo -n`; it never
prompts or rewrites existing entries. If privileges are unavailable it prints
the exact one-time helper command. mkcert may require `mkcert -install` once to
trust its local CA. Bootstrap is limited to local/test environments.

IDE's private Compose snapshot is refreshed after successful Make runner commands
when `.idea/php.xml` exists, including env/source changes applied with `make up`.
The shared IDE startup task still refreshes on project opening.

## Isolated test environment

```sh
make test-init
make db-prepare ENV=test
# Load a suitable dump and then test fixtures explicitly:
make db-import ENV=test file=/path/to/dump.sql.gz db-fixtures=test
make up ENV=test
```

`test-init` creates a private `env/test.env` with independent credentials and ports,
`.generated/test/app` with an independent application copy, and
`.generated/test/data` for DB/TLS/cache. It excludes Git metadata, node_modules,
application caches and logs from the copy. Unsafe external symlinks are not copied.
The local checkout, private env and DB directory are preserved. The test stack
uses local images/build stages but different container/network names. `ENV=test`
refuses app/data overrides outside `.generated/test`. Stage/prod defaults also
use separate `app/<env>/public` and `data/<env>` paths; existing deployments must
explicitly review their paths before adopting this release.

The resolved Compose model is checked too: writable bind mounts must stay under
`.generated/test`, resource names must use the test project prefix, and external
networks/volumes are rejected. Mount shared QA configuration and test sources
read-only in `compose/test.yaml`; send writable reports/caches to
`${PROJECT_DATA_DIRECTORY}`. These checks also apply when enabling optional QA
profiles. PS must connect to the test stack's own `database` service.

The copy is a snapshot. `make test-init refresh=1` stops only the test stack and
refreshes its code from local while preserving its DB. A plain rerun preserves
the existing test checkout. Provision any required test-specific external-service
settings explicitly; arbitrary local API credentials are not copied to test.env.

An empty test DB cannot pass a shop HTTP check until seeded. To bootstrap its DB
before the first shop startup, use `make db-prepare ENV=test` (starts only the DB),
import the dump, then `make up ENV=test`. `SQL_DOMAIN` can specify a canonical
hostname with port for after-import `${DOMAIN}` substitutions; test-init sets it
from the test URL. Put test-specific after-import fixes in
`database/after-import/test/`; `test` fixtures are still independent of `ENV`.

Fixtures do not erase previous data automatically. Use repeatable SQL or a known
dump to reset test state. `make down ENV=test` removes test containers, preserving
its data. No automatic command deletes the local database or shared application.

## Independent cache and runtime policies

`CACHE_MODE=auto|off|on` is independent of environment identity and optional service profiles. Local/test default to cache off; stage/prod default to on. `MAIL_MODE=auto` disables standard PS mail outside prod and preserves existing prod settings. Other frameworks need their project adapter for application cache/mail. See the [complete runtime guide](lt/12-cache-ir-hibridines-aplinkos.md) for overrides, Redis, preflight, resource limits and new daily commands.
