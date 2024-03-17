FROM prom/prometheus:v2.50.1

COPY /docker/prometheus/conf/prometheus.yml /etc/prometheus-config/prometheus.yaml

CMD ["--config.file=/etc/prometheus-config/prometheus.yaml"]
