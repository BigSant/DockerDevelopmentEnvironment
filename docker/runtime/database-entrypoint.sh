#!/bin/sh
# Render CORE derived settings and project MySQL/MariaDB drop-ins before the server reads them.
set -eu
sh /usr/local/bin/render-templates.sh \
    /etc/mysql/setup.d /etc/mysql/setup.r \
    /etc/mysql/project.d /etc/mysql/project.r \
    /etc/mysql/project.d.env /etc/mysql/project.r.env
exec docker-entrypoint.sh "$@"
