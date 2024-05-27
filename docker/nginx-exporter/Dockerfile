ARG NGINX_EXPORTER_VERSION=""
FROM nginx/nginx-prometheus-exporter:${NGINX_EXPORTER_VERSION} as base

CMD ["-nginx.scrape-uri=http://nginx/stub_status"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
