ARG APACHE_VERSION=""
FROM httpd:${APACHE_VERSION} AS base

ARG PROFILE=""

# Copy custom Apache configs
COPY /docker/apache/conf/httpd.custom.conf /usr/local/apache2/conf/httpd.custom.conf
RUN echo 'Include /usr/local/apache2/conf/httpd.custom.conf' >> /usr/local/apache2/conf/httpd.conf

# Profile-specific Apache config (PROFILE=prestashop|akeneo|"").
# Copy the whole profile tree, then apply only the file that exists for the selected
# profile. IncludeOptional + the always-present empty dir mean PROFILE="" is a no-op.
RUN mkdir -p /usr/local/apache2/conf/profile/
COPY /profile/ /tmp/profile/
RUN if [ -n "${PROFILE}" ] && [ -s "/tmp/profile/${PROFILE}/apache/conf/httpd.custom.conf" ]; then \
        cp "/tmp/profile/${PROFILE}/apache/conf/httpd.custom.conf" /usr/local/apache2/conf/profile/httpd.custom.conf; \
    fi; \
    rm -rf /tmp/profile
RUN echo 'IncludeOptional /usr/local/apache2/conf/profile/*.conf' >> /usr/local/apache2/conf/httpd.conf

# Per-project Apache config override (bind-mounted at runtime). Loaded AFTER the profile
# include so project .conf files win. project/ = always, project.env/ = ENV-match.
RUN mkdir -p /usr/local/apache2/conf/project /usr/local/apache2/conf/project.env \
    && echo 'IncludeOptional /usr/local/apache2/conf/project/*.conf'     >> /usr/local/apache2/conf/httpd.conf \
    && echo 'IncludeOptional /usr/local/apache2/conf/project.env/*.conf' >> /usr/local/apache2/conf/httpd.conf

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
