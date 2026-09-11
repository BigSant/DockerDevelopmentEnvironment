# Project image extension. Shared profile sources come from the named profiles context.
# Compose supplies the selected version; the default makes standalone validation valid.
ARG BASE_IMAGE=php-fpm-base-local-8.1-3.1.5-2.7.6
FROM ${BASE_IMAGE} AS base

ARG ENV=local
ARG PROFILE=prestashop

# Profile-specific PHP overlay (PROFILE=prestashop|akeneo|""). conf.d / php-fpm.d are read
# alphabetically, so the zz-/zzz- prefixes override the base php.ini / pool config. The base
# image stays profile-agnostic (shared across projects); the per-project image layers the
# profile here. Copy the whole profile tree, then apply only the files that exist for the
# selected PROFILE / ENV. A missing file (or PROFILE="") is simply skipped — no build break.
COPY --from=profiles / /tmp/profile/
RUN sh /tmp/profile/apply-php-profile.sh "${PROFILE}" "${ENV}" && rm -rf /tmp/profile

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
