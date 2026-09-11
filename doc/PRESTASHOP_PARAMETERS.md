# PrestaShop database parameters at startup

Keep connection values in the project's private `env/<environment>.env`.
The optional shared updater writes them into the application's existing
`app/config/parameters.php` before PHP-FPM starts. Credentials belong to runtime,
not Dockerfile build arguments or image layers.

Add this PHP override to a project's `compose/common.yaml`:

```yaml
services:
  php-fpm:
    environment:
      DATABASE_HOST: database
      DATABASE_PORT: "3306"
      DATABASE_NAME: ${DATABASE_NAME:?Set DATABASE_NAME in the private env file}
      DATABASE_USER: ${DATABASE_USER:?Set DATABASE_USER in the private env file}
      DATABASE_PASSWORD: ${DATABASE_PASSWORD:?Set DATABASE_PASSWORD in the private env file}
    volumes:
      - ${ROOT_DIRECTORY}/config/prestashop/update-parameters.php:/opt/setup/update-parameters.php:ro
    entrypoint:
      - /bin/sh
      - -ec
      - 'php /opt/setup/update-parameters.php; exec docker-php-entrypoint "$$@"'
      - prestashop-php
    command: ["php-fpm", "-R"]
```

An explicit command is necessary because overriding an entrypoint clears the
image's default command. [Docker Compose service reference](https://docs.docker.com/reference/compose-file/services/#entrypoint).
`exec` preserves normal PHP-FPM signal handling, and an updater failure stops
startup. Containers need PHP 7+ and a writable application bind mount; this was
validated with Forsena's PHP 8.1 and host PHP 8.5.

`DATABASE_HOST=database` is the internal Compose service DNS name and `3306` its
internal port, not a published host port. Do not reuse the shared `.env`
`DATABASE_HOST` here: the MySQL definition currently uses it for account grants.
Projects using an external database should override this PHP service's host/port.

After editing private env values, run `make up`. Compose recreates the PHP
container when its environment changes and the entrypoint updates the file.
`docker restart` runs the updater again but retains the container's previous
environment; it does not reread env files. `make build` does not update parameters.
If using PhpStorm's native interpreter, also run `make ide-refresh` after editing
env/Compose sources so its private Compose snapshot is current.

Only `database_host`, `database_port`, `database_name`, `database_user` and
`database_password` are managed. The updater preserves all other values, including
the DB prefix, cookie/encryption keys, API keys and mail settings. It does not
invent a missing shop configuration: restore that shop's `parameters.php` once.
Keep this file ignored in the application repository. Generated files use mode
0600 and atomic replacement; credentials are not logged. `%` is escaped for
Symfony and decoded by PrestaShop's bootstrap.

When connection values change, both `var/cache/dev` and `var/cache/prod` are
cleared, including legacy `appParameters.php` and compiled Symfony containers.
Other `var` directories are preserved. Symlinked config/cache roots are rejected;
nested cache symlinks are unlinked without following their targets. If values
match, neither the file nor warmed caches are touched. Use this updater at
container startup, before accepting requests, not concurrently in a running shop.

Changing env values does not rotate an existing MySQL user's password, create
a database or import a dump. Those values must match the target database.
The shared MySQL initialization runs only for a fresh data directory.
