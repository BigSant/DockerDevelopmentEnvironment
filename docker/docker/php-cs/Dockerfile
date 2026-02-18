ARG PHP_CS_VERSION=""

FROM ghcr.io/php-cs-fixer/php-cs-fixer:${PHP_CS_VERSION} as base

# Store .php-cs-fixer.php template outside the mount path
RUN mkdir -p /opt/php-cs-fixer /tmp/php-cs-fixer/cache/ && cat > /opt/php-cs-fixer/.php-cs-fixer.php <<'EOF'
<?php

$finder = PhpCsFixer\Finder::create()
    ->in('/var/www/html');

return (new PhpCsFixer\Config())
    ->setRules([
        '@PSR12' => true,
        '@PHP80Migration' => true,
    ])
    ->setFinder($finder)
    ->setCacheFile('/tmp/php-cs-fixer/cache/.php-cs-fixer.cache');
EOF

RUN apk add --no-cache git bash make

COPY /docker/php-cs/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY /docker/php-cs/Makefile /php-cs/Makefile
WORKDIR /php-cs

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
