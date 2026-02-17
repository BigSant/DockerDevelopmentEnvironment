ARG PHP_PHPSTAN_VERSION=""

FROM ghcr.io/phpstan/phpstan:${PHP_PHPSTAN_VERSION} as base

# Store phpstan.neon template outside the mount path
RUN mkdir -p /opt/phpstan /tmp/phpstan/cache/ && cat > /opt/phpstan/phpstan.neon <<'EOF'
parameters:
    level: 6
    paths:
        - /var/www/html/
    tmpDir: /tmp/phpstan/cache
    errorFormat: table
EOF

RUN apk add --no-cache git bash make jq

COPY /docker/php-phpstan/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-phpstan/generate-report.sh /usr/local/bin/generate-report.sh
RUN chmod +x /usr/local/bin/generate-report.sh

COPY /docker/php-phpstan/Makefile /phpstan/Makefile
WORKDIR /phpstan

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
