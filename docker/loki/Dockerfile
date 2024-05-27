ARG LOKI_VERSION=""
FROM grafana/loki:${LOKI_VERSION} as base

COPY ./docker/loki/conf/loki.yml /etc/loki/loki-config.yml
CMD ["-config.file=/etc/loki/loki-config.yml"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
