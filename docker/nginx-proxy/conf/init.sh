#!/bin/bash

domain="$1"
whitelist="$2"

if [[ -z "$domain" || -z "$whitelist" ]]; then
  echo "Domain and whitelist parameters is required"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf
sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites_env.conf

sed -i "s/#{ALLOW_IP};/$whitelist/g" /etc/nginx/conf.d/sites.conf
sed -i "s/#{ALLOW_IP};/$whitelist/g" /etc/nginx/conf.d/sites_env.conf

exec nginx -g "daemon off;"
