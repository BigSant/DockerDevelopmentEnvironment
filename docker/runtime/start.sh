#!/bin/sh
set -eu
# Effective PHP drop-ins: copy and render *.template with this container's environment.
sh /opt/setup/runtime/render-templates.sh \
    /usr/local/etc/php/project.d /usr/local/etc/php/project.r \
    /usr/local/etc/php/project.d.env /usr/local/etc/php/project.r.env
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
