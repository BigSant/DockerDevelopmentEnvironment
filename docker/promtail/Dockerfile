FROM grafana/promtail:latest

COPY ./docker/promtail/conf/promtail.yml /etc/promtail/promtail-config.yml

CMD ["/usr/bin/promtail", "-config.file=/etc/config/promtail-local-config.yaml"]
