ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} as base

ARG ENV=""

COPY /docker/nginx-proxy/conf/nginx.conf /etc/nginx/nginx.conf
COPY /docker/nginx-proxy/conf/sites.conf /etc/nginx/conf.d/sites.conf
COPY /docker/nginx-proxy/conf/sites.${ENV}.conf /etc/nginx/conf.d/sites_env.conf

COPY /docker/nginx-proxy/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

RUN mkdir -p /var/www/html
WORKDIR /var/www/html
RUN echo '<h1>Nginx proxy</h1>' > index.html

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
