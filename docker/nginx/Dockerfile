FROM nginx:1.25

ARG DOMAIN=""

COPY /docker/nginx/conf/nginx.conf /etc/nginx/nginx.conf

COPY /docker/nginx/conf/sites.conf /etc/nginx/conf.d/sites.conf
RUN sed -i "s/{DOMAIN};/${DOMAIN};/g" /etc/nginx/conf.d/sites.conf

RUN touch /var/run/nginx.pid && chown -R root:root /var/cache/nginx /var/run/nginx.pid
RUN usermod -u 0 -o nginx && groupmod -g 0 -o nginx

RUN mkdir -p /var/www/html
#COPY /app /var/www/html
RUN chown -R root:root /var/www/html/
RUN find /var/www/html/ -type d -exec chmod 755 {} +
RUN find /var/www/html/ -type f -exec chmod 644 {} +

WORKDIR /var/www/html
RUN echo '<h1>Nginx</h1>' > index.html
