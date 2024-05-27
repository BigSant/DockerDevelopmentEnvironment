#!/bin/bash

domain="$1"

if [[ -z "$domain" ]]; then
  echo "Domain is required"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf

exec nginx -g "daemon off;"
