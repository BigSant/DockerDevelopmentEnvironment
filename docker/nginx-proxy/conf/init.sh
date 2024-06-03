#!/bin/bash

domain="$1"
whitelist="$2"
timeout="$3"

if [[ -z "$domain" || -z "$whitelist" || -z "$timeout" ]]; then
  echo "No required parameters"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf
sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites_env.conf

sed -i "s/#{ALLOW_IP};/$whitelist/g" /etc/nginx/conf.d/sites.conf
sed -i "s/#{ALLOW_IP};/$whitelist/g" /etc/nginx/conf.d/sites_env.conf

sed -i "s/0; #{TIMEOUT}/$timeout;/g" /etc/nginx/conf.d/sites.conf
sed -i "s/0; #{TIMEOUT}/$timeout;/g" /etc/nginx/conf.d/sites_env.conf

exec nginx -g "daemon off;"
