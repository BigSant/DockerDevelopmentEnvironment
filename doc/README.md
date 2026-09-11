# Documentation

Local Docker environment generator for per-project PHP (PrestaShop / Akeneo) stacks.
One central `setup/` repo provisions and orchestrates an isolated container stack for every
project under `~/Projects/`.

## Documents

| File | Purpose | Audience |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system works & where everything lives — structure, image model, build flow, profile system, config cascade, services, invariants. | LLM / maintainer |
| [USAGE.md](USAGE.md) | Practical how-to: create a host, start/stop, import DB, select containers, override config per project, profiles, QA tools. | Developer |
| [PROJECT_TEMPLATES.md](PROJECT_TEMPLATES.md) | Original project sources, batch preparation, private settings and migration from app/docker. | Developer / maintainer |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | MySQL table snapshots, exact staged-file checks and opt-in Git hooks. | Developer / maintainer |
| [DATABASE_FIXTURES.md](DATABASE_FIXTURES.md) | Project SQL data sets for local development/testing and optional loading after imports. | Developer |
| [PHPSTORM.md](PHPSTORM.md) | Portable project settings, Docker PHP interpreter and startup/run commands. | Developer |
| [PRESTASHOP_PARAMETERS.md](PRESTASHOP_PARAMETERS.md) | Opt-in runtime DB connection synchronization without rebuilding images. | Developer |

## Quick orientation

- **Prepare reusable sources:** `python3 prepare_project.py ../project-one ../project-two`.
- **Provision a host:** `./new_host.sh <domain>` (from repo root).
- **Validate prepared sources:** `make check` in `~/Projects/<domain>/docker/`.
- **Run prepared sources:** explicit `make build`, then `make up`; the legacy
  `app/docker/Makefile` still combines those actions in `make up`.
- **Pick containers:** `COMPOSE_PROFILES` in the project `.env.local` (default `mailpit,pma,cron`).
- **Override container config:** drop files in `~/Projects/<domain>/app/docker/config/<service>/`
  (and `…/<env>/` for env-specific). Config cascade, low → high:
  `image base < profile < profile-env < project base < project-env`.

## ⚠️ Maintenance rule

**This documentation is the source of truth for the Docker logic and MUST be kept in sync.**
Whenever you change any of the following, update the docs in the *same* change (primarily
[ARCHITECTURE.md](ARCHITECTURE.md)):

- any `Dockerfile` or `docker-compose.yml`
- `docker/Makefile`, `docker/Makefile.local`, `docker/.env`
- the profile system (`docker/profile/…`, profile wiring in Dockerfiles)
- the per-project config cascade / mounts
- service selection (`COMPOSE_PROFILES`, `profiles:` tags)
- `new_host.sh`

Create additional files under `doc/` when a topic outgrows the existing documents, and link
them from this index.
