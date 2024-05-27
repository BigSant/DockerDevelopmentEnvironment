ARG MYSQLD_EXPORTER_VERSION=""
FROM prom/mysqld-exporter:v${MYSQLD_EXPORTER_VERSION} as base

USER root
COPY /docker/mysqld-exporter/conf/my.cnf /.my.cnf

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
