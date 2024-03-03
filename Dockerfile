FROM php:8.1-nginx




#
#ARG PROJECT_NAME=""
#ARG ENV=""
#
#ARG DATABSE_PORT="presta"
#ARG PS_VERSION_TAB="presta"
#ARG LOCALHOST_PORT=8080
#
#
#COPY /app /var/www/html
#






#RUN apt-get update
#RUN apt-get install -y libmcrypt-dev \
#                            libjpeg-dev \
#                       		libjpeg62-turbo-dev \
#                       		libpcre3-dev \
#                       		libpng-dev \
#                       		libfreetype6-dev \
#                       		libxml2-dev \
#                       		libicu-dev \
#                       		libzip-dev \
#                       		libmemcached-dev \
#                       		default-mysql-client \
#                       		zip \
#                            unzip \
#                            git
#RUN apt-get install -y sendmail
#RUN docker-php-ext-configure gd \
#        --with-freetype-dir=/usr/include/ \
#        --with-jpeg-dir=/usr/include/
#
#RUN docker-php-ext-install zip iconv pdo_mysql intl soap gd
#RUN pecl install mcrypt-1.0.3 memcached
#RUN docker-php-ext-enable mcrypt opcache memcached
#RUN curl -sS https://getcomposer.org/installer | php -- --install-dir=/usr/local/bin --filename=composer
#
#COPY 000-default.conf /etc/apache2/sites-available/000-default.conf
#COPY mpm_prefork.conf /etc/apache2/mods-available/mpm_prefork.conf
#COPY php.ini /usr/local/etc/php/php.ini
#
#RUN sed -i 's/LogFormat "%h %l %u %t \\"%r\\" %>s %O \\"%{Referer}i\\" \\"%{User-Agent}i\\"" combined/LogFormat "%h %l %u %t \\"%r\\" %>s %O \\"%{Referer}i\\" \\"%{User-Agent}i\\" %D" combined/' /etc/apache2/apache2.conf
#
#EXPOSE 80
#
#RUN a2enmod rewrite
#
#ARG SSH_PRIVATE_KEY
#RUN mkdir /root/.ssh/
#RUN touch /root/.ssh/known_hosts
#RUN echo "${SSH_PRIVATE_KEY}" > /root/.ssh/id_rsa
#RUN chmod 600 ~/.ssh/id_rsa
#RUN ssh-keyscan bitbucket.org >> /root/.ssh/known_hosts
#
#ARG DATABASE_HOST=""
#ARG DATABASE_PORT=""
#ARG DATABASE_NAME=""
#ARG DATABASE_USER=""
#ARG DATABASE_PASSWORD=""
#ARG MAILER_HOST=""
#ARG MAILER_USER=""
#ARG MAILER_PASSWORD=""
#ARG SHOP_SECRET=""
#ARG SHOP_COOKIE_KEY=""
#ARG SHOP_COOKIE_IV=""
#ARG SHOP_NEW_COOKIE_KEY=""
#COPY ./camelia /var/www/html
#RUN chown -R www-data:www-data /var/www/html
#COPY ./parameters.php /var/www/html/app/config/parameters.php
#COPY ./parameters-init.php /var/www/html/app/config/parameters-init.php
#RUN cd /var/www/html/app/config/ && php parameters-init.php "${DATABASE_HOST}" "${DATABASE_PORT}" "${DATABASE_NAME}" "${DATABASE_USER}" "${DATABASE_PASSWORD}" "${MAILER_HOST}" "${MAILER_USER}" "${MAILER_PASSWORD}" "${SHOP_SECRET}" "${SHOP_COOKIE_KEY}" "${SHOP_COOKIE_IV}" "${SHOP_NEW_COOKIE_KEY}"
#RUN rm /var/www/html/app/config/parameters-init.php
#RUN cd /var/www/html/modules/custommodifications && composer install  --no-dev --optimize-autoloader --prefer-dist
#RUN ln -s /var/www/html/img/ps_imageslider /var/www/html/modules/ps_imageslider/images
#RUN ln -s /var/www/html/img/ps_imageslider2 /var/www/html/modules/ps_imageslider2/images
#RUN ln -s /var/www/html/img/ph_simpleblog /var/www/html/modules/ph_simpleblog/galleries
#RUN ln -s /var/www/html/img/ph_simpleblog_covers /var/www/html/modules/ph_simpleblog/covers
#RUN ln -s /var/www/html/img/ph_simpleblog_covers_cat /var/www/html/modules/ph_simpleblog/covers_cat
#RUN ln -s /var/www/html/img/bonpromotion /var/www/html/modules/bonpromotion/images
#RUN ln -s /var/www/html/img/custombanners /var/www/html/modules/custombanners/views/img/uploads
#RUN rm -rf /var/www/html/var/cache/
#RUN chmod -R 777 /var/www/html/var/
#RUN chmod -R 777 /var/www/html/modules/
#RUN chown -R www-data:www-data /var/www/html
