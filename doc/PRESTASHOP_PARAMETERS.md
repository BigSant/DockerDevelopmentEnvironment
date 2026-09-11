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

## Debug mode in config/defines.inc.php

The same startup wrapper supports `PS_DEBUG_MODE=off|on|ip` for both PS layouts.
Set it in the selected project env file; PHP-FPM receives it automatically.
`off` writes `false`, `on` writes `true`, and `ip` writes a per-request expression
into the existing `_PS_MODE_DEV_` definition. Other constants remain intact.
The original defines file is required; missing/duplicate definitions and symlinks
are rejected. Replacement is atomic and retains the file's permissions; identical
content is not rewritten. A debug-only change does not clear application caches.

For restricted HTTP debugging, use e.g. `PS_DEBUG_MODE=ip` and
`PS_DEBUG_IPS=192.0.2.10,2001:db8::10`, replacing the example IPs with real client
addresses. The list is required in `ip` mode and accepts exact IPv4/IPv6 addresses,
not CIDR, hostnames or ports. CLI/phpdbg debug is off in `ip` mode. `on` enables
debug for everyone, regardless of the IP list. Invalid nonempty lists are rejected
in any managed mode. Unset/empty mode leaves existing behavior unchanged; it does
not undo a previous generated policy. Use explicit `off` to disable it.

Matching uses the web server's `REMOTE_ADDR`, never raw forwarded headers. Configure
trusted proxies correctly before allowing any IP; allowing a shared gateway address
would allow everyone behind it. IPv4-mapped IPv6 and IPv4 addresses are distinct.
Earlier custom definitions of `_PS_MODE_DEV_` must be removed to let this setting
take effect. Back-office edits can last until the next PHP container startup.

Run `make up` after env changes; builds do not modify these settings. A restart with
unchanged container env can reapply the policy after a core/back-office file edit.
Application code mounts must be writable and separate between environments. If the
core file is tracked in the app repo, the generated change will appear in Git.

See the [Lithuanian debug recipes](lt/06-aplikacija.md#f-prestashop-debug-iš-env)
for local, restricted-IP and production examples.
