ARG ENV=''
ARG PHP_VERSION=''
ARG XDEBUG_VERSION=''
ARG COMPOSER_VERSION=''
ARG PROFILE=''

FROM php-fpm-base-${ENV}-${PHP_VERSION}-${XDEBUG_VERSION}-${COMPOSER_VERSION} AS base

# Re-declare ARGs needed inside this stage (ARGs before the first FROM are only visible to FROM).
ARG ENV=''
ARG PROFILE=''

# Profile-specific PHP overlay (PROFILE=prestashop|akeneo|""). conf.d / php-fpm.d are read
# alphabetically, so the zz-/zzz- prefixes override the base php.ini / pool config. The base
# image stays profile-agnostic (shared across projects); the per-project image layers the
# profile here. Copy the whole profile tree, then apply only the files that exist for the
# selected PROFILE / ENV. A missing file (or PROFILE="") is simply skipped — no build break.
COPY /profile/ /tmp/profile/
RUN if [ -n "${PROFILE}" ] && [ -s "/tmp/profile/${PROFILE}/php-fpm/conf/php.ini" ]; then \
        cp "/tmp/profile/${PROFILE}/php-fpm/conf/php.ini" /usr/local/etc/php/conf.d/zz-profile.ini; \
    fi; \
    if [ -n "${PROFILE}" ] && [ -n "${ENV}" ] && [ -s "/tmp/profile/${PROFILE}/php-fpm/conf/php.${ENV}.ini" ]; then \
        cp "/tmp/profile/${PROFILE}/php-fpm/conf/php.${ENV}.ini" /usr/local/etc/php/conf.d/zzz-profile-env.ini; \
    fi; \
    if [ -n "${PROFILE}" ] && [ -s "/tmp/profile/${PROFILE}/php-fpm/conf/pool.conf" ]; then \
        cp "/tmp/profile/${PROFILE}/php-fpm/conf/pool.conf" /usr/local/etc/php-fpm.d/zz-profile.conf; \
    fi; \
    rm -rf /tmp/profile

# Per-project PHP config override dirs (bind-mounted at runtime; loaded via PHP_INI_SCAN_DIR
# after conf.d so project .ini files win). project.d = always, project.d.env = ENV-match.
RUN mkdir -p /usr/local/etc/php/project.d /usr/local/etc/php/project.d.env

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
