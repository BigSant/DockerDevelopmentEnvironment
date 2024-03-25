ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} as base

ARG DOMAIN=""

COPY /docker/nginx/conf/nginx.conf /etc/nginx/nginx.conf

COPY /docker/nginx/conf/sites.conf /etc/nginx/conf.d/sites.conf
RUN sed -i "s/{DOMAIN};/${DOMAIN};/g" /etc/nginx/conf.d/sites.conf

RUN touch /var/run/nginx.pid && chown -R root:root /var/cache/nginx /var/run/nginx.pid
RUN usermod -u 0 -o nginx && groupmod -g 0 -o nginx

RUN mkdir -p /var/www/html
WORKDIR /var/www/html

FROM base as env-local

FROM base as env-prod
#COPY ./../../app /var/www/html
RUN chown -R root:root /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

FROM base as env-stage
#COPY ./../../app /var/www/html
RUN chown -R root:root /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +
