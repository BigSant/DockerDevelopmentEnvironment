ARG REDIS_VERSION=""
FROM redis:${REDIS_VERSION}-alpine AS base

# CORE configuration: a cache by default (no RDB snapshots, no AOF). Project drop-ins in
# config/redis (rendered into project.r*) can enable persistence into /data.
COPY /docker/redis/conf/redis.conf /usr/local/etc/redis/redis.conf
COPY /runtime/render-templates.sh /usr/local/bin/render-templates.sh
COPY /docker/redis/conf/entrypoint.sh /usr/local/bin/setup-redis-entrypoint.sh
RUN mkdir -p /usr/local/etc/redis/project.d /usr/local/etc/redis/project.d.env \
    && mkdir -m 0777 -p /usr/local/etc/redis/project.r /usr/local/etc/redis/project.r.env \
    && chmod +x /usr/local/bin/setup-redis-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/setup-redis-entrypoint.sh"]

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
