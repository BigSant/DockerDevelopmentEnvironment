ARG LOKI_VERSION=""
FROM grafana/loki:${LOKI_VERSION} as base

COPY ./docker/loki/conf/loki-config.yaml /etc/loki/loki-config.yaml
CMD ["-config.file=/etc/loki/loki-config.yaml"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
