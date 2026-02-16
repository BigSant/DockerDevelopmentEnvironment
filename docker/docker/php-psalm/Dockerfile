ARG PHP_PSALM_VERSION=""
FROM ghcr.io/danog/psalm:${PHP_PSALM_VERSION} as base

# Store psalm.xml template outside the mount path
RUN mkdir -p /opt/psalm /tmp/analysis/psalm/cache && \
    cat > /opt/psalm/psalm.xml <<'EOF'
<?xml version="1.0"?>
<psalm
    errorBaseline="/tmp/analysis/psalm/psalm-baseline.xml"
    resolveFromConfigFile="false"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xmlns="https://getpsalm.org/schema/config"
    xsi:schemaLocation="https://getpsalm.org/schema/config vendor/vimeo/psalm/config.xsd"
    errorLevel="4"
    cacheDirectory="/tmp/analysis/psalm/cache"
>
    <projectFiles>
        <directory name="/var/www/html/" />
    </projectFiles>
</psalm>
EOF

COPY /docker/php-psalm/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-psalm/Makefile /psalm/Makefile
RUN chmod +x /psalm/Makefile
WORKDIR /psalm

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
