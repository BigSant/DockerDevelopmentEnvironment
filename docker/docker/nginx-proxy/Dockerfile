ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} AS base

ARG ENV=""

COPY /docker/nginx-proxy/conf/nginx.conf      /etc/nginx/nginx.conf
COPY /docker/nginx-proxy/conf/proxy_params    /etc/nginx/proxy_params
COPY /docker/nginx-proxy/conf/sites.conf      /etc/nginx/conf.d/sites.conf
COPY /docker/nginx-proxy/conf/sites.${ENV}.conf /etc/nginx/conf.d/sites_env.conf

COPY /docker/nginx-proxy/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

# Per-project config override dirs (bind-mounted at runtime). Included after conf.d in
# nginx.conf so project *.conf win. project.d = always, project.d.env = ENV-match.
RUN mkdir -p /etc/nginx/project.d /etc/nginx/project.d.env

RUN mkdir -p /var/www/html
WORKDIR /var/www/html
RUN echo '<h1>Nginx proxy</h1>' > index.html

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
