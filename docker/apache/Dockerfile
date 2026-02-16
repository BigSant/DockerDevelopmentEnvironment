ARG APACHE_VERSION=""
FROM httpd:${APACHE_VERSION} AS base

# Copy custom Apache configs
COPY /docker/apache/conf/httpd.custom.conf /usr/local/apache2/conf/httpd.custom.conf
RUN echo 'Include /usr/local/apache2/conf/httpd.custom.conf' >> /usr/local/apache2/conf/httpd.conf

COPY /docker/apache/conf/sites.conf /usr/local/apache2/conf/sites.conf
RUN echo 'Include /usr/local/apache2/conf/sites.conf' >> /usr/local/apache2/conf/httpd.conf

# Copy and enable init script
COPY /docker/apache/conf/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh

# Create required directories
RUN mkdir -p /var/log/apache2 \
    && mkdir -p /etc/apache2/ssl \
    && mkdir -p /var/www/html \
    && chown -R root:root /var/www/html/ \
    && find /var/www/html/ -type d -exec chmod 755 {} + \
    && find /var/www/html/ -type f -exec chmod 644 {} +

WORKDIR /var/www/html

FROM base AS env-local
FROM base AS env-prod
FROM base AS env-stage
