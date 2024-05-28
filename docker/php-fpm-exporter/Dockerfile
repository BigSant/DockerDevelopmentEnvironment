ARG PHP_FPM_EXPORTER_VERSION=""
FROM hipages/php-fpm_exporter:${PHP_FPM_EXPORTER_VERSION} as base

CMD ["--phpfpm.scrape-uri=tcp://php-fpm:9000/fpm_stub_status", "--phpfpm.fix-process-count=true"]

FROM base as env-local
FROM base as env-prod
FROM base as env-stage
