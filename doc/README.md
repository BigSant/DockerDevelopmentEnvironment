# Documentation

Local Docker environment generator for per-project PHP (including PrestaShop and Akeneo) stacks.
One central `setup/` repo provisions and orchestrates an isolated container stack for every
project under `~/Projects/`.

## Documents

| File | Purpose | Audience |
|---|---|---|
| [Lietuviškas vadovas](lt/README.md) | Nuoseklus katalogų, visų setup nustatymų, komandų ir praktinių situacijų vadovas su paruošimo pavyzdžiais. | Pradedantysis / projekto kūrėjas |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the system works & where everything lives — structure, image model, build flow, profile system, config cascade, services, invariants. | LLM / maintainer |
| [USAGE.md](USAGE.md) | Practical how-to: create a host, start/stop, import DB, select containers, override config per project, profiles, QA tools. | Developer |
| [PROJECT_TEMPLATES.md](PROJECT_TEMPLATES.md) | Original project sources, batch preparation, private settings and migration from app/docker. | Developer / maintainer |
| [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) | MySQL table snapshots, exact staged-file checks and opt-in Git hooks. | Developer / maintainer |
| [DATABASE_FIXTURES.md](DATABASE_FIXTURES.md) | Project SQL data sets for local development/testing and optional loading after imports. | Developer |
| [ENVIRONMENT_WORKFLOW.md](ENVIRONMENT_WORKFLOW.md) | Profile-aware readiness, PS versions, bootstrap and isolated test stacks. | Developer |
| [RELEASES.md](RELEASES.md) | Setup API, image namespaces and compatibility CI. | Maintainer |
| [PHPSTORM.md](PHPSTORM.md) | Portable project settings, Docker PHP interpreter and startup/run commands. | Developer |
| [PRESTASHOP_PARAMETERS.md](PRESTASHOP_PARAMETERS.md) | Opt-in runtime DB connection synchronization without rebuilding images. | Developer |

## Quick orientation

- **Start with the current walkthrough:** [Lietuviškas vadovas](lt/README.md).
- **Prepare sources beside application code:** `python3 prepare_project.py --layout app ../project-one`.
- **Provision local settings:** `make bootstrap` from that project's `app/` directory.
- **Validate / build / run:** `make check`, explicit `make build`, then `make up`.
- **Pick optional services:** `COMPOSE_PROFILES` in `env/local.env`; the new grouped example explicitly selects core services with an empty value.
- **Override container config:** `config/<service>/` and `config/<service>/<ENV>/` under the project's source directory; see service-specific include contexts in [the guide](lt/05-servisai.md).
- **Historical host workflow:** `new_host.sh` and the old Makefiles remain separate; their `up` and DB commands are not interchangeable with `project.mk`.

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
