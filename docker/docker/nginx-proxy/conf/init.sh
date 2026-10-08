#!/bin/bash

set -euo pipefail

# Effective project drop-ins: copy and render *.template with this container's environment.
sh /usr/local/bin/render-templates.sh /etc/nginx/project.d /etc/nginx/project.r /etc/nginx/project.d.env /etc/nginx/project.r.env

: > /etc/nginx/conf.d/sites_env.conf
/usr/local/bin/access.sh
if [[ "${SETUP_ENABLE_PMA:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-pma.conf >> /etc/nginx/conf.d/sites_env.conf; fi
if [[ "${SETUP_ENABLE_MAILPIT:-0}" == 1 ]]; then cat /etc/nginx/templates/setup-mailpit.conf >> /etc/nginx/conf.d/sites_env.conf; fi
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

exec nginx -g "daemon off;"
