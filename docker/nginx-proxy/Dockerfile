ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} as base

ARG ENV=""
ARG DOMAIN=""
ARG WHITELISTED_IP=""

COPY /docker/nginx-proxy/conf/sites.conf /etc/nginx/conf.d/sites.conf
COPY /docker/nginx-proxy/conf/sites.${ENV}.conf /etc/nginx/conf.d/sites_env.conf

RUN sed -i "s/{DOMAIN};/${DOMAIN};/g" /etc/nginx/conf.d/sites.conf
RUN sed -i "s/{DOMAIN};/${DOMAIN};/g" /etc/nginx/conf.d/sites_env.conf

RUN sed -i "s/#{ALLOW_IP};/${WHITELISTED_IP}/g" /etc/nginx/conf.d/sites.conf
RUN sed -i "s/#{ALLOW_IP};/${WHITELISTED_IP}/g" /etc/nginx/conf.d/sites_env.conf

COPY /docker/nginx-proxy/conf/nginx.conf /etc/nginx/nginx.conf

RUN mkdir -p /var/www/html
WORKDIR /var/www/html
RUN echo '<h1>Nginx proxy</h1>' > index.html

FROM base as env-local
FROM base as env-prod
FROM base as env-stage