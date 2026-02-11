ARG PMA_VERSION=""
FROM phpmyadmin/phpmyadmin:${PMA_VERSION} as base

FROM base as env-local

ARG PMA_USER=""
ARG PMA_PASSWORD=""
ENV DATABASE_DEFAULT_ALLOW_NO_PASSWORD="yes"
ENV PMA_USER=${PMA_USER}
ENV PMA_PASSWORD=${PMA_PASSWORD}

FROM base as env-prod
FROM base as env-stage
