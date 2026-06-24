ARG PHP_VERSION=""
ARG COMPOSER_VERSION=""
FROM composer:${COMPOSER_VERSION} AS composer
FROM ghcr.io/phpstan/phpstan:1 AS phpstan

FROM php:${PHP_VERSION}-fpm AS base

ARG PHP_VERSION=""

RUN apt-get update
RUN apt-get install -y libmcrypt-dev
RUN apt-get install -y libjpeg62-turbo-dev
RUN apt-get install -y libpng-dev
RUN apt-get install -y libwebp-dev
RUN apt-get install -y libfreetype6-dev
RUN apt-get install -y libxml2-dev
RUN apt-get install -y libicu-dev
RUN apt-get install -y libzip-dev
RUN apt-get install -y default-mysql-client
RUN apt-get install -y wget
RUN apt-get install -y unzip
RUN apt-get install -y libonig-dev
RUN apt-get install -y pv

# Install APCu and APC backward compatibility
RUN pecl install apcu
RUN docker-php-ext-enable apcu --ini-name 10-docker-php-ext-apcu.ini \
    && docker-php-ext-enable apcu --ini-name 20-docker-php-ext-apc.ini

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

COPY --from=phpstan /composer/vendor/phpstan/phpstan/phpstan.phar /usr/local/bin/phpstan
RUN chmod a+x /usr/local/bin/phpstan

ARG SUPERCRONIC_VERSION=0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
RUN curl -fsSLo /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

ARG NODE_VERSION=""
RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.4/install.sh | bash && . ~/.nvm/nvm.sh && nvm install ${NODE_VERSION} && npm install -g webpack webpack-cli
RUN echo 'export NVM_DIR="$HOME/.nvm" && [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"' >> /root/.bashrc

RUN rm -rf /var/lib/apt/lists/*
RUN #docker-php-ext-configure gd --with-freetype=/usr/include/ --with-jpeg=/usr/include/ --with-webp=/usr/include
RUN docker-php-ext-install iconv intl pdo_mysql mbstring soap gd zip bcmath exif
RUN docker-php-ext-enable exif
RUN docker-php-ext-enable gd

RUN docker-php-source extract \
    && if [ -d "/usr/src/php/ext/mysql" ]; then docker-php-ext-install mysql; fi \
    && if [ -d "/usr/src/php/ext/mcrypt" ]; then docker-php-ext-install mcrypt; fi \
    && if [ -d "/usr/src/php/ext/opcache" ]; then docker-php-ext-install opcache; fi \
    && docker-php-source delete

COPY /docker/php-fpm/${PHP_VERSION}/conf/php.ini /usr/local/etc/php/php.ini

ARG CONTAINER_USER="root"
ARG CONTAINER_USERID="0"
ARG CONTAINER_GROUP="root"
ARG CONTAINER_GROUPID="0"

RUN getent group ${CONTAINER_GROUPID} || addgroup --gid ${CONTAINER_GROUPID} ${CONTAINER_GROUP}
RUN id ${CONTAINER_USER} || adduser --system --no-create-home --disabled-password --uid ${CONTAINER_USERID} --ingroup ${CONTAINER_GROUP} ${CONTAINER_USER}

COPY /docker/php-fpm/${PHP_VERSION}/conf/php-fpm.conf /usr/local/etc/php-fpm.d/php-fpm.conf
# The pool runs as the launching (host) user via compose `user:` — comment out the pool
# user/group so php-fpm does not attempt to setuid (avoids "directive ignored" notices when
# running as non-root and prevents root-owned files on the bind-mounted web dir).
RUN sed -i "s#^user = www-data#;user = www-data#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#^group = www-data#;group = www-data#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#;pm\.status_path = \/status#pm\.status_path = \/fpm_stub_status#g" /usr/local/etc/php-fpm.d/www.conf

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