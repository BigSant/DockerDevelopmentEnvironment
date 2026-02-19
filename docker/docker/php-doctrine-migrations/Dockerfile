ARG PHP_VERSION=""
ARG COMPOSER_VERSION=""
FROM composer:${COMPOSER_VERSION} as composer

FROM php:${PHP_VERSION}-cli as base

ARG PHP_VERSION=""
ARG DOCTRINE_MIGRATIONS_VERSION=""

RUN apt-get update && apt-get install -y \
    git \
    bash \
    make \
    unzip \
    default-mysql-client \
    libzip-dev \
    && rm -rf /var/lib/apt/lists/*

RUN docker-php-ext-install pdo_mysql

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

RUN composer global require \
    doctrine/migrations:^${DOCTRINE_MIGRATIONS_VERSION} \
    doctrine/dbal \
    && composer clear-cache

ENV PATH="/root/.composer/vendor/bin:${PATH}"

# Store doctrine-migrations.php template outside the mount path
RUN mkdir -p /opt/doctrine-migrations /tmp/doctrine-migrations/config/ /tmp/doctrine-migrations/cache/ \
    && cat > /opt/doctrine-migrations/doctrine-migrations.php <<'EOF'
<?php

return [
    'table_storage' => [
        'table_name'                 => 'doctrine_migration_versions',
        'version_column_name'        => 'version',
        'version_column_length'      => 191,
        'executed_at_column_name'    => 'executed_at',
        'execution_time_column_name' => 'execution_time',
    ],
    'migrations_paths' => [
        'DoctrineMigrations' => '/tmp/doctrine-migrations/config/versions',
    ],
    'all_or_nothing'          => true,
    'transactional'           => true,
    'check_database_platform' => true,
    'organize_migrations'     => 'none',
    'connection'              => null,
    'em'                      => null,
];
EOF

RUN cat > /opt/doctrine-migrations/migrations-db.php <<'EOF'
<?php

return [
    'driver'   => 'pdo_mysql',
    'host'     => getenv('DATABASE_HOST'),
    'user'     => getenv('DATABASE_USER'),
    'password' => getenv('DATABASE_PASSWORD'),
    'dbname'   => getenv('DATABASE_NAME'),
    'charset'  => 'utf8mb4',
];
EOF

COPY /docker/php-doctrine-migrations/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-doctrine-migrations/Makefile /doctrine-migrations/Makefile
WORKDIR /doctrine-migrations

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
