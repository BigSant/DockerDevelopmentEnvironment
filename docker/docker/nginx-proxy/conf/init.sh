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
if [[ "${SETUP_ENABLE_BACKUP:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-backup.conf >> /etc/nginx/conf.d/sites_env.conf; fi
if [[ "${SETUP_ENABLE_METRICS:-0}" == 1 ]]; then
    cat /etc/nginx/templates/setup-metrics.conf >> /etc/nginx/conf.d/sites_env.conf
    # This proxy's TCP table for the metrics service's security group (the log collector keeps the access log).
    mkdir -p /var/log/setup-http
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

# DOMAIN may list several hostnames (setup passes them all in SETUP_DOMAIN_HOSTS): the site answers to each.
names=""
alternatives="DNS:$domain,DNS:*.$domain"
for host in ${SETUP_DOMAIN_HOSTS:-}; do
  [[ "$host" == "$domain" ]] && continue
  if [[ ! "$host" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
    echo "[init] ERROR: SETUP_DOMAIN_HOSTS holds an invalid hostname" >&2
    exit 1
  fi
  names+=" $host www.$host"
  alternatives+=",DNS:$host"
done
if [[ -n "$names" ]]; then
  sed -i "s/^\([[:space:]]*\)# {DOMAIN_HOSTS}:.*/\1server_name$names;/" /etc/nginx/conf.d/sites.conf
else
  sed -i "/# {DOMAIN_HOSTS}:/d" /etc/nginx/conf.d/sites.conf
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
        -addext "subjectAltName=$alternatives" \
        -keyout "$generated/domain.key" -out "$generated/domain.crt" 2>/dev/null)
    chmod 600 "$generated/domain.key"
    echo "[init] TLS: no certificate in data/<env>/ssl; generated a self-signed pair for $domain"
  fi
  sed -i "s#$provisioned/domain\.#$generated/domain.#g" /etc/nginx/conf.d/sites.conf /etc/nginx/conf.d/sites_env.conf
fi

exec nginx -g "daemon off;"
