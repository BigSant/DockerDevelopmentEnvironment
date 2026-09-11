#!/bin/bash
if [ "${CACHE_MODE:-off}" = off ]; then
    sed -i -E 's/^[[:space:]]*open_file_cache[[:space:]].*/    open_file_cache off;/' /etc/nginx/nginx.conf
    sed -i 's/expires 30d;/expires off; add_header Cache-Control "no-store" always;/' /etc/nginx/conf.d/sites.conf
fi

domain="$1"
document_root="$2"
timeout="$3"

if [[ -z "$domain" ]]; then
  echo "Domain is required"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/custom_conf/sites.custom.before.conf
sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/custom_conf/sites.custom.root.conf
sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/custom_conf/sites.custom.conf
sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/custom_conf/sites.custom.after.conf

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf
#sed -i "s/#{SITES_CUSTOM_CONFIG_BEFORE}/$custom_before/g" /etc/nginx/conf.d/sites.conf
#sed -i "s|#{SITES_CUSTOM_CONFIG_ROOT}|$custom_root|g" /etc/nginx/conf.d/sites.conf
#sed -i "s|#{SITES_CUSTOM_CONFIG}|$custom|g" /etc/nginx/conf.d/sites.conf
#sed -i "s|#{SITES_CUSTOM_CONFIG_AFTER}|$custom_after|g" /etc/nginx/conf.d/sites.conf

sed -i "s/0; #{TIMEOUT}/$timeout;/g" /etc/nginx/conf.d/sites.conf

if [[ ! -z "$document_root" && "$document_root" != "/" ]]; then
  sed -i "s#root /var/www/html;#root /var/www/html$document_root;#g" /etc/nginx/conf.d/sites.conf
fi

exec nginx -g "daemon off;"
