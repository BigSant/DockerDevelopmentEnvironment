"""The Database client: setup's one way into a project's database from the host, without leaking connection values."""

import contextlib
import re
import subprocess
import tempfile

# Schema names stay plain so they can appear in SQL, shell and grant patterns without quoting rules.
SCHEMA_NAME = re.compile(r'[A-Za-z0-9_]{1,64}')
SYSTEM_SCHEMAS = ('mysql', 'sys', 'information_schema', 'performance_schema')


# SQL quoting for values setup writes into statements: a string literal (backslash escapes on, as MySQL's
# default sql_mode has them) and a backquoted identifier.
def literal(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def identifier(value):
    return '`' + value.replace('`', '``') + '`'


# A table of an extra schema is named schema.table in the snapshot manifest, the table registry and backups;
# a name whose part before the first dot is no schema name is a main database table.
def split_name(name):
    """(extra schema or None, table) of a snapshot or registry name."""
    schema, dot, table = name.partition('.')
    return (schema, table) if dot and SCHEMA_NAME.fullmatch(schema) else (None, name)


def quoted(name):
    """The SQL identifier of a name: `table`, or `schema`.`table` for an extra schema's table."""
    schema, table = split_name(name)
    return (identifier(schema) + '.' if schema else '') + identifier(table)


def extra_schemas(settings):
    """DATABASE_EXTRA_SCHEMAS as a list: schemas besides DATABASE_NAME that belong to the project.

    The application user owns them like the main database; backup, import, the grant restriction, the
    schema snapshot (<schema>/table-*.sql), guarded migrations and after-import scripts (<group>/<schema>/)
    cover them, and the table registry classifies their tables as schema.table; the data set, db-load and
    the Doctrine diff stay on the main one.
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


# A test schema name: letters, digits, _ and the * wildcard, ending in _test.
TEST_SCHEMA = re.compile(r'[A-Za-z0-9_*]{1,59}_test')


def test_schemas(settings):
    """DATABASE_TEST_SCHEMAS as a list: schemas the application's own tests create and drop.

    The application user is granted them like the extra schemas, but nothing else of setup touches them:
    no snapshot, backup, import, registry or migration, and they are not created. `*` matches any
    characters (`asana*_test` covers `asana_test` and `asana_graph_x1_test`); every name ends in `_test`,
    so a pattern cannot reach a real schema. Local and test only (project.py refuses it on stage/prod).
    """
    names = str(settings.get('DATABASE_TEST_SCHEMAS') or '').split()
    for name in names:
        if not TEST_SCHEMA.fullmatch(name):
            raise ValueError(f'DATABASE_TEST_SCHEMAS: {name!r} is not allowed; use 1-64 letters, digits, _ or * '
                             'and end the name in _test')
    if len(set(names)) != len(names):
        raise ValueError('DATABASE_TEST_SCHEMAS lists a schema twice')
    taken = [name for name in names if name == settings.get('DATABASE_NAME') or name in extra_schemas(settings)]
    if taken:
        raise ValueError(f"DATABASE_TEST_SCHEMAS must not repeat DATABASE_NAME or DATABASE_EXTRA_SCHEMAS: {', '.join(taken)}")
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
# An extra schema (DATABASE_EXTRA_SCHEMAS) is dumped by name after the checked main database: --schema NAME.
if [ "${1:-}" = --schema ]; then
    database="$2"
    shift 2
fi
# Extra mysqldump options follow the database name: --ignore-table=..., --no-data, table names.
exec "$dump_client" $dump_options --protocol=TCP --host=127.0.0.1 --single-transaction --quick --hex-blob \\
    --routines --events --triggers --no-tablespaces --user="$MYSQL_USER" "$database" "$@"
'''


class DatabaseError(ValueError):
    """A database operation failed. The message names the operation and, when there is one, the client's own
    error line; Compose diagnostics never reach it, because they may hold interpolated secrets."""


# The client's own error lines: server errors and messages of the client and dump programs. Compose and
# Docker lines never match. An error line names the statement or table that failed, not the rows.
CLIENT_ERROR = re.compile(r'(ERROR \d+|(mysql|mariadb|mysqldump|mariadb-dump): )')
FAILED = {'read': 'Cannot read the database', 'write': 'Database write failed', 'load': 'Database load failed',
          'dump': 'Database dump failed'}


def unescape_batch(value):
    # MySQL batch output escapes backslashes before control characters.
    escapes = {"0": "\0", "t": "\t", "n": "\n", "r": "\r", "b": "\b", "\\": "\\"}
    return re.sub(r"\\([0tnrb\\])", lambda match: escapes[match[1]], value)


def client_error(stderr):
    """The client's last error line (bounded), or ''."""
    lines = [line.strip() for line in stderr.decode('utf-8', 'replace').splitlines()
             if CLIENT_ERROR.match(line.strip()) and '[Warning]' not in line]
    return lines[-1][:300] if lines else ''


class DatabaseClient:
    """Runs the installed client inside the project's database service (docker compose exec, so stage/prod
    reach DEPLOY_CONTEXT) and picks the account: the application user for query, extract, execute and dump;
    root for load, because dumps may carry DEFINER or GTID statements. execute and load run their whole input
    in one session and stop at the first error, so a migration and the row recording it fail together."""

    def __init__(self, project):
        self.project = project

    def query(self, sql, timeout=60):
        """Result rows as tuples of unescaped fields; empty lines are skipped."""
        output = self._run('read', READ, sql, timeout).decode('utf-8')
        return [tuple(unescape_batch(field) for field in line.split('\t')) for line in output.split('\n') if line]

    def extract(self, sql, timeout=900):
        """The raw result bytes: values may hold tabs, newlines and binary data, so the SQL delimits rows."""
        return self._run('read', READ_RAW, sql, timeout)

    def execute(self, sql, timeout=None):
        self._run('write', READ, sql, timeout)

    @contextlib.contextmanager
    def load(self):
        """A writable byte stream into the database; leaving the block waits for the client to finish."""
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self._command(IMPORT, 'load'), env=self._env(), stdin=subprocess.PIPE,
                                       stderr=errors)
            try:
                yield process.stdin
            except BrokenPipeError:
                pass  # The client stopped early; its exit status reports why.
            finally:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                status = process.wait()
            if status:
                errors.seek(0)
                raise self._failure('load', status, errors.read())

    @contextlib.contextmanager
    def dump(self, *options, schema=None):
        """A readable byte stream of the dump client's output: the main database, or the extra schema named.
        options are dump client options (--no-data, --ignore-table=…, --databases …, table names). Read it to
        the end: a dump left unread is stopped and reported as failed."""
        extra = ['--schema', schema] if schema and schema != self.project.settings.get('DATABASE_NAME') else []
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(self._command(DUMP, 'dump', *extra, *options), env=self._env(),
                                       stdout=subprocess.PIPE, stderr=errors)
            completed = False
            try:
                yield process.stdout
                completed = True
            finally:
                process.stdout.close()
                if not completed:
                    process.terminate()
                status = process.wait()
            if status:
                errors.seek(0)
                raise self._failure('dump', status, errors.read())

    def _command(self, script, operation, *arguments):
        database = self.project.settings.get('DATABASE_NAME')
        if not database:
            raise DatabaseError('DATABASE_NAME is required for database commands')
        # The script checks $1 against the container's MYSQL_DATABASE before it connects.
        return self.project.command + ['exec', '-T', 'database', 'sh', '-c', script, f'database-{operation}',
                                       database, *arguments]

    def _env(self):
        return self.project.compose_env(['exec'])

    def _run(self, operation, script, sql, timeout):
        try:
            result = subprocess.run(self._command(script, operation), input=sql.encode(), capture_output=True,
                                    env=self._env(), timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise DatabaseError(f'{FAILED[operation]}: no answer within {timeout} s') from error
        if result.returncode:
            raise self._failure(operation, result.returncode, result.stderr)
        return result.stdout

    def _failure(self, operation, status, stderr):
        if status == 64:
            return DatabaseError(f'{FAILED[operation]}: the running database container belongs to another '
                                 'DATABASE_NAME')
        line = client_error(stderr)
        if line:
            return DatabaseError(f'{FAILED[operation]}: {line}')
        if not self._running():
            hint = '; start it with make up' if getattr(self.project, 'environment', '') in ('local', 'test') else ''
            return DatabaseError(f'{FAILED[operation]}: the database service is not running{hint}')
        return DatabaseError(f'{FAILED[operation]}; check the database service, name and credentials')

    def _running(self):
        try:
            return 'database' in self.project.capture(['ps', '--status', 'running', '--services']).split()
        except (ValueError, OSError):
            return True  # Unknown: do not claim it is stopped.
