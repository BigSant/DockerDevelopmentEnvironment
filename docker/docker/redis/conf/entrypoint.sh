#!/bin/sh
# Render project drop-ins, then start Redis with the derived memory cap.
set -eu
sh /usr/local/bin/render-templates.sh \
    /usr/local/etc/redis/project.d /usr/local/etc/redis/project.r \
    /usr/local/etc/redis/project.d.env /usr/local/etc/redis/project.r.env
exec redis-server /usr/local/etc/redis/redis.conf --maxmemory "${REDIS_MAXMEMORY_MB}mb" "$@"
