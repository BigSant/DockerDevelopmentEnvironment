# Architecture & Structure (LLM reference)

> **Audience:** an LLM or engineer maintaining this repository. It describes *how the
> system works* and *where everything lives*, so changes can be made safely.
>
> ⚠️ **MAINTENANCE RULE — read before editing.** This document is the source of truth for
> the Docker logic. **Whenever you change that logic — any `Dockerfile`, `docker-compose.yml`,
> `Makefile`, the profile system, the config cascade, `new_host.sh`, or service selection —
> update this document in the *same* change.** Add new files under `doc/` if a topic grows
> too large for this one. Stale docs are worse than no docs.

---

## 1. What this repository is

A generator and orchestrator for **per-project local (and prod/stage) PHP environments**,
optimised for **PrestaShop** and **Akeneo** projects. One central `setup/` repository serves
*every* project on the machine: each project gets an isolated multi-container stack (its own
DB, TLS, cron, mail catcher, QA tooling), wired up by a thin per-project `Makefile` that
points back here.

## 2. Two-repository model

| Location | Role |
|---|---|
| `setup/` (this repo) | Central definitions: Dockerfiles, base `docker-compose.yml`, `.env` defaults, `Makefile`, profiles, `new_host.sh`. |
| `~/Projects/<domain>/` | A generated project. Contains the app code, data, and a thin `app/docker/Makefile` (a copy of `Makefile.local`) that delegates to the central `Makefile`. |

### Central repo layout (`setup/`)
```
new_host.sh                 Host provisioning (ports, dirs, mkcert, host nginx, /etc/hosts)
index_counter               (obsolete — ports are now allocated by scanning projects' .env.local)
doc/                        This documentation
docker/
  docker-compose.yml        Base compose: includes every service, defines the network
  .env                      Default versions + tuning + COMPOSE_PROFILES default
  Dockerfile                Per-project php-fpm image (FROM php-fpm-base-…) + PROFILE overlay
  Makefile                  Orchestration (env merge, compose merge, build, up/down, db-init)
  Makefile.local            Copied into each project as app/docker/Makefile (thin wrapper)
  qt                        yq v4 binary (renamed) — used to deep-merge compose files
  docker/<service>/         Per-service docker-compose.yml + Dockerfile + conf/
    php-fpm/<version>/       Per-PHP-version base Dockerfile + php.ini/php-fpm.conf/xdebug.ini
  profile/<profile>/<service>/conf/   Profile-specific config (prestashop, akeneo)
  config/                   Central defaults: nginx/, sql/, phpstan/, php-cs/
```

### Per-project layout (`~/Projects/<domain>/`)
```
app/
  public/                  Web root (mounted into php-fpm/apache as /var/www/html)
  config/                  App/tooling config: phpstan/, php-cs/, playwright/, cron/
  docker/
    Makefile               Thin wrapper → central Makefile
    .env                   PROJECT_NAME
    .env.local             DOMAIN, ports, DB creds, optional COMPOSE_PROFILES override
    config/<service>/      Per-project CONTAINER config overrides (see §8)
      <service>/<env>/     ENV-specific container config overrides
data/                      Persistent: mysql/, ssl/, mailpit/, phpstan/, php-cs/, profiling/
backup/<date>/
```

> **Distinction:** `app/config/` = *application/tooling* config (phpstan, php-cs, playwright,
> cron jobs). `app/docker/config/` = *container/infrastructure* config overrides (mysql, php,
> apache, nginx-proxy). Keep them separate.

## 3. The two-step flow

1. **`./new_host.sh <domain>`** (host-level, needs `sudo`; run as a normal user):
   allocates a free localhost port pair (base `3000`; the lowest free pair is found by
   scanning existing projects' `.env.local`, so ports of removed projects are reclaimed —
   no monotonic counter). An existing project keeps its current pair. Then creates the
   project directory skeleton, generates an mkcert cert for `<domain>.local` + `*.<domain>.local`,
   writes a **host nginx** reverse-proxy vhost (`/etc/nginx/sites-available/…`) pointing
   `<domain>.local`, `pma.…`, `mailpit.…` at `127.0.0.1:<port>`, and adds `/etc/hosts` entries.
2. **`make up`** in `app/docker/` → delegates to the central `Makefile`:
   `config-local` → `build-local` → `up-local`.

## 4. Request path

```
Browser
  → HOST nginx        (:300X, TLS via mkcert)          [managed by new_host.sh, host-level]
    → nginx-proxy     (container, only one with published ports, terminates TLS again)
      → apache         (webserver, serves static + proxies PHP via FastCGI)
        → php-fpm:9000 (FastCGI)
```
For prod/stage there is no host nginx; `nginx-proxy` is the public entry. The double layer
keeps **local ≈ prod parity** with one compose stack.

## 5. Image model (critical)

Most images are **shared across projects**, versioned by tag — NOT per project:
- `nginx-proxy-${ENV}-${NGINX_VERSION}`, `webserver-apache-${ENV}-${APACHE_VERSION}`,
  `mysql-${ENV}-${MYSQL_VERSION}`, `pma-${ENV}-${PMA_VERSION}`, `mailpit-${ENV}-${MAILPIT_VERSION}`,
  `php-phpstan-…`, `php-cs-…`, `playwright-…`.
- `php-fpm-base-${ENV}-${PHP_VERSION}-${XDEBUG_VERSION}-${COMPOSER_VERSION}` — the shared PHP
  base, built **once per machine**, reused by every project. **It MUST stay profile-agnostic**
  (its tag has no project/profile) — never bake profile- or project-specific config into it.

Only **`php-fpm`** is per-project: image `${PROJECT_NAME}-php-fpm-${ENV}`, built from the
top-level `docker/Dockerfile` (`FROM php-fpm-base-…`). **Project- and profile-specific PHP
layers belong here**, not in the base.

> **Consequence / gotcha:** because mysql/apache/etc. images are shared by version, baking a
> PROFILE into them works cleanly only when all projects share the same profile (the common
> all-PrestaShop case). Profile divergence across projects on the same versions would conflict
> on the shared image. PHP avoids this (per-project image). Per-project *runtime* config (§8)
> is always isolated regardless.

### Process user & file ownership
`php-fpm`, `cron`, and `mysql` run as the **host user** — `user: "${HOST_UID:-1000}:${HOST_GID:-1000}"`
in compose, with `HOST_UID`/`HOST_GID` injected by the Makefile from `id -u`/`id -g`. (`mysql` uses
`user: "${DATABASE_UID:-${HOST_UID:-1000}}:${DATABASE_GID:-${HOST_GID:-1000}}"` — the same host-user
default, but a prod/stage deploy can pin a fixed DB service account by setting `DATABASE_UID`/`DATABASE_GID`
in the project `.env.${ENV}`; those are project vars, so they win over the Makefile-injected
`HOST_UID`/`HOST_GID`.) So files
php-fpm/cron write to the bind-mounted web dir (cache, logs, uploads, generated files) are
owned by the host user, **not root**. The php-fpm base image therefore carries **no** pool
`user`/`group` directive (php-fpm runs workers as the launching uid) and no `usermod -u 0`
remap. `apache` and `nginx-proxy` keep a root master (needed to bind :80/:443) but never write
to the project bind mounts — only php-fpm/cron do — so they create no root-owned project files.
> Migration note: a project whose `app/public` already contains root-owned files from an older
> root-based image may need a one-time `sudo chown -R $(id -u):$(id -g) app/public data`.

## 6. Build orchestration (`docker/Makefile`)

- **`generate-env-file`** builds `/tmp/.env`: root `.env` ← project `.env` ← project
  `.env.${env}`, merged with `sort -u -t= -k1,1` (project entries listed first → they win),
  then appends computed paths (`ROOT_DIRECTORY`, `PROJECT_*`, `ENV`, `DOCKERFILE_DIRECTORY`).
- **`generate-compose-yml`** copies the base `docker-compose.yml` to `/tmp`, then deep-merges
  (`qt '. *= load(…)'`) the project's `docker-compose.yml` and `docker-compose.${env}.yml`
  if present. `qt` is yq v4 (auto-downloaded by `load-qt`).
- **`build-docker-compose`**: builds `php-fpm-base` first (`COMPOSE_PROFILES=build_only`),
  then `docker compose build` for the selected runtime profiles.
- **Build hygiene & hardening defaults:** each per-version php-fpm `Dockerfile` installs apt packages
  in a single `RUN apt-get update && apt-get install -y --no-install-recommends … && rm -rf /var/lib/apt/lists/*`
  layer (smaller image, better cache), and every fetched binary is checksum-verified — supercronic
  (`sha1sum -c`), nvm `install.sh` (`sha256sum -c`, pinned tag), and `qt`/yq in the Makefile (`sha256sum -c`).
  Keep this when adding packages or bumping versions. (5.6 keeps apt commented out, and 8.2 still carries
  a malformed yarn `RUN` — both pre-existing, left untouched.) `nginx-proxy`/`nginx` set `server_tokens off`
  so the version is not leaked in the `Server` header.
- **Version pinning (reproducible rebuilds):** base images and baked tools are pinned to exact versions
  via `docker/.env`, not floating tags. Pinned to the currently-installed versions: `APCU_VERSION`
  (`pecl install apcu-${APCU_VERSION}`, 8.x only), `NODE_VERSION` (exact patch via `nvm install`),
  `PHPSTAN_PHAR_VERSION` (the `phpstan.phar` baked into every php-fpm image — was the floating `:1` tag),
  and `PHP_PHPSTAN_VERSION`/`PHP_CS_VERSION` (exact, were `2-php8.1`/`3-php8.1`). `APCU_VERSION` and
  `PHPSTAN_PHAR_VERSION` are declared as `ARG` in **every** per-version php-fpm Dockerfile (consumed by the
  8.x apcu install / the pre-`FROM` phpstan COPY stage) and passed through the `php-fpm-base` compose
  `args:`. Known gaps, intentionally left:
  1. `php:${PHP_VERSION}-fpm` stays a **minor** tag on purpose — it keeps receiving PHP/Debian security
     patches on rebuild; pin the exact patch (or a `@sha256` digest) only if bit-for-bit reproducibility is
     required (it would also freeze security fixes).
  2. `npm install -g webpack webpack-cli` does **not** persist under the build's `/bin/sh` (it works under
     `bash`), so webpack is effectively absent from the image — pre-existing; fix the RUN shell before
     relying on webpack, then pin it.
  3. the baked `phpstan.phar` is 1.x while the `php-phpstan` service is 2.x — unify when convenient.
  4. `php-doctrine-migrations` (disabled service) still uses `^${DOCTRINE_MIGRATIONS_VERSION}` + an
     unconstrained `doctrine/dbal` — pin when the service is activated.
- **`run-docker-compose`** resolves `COMPOSE_PROFILES`: uses the `profile=` make-var when set
  (e.g. `build_only`, or `ALL_PROFILES` for the config snapshot), otherwise reads
  `COMPOSE_PROFILES` from the merged `/tmp/.env` (the project's selection). It also exports
  `BUILDX_NO_DEFAULT_ATTESTATIONS=1` (skips the attestation manifest) **and**
  `BUILDX_METADATA_PROVENANCE=disabled` (skips "resolving provenance for metadata file").
  Both are needed: that provenance/attestation work is slow and can hang on
  `docker compose build`, and adds no value for local images.
- **`db-init`** loads a SQL dump as the app user and runs `config/sql/init.sql` +
  `init.${env}.sql` via `envsubst`. The central `config/sql/` ships defaults: `init.local.sql`
  (disables SSL, points mail at the local catcher) and `init.prod.sql` (enforces `PS_SSL_ENABLED*`,
  leaves the dump's real mail config intact). ⚠️ These templates use a `%{DOMAIN}` placeholder that
  `envsubst` (which expands `${VAR}`) does **not** substitute — a pre-existing convention; a project's
  own `app/config/sql/` files must use `${DOMAIN}` if they rely on the envsubst pass.
- **Startup ordering:** `database` (probe: `mysqladmin ping -h 127.0.0.1`) and `php-fpm`
  (probe: `php` opening the FastCGI port) have healthchecks; dependents (`webserver`, `cron`,
  `pma`, `php-phpstan`, `php-cs`) gate on them via `depends_on: condition: service_healthy`.
  `nginx-proxy` and `playwright` depend on `webserver` order-only (apache has no healthcheck —
  it binds :80 as root and needs no readiness gate beyond php-fpm).

## 7. Multi-environment & profiles (build-time)

- **ENV** (`local|prod|stage`): every Dockerfile has `FROM base as env-local|env-prod|env-stage`;
  compose selects `target: env-${ENV}`. Only `env-local` adds Xdebug. Xdebug uses
  `start_with_request=trigger` (xdebug 3) / `remote_autostart=0` (xdebug 2), **not** `yes`:
  it activates only on an explicit trigger — CLI: `XDEBUG_TRIGGER=1`; web: an Xdebug-helper
  browser extension or `XDEBUG_SESSION` cookie — not automatically on every request. This is
  **required**: with auto-start, any build-time `php`/`composer` step (e.g. a project Dockerfile
  that installs a PHP extension, like joldija's gd) hangs trying to reach
  `host.docker.internal:9003`, which is unreachable during `docker build`.
- **PROFILE** (`prestashop|akeneo|""`, default `prestashop` in `.env`): wired into **mysql,
  apache, php-fpm** at build time. Mechanism (identical pattern in each):
  `COPY /profile/ /tmp/profile/`, then apply only files that exist for the selected
  PROFILE / ENV (`[ -s ]` guard), into the service's config dir with `zz-`/`zzz-` prefixes
  so they override the base config. `PROFILE=""` and missing files are no-ops.
  - mysql: `profile/<p>/mysql/conf/my.cnf` → `conf.d/zz-profile.cnf`; `my.${ENV}.cnf` → `zzz-profile-env.cnf`
  - apache: `profile/<p>/apache/conf/httpd.custom.conf` → `conf/profile/` (via `IncludeOptional`)
  - php-fpm: `profile/<p>/php-fpm/conf/php.ini` → `conf.d/zz-profile.ini`; `php.${ENV}.ini` → `zzz-profile-env.ini`; optional `pool.conf` → `php-fpm.d/zz-profile.conf`
  - Profile content is currently documented/commented (zero behaviour change until enabled).
  - `nginx` (the commented-out alternative webserver) uses the older `config/nginx/${PROFILE}/`
    location; the active stack uses apache.

## 8. Per-project config override + ENV layering (runtime)

Each file-config service mounts a per-project config dir at runtime from
`${PROJECT_DOCKER_DIRECTORY}/config/<service>/`, loaded **after** base+profile so the project
wins. **No rebuild, isolated to that project.** Two levels:
- `config/<service>/*`        → always loaded
- `config/<service>/${ENV}/*` → loaded only when ENV matches (mounted via `${ENV}` interpolation)

| Service | host base / env dir | container target(s) | load mechanism |
|---|---|---|---|
| mysql | `config/mysql/`, `…/${ENV}/` | `/etc/mysql/project.d[.env]/` | `!includedir` (appended to `/etc/my.cnf` after `conf.d`) |
| php-fpm, cron | `config/php/`, `…/${ENV}/` | `/usr/local/etc/php/project.d[.env]/` | `PHP_INI_SCAN_DIR=":…/project.d:…/project.d.env"` |
| apache | `config/apache/`, `…/${ENV}/` | `…/conf/project[.env]/` | `IncludeOptional` (after the profile include) |
| nginx-proxy | `config/nginx-proxy/`, `…/${ENV}/` | `/etc/nginx/project.d[.env]/` | `include` in `nginx.conf` http{} (after `conf.d`) |

`pma` and `mailpit` have no file-include config system; they are configured by **env vars**
and overridden per project (and per env) through `.env` / `.env.${ENV}` — the same layering
principle, applied to environment variables rather than mounted files.

### Full config cascade (lowest → highest precedence)
```
image base config  <  profile  <  profile-env  <  project base  <  project-env
```

## 9. Service selection (`COMPOSE_PROFILES`)

Optional services carry a compose `profiles:` tag (`phpcs`, `phpstan`, `mailpit`, `pma`,
`cron`, `playwright`). Core services (`nginx-proxy`, `webserver`/apache, `php-fpm`,
`database`/mysql) have **no** profile → always run.

- Default in `docker/.env`: `COMPOSE_PROFILES=mailpit,pma,cron`.
- A project overrides it in `app/docker/.env.local`, e.g. `COMPOSE_PROFILES=phpcs,playwright`
  (or empty for core-only). The env merge makes the project value win.
- `ALL_PROFILES` (in the Makefile) lists every profile and is used only when rendering the
  full project compose snapshot (`config-docker-compose`), so the snapshot stays complete.

## 10. Services catalog

| Service (compose name) | Role | Image | Profile | Project config |
|---|---|---|---|---|
| `nginx-proxy` | TLS termination, public entry (only published ports) | shared | core | `config/nginx-proxy/` |
| `webserver` (apache) | static + FastCGI → php-fpm | shared | core | `config/apache/` |
| `database` (mysql) | MySQL 8.4, data in `data/mysql` | shared | core | `config/mysql/` |
| `php-fpm` | app runtime (+Xdebug in local) | **per-project** | core | `config/php/` |
| `cron` | supercronic over project crontab | reuses php-fpm image | `cron` | `config/php/` + `app/config/cron` |
| `pma` | phpMyAdmin | shared | `pma` | env vars |
| `mailpit` | mail catcher | shared | `mailpit` | env vars |
| `php-phpstan` | static analysis (idle, exec on demand) | shared | `phpstan` | `app/config/phpstan` (seeded default) |
| `php-cs` | PHP-CS-Fixer (idle) | shared | `phpcs` | `app/config/php-cs` (seeded default) |
| `playwright` | E2E tests (idle) | shared | `playwright` | `app/config/playwright` |

Commented-out in the base compose: `php-doctrine-migrations`, `mariadb`, `nginx`.
`mariadb` is kept at **full parity with `mysql`** (same profile wiring, per-project config
cascade under `config/mariadb/`, deterministic `01-init-app-user.sh`, host-user, healthcheck
via `mariadb-admin ping`) — both define service `database`, so switching DB engine is just a
matter of which include is active. MariaDB uses `IDENTIFIED BY` (mysql_native_password), not
`caching_sha2_password`, and its config root is `/etc/mysql/my.cnf`.

### QA tool default-config seeding
`php-phpstan` and `php-cs` bake a default config (from `docker/config/phpstan/phpstan.neon`
and `docker/config/php-cs/.php-cs-fixer.php`) into `/opt/…`. Their entrypoint copies it into
the project's mounted config dir on first run **only if absent**, so the project then owns and
may edit it.

## 11. MySQL initialisation

A single deterministic init script `docker/mysql/conf/init.sh` → `01-init-app-user.sh`. It
runs SQL directly against the init server (`set -euo pipefail`, fails loudly) to create the
app user `@localhost` + `@%` (`caching_sha2_password`) with `ALL PRIVILEGES ON *.* WITH GRANT
OPTION`. No self-modifying init files. (Historical `grant.sql` / `initialize_exporter.sql`
and the unused `MYSQLD_EXPORTER_PASSWORD` were removed — there is no mysqld_exporter service.
If DB metrics are ever needed, add a real `mysqld-exporter` service and inject its credentials
via env at that point; do not commit a password.)

## 12. Invariants — do not break these

1. `php-fpm-base` image must remain **profile- and project-agnostic** (shared by version).
2. Profile config goes in `profile/<profile>/<service>/conf/`; selected at **build** time with
   the `[ -s ]`-guarded `COPY /profile/` + conditional copy pattern. `PROFILE=""` must be a no-op.
3. Per-project runtime overrides go in `app/docker/config/<service>/[<env>/]` and must load
   **after** base+profile (`zz`/`project.d`/`IncludeOptional`-after-profile ordering).
4. Core services carry **no** `profiles:` tag; optional ones do.
5. `new_host.sh` creates `app/docker/config/{mysql,mariadb,php,apache,nginx-proxy}/local/` —
   keep this list in sync with the services that have a project config mount.
6. The mysql/php-fpm/apache compose files already pass `PROFILE`/`ENV` build-args; the
   Dockerfiles must declare the matching `ARG`.
7. `php-fpm`/`cron`/`mysql` run as the host user via compose `user:` (`HOST_UID`/`HOST_GID`
   from the Makefile; `mysql` allows a `DATABASE_UID`/`DATABASE_GID` override for a fixed prod/stage
   service account — see §5). Keep the php-fpm pool free of a `user`/`group` directive and do **not**
   reintroduce `usermod -u 0 -o www-data` or pool `user=root` — they cause root-owned files.
   (The per-version php-fpm `Dockerfile`/`php-fpm.conf` de-root change is applied to **all twelve**
   versions: 5.6, 7.0–7.4, 8.0–8.5.)
8. `pma` DB credentials (`PMA_USER`/`PMA_PASSWORD`) are passed at **runtime** via compose
   `environment:`, never as build-args or `ENV` in the image — the pma image is shared, so
   per-project secrets must not be baked into it.
9. Shared images (any whose tag has no `${PROJECT_NAME}` — mysql, mariadb, apache, nginx-proxy,
   pma, mailpit, phpstan, php-cs, playwright, php-fpm-base) must receive only **global**
   build-args (versions, `ENV`, `PROFILE`). Project-specific content (creds, paths like
   `PROJECT_DIRECTORY`, `DOMAIN`) is provided at **runtime** via `environment:`/volume mounts,
   never as build-args — otherwise it bakes into (or churns) the shared image. Only the
   per-project `php-fpm` image (`${PROJECT_NAME}-php-fpm-${ENV}`) may carry project-built layers.
10. Keep Xdebug at `start_with_request=trigger` / `remote_autostart=0`. Do **not** set it back
    to `yes`/`1` — that makes every build-time `php`/`composer` invocation on the `env-local`
    base hang on `host.docker.internal` (unreachable during build).
11. The php-fpm **status page is intentionally disabled** in every version Dockerfile (FPM has no
    per-path ACL of its own). Do not re-add the `pm.status_path = /fpm_stub_status` sed. If a project
    needs `pm.status` monitoring, enable it there behind a webserver route restricted to the internal
    network (the inactive `nginx/default.conf` shows the pattern: `allow 172.16.0.0/12; deny all;`).

## 13. Verification notes (how changes were validated)

- mysql cascade: build image, init container, `SHOW VARIABLES` confirm project.d.env > project.d > conf.d.
- php: `PHP_INI_SCAN_DIR` ordering via `php -i` (base php.ini < project.d < project.d.env).
- apache: `httpd -t` (Syntax OK).
- nginx-proxy: `nginx -t` passes only after placeholder substitution (`init.sh`), resolvable
  upstreams (`--add-host webserver/pma/mailpit`), and an existing cert — these are runtime
  deps, not config errors. Standalone `nginx -t` will *expectedly* fail on them.
