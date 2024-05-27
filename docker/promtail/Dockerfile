ARG PROMTAIL_VERSION=""
FROM grafana/promtail:${PROMTAIL_VERSION} as base

COPY ./docker/promtail/conf/promtail.yml /etc/promtail/promtail-config.yml
CMD ["-config.file=/etc/promtail/promtail-config.yml"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
