ARG PROMETHEUS_VERSION=""
FROM prom/prometheus:v${PROMETHEUS_VERSION} as base

COPY /docker/prometheus/conf/prometheus.yml /etc/prometheus-config/prometheus.yaml

CMD ["--config.file=/etc/prometheus-config/prometheus.yaml"]

FROM base as env-local

FROM base as env-prod

FROM base as env-stage
