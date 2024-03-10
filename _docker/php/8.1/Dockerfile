FROM php:8.1-fpm-alpine

ARG CONTAINER_USER=""
ARG CONTAINER_USERID=""
ARG CONTAINER_GROUP=""
ARG CONTAINER_GROUPID=""

RUN getent group ${CONTAINER_GROUPID} || addgroup --gid ${CONTAINER_GROUPID} ${CONTAINER_GROUP}
RUN id ${CONTAINER_USER} || adduser --system --no-create-home --disabled-password --uid ${CONTAINER_USERID} --ingroup ${CONTAINER_GROUP} ${CONTAINER_USER}

#COPY ./php-fpm.conf /usr/local/etc/php-fpm.d/php-fpm.conf
RUN sed -i "s/user = www-data/user = ${CONTAINER_USER}/g" /usr/local/etc/php-fpm.d/www.conf
RUN sed -i "s/group = www-data/group = ${CONTAINER_GROUP}/g" /usr/local/etc/php-fpm.d/www.conf

RUN mkdir -p /var/www/html
COPY /app /var/www/html
RUN chown -R ${CONTAINER_USER}:${CONTAINER_GROUP} /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

USER ${CONTAINER_USER}
