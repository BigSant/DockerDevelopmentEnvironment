ARG ENV=''
ARG PHP_VERSION=''
ARG XDEBUG_VERSION=''
ARG COMPOSER_VERSION=''

FROM php-fpm-base-${ENV}-${PHP_VERSION}-${XDEBUG_VERSION}-${COMPOSER_VERSION} as base

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
