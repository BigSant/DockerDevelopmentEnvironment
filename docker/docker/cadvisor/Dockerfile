ARG CADVISOR_VERSION=""
FROM gcr.io/cadvisor/cadvisor:v${CADVISOR_VERSION} as base

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
