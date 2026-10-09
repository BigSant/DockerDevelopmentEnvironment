#!/bin/bash
# Isolate shell options when the official entrypoint sources this hook.
(
set -euo pipefail

: "${MYSQL_USER:?MYSQL_USER is required}"
: "${MYSQL_PASSWORD:?MYSQL_PASSWORD is required}"

# Runs once during first-time DB initialization (docker-entrypoint-initdb.d). The official
# entrypoint already creates MYSQL_USER@'%' with all privileges on MYSQL_DATABASE only; add the
# @'localhost' variant with the same database-scoped privileges. No global privileges and no
# GRANT OPTION: the application must not be able to reach other databases or create users.
# The root account keeps its own DATABASE_ROOT_PASSWORD and is used only by setup tools.
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
sql_user=${MYSQL_USER//\'/\'\'}
sql_password=${MYSQL_PASSWORD//\'/\'\'}
sql_database=${MYSQL_DATABASE//\`/\`\`}
MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mariadb --protocol=socket --host=localhost --user=root <<EOSQL
SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';
CREATE USER IF NOT EXISTS '${sql_user}'@'localhost' IDENTIFIED BY '${sql_password}';
CREATE USER IF NOT EXISTS '${sql_user}'@'%' IDENTIFIED BY '${sql_password}';
GRANT ALL PRIVILEGES ON \`${sql_database}\`.* TO '${sql_user}'@'localhost';
GRANT ALL PRIVILEGES ON \`${sql_database}\`.* TO '${sql_user}'@'%';
FLUSH PRIVILEGES;
EOSQL
)
