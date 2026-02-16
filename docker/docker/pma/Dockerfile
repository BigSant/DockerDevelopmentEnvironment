ARG PMA_VERSION=""
FROM phpmyadmin/phpmyadmin:${PMA_VERSION} as base

RUN apt-get update && apt-get install -y libpng16-16 libzip4 --no-install-recommends && rm -rf /var/lib/apt/lists/*

FROM base as env-local

ARG PMA_USER=""
ARG PMA_PASSWORD=""
ENV DATABASE_DEFAULT_ALLOW_NO_PASSWORD="yes"
ENV PMA_USER=${PMA_USER}
ENV PMA_PASSWORD=${PMA_PASSWORD}

FROM base as env-prod
FROM base as env-stage
