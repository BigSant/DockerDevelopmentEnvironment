ARG MEMCACHED_VERSION=""
FROM memcached:${MEMCACHED_VERSION}-alpine AS base

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
