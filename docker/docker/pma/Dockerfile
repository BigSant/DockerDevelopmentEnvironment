ARG PMA_VERSION=""
FROM phpmyadmin/phpmyadmin:${PMA_VERSION} AS base

RUN echo "ServerName localhost" >> /etc/apache2/apache2.conf

RUN apt-get update && apt-get install -y libpng16-16 libzip5 --no-install-recommends && rm -rf /var/lib/apt/lists/*

FROM base AS env-local

# Credentials (PMA_USER/PMA_PASSWORD) are provided at runtime via compose `environment:`,
# not baked here — keeps them out of the shared image's layers and `docker history`.
ENV DATABASE_DEFAULT_ALLOW_NO_PASSWORD="yes"

FROM base AS env-prod
FROM base AS env-stage
