#!/bin/bash
# The official entrypoint may source a non-executable bind-mounted hook.
# Keep our shell options local in either invocation mode.
(
set -euo pipefail

: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

# Runs once during first-time DB initialization (docker-entrypoint-initdb.d). The official
# entrypoint already creates MYSQL_USER@'%' and grants it MYSQL_DATABASE; here we also add
# the @'localhost' variant and elevate the app user to full privileges so it can create /
# drop databases, run migrations, etc. (dev environment). SQL is executed directly against
# the init server — no self-modifying init files.
sql_user=${MYSQL_USER//\'/\'\'}
sql_password=${MYSQL_PASSWORD//\'/\'\'}
MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mysql --protocol=socket --host=localhost --user=root <<EOSQL
SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';
CREATE USER IF NOT EXISTS '${sql_user}'@'localhost' IDENTIFIED BY '${sql_password}';
CREATE USER IF NOT EXISTS '${sql_user}'@'%' IDENTIFIED BY '${sql_password}';
GRANT ALL PRIVILEGES ON *.* TO '${sql_user}'@'localhost' WITH GRANT OPTION;
GRANT ALL PRIVILEGES ON *.* TO '${sql_user}'@'%' WITH GRANT OPTION;
FLUSH PRIVILEGES;
EOSQL
)
