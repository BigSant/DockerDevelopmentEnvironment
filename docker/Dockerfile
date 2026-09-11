ARG ENV=''
ARG PHP_VERSION=''
ARG XDEBUG_VERSION=''
ARG COMPOSER_VERSION=''
ARG PROFILE=''
ARG SETUP_IMAGE_SUFFIX=''

FROM php-fpm-base-${ENV}-${PHP_VERSION}-${XDEBUG_VERSION}-${COMPOSER_VERSION}${SETUP_IMAGE_SUFFIX} AS base

# Re-declare ARGs needed inside this stage (ARGs before the first FROM are only visible to FROM).
ARG ENV=''
ARG PROFILE=''
ARG SETUP_IMAGE_SUFFIX=''

# Profile-specific PHP overlay (PROFILE=prestashop|akeneo|""). conf.d / php-fpm.d are read
# alphabetically, so the zz-/zzz- prefixes override the base php.ini / pool config. The base
# image stays profile-agnostic (shared across projects); the per-project image layers the
# profile here. Copy the whole profile tree, then apply only the files that exist for the
# selected PROFILE / ENV. A missing file (or PROFILE="") is simply skipped — no build break.
COPY /profile/ /tmp/profile/
RUN sh /tmp/profile/apply-php-profile.sh "${PROFILE}" "${ENV}" && rm -rf /tmp/profile

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
