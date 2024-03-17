FROM prom/mysqld-exporter

ARG EXPORTER_PASSWORD=""

USER root
COPY /docker/mysqld-exporter/conf/my.cnf /.my.cnf
RUN sed -i "s/{EXPORTER_PASSWORD}/${EXPORTER_PASSWORD}/g" /.my.cnf

USER nobody
CMD ["--config.my-cnf=/.my.cnf"]
