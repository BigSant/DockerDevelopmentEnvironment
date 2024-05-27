#!/bin/bash

database_user="$1"
database_password="$2"
database_name="$3"
exporter_password="$4"

if [[ -z "$database_user" || -z "$database_password" || -z "$database_name" || -z "$exporter_password" ]]; then
  echo "Not all required parameters provided"
  exit 1
fi

sed -i "s/{DATABASE_USER}/$database_user/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
sed -i "s/{DATABASE_PASSWORD}/$database_password/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
sed -i "s/{DATABASE_NAME}/$database_name/g" /docker-entrypoint-initdb.d/initialize_exporter.sql
sed -i "s/{EXPORTER_PASSWORD}/$exporter_password/g" /docker-entrypoint-initdb.d/initialize_exporter.sql

chown -R mysql:mysql /var/lib/mysql
chown -R mysql:mysql /var/run/mysqld
runuser -u mysql -- mariadbd --skip-grant-tables
