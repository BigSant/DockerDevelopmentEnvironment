ARG PHP_CS_VERSION=""

FROM ghcr.io/php-cs-fixer/php-cs-fixer:${PHP_CS_VERSION} AS base

# Default .php-cs-fixer.php template, stored outside the mount path. The entrypoint seeds
# it into the project config dir on first run; the project then owns and may override it.
RUN mkdir -p /opt/php-cs-fixer /tmp/php-cs-fixer/cache/
COPY /config/php-cs/.php-cs-fixer.php /opt/php-cs-fixer/.php-cs-fixer.php

RUN apk add --no-cache git bash make

COPY /docker/php-cs/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-cs/Makefile /php-cs/Makefile
WORKDIR /php-cs

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
