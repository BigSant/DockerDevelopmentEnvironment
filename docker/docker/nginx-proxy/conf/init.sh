#!/bin/bash

set -euo pipefail

# Effective project drop-ins: copy and render *.template with this container's environment.
sh /usr/local/bin/render-templates.sh /etc/nginx/project.d /etc/nginx/project.r /etc/nginx/project.d.env /etc/nginx/project.r.env

: > /etc/nginx/conf.d/sites_env.conf
/usr/local/bin/access.sh
if [[ "${SETUP_ENABLE_PMA:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-pma.conf >> /etc/nginx/conf.d/sites_env.conf; fi
if [[ "${SETUP_ENABLE_MAILPIT:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-mailpit.conf >> /etc/nginx/conf.d/sites_env.conf; fi
if [[ "${SETUP_ENABLE_CRON:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-cron.conf >> /etc/nginx/conf.d/sites_env.conf; fi
if [[ "${SETUP_ENABLE_DBMONITOR:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-dbmonitor.conf >> /etc/nginx/conf.d/sites_env.conf; fi
mkdir -p /etc/nginx/setup-site.d && rm -f /etc/nginx/setup-site.d/*.conf
if [[ "${SETUP_ENABLE_METRICS:-0}" == 1 ]]; then
    cp /etc/nginx/templates/setup-metrics.conf /etc/nginx/setup-site.d/metrics.conf
    # A second copy of the site's access log for the metrics service; docker logs keep theirs.
    mkdir -p /var/log/setup-http
    echo 'access_log /var/log/setup-http/access.log combined buffer=32k flush=5s;' > /etc/nginx/setup-site.d/http-log.conf
    /usr/local/bin/http-logs.sh &
fi
if [[ "${CACHE_MODE:-off}" == off ]]; then
    sed -i -E 's/^[[:space:]]*open_file_cache[[:space:]].*/    open_file_cache off;/' /etc/nginx/nginx.conf
fi

domain="$1"
timeout="$2"

if [[ -z "$domain" || -z "$timeout" ]]; then
  echo "No required parameters"
  exit 1
fi

sed -i "s/{DOMAIN}/$domain/g" /etc/nginx/conf.d/sites.conf
sed -i "s/{DOMAIN}/$domain/g" /etc/nginx/conf.d/sites_env.conf

sed -i "s/0; #{TIMEOUT}/$timeout;/g" /etc/nginx/conf.d/sites.conf
sed -i "s/0; #{TIMEOUT}/$timeout;/g" /etc/nginx/conf.d/sites_env.conf

# TLS: data/<env>/ssl holds the pair (mkcert locally, a real certificate where one is provisioned).
# A server that terminates TLS elsewhere (DirectAdmin) or serves plain HTTP (VPN) has none: then a
# self-signed pair is generated once per container, root-only, so the 443 listener still starts.
# A provisioned pair is never touched; half a pair is an error, not a reason to generate.
provisioned=/etc/ssl/certs
generated=/etc/nginx/ssl-generated
if [[ -e $provisioned/domain.crt || -e $provisioned/domain.key ]]; then
  if [[ ! -r $provisioned/domain.crt || ! -r $provisioned/domain.key ]]; then
    echo "[init] ERROR: $provisioned needs both domain.crt and domain.key (data/<env>/ssl)" >&2
    exit 1
  fi
else
  if [[ ! -s $generated/domain.crt || ! -s $generated/domain.key ]]; then
    (umask 077 && mkdir -p "$generated" &&
      openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=$domain" \
        -addext "subjectAltName=DNS:$domain,DNS:*.$domain" \
        -keyout "$generated/domain.key" -out "$generated/domain.crt" 2>/dev/null)
    chmod 600 "$generated/domain.key"
    echo "[init] TLS: no certificate in data/<env>/ssl; generated a self-signed pair for $domain"
  fi
  sed -i "s#$provisioned/domain\.#$generated/domain.#g" /etc/nginx/conf.d/sites.conf /etc/nginx/conf.d/sites_env.conf
fi

exec nginx -g "daemon off;"
