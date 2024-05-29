#!/bin/bash

domain="$1"
document_root="$2"

if [[ -z "$domain" ]]; then
  echo "Domain is required"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf

if [[ ! -z "$document_root" && "$document_root" != "/" ]]; then
  sed -i "s#root /var/www/html;#root /var/www/html$document_root;#g" /etc/nginx/conf.d/sites.conf
fi

exec nginx -g "daemon off;"
