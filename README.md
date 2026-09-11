# setup — local Docker environment generator

Provisions and orchestrates an isolated multi-container stack (nginx-proxy, apache, php-fpm,
mysql, + optional cron/pma/mailpit/phpstan/php-cs/playwright) for each PHP project
(PrestaShop / Akeneo) under `~/Projects/`.

## Create a new project

From this shared setup checkout, run one command:

```bash
./create-project demo
```

This creates the sibling `../demo/app` with configuration and a PHP page in
`public/`, chooses free local ports, generates a private database password,
prepares local TLS and PhpStorm, builds images and starts the four core services.
The command checks the page and prints its URL. No manual env editing or Python
command is needed. Docker must be running; Make, Python 3.10+, Compose 2.24.4+,
OpenSSL and mkcert must be installed. A first-time local CA/host permission step
may be necessary; the command prints the exact action and can be rerun afterward.

Repeating `./create-project demo` preserves the generated project's files and
credentials and retries startup. An unrelated existing directory is rejected.
Project names use lowercase letters, digits and single hyphens, start with a
letter and contain at most 32 characters.

Use `./create-project demo --no-start` to create files without Docker or host
provisioning; run the same command without that flag when ready. This creates a
generic PHP environment, not a PrestaShop installation. Additional service files
are provided but their profiles are disabled by default. Put your application
in `../demo/app/public`; keep project-specific changes in `env/`, `compose/` and
`config/`. The shared setup remains a single checkout, without a Git submodule.

See the [Lithuanian quick start](doc/lt/02-pradzia.md#c-naujas-projektas-viena-komanda).

## Reusable project sources

Keep one shared `setup` checkout on each machine. Prepare identical original
Makefile/Compose sources for any number of projects; only project settings differ:

```bash
python3 prepare_project.py --check ../shop-one ../shop-two
python3 prepare_project.py ../shop-one ../shop-two
# Or keep all project sources beside app/public:
python3 prepare_project.py --layout app ../shop-three
# Existing projects: copy their app/docker settings without switching containers.
python3 prepare_project.py --from-legacy ../forsena
make -C ../forsena/docker check
make -C ../forsena/docker config
```

For a new project, run `make init` and fill `env/local.env` before checking. Use
`make doctor` to check readiness; `make pull` fetches external images and
`make shell` opens the running PHP container. The
preparer writes missing files only, keeps existing project settings and refuses
conflicting bootstrap files. It does not provision the host or start Docker.
`make build` explicitly builds images; `make up` uses existing local images.
See [reusable sources](doc/PROJECT_TEMPLATES.md) for setup and migration.

`make ide-init` prepares PhpStorm project settings, a Docker PHP interpreter and
shared run/startup configurations. See [PhpStorm setup](doc/PHPSTORM.md).

## Legacy host provisioning

```bash
./new_host.sh <domain>                 # provision a project host
cd ~/Projects/<domain>/app/docker
make up                                # build + start (local)
```

## Documentation

**[Pilnas vadovas lietuviškai](doc/lt/README.md)** — nuo pirmo paleidimo iki katalogų,
env ir servisų konfigūracijos, PS versijų, DB, fixtures, QA, testinių aplinkų ir IDE.
Kiekviename praktiniame skyriuje pateikti failai, komandos ir rezultato patikra.

See [`doc/`](doc/README.md):
- [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md) — how it works & structure (LLM / maintainer reference)
- [doc/USAGE.md](doc/USAGE.md) — practical usage guide

> ⚠️ When changing the Docker logic (Dockerfiles, compose, Makefile, profiles, config cascade,
> `new_host.sh`), update `doc/` in the same change — see the maintenance rule in
> [doc/README.md](doc/README.md).

`make bootstrap` prepares local host/TLS/IDE settings. `make up` waits for service
readiness and runs profile-aware smoke checks. `make db-backup` and compressed
imports support DB workflows; `make test-init` prepares independent test app/data
paths. See [runtime workflow](doc/ENVIRONMENT_WORKFLOW.md) and
[setup releases](doc/RELEASES.md) before adopting changed stage/prod paths.
