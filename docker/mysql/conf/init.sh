#!/bin/bash

database_user="${MYSQL_USER}"
database_password="${MYSQL_PASSWORD}"
database_name="${MYSQL_DATABASE}"
exporter_password="${MYSQLD_EXPORTER_PASSWORD}"

if [[ -z "$database_user" || -z "$database_password" || -z "$database_name" || -z "$exporter_password" ]]; then
  echo "Not all required parameters provided"
  exit 1
fi

echo "
CREATE USER IF NOT EXISTS '$database_user'@'localhost' IDENTIFIED BY '$database_password';
GRANT ALL PRIVILEGES ON *.* TO '$database_user'@'%' IDENTIFIED BY '$database_password' WITH GRANT OPTION;

CREATE USER IF NOT EXISTS 'exporter'@'localhost' IDENTIFIED BY '$exporter_password' WITH MAX_USER_CONNECTIONS 3;
GRANT ALL PRIVILEGES ON *.* TO 'exporter'@'%' IDENTIFIED BY '$exporter_password' WITH GRANT OPTION;
--GRANT PROCESS, REPLICATION CLIENT, SELECT ON *.* TO 'exporter'@'%' IDENTIFIED BY '$exporter_password' WITH GRANT OPTION;

FLUSH PRIVILEGES;
" | tee /docker-entrypoint-initdb.d/3-initialize_exporter.sql > /dev/null

exit 0
