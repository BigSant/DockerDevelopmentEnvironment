FROM mariadb:11.3.2

ARG EXPORTER_PASSWORD=""

COPY /docker/mariadb/conf/my.cnf /etc/mysql/conf.d/my.cnf

ADD /docker/mariadb/conf/initialize_exporter.sql /docker-entrypoint-initdb.d
RUN sed -i "s/{EXPORTER_PASSWORD}/${EXPORTER_PASSWORD}/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
