ARG NGINX_VERSION=""
FROM nginx:${NGINX_VERSION} as base

COPY /docker/nginx/conf/nginx.conf /etc/nginx/nginx.conf
COPY /docker/nginx/conf/default.conf /etc/nginx/conf.d/default.conf
COPY /docker/nginx/conf/sites.conf /etc/nginx/conf.d/sites.conf

RUN mkdir -p /etc/nginx/custom_conf/
COPY /config/nginx/sites.custom.before.conf /etc/nginx/custom_conf/sites.custom.before.conf
COPY /config/nginx/sites.custom.conf /etc/nginx/custom_conf/sites.custom.conf
COPY /config/nginx/sites.custom.root.conf /etc/nginx/custom_conf/sites.custom.root.conf
COPY /config/nginx/sites.custom.after.conf /etc/nginx/custom_conf/sites.custom.after.conf

ARG PROFILE=""
# TODO: Profile should validate if is set
COPY /config/nginx/${PROFILE}/sites.custom.conf /etc/nginx/custom_conf/sites.custom.conf
COPY /config/nginx/${PROFILE}/sites.custom.root.conf /etc/nginx/custom_conf/sites.custom.root.conf

COPY /docker/nginx/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

RUN touch /var/run/nginx.pid && chown -R root:root /var/cache/nginx /var/run/nginx.pid
RUN usermod -u 0 -o nginx && groupmod -g 0 -o nginx

RUN mkdir -p /var/www/html
RUN chown -R root:root /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

WORKDIR /var/www/html

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
