ARG MARIADB_VERSION=""
FROM mariadb:${MARIADB_VERSION} as base

COPY /docker/mariadb/conf/my.cnf /etc/mysql/conf.d/my.cnf
COPY /docker/mariadb/conf/initialize_exporter.sql /docker-entrypoint-initdb.d/initialize_exporter.sql
RUN chmod 777 /docker-entrypoint-initdb.d/initialize_exporter.sql

COPY /docker/mariadb/conf/init.sh /docker-entrypoint-initdb.d/init.sh
RUN chmod +x /docker-entrypoint-initdb.d/init.sh

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
