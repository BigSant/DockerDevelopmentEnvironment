ARG LOKI_VERSION=""
ARG ROOT_DIRECTORY=""
FROM grafana/loki:${LOKI_VERSION} as base

COPY ${ROOT_DIRECTORY}/docker/loki/conf/loki-config.yaml /etc/loki/loki-config.yaml
CMD ["-config.file=/etc/loki/loki-config.yaml"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
