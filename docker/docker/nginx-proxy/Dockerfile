ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} AS base

ARG ENV=""

COPY /docker/nginx-proxy/conf/nginx.conf      /etc/nginx/nginx.conf
COPY /docker/nginx-proxy/conf/proxy_params    /etc/nginx/proxy_params
COPY /docker/nginx-proxy/conf/sites.conf      /etc/nginx/conf.d/sites.conf
COPY /docker/nginx-proxy/conf/sites.pma.conf /etc/nginx/templates/setup-pma.conf
COPY /docker/nginx-proxy/conf/sites.mailpit.conf /etc/nginx/templates/setup-mailpit.conf

COPY /docker/nginx-proxy/conf/init.sh /usr/local/bin/init.sh
COPY /docker/nginx-proxy/conf/pma-access.sh /usr/local/bin/pma-access.sh
RUN chmod +x /usr/local/bin/init.sh /usr/local/bin/pma-access.sh

# Per-project drop-in dirs (bind-mounted read-only at runtime) and their rendered copies.
# project.d = always, project.d.env = ENV-match; nginx includes the rendered project.r*.
RUN mkdir -p /etc/nginx/project.d /etc/nginx/project.d.env /etc/nginx/project.r /etc/nginx/project.r.env
# Renders project drop-ins (*.template) into the effective project.r directories at start.
COPY /runtime/render-templates.sh /usr/local/bin/render-templates.sh

RUN mkdir -p /var/www/html
WORKDIR /var/www/html
RUN echo '<h1>Nginx proxy</h1>' > index.html

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
