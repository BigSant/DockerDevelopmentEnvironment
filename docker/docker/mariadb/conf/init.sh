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
# Escaped _ like the official entrypoint: an unescaped _ in a database grant matches any character.
sql_database=${sql_database//_/\\_}
# DATABASE_EXTRA_SCHEMAS: further project schemas with the same database-scoped privileges.
# The grant escapes _ because an unescaped _ in a database grant matches any character.
extra_sql=
read -r -a extra_schemas <<< "${DATABASE_EXTRA_SCHEMAS:-}"
# ${a[@]+"${a[@]}"}: bash < 4.4 (mysql:5.7 ships 4.2) treats an empty array as unset under set -u.
for schema in ${extra_schemas[@]+"${extra_schemas[@]}"}; do
    if [[ ! $schema =~ ^[A-Za-z0-9_]{1,64}$ ]]; then
        echo "DATABASE_EXTRA_SCHEMAS: invalid schema name; use 1-64 letters, digits or _" >&2
        exit 64
    fi
    pattern=${schema//_/\\_}
    extra_sql+="CREATE DATABASE IF NOT EXISTS \`${schema}\`;
GRANT ALL PRIVILEGES ON \`${pattern}\`.* TO '${sql_user}'@'localhost';
GRANT ALL PRIVILEGES ON \`${pattern}\`.* TO '${sql_user}'@'%';
"
done
MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" mariadb --protocol=socket --host=localhost --user=root <<EOSQL
SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';
CREATE USER IF NOT EXISTS '${sql_user}'@'localhost' IDENTIFIED BY '${sql_password}';
CREATE USER IF NOT EXISTS '${sql_user}'@'%' IDENTIFIED BY '${sql_password}';
GRANT ALL PRIVILEGES ON \`${sql_database}\`.* TO '${sql_user}'@'localhost';
GRANT ALL PRIVILEGES ON \`${sql_database}\`.* TO '${sql_user}'@'%';
${extra_sql}
FLUSH PRIVILEGES;
EOSQL
)
