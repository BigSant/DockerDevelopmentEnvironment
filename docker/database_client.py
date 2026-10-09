"""Select the installed MySQL/MariaDB client without leaking connection values."""

import re

# Schema names stay plain so they can appear in SQL, shell and grant patterns without quoting rules.
SCHEMA_NAME = re.compile(r'[A-Za-z0-9_]{1,64}')
SYSTEM_SCHEMAS = ('mysql', 'sys', 'information_schema', 'performance_schema')


def extra_schemas(settings):
    """DATABASE_EXTRA_SCHEMAS as a list: schemas besides DATABASE_NAME that belong to the project.

    The application user owns them like the main database; backup, import and the grant
    restriction cover them, while the schema snapshot, table registry and data set stay on the main one.
    """
    names = str(settings.get('DATABASE_EXTRA_SCHEMAS') or '').split()
    for name in names:
        if not SCHEMA_NAME.fullmatch(name) or name.lower() in SYSTEM_SCHEMAS:
            raise ValueError(f'DATABASE_EXTRA_SCHEMAS: {name!r} is not allowed; use 1-64 letters, digits or _ '
                             'and no system schema')
    if len(set(names)) != len(names):
        raise ValueError('DATABASE_EXTRA_SCHEMAS lists a schema twice')
    if settings.get('DATABASE_NAME') in names:
        raise ValueError('DATABASE_EXTRA_SCHEMAS must not repeat DATABASE_NAME')
    return names


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

# Unescaped output for data-set extraction: values may contain tabs and newlines, so callers
# delimit rows themselves.
READ_RAW = READ.replace('--binary-mode', '--binary-mode --raw')

# Imports run as root: dumps may carry DEFINER clauses or GTID statements that the
# database-scoped application user is not allowed to execute.
IMPORT = CLIENT + '''
export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
exec "$db_client" $key_option --protocol=TCP --host=127.0.0.1 --binary-mode --user=root --database="$1"
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
database="$1"
shift
# Extra mysqldump options follow the database name: --ignore-table=..., --no-data, table names.
exec "$dump_client" $dump_options --protocol=TCP --host=127.0.0.1 --single-transaction --quick --hex-blob \\
    --routines --events --triggers --no-tablespaces --user="$MYSQL_USER" "$database" "$@"
'''
