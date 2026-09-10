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

`make check`, `config`, `build`, `up`, `down`, `ps`, `logs`, `phpstan`, `phpcs`
and `e2e` are defined once in the shared project.mk. `ENV=local|stage|prod`
selects `.env.<env>`. `PROFILES=` explicitly means core-only, while absent
`PROFILES` uses the project's setting or the shared environment default.
`check` validates all declared profiles. Use `cmd='...'` to override a QA
command; it is parsed as arguments rather than executed by a host shell.

Real Compose validation tests run without starting/building any container:

```bash
python3 -m unittest discover -s tests -v
```

The tests cover both layouts, literal/multiline dotenv values, included-service
overrides, explicit empty profiles, concurrent project/same-project renders,
unchanged original files, retry behavior and port reservations across layouts.
The new runner does not use the shared `/tmp` files employed by the legacy
Makefile. Legacy commands and host provisioning still need sequential use.
