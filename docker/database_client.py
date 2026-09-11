"""Select the installed MySQL/MariaDB client without leaking connection values."""

CLIENT = '''
set -eu
if [ "$MYSQL_DATABASE" != "$1" ]; then
    echo 'Configured database differs from the running container.' >&2
    exit 64
fi
export MYSQL_PWD="$MYSQL_PASSWORD"
if command -v mariadb >/dev/null 2>&1; then
    db_client=mariadb
    key_option=
else
    db_client=mysql
    key_option=
    if "$db_client" --help 2>/dev/null | grep -q 'get-server-public-key'; then
        key_option=--get-server-public-key
    fi
fi
'''

READ = CLIENT + '''
exec "$db_client" $key_option --connect-timeout=5 --protocol=TCP --host=127.0.0.1 \\
    --default-character-set=utf8mb4 --batch --skip-column-names --binary-mode \\
    --user="$MYSQL_USER" --database="$1"
'''

IMPORT = CLIENT + '''
exec "$db_client" $key_option --protocol=TCP --host=127.0.0.1 --binary-mode --user="$MYSQL_USER" --database="$1"
'''

DUMP = CLIENT + '''
if command -v mariadb-dump >/dev/null 2>&1; then
    dump_client=mariadb-dump
    dump_options=
else
    dump_client=mysqldump
    dump_options=--set-gtid-purged=OFF
    if "$dump_client" --help 2>/dev/null | grep -q 'get-server-public-key'; then dump_options="$dump_options --get-server-public-key"; fi
    if "$dump_client" --help 2>/dev/null | grep -q 'column-statistics'; then dump_options="$dump_options --column-statistics=0"; fi
fi
exec "$dump_client" $dump_options --protocol=TCP --host=127.0.0.1 --single-transaction --quick --hex-blob \\
    --routines --events --triggers --no-tablespaces --user="$MYSQL_USER" "$1"
'''
