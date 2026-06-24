#!/bin/bash
set -euo pipefail

: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

# Runs once during first-time DB initialization (docker-entrypoint-initdb.d). The official
# entrypoint already creates MYSQL_USER@'%' and grants it MYSQL_DATABASE; here we also add the
# @'localhost' variant and elevate the app user to full privileges (dev environment). SQL is
# executed directly against the init server — no self-modifying init files. MariaDB uses
# mysql_native_password (the default for IDENTIFIED BY) — there is no caching_sha2_password.
mariadb --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<EOSQL
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}';
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';
GRANT ALL PRIVILEGES ON *.* TO '${MYSQL_USER}'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO '${MYSQL_USER}'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOSQL
