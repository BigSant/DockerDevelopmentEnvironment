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




#RUN mkdir -p /etc/nginx/ssl/certs
#RUN touch /etc/nginx/ssl/certs/nginx-selfsigned.crt

#RUN mkdir -p /etc/nginx/ssl/private
#RUN touch /etc/nginx/ssl/private/nginx-selfsigned.key

#COPY /conf/nginx/app.conf /etc/nginx/conf.d/app.conf
#RUN sed -i "s/server_name prestarock;/server_name ${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#RUN sed -i "s/server_name pma.prestarock;/server_name pma.${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#RUN sed -i "s/server_name mailpit.prestarock;/server_name mailpit.${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#
#RUN sed -i "s/server_name prometheus.prestarock;/server_name prometheus.${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#RUN sed -i "s/server_name grafana.prestarock;/server_name grafana.${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#RUN sed -i "s/server_name cadvisor.prestarock;/server_name cadvisor.${DOMAIN};/g" /etc/nginx/conf.d/app.conf
#
#COPY /conf/nginx/nginx.conf /etc/nginx/nginx.conf
