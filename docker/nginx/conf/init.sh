#!/bin/bash

domain="$1"
document_root="$2"

if [[ -z "$domain" ]]; then
  echo "Domain is required"
  exit 1
fi

sed -i "s/{DOMAIN};/$domain;/g" /etc/nginx/conf.d/sites.conf
sed -i "s/{DOCUMENT_ROOT};/$document_root;/g" /etc/nginx/conf.d/sites.conf

exec nginx -g "daemon off;"
