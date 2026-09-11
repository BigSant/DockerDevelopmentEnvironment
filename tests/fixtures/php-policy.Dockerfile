FROM php:8.1-cli
RUN docker-php-ext-install pdo_mysql
