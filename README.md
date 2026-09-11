# setup — local Docker environment generator

Provisions and orchestrates an isolated multi-container stack (nginx-proxy, apache, php-fpm,
mysql, + optional cron/pma/mailpit/phpstan/php-cs/playwright) for each PHP project
(PrestaShop / Akeneo) under `~/Projects/`.

## Create a new project

From this shared setup checkout, run one command:

```bash
./create-project demo
```

This only creates the sibling `../demo/app`: project configuration, a PHP page
in `public/`, free internal Docker ports and a private database password. It does
not require a running Docker daemon, build images or start containers.
The application URL is `http://demo.local/`, without a port in the browser.

When you want to start the project, explicitly run:

```bash
./create-project demo --start
```

Startup prepares local DNS, TLS, a host Nginx route and PhpStorm, builds images,
starts the four core services and checks the page. Docker must be running; Make,
Python 3.10+, Compose 2.24.4+, OpenSSL, mkcert and host Nginx must be installed.
Host provisioning may require sudo/local CA approval once; an actionable command
is printed if privileges are unavailable. The host Nginx listens on 80/443 and
routes `demo.local` to the project's private port pair, matching the legacy URLs.

Repeating creation preserves the project's files and credentials and still does
not start it. An unrelated existing directory is rejected. Names use lowercase
letters, digits and single hyphens, start with a letter and have at most 32
characters. The previous `--no-start` option remains accepted, but is unnecessary.

This creates a generic PHP environment, not a PrestaShop installation. Optional
service files are provided but their profiles are disabled by default. Put your
application in `../demo/app/public`; keep changes in `env/`, `compose/` and
`config/`. The shared setup remains one checkout, without a Git submodule.

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
