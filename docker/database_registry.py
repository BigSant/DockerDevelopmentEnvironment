"""The table registry: every database table has a group, a base data-set rule and a LIVE import rule.

The project registry (database/table-registry.txt beside the schema directory) is read first, then the
profile defaults. Lines: `table group base live`; table may use * wildcards and {p} for the prefix.
"""

from fnmatch import fnmatchcase
from pathlib import Path
import re
import sys

BASE_RULES = ('full', 'slice', 'faker', 'empty', 'drop')
LIVE_RULES = ('import', 'skip', '-')
DOCKER_ROOT = Path(__file__).resolve().parent
# Setup's own bookkeeping (applied migrations) travels with the data and needs no classification.
SETUP_TABLES = ('setup_migrations',)
HEADER = ('# Project table registry: table  group  base-rule(full|slice|faker|empty|drop)  live-import(import|skip)\n'
          '# Read before the profile defaults; every table must be classified (make schema-resolve).\n')


def registry_file(project):
    from database_schema import schema_directory
    return schema_directory(project).parent / 'table-registry.txt'


def prefix(project):
    return project.settings.get('PS_DATABASE_PREFIX') or 'ps_'


def parse(text, source, table_prefix):
    entries = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 4 or fields[2] not in BASE_RULES or fields[3] not in LIVE_RULES:
            raise ValueError(f'{source}:{number}: expected "table group base live" with base in {BASE_RULES} '
                             f'and live in import/skip')
        entries.append((fields[0].replace('{p}', table_prefix), fields[1], fields[2], fields[3]))
    return entries


def load(project):
    entries = []
    project_file = registry_file(project)
    if project_file.is_file():
        entries += parse(project_file.read_text(), project_file.name, prefix(project))
    profile = project.settings.get('PROFILE')
    defaults = DOCKER_ROOT / 'database-profile' / ('prestashop' if profile in ('ps', 'prestashop') else str(profile)) / 'table-registry.txt'
    if profile and defaults.is_file():
        entries += parse(defaults.read_text(), str(defaults.relative_to(DOCKER_ROOT)), prefix(project))
    return entries


def classify(entries, table):
    return next(((pattern, group, base, live) for pattern, group, base, live in entries
                 if fnmatchcase(table, pattern)), None)


def unknown_tables(entries, tables):
    return [table for table in tables if table not in SETUP_TABLES and classify(entries, table) is None]


def dropped(entries, tables):
    return [table for table in tables if (classify(entries, table) or ('', '', ''))[2] == 'drop']


def append_entries(project, rows):
    path = registry_file(project)
    if path.is_symlink():
        raise ValueError('The table registry must not be a symlink')
    text = path.read_text() if path.exists() else HEADER
    if text and not text.endswith('\n'):
        text += '\n'
    text += ''.join(f'{table:<48} {group:<12} {base:<6} {live}\n' for table, group, base, live in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def guess(table, columns):
    """A starting classification offered by schema-resolve; the person decides."""
    if re.search(r'(^tmp_|^zz_|_bak_|_backup|_old$|_copy$)', table):
        return ('review', 'empty', 'skip')
    if re.search(r'(cache|_log$|_logs$|session|_stats?$|_index$|api_response)', table):
        return ('cache', 'empty', 'skip')
    if 'id_order' in columns or 'id_cart' in columns:
        return ('orders', 'faker', 'import')
    if 'id_customer' in columns or 'email' in columns:
        return ('customers', 'faker', 'import')
    if 'id_product' in columns:
        return ('catalog', 'slice', 'import')
    return ('config', 'full', 'import')


def table_columns(project, tables):
    from database_schema import mysql_query
    if not tables:
        return {}
    names = ','.join("'" + t.replace("'", "''") + "'" for t in tables)
    rows = mysql_query(project, 'SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE '
                                f'TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({names});\n')
    columns = {}
    for line in rows.splitlines():
        table, column = line.split('\t')
        columns.setdefault(table, set()).add(column)
    return columns


def resolve(project, ask=input, interactive=None, tables=None, columns=None):
    """Classify every table the registry does not know: include it, or mark it for a guarded DROP.

    Without `tables` the database is read; db-import passes the dump's tables and their columns.
    """
    entries = load(project)
    if tables is None:
        from database_schema import database_tables
        tables = database_tables(project)
    tables = unknown_tables(entries, tables)
    if not tables:
        print('Every database table is classified.')
        return []
    interactive = sys.stdin.isatty() if interactive is None else interactive
    columns = table_columns(project, tables) if columns is None else columns
    if not interactive:
        print('Unclassified tables (run make schema-resolve in a terminal, or add them to the registry):')
        for table in tables:
            print(f'  {table:<48} suggested: {" ".join(guess(table, columns.get(table, set())))}')
        raise ValueError(f'{len(tables)} tables are not classified')
    rows = []
    for table in tables:
        group, base, live = guess(table, columns.get(table, set()))
        answer = ask(f'{table}: [i]nclude ({group} {base} {live}) / [e]dit / [d]rop with a migration / [s]kip? ').strip().lower()
        if answer in ('i', ''):
            rows.append((table, group, base, live))
        elif answer == 'e':
            fields = ask('  group base live: ').split()
            if len(fields) != 3 or fields[1] not in BASE_RULES or fields[2] not in LIVE_RULES:
                raise ValueError('Expected: group base live')
            rows.append((table, *fields))
        elif answer == 'd':
            rows.append((table, 'drop', 'drop', '-'))
    if rows:
        append_entries(project, rows)
        print(f'Added {len(rows)} tables to {registry_file(project)}; review and commit it with the schema.')
    return rows
