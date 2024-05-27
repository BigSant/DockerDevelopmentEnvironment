ARG GRAFANA_VERSION=""
FROM grafana/grafana-oss:${GRAFANA_VERSION} as base

RUN mkdir -p /var/lib/grafana

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
