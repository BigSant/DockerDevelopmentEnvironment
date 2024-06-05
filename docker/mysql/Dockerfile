ARG MYSQL_VERSION=""
FROM mysql:${MYSQL_VERSION} as base

COPY /docker/mysql/conf/my.cnf /etc/mysql/conf.d/my.cnf
COPY /docker/mysql/conf/enable-mysql-native-password.cnf /etc/mysql/conf.d/enable-mysql-native-password.cnf

COPY /docker/mysql/conf/init.sh /docker-entrypoint-initdb.d/1-init.sh
RUN chmod +x /docker-entrypoint-initdb.d/1-init.sh

COPY /docker/mysql/conf/grant.sql /docker-entrypoint-initdb.d/2-grant.sql

COPY /docker/mysql/conf/initialize_exporter.sql /docker-entrypoint-initdb.d/3-initialize_exporter.sql
RUN chmod 777 /docker-entrypoint-initdb.d/3-initialize_exporter.sql

#COPY /docker/mysql/conf/init.sql /etc/mysql/init.sql
#CMD ["--init-file=/etc/mysql/init.sql"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
