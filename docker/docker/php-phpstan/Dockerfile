ARG PHP_PHPSTAN_VERSION=""

FROM ghcr.io/phpstan/phpstan:${PHP_PHPSTAN_VERSION} AS base

# Default phpstan.neon template, stored outside the mount path. The entrypoint seeds it
# into the project config dir on first run; the project then owns and may override it.
RUN mkdir -p /opt/phpstan /tmp/phpstan/cache/
COPY /config/phpstan/phpstan.neon /opt/phpstan/phpstan.neon

RUN apk add --no-cache git bash make jq

COPY /docker/php-phpstan/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-phpstan/generate-report.sh /usr/local/bin/generate-report.sh
RUN chmod +x /usr/local/bin/generate-report.sh

COPY /docker/php-phpstan/Makefile /phpstan/Makefile
WORKDIR /phpstan

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
