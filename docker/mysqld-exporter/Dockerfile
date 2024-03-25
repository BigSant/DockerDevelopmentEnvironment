ARG MYSQLD_EXPORTER_VERSION=""
FROM prom/mysqld-exporter:v${MYSQLD_EXPORTER_VERSION} as base

ARG EXPORTER_PASSWORD=""

USER root
COPY /docker/mysqld-exporter/conf/my.cnf /.my.cnf
RUN sed -i "s/{EXPORTER_PASSWORD}/${EXPORTER_PASSWORD}/g" /.my.cnf

USER nobody
CMD ["--config.my-cnf=/.my.cnf"]

FROM base as env-local

FROM base as env-prod

FROM base as env-stage
