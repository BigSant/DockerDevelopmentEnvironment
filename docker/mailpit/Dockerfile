ARG MAILPIT_VERSION=""
FROM axllent/mailpit:${MARIADB_VERSION} as base

RUN mkdir -p /data
RUN touch /data/authfile

FROM base as env-local

FROM base as env-prod

FROM base as env-stage
