ARG MARIADB_VERSION=""
FROM mariadb:${MARIADB_VERSION} as base

ARG ENV=""
ARG PROJECT_NAME=""
ARG DATABASE_USER=""
ARG DATABASE_PASSWORD=""
ARG DATABASE_NAME=""
ARG EXPORTER_PASSWORD=""

COPY /docker/mariadb/conf/my.cnf /etc/mysql/conf.d/my.cnf

ADD /docker/mariadb/conf/initialize_exporter.sql /docker-entrypoint-initdb.d
RUN sed -i "s/{DATABASE_USER}/${DATABASE_USER}/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
RUN sed -i "s/{DATABASE_PASSWORD}/${DATABASE_PASSWORD}/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
RUN sed -i "s/{DATABASE_NAME}/${DATABASE_NAME}/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
RUN sed -i "s/{EXPORTER_PASSWORD}/${EXPORTER_PASSWORD}/g" /docker-entrypoint-initdb.d/initialize_exporter.sql

FROM base as env-local

FROM base as env-prod
#COPY ./../../_cache/mariadb/${PROJECT_NAME}-${ENV} /var/lib/mysql

FROM base as env-stage
#COPY ./../../_cache/mariadb/${PROJECT_NAME}-${ENV} /var/lib/mysql
