#!/bin/bash

domain="$1"
document_root="$2"

if [[ -z "$domain" ]]; then
  echo "Domain is required"
  exit 1
fi

sed -i "s/DOMAIN_VAR/$domain/g" /usr/local/apache2/conf/sites.conf

if [[ ! -z "$document_root" && "$document_root" != "/" ]]; then
  sed -i "s#/var/www/html#/var/www/html/$document_root;#g" /usr/local/apache2/conf/sites.conf
fi

httpd-foreground
#exec nginx -g "daemon off;"
