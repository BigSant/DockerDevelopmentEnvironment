#!/bin/bash

database_user="${MYSQL_USER}"
database_password="${MYSQL_PASSWORD}"
database_name="${MYSQL_DATABASE}"

if [[ -z "$database_user" || -z "$database_password" || -z "$database_name" ]]; then
  echo "Not all required parameters provided"
  exit 1
fi

echo "
CREATE USER IF NOT EXISTS '$database_user'@'localhost' IDENTIFIED WITH caching_sha2_password BY '$database_password';
CREATE USER IF NOT EXISTS '$database_user'@'%' IDENTIFIED WITH caching_sha2_password BY '$database_password';

GRANT ALL PRIVILEGES ON *.* TO '$database_user'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO '$database_user'@'%' WITH GRANT OPTION;

FLUSH PRIVILEGES;
" | tee /docker-entrypoint-initdb.d/3-initialize_exporter.sql > /dev/null

exit 0
