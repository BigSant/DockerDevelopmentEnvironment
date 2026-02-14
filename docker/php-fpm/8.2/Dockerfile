ARG PHP_VERSION=""
ARG COMPOSER_VERSION=""
FROM composer:${COMPOSER_VERSION} as composer
FROM ghcr.io/phpstan/phpstan:1 as phpstan

FROM php:${PHP_VERSION}-fpm as base

ARG PHP_VERSION=""

RUN apt-get update \
    && apt-get install -y libmcrypt-dev \
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
        libonig-dev

RUN apt-get install -y pv

RUN apt-get install -y gnupg \
    curl -fsSL https://dl.yarnpkg.com/debian/pubkey.gpg | apt-key add \
    echo "deb https://dl.yarnpkg.com/debian/ stable main" | tee /etc/apt/sources.list.d/yarn.list \
    apt-get update && apt-get install -y yarn

# Install APCu and APC backward compatibility
RUN pecl install apcu
RUN docker-php-ext-enable apcu --ini-name 10-docker-php-ext-apcu.ini \
    && docker-php-ext-enable apcu --ini-name 20-docker-php-ext-apc.ini

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

COPY --from=phpstan /composer/vendor/phpstan/phpstan/phpstan.phar /usr/local/bin/phpstan
RUN chmod a+x /usr/local/bin/phpstan

RUN apt-get install -y cron

RUN rm -rf /var/lib/apt/lists/*
RUN #docker-php-ext-configure gd --with-freetype=/usr/include/ --with-jpeg=/usr/include/ --with-webp=/usr/include
RUN docker-php-ext-install iconv intl pdo_mysql mbstring soap gd zip bcmath exif
RUN docker-php-ext-enable exif

RUN docker-php-source extract \
    && if [ -d "/usr/src/php/ext/mysql" ]; then docker-php-ext-install mysql; fi \
    && if [ -d "/usr/src/php/ext/mcrypt" ]; then docker-php-ext-install mcrypt; fi \
    && if [ -d "/usr/src/php/ext/opcache" ]; then docker-php-ext-install opcache; fi \
    && docker-php-source delete

COPY /docker/php-fpm/${PHP_VERSION}/conf/php.ini /usr/local/etc/php/php.ini

COPY /docker/php-fpm/conf/write-node-exporter-metric.sh /usr/local/bin/write-node-exporter-metric
RUN chmod +x /usr/local/bin/write-node-exporter-metric
CMD ["--collector.textfile.directory=/hostfs/var/tmp"]

ARG CONTAINER_USER="root"
ARG CONTAINER_USERID="0"
ARG CONTAINER_GROUP="root"
ARG CONTAINER_GROUPID="0"

RUN getent group ${CONTAINER_GROUPID} || addgroup --gid ${CONTAINER_GROUPID} ${CONTAINER_GROUP}
RUN id ${CONTAINER_USER} || adduser --system --no-create-home --disabled-password --uid ${CONTAINER_USERID} --ingroup ${CONTAINER_GROUP} ${CONTAINER_USER}

COPY /docker/php-fpm/${PHP_VERSION}/conf/php-fpm.conf /usr/local/etc/php-fpm.d/php-fpm.conf
RUN sed -i "s#user = www-data#user = ${CONTAINER_USER}#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#group = www-data#group = ${CONTAINER_GROUP}#g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s#;pm\.status_path = \/status#pm\.status_path = \/fpm_stub_status#g" /usr/local/etc/php-fpm.d/www.conf

RUN mkdir -p /var/www/html
#COPY /app /var/www/html

RUN chown -R ${CONTAINER_USER}:${CONTAINER_GROUP} /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

RUN usermod -u 0 -o www-data && groupmod -g 0 -o www-data
WORKDIR /var/www/html

CMD ["cron", "-f"]
CMD ["php-fpm", "-R"]

FROM base as env-local

ARG PHP_VERSION=""
ARG XDEBUG_VERSION=""

RUN pecl install xdebug-${XDEBUG_VERSION} \
    && docker-php-ext-enable xdebug
#    && docker-php-source delete \

COPY /docker/php-fpm/${PHP_VERSION}/conf/xdebug.ini /usr/local/etc/php/conf.d/docker-php-ext-xdebug.ini
#COPY /docker/php-fpm/${PHP_VERSION}/conf/xdebug.ini /usr/local/etc/php/conf.d/xdebug.ini

CMD ["php-fpm", "-R"]

FROM base as env-prod
FROM base as env-stage