ARG PROMTAIL_VERSION=""
FROM grafana/promtail:${PROMTAIL_VERSION} as base

COPY ./docker/promtail/conf/promtail-config.yaml /etc/promtail/promtail-config.yaml
CMD ["-config.file=/etc/promtail/promtail-config.yaml"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
