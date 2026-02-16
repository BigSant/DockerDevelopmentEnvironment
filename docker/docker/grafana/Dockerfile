ARG GRAFANA_VERSION=""
FROM grafana/grafana-oss:${GRAFANA_VERSION} as base

COPY /docker/grafana/conf/datasource_prometheus.yaml /etc/grafana/provisioning/datasources/prometheus.yaml
COPY /docker/grafana/conf/datasource_loki.yaml /etc/grafana/provisioning/datasources/loki.yaml

RUN mkdir -p /var/lib/grafana

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
