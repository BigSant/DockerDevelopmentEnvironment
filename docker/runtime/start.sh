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
# php-fpm points its own stdout at /dev/null; fd 3 keeps the container's stdout for the request lines
# (zz-setup-global.conf), so docker logs hold requests on stdout and PHP's messages on stderr.
if [ "${1:-}" = php-fpm ]; then
    exec 3>&1
    export SETUP_FPM_ACCESS_LOG=/proc/self/fd/3
fi
exec docker-php-entrypoint "$@"
