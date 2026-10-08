#!/bin/sh
# First initialisation only (docker-entrypoint-initdb.d): run the project's init.d
# drop-ins in name order — common first, then the environment directory. *.sql and
# rendered *.sql.template run against MYSQL_DATABASE as root; *.sh run with sh.
set -eu
client=$(command -v mysql || command -v mariadb)
for directory in /etc/mysql/project.r/init.d /etc/mysql/project.r.env/init.d; do
    [ -d "$directory" ] || continue
    for file in $(find "$directory" -maxdepth 1 -type f | sort); do
        case "$file" in
            *.sql)
                echo "[project-init] SQL: ${file##*/}"
                MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$client" --protocol=socket --user=root "$MYSQL_DATABASE" < "$file"
                ;;
            *.sh)
                echo "[project-init] script: ${file##*/}"
                sh "$file"
                ;;
        esac
    done
done
