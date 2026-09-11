# Runtime readiness, application preparation and isolated tests

The project `PROFILE` selects application behavior. `ps` is an alias for
`prestashop`. Other values (including empty) never run PrestaShop configuration
or DB probes. New grouped templates explicitly use an empty profile; choose
`PROFILE=ps` only for a PrestaShop project. Legacy projects can still inherit the
historical profile default from the shared `.env`; set their profile explicitly
when adopting these commands.

## Startup and health

`make up` uses Compose `--wait --wait-timeout 90` and existing images, then runs
smoke checks. Docker healthchecks are infrastructure checks, not proof that an
application request works. `make smoke timeout=120` reruns application probes.
For PS, the probe reads the actual installed configuration and connects using
its DB credentials, then checks HTTP. `make doctor` adds these PS runtime checks
when PHP is running, otherwise reports them as pending.

`SMOKE_URL` configures an HTTP(S) URL for any profile; optional `SMOKE_EXPECT`
is a required literal substring in the first 2 MiB of the response. Without a
URL, PS derives `http://DOMAIN:LOCALHOST_PORT/`; other profiles only check running
services. HTTP must return 2xx. Redirects to a different origin fail so a local
or test check cannot accidentally pass by reaching another shop. No response
body or credentials are logged. TLS verification stays enabled.

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
checks local hostname routing, creates/renews mkcert certificates, and initializes
PhpStorm. Existing credentials, custom settings and shop keys are preserved.
Generated passwords go only to the private mode-0600 env file. The command does
not clone an unknown application, start containers, build images or import data.
Supply the app checkout first, then run `make build` if images are missing and
`make up` to start and verify it.

Prerequisites: Python 3.10+, Docker Compose 2.24.4+, OpenSSL, mkcert and rsync
(for test copies). New default domains use `<project>.localhost`. If a hostname
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
