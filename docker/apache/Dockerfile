ARG APACHE_VERSION=""
FROM httpd:${APACHE_VERSION} as base

COPY /docker/apache/conf/httpd.custom.conf /usr/local/apache2/conf/httpd.custom.conf
RUN echo 'Include /usr/local/apache2/conf/httpd.custom.conf' >> /usr/local/apache2/conf/httpd.conf

COPY /docker/apache/conf/sites.conf /usr/local/apache2/conf/sites.conf
RUN echo 'Include /usr/local/apache2/conf/sites.conf' >> /usr/local/apache2/conf/httpd.conf

COPY /docker/apache/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

RUN mkdir -p /var/log/apache2

RUN #a2enmod ssl
RUN #a2enmod proxy
RUN #a2enmod proxy_balancer
RUN #a2enmod proxy_http

#RUN touch /var/run/nginx.pid && chown -R root:root /var/cache/nginx /var/run/nginx.pid
#RUN usermod -u 0 -o nginx && groupmod -g 0 -o nginx

RUN mkdir -p /var/www/html
RUN chown -R root:root /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

WORKDIR /var/www/html

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
