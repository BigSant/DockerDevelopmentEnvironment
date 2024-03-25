ARG PMA_VERSION=""
FROM phpmyadmin:${PMA_VERSION}-apache as base

ARG DATABASE_USER=""
ARG DATABASE_PASSWORD=""

COPY /docker/pma/conf/config.php /etc/phpmyadmin/config.user.inc.php

FROM base as env-local
ENV PMA_USER=${DATABASE_USER}
ENV PMA_PASSWORD=${DATABASE_PASSWORD}
ENV MYSQL_ROOT_PASSWORD=${DATABASE_PASSWORD}

FROM base as env-prod
FROM base as env-stage
