ARG ENV=''
ARG PHP_VERSION=''
ARG XDEBUG_VERSION=''
ARG COMPOSER_VERSION=''
ARG PROFILE=''
ARG SETUP_IMAGE_SUFFIX=''
# PS_RELEASE=on with PS >= 1.7: the official prestashop/prestashop:<version> image holds the
# release in /var/www/html. Otherwise the runner passes the base image (empty /var/www/html).
ARG PS_RELEASE_IMAGE=''

FROM ${PS_RELEASE_IMAGE} AS release

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

# Master and pool settings for every PHP version: a deploy's graceful reload, the access line format.
COPY /docker/php-fpm/setup-global.conf /usr/local/etc/php-fpm.d/zz-setup-global.conf
# Where that pool writes its access lines when php-fpm starts without start.sh (which sets fd 3, stdout).
ENV SETUP_FPM_ACCESS_LOG=/proc/self/fd/2

# Rendered copies of the read-only project PHP drop-ins (written at start by any UID).
RUN mkdir -m 0777 -p /usr/local/etc/php/project.r /usr/local/etc/php/project.r.env


# Optional official PrestaShop release, kept in the image for PS_RELEASE_PATHS and comparison.
# It is never copied into the project automatically; make setup copies only the listed paths.
ARG PS_RELEASE=off
ARG PS_VERSION=''
ARG PS_RELEASE_SHA256=''
COPY --from=release /var/www/html /opt/prestashop/release-image/
RUN set -eu; mkdir -p /opt/prestashop/release; \
    if [ "${PS_RELEASE}" = on ]; then \
        target="/opt/prestashop/release/${PS_VERSION}"; \
        if [ -f /opt/prestashop/release-image/index.php ]; then \
            mv /opt/prestashop/release-image "$target"; \
        else \
            url="https://github.com/PrestaShop/PrestaShop/releases/download/${PS_VERSION}/prestashop_${PS_VERSION}.zip"; \
            mkdir -p /tmp/ps "$target"; curl -fsSL "$url" -o /tmp/ps/release.zip; \
            sum="$(sha256sum /tmp/ps/release.zip | cut -d' ' -f1)"; echo "PrestaShop ${PS_VERSION} release sha256: $sum"; \
            if [ -n "${PS_RELEASE_SHA256}" ] && [ "$sum" != "${PS_RELEASE_SHA256}" ]; then echo 'Release checksum mismatch' >&2; exit 1; fi; \
            cd /tmp/ps && unzip -q release.zip; \
            if [ -f prestashop.zip ]; then unzip -q prestashop.zip -d "$target"; \
            elif [ -d prestashop ]; then cp -a prestashop/. "$target/"; \
            else echo 'Unknown PrestaShop release layout' >&2; exit 1; fi; \
            cd / && rm -rf /tmp/ps; \
        fi; \
        test -f "$target/index.php"; \
    fi; \
    rm -rf /opt/prestashop/release-image

# Setup runtime (start, templates, PrestaShop adapters, cron) and the profiles' table registry defaults
# (the backup service reads them) travel in the image so a release target needs no setup checkout;
# local development mounts the checkout over them.
COPY /runtime/ /opt/setup/runtime/
COPY /database-profile/ /opt/setup/database-profile/

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
