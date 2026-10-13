"""Guarded, idempotent SQL migrations beside the schema snapshot.

The commit hook drafts one migration from the committed snapshot to the local DB: new tables
become CREATE TABLE IF NOT EXISTS, columns and indexes are added only after an
information_schema check, and anything that can lose data (DROP, RENAME, changed keys) is left
as a TODO for a person. A commit that changes the snapshot needs a staged migration without TODO.
make db-migrate applies pending migrations once per database and records them in setup_migrations.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from database_schema import git, mysql_query, quoted, schema_directory, schema_repository, split_name, staged_schema

TABLE = 'setup_migrations'
MARKER = '-- Schema change: '
CHECKSUM = '-- Draft checksum: '
NAME = re.compile(r'^\d{14}-[a-z0-9-]+\.sql$')
OPEN = re.compile(r'^\s*-- TODO:', re.M)
INDEX = re.compile(r'^(?:PRIMARY KEY|(?:UNIQUE |FULLTEXT |SPATIAL )?KEY `((?:[^`]|``)+)`)')


def migrations_directory(project):
    return schema_directory(project).parent / 'migrations'


def literal(value):
    return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"


def identifier(value):
    return '`' + value.replace('`', '``') + '`'


def parse_table(ddl):
    """Columns, keys and the rest of one normalised SHOW CREATE TABLE statement."""
    lines = ddl.rstrip().rstrip(';').splitlines()
    columns, keys, other = {}, {}, []
    for raw in lines[1:-1]:
        line = raw.strip().rstrip(',')
        if line.startswith('`'):
            name = re.match(r'`((?:[^`]|``)+)`', line)[1].replace('``', '`')
            columns[name] = line
        elif (match := INDEX.match(line)):
            keys['PRIMARY' if line.startswith('PRIMARY') else match[1].replace('``', '`')] = line
        else:
            other.append(line)
    return {'columns': columns, 'keys': keys, 'other': other, 'options': lines[-1]}


def guarded(check, statement, run_when_found=False):
    """Run statement only when the information_schema count says it is still needed."""
    condition = '> 0' if run_when_found else '= 0'
    return (f'SET @setup_sql = IF(({check}) {condition}, {literal(statement)}, \'DO 0\');\n'
            'PREPARE setup_statement FROM @setup_sql;\nEXECUTE setup_statement;\nDEALLOCATE PREPARE setup_statement;\n')


def where_table(table):
    """information_schema condition for a snapshot name: the main database's table, or schema.table's."""
    schema, name = split_name(table)
    return f'TABLE_SCHEMA = {literal(schema) if schema else "DATABASE()"} AND TABLE_NAME = {literal(name)}'


def column_check(table, column):
    return f'SELECT COUNT(*) FROM information_schema.COLUMNS WHERE {where_table(table)} AND COLUMN_NAME = {literal(column)}'


def key_check(table, key):
    return f'SELECT COUNT(*) FROM information_schema.STATISTICS WHERE {where_table(table)} AND INDEX_NAME = {literal(key)}'


def create_table(table, ddl):
    """The snapshot's CREATE TABLE as CREATE TABLE IF NOT EXISTS, in the table's own schema."""
    head = 'CREATE TABLE ' + identifier(split_name(table)[1]) + ' '
    if not ddl.startswith(head):
        raise ValueError(f'Unexpected CREATE TABLE statement for {table}')
    return 'CREATE TABLE IF NOT EXISTS ' + quoted(table) + ' ' + ddl[len(head):]


def todo(text, statements=()):
    suggestion = ''.join(f'--   {line}\n' for statement in statements for line in statement.splitlines())
    return f'-- TODO: {text}\n' + (f'-- Suggested, once checked:\n{suggestion}' if suggestion else '')


def table_changes(table, before, after):
    old, new = parse_table(before), parse_table(after)
    t = quoted(table)
    blocks = []
    names = list(new['columns'])
    for position, column in enumerate(names):
        place = 'FIRST' if position == 0 else 'AFTER ' + identifier(names[position - 1])
        if column not in old['columns']:
            blocks.append((f'{table}: new column {column}',
                           guarded(column_check(table, column), f'ALTER TABLE {t} ADD COLUMN {new["columns"][column]} {place}')))
        elif old['columns'][column] != new['columns'][column]:
            blocks.append((f'{table}: changed column {column}',
                           guarded(column_check(table, column), f'ALTER TABLE {t} MODIFY COLUMN {new["columns"][column]}', True)))
    for column in old['columns'].keys() - new['columns'].keys():
        blocks.append((f'{table}: column {column} is gone',
                       todo(f'drop or rename column {column} of {table} by hand; check that its data is not needed '
                            '(a RENAME shows up as one column gone and one new)',
                            [guarded(column_check(table, column), f'ALTER TABLE {t} DROP COLUMN {identifier(column)}', True)])))
    for key, definition in new['keys'].items():
        if key not in old['keys']:
            blocks.append((f'{table}: new key {key}',
                           guarded(key_check(table, key), f'ALTER TABLE {t} ADD {definition}')))
        elif old['keys'][key] != definition:
            drop = 'DROP PRIMARY KEY' if key == 'PRIMARY' else f'DROP KEY {identifier(key)}'
            blocks.append((f'{table}: changed key {key}',
                           todo(f'replace key {key} of {table}; it may reject existing rows',
                                [f'ALTER TABLE {t} {drop}, ADD {definition};'])))
    for key in old['keys'].keys() - new['keys'].keys():
        drop = 'DROP PRIMARY KEY' if key == 'PRIMARY' else f'DROP KEY {identifier(key)}'
        blocks.append((f'{table}: key {key} is gone', guarded(key_check(table, key), f'ALTER TABLE {t} {drop}', True)))
    if old['other'] != new['other'] or old['options'] != new['options']:
        blocks.append((f'{table}: constraints or table options changed',
                       todo(f'apply the constraint or option change of {table} by hand: '
                            + '; '.join(sorted(set(new['other']) - set(old['other'])) + ([new['options']] if old['options'] != new['options'] else [])))))
    return blocks


def draft_text(before, after, source):
    blocks = []
    for table in sorted(after.keys() - before.keys()):
        blocks.append((f'new table {table}', create_table(table, after[table])))
    for table in sorted(after.keys() & before.keys()):
        if before[table] != after[table]:
            blocks += table_changes(table, before[table], after[table])
    for table in sorted(before.keys() - after.keys()):
        blocks.append((f'table {table} is gone',
                       todo(f'drop or rename table {table} by hand; back up its data first. A RENAME shows up '
                            'as one table gone and one new: replace both with a guarded RENAME TABLE.',
                            [f'DROP TABLE IF EXISTS {quoted(table)};'])))
    fingerprint = hashlib.sha256(json.dumps([before, after], sort_keys=True).encode()).hexdigest()
    body = ''.join(f'\n-- {title}\n{sql}' for title, sql in blocks)
    header = (f'-- Guarded migration drafted by setup from {source} on {datetime.now(timezone.utc):%Y-%m-%d}.\n'
              f'{MARKER}{fingerprint}\n'
              '-- Review every statement; replace each "-- TODO:" block with a guarded statement or remove it.\n'
              '-- make db-migrate applies it once per database; every statement must be safe to rerun.\n')
    return header + CHECKSUM + hashlib.sha256(body.encode()).hexdigest() + '\n' + body


def untouched_draft(text):
    """True for a draft nobody has edited since setup wrote it."""
    match = re.search(f'^{CHECKSUM}([0-9a-f]{{64}})\n', text, re.M)
    return bool(match) and hashlib.sha256(text[match.end():].encode()).hexdigest() == match[1]


def committed_definitions(project, reference='HEAD'):
    from database_migrations import committed_schema
    from database_schema import normalize_ddl
    try:
        # Compare like with like: a snapshot committed before a normalisation rule changed is normalised too.
        return {table: normalize_ddl(sql) for table, sql in committed_schema(schema_directory(project), reference)[2].items()}
    except ValueError:
        return None  # No commit or no committed snapshot yet: this commit is the baseline.


def baselined(before, after):
    """after without the schemas the committed snapshot does not have yet: a schema's first snapshot is its
    baseline, as the main database's first snapshot is, so adding a schema to the snapshot needs no migration."""
    known = {split_name(table)[0] for table in before} | {None}
    return {table: sql for table, sql in after.items() if split_name(table)[0] in known}


def new_migrations(project, reference='HEAD'):
    """Migration files in the working tree that the reference commit does not have."""
    directory = migrations_directory(project)
    if not directory.is_dir():
        return []
    root, prefix = schema_repository(directory)
    try:
        committed = set(os.fsdecode(name)[len(prefix):] for name in
                        git(root, 'ls-tree', '-r', '-z', '--name-only', reference, '--', ':(literal)' + prefix).split(b'\0') if name)
    except ValueError:
        committed = set()
    return sorted(path for path in directory.glob('*.sql') if path.name not in committed)


def write_draft(project, actual):
    """Draft the migration for the change from the committed snapshot to actual; returns its path or None."""
    from database_migrations import definitions
    before = committed_definitions(project)
    after = definitions(actual)
    if before is None or before == baselined(before, after):
        return None
    after = baselined(before, after)
    text = draft_text(before, after, 'the last commit')
    marker = re.search(f'^{MARKER}.*$', text, re.M)[0]
    pending = new_migrations(project)
    for path in pending:
        if marker in path.read_text():
            return path
    drafts = [path for path in pending if untouched_draft(path.read_text())]
    if pending and not drafts:
        print(f'The DB changed beyond the edited migration {pending[-1].name}; extend it by hand.')
        return None
    target = drafts[-1] if drafts else migrations_directory(project) / f'{datetime.now(timezone.utc):%Y%m%d%H%M%S}-schema.sql'
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('Migration files must not be symlinks')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def staged_migrations(project):
    """Staged migration files that HEAD does not have, with their staged contents."""
    directory = migrations_directory(project)
    root, prefix = schema_repository(directory)
    try:
        committed = set(git(root, 'ls-tree', '-r', '-z', '--name-only', 'HEAD', '--', ':(literal)' + prefix).split(b'\0'))
    except ValueError:
        committed = set()
    files = {}
    for record in git(root, 'ls-files', '--stage', '-z', '--', ':(literal)' + prefix).split(b'\0'):
        if not record:
            continue
        metadata, pathname = record.split(b'\t', 1)
        if pathname in committed:
            continue
        files[os.fsdecode(pathname)[len(prefix):]] = git(root, 'cat-file', 'blob', metadata.decode().split()[1]).decode()
    return files


def commit_problems(project, staged):
    """What still keeps a commit with this staged snapshot from passing."""
    from database_migrations import definitions
    from database_registry import registry_file
    from database_schema import normalize_ddl
    problems = []
    before = committed_definitions(project)
    migrations = staged_migrations(project)
    for name, text in sorted(migrations.items()):
        if not NAME.match(name):
            problems.append(f'migration file name must look like 20260101120000-short-name.sql: {name}')
        if OPEN.search(text):
            problems.append(f'TODO left in migration {name}')
    if before is not None and staged and baselined(before, {t: normalize_ddl(sql) for t, sql in definitions(staged).items()}) != before \
            and not migrations:
        problems.append('the schema changed but no new migration is staged')
    registry = registry_file(project)
    root, _ = schema_repository(registry.parent)
    relative = registry.relative_to(root).as_posix()
    unstaged = registry.exists() and any(line[1:2] != ' ' for line in git(
        root, 'status', '--porcelain', '--', ':(literal)' + relative).decode().splitlines())
    if unstaged:
        problems.append(f'stage the table registry: git add {relative}')
    return problems


def applied_migrations(project):
    mysql_query(project, f'CREATE TABLE IF NOT EXISTS {TABLE} (name varchar(255) NOT NULL PRIMARY KEY, '
                         'checksum char(64) NOT NULL, applied_at datetime NOT NULL) ENGINE=InnoDB;\n')
    rows = mysql_query(project, f'SELECT name, checksum FROM {TABLE};\n')
    return dict(line.split('\t') for line in rows.splitlines() if line)


def pending_migrations(project):
    directory = migrations_directory(project)
    files = sorted(directory.glob('*.sql')) if directory.is_dir() else []
    applied = applied_migrations(project)
    pending, changed = [], []
    for path in files:
        if path.is_symlink() or not NAME.match(path.name):
            raise ValueError(f'Unexpected migration file (expected 20260101120000-short-name.sql): {path.name}')
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name not in applied:
            pending.append((path, checksum))
        elif applied[path.name] != checksum:
            changed.append(path.name)
    return pending, changed


def migrate(project):
    """Apply pending migrations in name order; stop at the first failure."""
    pending, changed = pending_migrations(project)
    for name in changed:
        print(f'Warning: {name} changed after it was applied here; it is not rerun.')
    if not pending:
        print('No pending migrations.')
        return []
    for path, checksum in pending:
        text = path.read_text()
        if OPEN.search(text):
            raise ValueError(f'{path.name} still has a TODO; finish it before applying')
        print(f'Applying {path.name}...', flush=True)
        try:
            mysql_query(project, text + f'\nINSERT INTO {TABLE} (name, checksum, applied_at) VALUES '
                                        f'({literal(path.name)}, {literal(checksum)}, NOW());\n', timeout=None)
        except ValueError as error:
            raise ValueError(f'Migration {path.name} failed; it is not recorded. Fix it and rerun make db-migrate.') from error
    print(f'Applied {len(pending)} migrations.')
    return [path for path, _ in pending]
