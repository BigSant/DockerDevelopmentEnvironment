# PrestaShop configuration at startup

The shared PHP startup wrapper handles the `ps` / `prestashop` profile only.
PS 1.6 uses `config/settings.inc.php`; newer installed shops use
`app/config/parameters.php`. The shop's original configuration and keys are required.

Configure DB values in private env files. The PHP service passes those values at
runtime and mounts the updater automatically; no project-specific entrypoint or
Dockerfile is necessary. `APP_DATABASE_HOST` / `APP_DATABASE_PORT` select an external
DB when needed; their defaults are `database` / `3306`.

Project additions belong in `config/prestashop/parameters.override.php` (modern)
or `config/prestashop/settings.override.php` (legacy). Each returns an array; DB
connection keys remain reserved for env. Existing unrelated values and keys survive.
Changed values invalidate the relevant cache before an atomic mode-0600 replacement.
Unchanged restarts preserve the file and warmed caches. Values are never logged.

Run `make up` after env changes. Changing an existing MySQL/MariaDB account password
still requires a separate DB operation; synchronization does not rotate DB accounts.

See [profile/version behavior and examples](ENVIRONMENT_WORKFLOW.md).
