ARG MYSQL_VERSION=""
FROM mysql:${MYSQL_VERSION} AS base

ARG PROFILE=""
ARG ENV=""

COPY /docker/mysql/conf/my.cnf /etc/mysql/conf.d/my.cnf

# Per-project config override (bind-mounted at runtime from ${PROJECT_DOCKER_DIRECTORY}/config/mysql[/${ENV}]).
# Loaded AFTER conf.d (base + profile) so project .cnf files win. Two levels:
# project.d/ = always, project.d.env/ = ENV-match. Empty dirs are a no-op.
RUN mkdir -p /etc/mysql/project.d /etc/mysql/project.d.env \
    && printf '\n!includedir /etc/mysql/project.d/\n!includedir /etc/mysql/project.d.env/\n' >> /etc/my.cnf

# Profile-specific MySQL tuning (PROFILE=prestashop|akeneo|"").
# conf.d is read alphabetically, so the zz-/zzz- prefixes override the base my.cnf.
# Copy the whole profile tree, then apply only the files that actually exist for the
# selected PROFILE / ENV. A missing file (or PROFILE="") is simply skipped — no build break.
COPY /profile/ /tmp/profile/
RUN if [ -n "${PROFILE}" ] && [ -s "/tmp/profile/${PROFILE}/mysql/conf/my.cnf" ]; then \
        cp "/tmp/profile/${PROFILE}/mysql/conf/my.cnf" /etc/mysql/conf.d/zz-profile.cnf; \
    fi; \
    if [ -n "${PROFILE}" ] && [ -n "${ENV}" ] && [ -s "/tmp/profile/${PROFILE}/mysql/conf/my.${ENV}.cnf" ]; then \
        cp "/tmp/profile/${PROFILE}/mysql/conf/my.${ENV}.cnf" /etc/mysql/conf.d/zzz-profile-env.cnf; \
    fi; \
    rm -rf /tmp/profile

COPY /docker/mysql/conf/init.sh /docker-entrypoint-initdb.d/01-init-app-user.sh
RUN chmod +x /docker-entrypoint-initdb.d/01-init-app-user.sh

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
