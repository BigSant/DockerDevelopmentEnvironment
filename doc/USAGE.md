# Usage

Practical guide for working with the local Docker environment. For *how it works
internally*, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Prerequisites

- Docker (with `docker compose` v2)
- `mkcert` and `libnss3-tools` (`certutil`) — for trusted local TLS
- `nginx` running on the host (the host-level reverse proxy)
- Projects live under `~/Projects/`

## 1. Create a new project host

From the `setup/` repo root (run as a normal user, NOT root — it uses `sudo` where needed):

```bash
./new_host.sh <domain>        # e.g. ./new_host.sh myshop
```

This:
- allocates an HTTP + HTTPS port pair (from `index_counter`, starting at 3000),
- creates `~/Projects/<domain>/{app/{public,docs,config,docker/config/...},data,backup}`,
- generates an mkcert certificate for `<domain>.local` and `*.<domain>.local`,
- writes a host nginx vhost and `/etc/hosts` entries for `<domain>.local`,
  `pma.<domain>.local`, `mailpit.<domain>.local`,
- writes `app/docker/.env` (PROJECT_NAME) and `app/docker/.env.local` (DOMAIN, ports, DB creds).

Put your application code in `~/Projects/<domain>/app/public/`.

## 2. Start / stop the stack

From the project's docker directory (`~/Projects/<domain>/app/docker/`):

```bash
make up      # config + build + start (local env)
make down    # stop
```

Open `https://<domain>.local`, `http://pma.<domain>.local`, `http://mailpit.<domain>.local`.

(The project `Makefile` is a thin wrapper; it calls the central `setup/docker/Makefile`
targets `local` / `down-local` with `PROJECT_DIRECTORY` set.)

## 3. Import a database dump

```bash
make db <dump.sql>                          # load into the project DB
make db <dump.sql> drop=1                    # drop + recreate first
make db <dump.sql> database=other initialize=0
```
After loading, `config/sql/init.sql` and `init.<env>.sql` are applied (unless `initialize=0`).

## 4. Choose which containers run (per project)

Core services (nginx-proxy, apache, php-fpm, mysql) always run. Optional services are
selected with `COMPOSE_PROFILES`. The default (in `setup/docker/.env`) is:

```
COMPOSE_PROFILES=mailpit,pma,cron
```

Override per project in `~/Projects/<domain>/app/docker/.env.local`:

```ini
# Available: phpcs, phpstan, mailpit, pma, cron, playwright
COMPOSE_PROFILES=phpcs,phpstan,mailpit,pma,cron        # add static analysis
# COMPOSE_PROFILES=                                    # core only
```
Then `make up` again.

## 5. Override container config (per project)

Drop config files under `~/Projects/<domain>/app/docker/config/<service>/`. They load
**after** the base and profile config, so they win — for that project only, no rebuild
(just restart the container). Two levels:

- `config/<service>/<file>`        → applied in **all** environments
- `config/<service>/<env>/<file>`  → applied **only** when ENV matches (`local`, `prod`, `stage`)

| Service | Directory | File type |
|---|---|---|
| MySQL | `config/mysql/` | `*.cnf` (`[mysqld]` …) |
| PHP (php-fpm & cron) | `config/php/` | `*.ini` |
| Apache | `config/apache/` | `*.conf` |
| nginx-proxy | `config/nginx-proxy/` | `*.conf` |

**Example — enable `performance_schema` for this project, local env only:**
```ini
# ~/Projects/<domain>/app/docker/config/mysql/local/perf.cnf
[mysqld]
performance_schema = ON
```

**Example — raise PHP memory for this project (all envs):**
```ini
# ~/Projects/<domain>/app/docker/config/php/memory.ini
memory_limit = 1024M
```
> Note: `memory_limit`, `max_execution_time`, `upload_max_filesize`, `post_max_size` for
> php-fpm web requests are locked via `php_admin_value` and are set from the env vars
> `PHP_*` in `.env.local` instead.

`pma` and `mailpit` are configured via environment variables — override them in
`.env.local` (always) or `.env.<env>` (per env).

## 6. Profiles (PrestaShop / Akeneo)

`PROFILE` (in `.env`, default `prestashop`) loads framework-specific base config at build
time, shared by all projects of that profile. To customise a profile for *all* its projects,
edit `setup/docker/profile/<profile>/<service>/conf/…` and rebuild. To customise just one
project, use the per-project override in §5 instead.

## 7. QA tooling

`phpstan` and `php-cs` ship a default config, copied into your project's `app/config/{phpstan,php-cs}/`
on first run (edit it there to customise). Enable them via `COMPOSE_PROFILES`, then exec:

```bash
docker compose exec php-phpstan make report      # or: dir-analysis, git-analysis, baseline
docker compose exec php-cs      make check        # or: fix, git-check, git-fix
```

## 8. Switching environment (prod / stage)

The same stack runs other environments via the corresponding Makefile targets
(`make prod`, `make stage` in the central Makefile). ENV-specific config comes from
`.env.<env>`, `docker-compose.<env>.yml`, profile `*.<env>.*` files, and per-project
`config/<service>/<env>/`.
