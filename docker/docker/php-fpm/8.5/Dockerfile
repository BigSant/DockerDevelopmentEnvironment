ARG PHP_VERSION=""
ARG COMPOSER_VERSION=""
ARG PHPSTAN_PHAR_VERSION
FROM composer:${COMPOSER_VERSION} AS composer
FROM ghcr.io/phpstan/phpstan:${PHPSTAN_PHAR_VERSION} AS phpstan

FROM php:${PHP_VERSION}-fpm AS base

ARG PHP_VERSION=""
ARG APCU_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmcrypt-dev \
        libjpeg62-turbo-dev \
        libpng-dev \
        libwebp-dev \
        libfreetype6-dev \
        libxml2-dev \
        libicu-dev \
        libzip-dev \
        default-mysql-client \
        wget \
        unzip \
        libonig-dev \
        pv \
    && rm -rf /var/lib/apt/lists/*

# Install APCu and APC backward compatibility
RUN pecl install apcu-${APCU_VERSION}
RUN docker-php-ext-enable apcu --ini-name 10-docker-php-ext-apcu.ini \
    && docker-php-ext-enable apcu --ini-name 20-docker-php-ext-apc.ini

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

COPY --from=phpstan /composer/vendor/phpstan/phpstan/phpstan.phar /usr/local/bin/phpstan
RUN chmod a+x /usr/local/bin/phpstan

ARG SUPERCRONIC_VERSION
ARG SUPERCRONIC_SHA1SUM
RUN curl -fsSLo /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

ARG NODE_VERSION=""
ARG NPM_VERSION
ARG WEBPACK_VERSION
ARG WEBPACK_CLI_VERSION
ARG NVM_VERSION
ARG NVM_INSTALL_SHA256
# nvm lives in /usr/local/nvm, NOT /root/.nvm: the php-fpm pool runs as the host
# user (compose `user:`), and /root is 0700 — a non-root uid cannot even traverse
# it, so `npm run build` inside the container died with "command not found".
# The node/npm/npx binaries are symlinked into /usr/local/bin so they resolve on
# the default PATH for every user and every shell kind (sh -c included), not just
# an interactive root bash that happened to source .bashrc.
ENV NVM_DIR=/usr/local/nvm
RUN mkdir -p "${NVM_DIR}" \
    && curl -fsSLo /tmp/nvm-install.sh "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" \
    && echo "${NVM_INSTALL_SHA256}  /tmp/nvm-install.sh" | sha256sum -c - \
    && bash /tmp/nvm-install.sh \
    && rm /tmp/nvm-install.sh \
    && . "${NVM_DIR}/nvm.sh" && nvm install ${NODE_VERSION} && npm install -g "npm@${NPM_VERSION}" \
    && npm install -g "webpack@${WEBPACK_VERSION}" "webpack-cli@${WEBPACK_CLI_VERSION}" \
    && NODE_BIN="$(ls -d ${NVM_DIR}/versions/node/*/bin | head -n1)" \
    && for b in node npm npx corepack webpack webpack-cli; do \
           if [ -e "$NODE_BIN/$b" ]; then ln -sf "$NODE_BIN/$b" "/usr/local/bin/$b"; fi; \
       done \
    && chmod -R a+rX "${NVM_DIR}" \
    && npm --version && node --version
RUN printf '%s\n' 'export NVM_DIR="/usr/local/nvm"' '[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' > /etc/profile.d/nvm.sh

RUN docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp
RUN docker-php-ext-install iconv intl pdo_mysql mbstring soap gd zip bcmath exif
RUN docker-php-ext-enable exif
RUN docker-php-ext-enable gd

# PHP 8.5 includes OPcache in the binary; building it as a shared extension fails.
RUN docker-php-source extract \
    && if [ -d "/usr/src/php/ext/mysql" ]; then docker-php-ext-install mysql; fi \
    && if [ -d "/usr/src/php/ext/mcrypt" ]; then docker-php-ext-install mcrypt; fi \
    && docker-php-source delete

COPY /docker/php-fpm/${PHP_VERSION}/conf/php.ini /usr/local/etc/php/php.ini

ARG CONTAINER_USER="root"
ARG CONTAINER_USERID="0"
ARG CONTAINER_GROUP="root"
ARG CONTAINER_GROUPID="0"

RUN getent group ${CONTAINER_GROUPID} || addgroup --gid ${CONTAINER_GROUPID} ${CONTAINER_GROUP}
RUN id ${CONTAINER_USER} || adduser --system --no-create-home --disabled-password --uid ${CONTAINER_USERID} --ingroup ${CONTAINER_GROUP} ${CONTAINER_USER}

ENV PHP_FPM_MAX_CHILDREN=4 \
    PHP_FPM_START_SERVERS=1 \
    PHP_FPM_MIN_SPARE_SERVERS=1 \
    PHP_FPM_MAX_SPARE_SERVERS=2 \
    PHP_FPM_MAX_REQUESTS=500

COPY /docker/php-fpm/${PHP_VERSION}/conf/php-fpm.conf /usr/local/etc/php-fpm.d/zz-base-pool.conf
# The pool runs as the launching (host) user via compose `user:` — comment out the pool
# user/group so php-fpm does not attempt to setuid (avoids "directive ignored" notices when
# running as non-root and prevents root-owned files on the bind-mounted web dir).
RUN sed -i "s#^user = www-data#;user = www-data#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#^group = www-data#;group = www-data#g" /usr/local/etc/php-fpm.d/www.conf
# php-fpm status page intentionally disabled — FPM has no per-path ACL. Enable per project
# behind a restricted webserver route if pm.status monitoring is ever needed.

RUN mkdir -p /var/www/html
#COPY /app /var/www/html

RUN chown -R ${CONTAINER_USER}:${CONTAINER_GROUP} /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

WORKDIR /var/www/html

CMD ["php-fpm", "-R"]

FROM base AS env-local

ARG PHP_VERSION=""
ARG XDEBUG_VERSION=""

RUN pecl install xdebug-${XDEBUG_VERSION} \
    && docker-php-ext-enable xdebug \
    && docker-php-source delete

COPY /docker/php-fpm/${PHP_VERSION}/conf/xdebug.ini /usr/local/etc/php/conf.d/docker-php-ext-xdebug.ini

FROM base AS env-prod
FROM base AS env-stage
