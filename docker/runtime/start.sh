#!/bin/sh
set -eu
case "${PROFILE:-}" in
    ps|prestashop) php /opt/setup/runtime/prestashop-command.php prepare ;;
esac
# Hooks are project-owned and run in filename order before accepting requests.
# They receive runtime env; SQL imports are separate explicit Make commands.
for script in /opt/setup/project/startup/*.sh; do
    [ -f "$script" ] || continue
    [ ! -L "$script" ] || { echo 'Startup hooks must not be symlinks.' >&2; exit 1; }
    sh "$script"
done
exec docker-php-entrypoint "$@"
