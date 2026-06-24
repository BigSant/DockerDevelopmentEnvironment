# setup — local Docker environment generator

Provisions and orchestrates an isolated multi-container stack (nginx-proxy, apache, php-fpm,
mysql, + optional cron/pma/mailpit/phpstan/php-cs/playwright) for each PHP project
(PrestaShop / Akeneo) under `~/Projects/`.

## Quick start

```bash
./new_host.sh <domain>                 # provision a project host
cd ~/Projects/<domain>/app/docker
make up                                # build + start (local)
```

## Documentation

See [`doc/`](doc/README.md):
- [doc/ARCHITECTURE.md](doc/ARCHITECTURE.md) — how it works & structure (LLM / maintainer reference)
- [doc/USAGE.md](doc/USAGE.md) — practical usage guide

> ⚠️ When changing the Docker logic (Dockerfiles, compose, Makefile, profiles, config cascade,
> `new_host.sh`), update `doc/` in the same change — see the maintenance rule in
> [doc/README.md](doc/README.md).
