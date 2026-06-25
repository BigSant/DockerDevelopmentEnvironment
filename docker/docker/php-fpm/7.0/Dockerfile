ARG PHP_VERSION=""
ARG COMPOSER_VERSION=""
ARG PHPSTAN_PHAR_VERSION=1.12.33
FROM composer:${COMPOSER_VERSION} AS composer
FROM ghcr.io/phpstan/phpstan:${PHPSTAN_PHAR_VERSION} AS phpstan

FROM php:${PHP_VERSION}-fpm AS base

ARG PHP_VERSION=""
ARG APCU_VERSION=5.1.28
ENV ACCEPT_EULA=Y

RUN sed -i \
    -e 's|http://deb.debian.org/debian|http://archive.debian.org/debian|g' \
    -e 's|http://security.debian.org/debian-security|http://archive.debian.org/debian-security|g' \
    -e '/buster-updates/d' \
    /etc/apt/sources.list

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libmcrypt-dev \
        libjpeg62-turbo-dev \
        libpcre3-dev \
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
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

COPY --from=phpstan /composer/vendor/phpstan/phpstan/phpstan.phar /usr/local/bin/phpstan
RUN chmod a+x /usr/local/bin/phpstan

ARG SUPERCRONIC_VERSION=0.2.29
ARG SUPERCRONIC_SHA1SUM=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
RUN curl -fsSLo /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
    && echo "${SUPERCRONIC_SHA1SUM}  /usr/local/bin/supercronic" | sha1sum -c - \
    && chmod +x /usr/local/bin/supercronic

RUN docker-php-ext-configure gd --with-freetype-dir=/usr/include/ --with-png-dir=/usr/include/ --with-jpeg-dir=/usr/include/
RUN docker-php-ext-install iconv intl pdo_mysql mbstring soap gd zip bcmath exif

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
RUN sed -i "s#^user = www-data#;user = www-data#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#^group = www-data#;group = www-data#g" /usr/local/etc/php-fpm.d/www.conf
# php-fpm status page intentionally disabled — FPM has no per-path ACL. Enable per project
# behind a restricted webserver route if pm.status monitoring is ever needed.
RUN sed -i "s#pm\.max_children = 5#pm\.max_children = 50#g" /usr/local/etc/php-fpm.d/www.conf

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