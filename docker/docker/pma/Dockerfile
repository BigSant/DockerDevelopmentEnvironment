ARG PMA_VERSION=""
FROM phpmyadmin/phpmyadmin:${PMA_VERSION} AS base

RUN echo "ServerName localhost" >> /etc/apache2/apache2.conf

# The official image already ships the gd/zip runtime libraries. Their Debian package
# names differ per release (bullseye libzip4, trixie libzip5), so do not reinstall them.

FROM base AS env-local

# Credentials (PMA_USER/PMA_PASSWORD) are provided at runtime via compose `environment:`,
# not baked here — keeps them out of the shared image's layers and `docker history`.
ENV DATABASE_DEFAULT_ALLOW_NO_PASSWORD="yes"

FROM base AS env-prod
FROM base AS env-stage
