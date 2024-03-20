FROM certbot/certbot:v1.8.0 as base

ARG DOMAIN=""

CMD ["certonly", "--webroot", "--agree-tos", "--renew-by-default", "--preferred-challenges", "http-01", "--server", "https://acme-v02.api.letsencrypt.org/directory", "--text", "--email", "tomas.z@prestarock.com", "-w", "/tmp/letsencrypt", "-d", "prestashop-framework.local"]


FROM base as env-local

FROM base as env-prod

FROM base as env-stage
