# Documentation

Local Docker environment generator for per-project PHP (PrestaShop / Akeneo) stacks.
One central `setup/` repo provisions and orchestrates an isolated container stack for every
project under `~/Projects/`.

## Documents

| File | Purpose | Audience |
|---|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system works & where everything lives — structure, image model, build flow, profile system, config cascade, services, invariants. | LLM / maintainer |
| [USAGE.md](USAGE.md) | Practical how-to: create a host, start/stop, import DB, select containers, override config per project, profiles, QA tools. | Developer |

## Quick orientation

- **Create a project:** `./new_host.sh <domain>` (from repo root).
- **Run it:** `make up` in `~/Projects/<domain>/app/docker/`.
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
