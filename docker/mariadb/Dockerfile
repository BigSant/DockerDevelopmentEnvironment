ARG MARIADB_VERSION=""
FROM mariadb:${MARIADB_VERSION} as base

COPY /docker/mariadb/conf/my.cnf /etc/mysql/conf.d/my.cnf
COPY /docker/mariadb/conf/initialize_exporter.sql /docker-entrypoint-initdb.d

COPY /docker/mariadb/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
