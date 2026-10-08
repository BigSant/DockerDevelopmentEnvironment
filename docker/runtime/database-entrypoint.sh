#!/bin/sh
# Render project MySQL/MariaDB drop-ins before the official entrypoint reads them.
set -eu
sh /usr/local/bin/render-templates.sh \
    /etc/mysql/project.d /etc/mysql/project.r \
    /etc/mysql/project.d.env /etc/mysql/project.r.env
exec docker-entrypoint.sh "$@"
