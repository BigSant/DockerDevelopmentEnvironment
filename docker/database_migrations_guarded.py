"""Guarded, idempotent SQL migrations beside the schema snapshot.

The commit hook (or make migration-draft) drafts a migration from the committed snapshot to the local
DB: new tables become CREATE TABLE IF NOT EXISTS, columns and indexes are added only after an
information_schema check, and a dropped table, column or key is dropped the same guarded way (a
rename is a drop and a new one: a later migration moves data when it must). Changed keys and table
options are left as a TODO for a person. Below a "-- Rollback:" line the draft holds the way
back, newest first for make db-rollback; it restores structure, not data. A commit that changes the snapshot needs a staged
migration without TODO. make db-migrate applies pending migrations once per database and records
them in setup_migrations of the main database.

Like the snapshot, migrations are flat with one database; with DATABASE_EXTRA_SCHEMAS each schema's
are in its own directory (migrations/<DATABASE_NAME>/, migrations/<schema>/) and run in that schema.
A main database's migration is recorded by its file name, an extra schema's as <schema>/<file>, so
moving the main database's files between layouts changes nothing. DATABASE_MIGRATIONS=off leaves
migrations to the project (e.g. its own Doctrine): the snapshot and its hook stay.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from database_client import extra_schemas, identifier, literal
from database_schema import (git, main_directory, quoted, schema_directory, schema_repository,
                             split_name, staged_schema)

TABLE = 'setup_migrations'
MARKER = '-- Schema change: '
CHECKSUM = '-- Draft checksum: '
ROLLBACK = re.compile(r'^-- Rollback:.*$', re.M)
NAME = re.compile(r'^\d{14}-[a-z0-9-]+\.sql$')
OPEN = re.compile(r'^\s*-- TODO:', re.M)
INDEX = re.compile(r'^(?:PRIMARY KEY|(?:UNIQUE |FULLTEXT |SPATIAL )?KEY `((?:[^`]|``)+)`)')


def migrations_directory(project):
    return schema_directory(project).parent / 'migrations'


def migrations_enabled(project):
    """Setup's guarded migrations, unless DATABASE_MIGRATIONS=off leaves them to the project."""
    return bool(project.settings.get('SCHEMA_DIRECTORY')) and project.settings.get('DATABASE_MIGRATIONS', 'on') != 'off'


def split_rollback(text):
    """(what make db-migrate runs, what make db-rollback runs or None) of a migration file."""
    match = ROLLBACK.search(text)
    return (text[:match.start()], text[match.end():]) if match else (text, None)


def migration_checksum(text):
    """Checksum of the part make db-migrate runs: the rollback may be completed after the migration was applied."""
    return hashlib.sha256(split_rollback(text)[0].encode()).hexdigest()


def migration_folder(project, schema):
    """Where a schema's new migrations go: flat with one database, else the schema's (or main database's) directory."""
    main = main_directory(project)
    if main is None:
        return migrations_directory(project)
    return migrations_directory(project) / (schema or main)


def folder_schema(project, relative):
    """The extra schema of a migration path relative to the migrations directory, or None for the main database."""
    folder = relative.rpartition('/')[0]
    return folder if folder in extra_schemas(project.settings) else None


def migration_files(project):
    """[(identity, schema or None, path)] in apply order: by the file's time, then main database, then schemas.

    Flat files and the one directory that is not an extra schema are the main database's (whatever the
    DATABASE_NAME it was named after); an identity is the file name, or <schema>/<file> for an extra schema."""
    directory = migrations_directory(project)
    if not directory.is_dir():
        return []
    extras = extra_schemas(project.settings)
    found, main_folders = [], set()
    for path in sorted(directory.iterdir()):
        if path.is_symlink():
            raise ValueError(f'Migration files must not be symlinks: {path.name}')
        if path.is_dir():
            files = sorted(path.glob('*.sql'))
            if files and path.name not in extras:
                main_folders.add(path.name)
            found += [(path.name if path.name in extras else None, file) for file in files]
        elif path.suffix == '.sql':
            found.append((None, path))
    if len(main_folders) > 1:
        raise ValueError(f'Migrations in {", ".join(sorted(main_folders))}: a migrations directory is an extra schema '
                         '(DATABASE_EXTRA_SCHEMAS) or the one of the main database')
    result, seen = [], set()
    for schema, path in found:
        if path.is_symlink() or not NAME.match(path.name):
            raise ValueError(f'Unexpected migration file (expected 20260101120000-short-name.sql): {path.relative_to(directory)}')
        identity = f'{schema}/{path.name}' if schema else path.name
        if identity in seen:
            raise ValueError(f'Two migrations of the main database are named {path.name}')
        seen.add(identity)
        result.append((identity, schema, path))
    order = {schema: index for index, schema in enumerate([None, *extras])}
    return sorted(result, key=lambda item: (item[2].name[:14], order[item[1]], item[2].name))


def use(schema):
    return f'USE {identifier(schema)};\n'


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


def table_changes(table, before, after, rollback=False):
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
    for column in [column for column in old['columns'] if column not in new['columns']]:
        blocks.append((f'{table}: drop column {column}',
                       guarded(column_check(table, column), f'ALTER TABLE {t} DROP COLUMN {identifier(column)}', True)))
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


def changes(before, after, rollback=False):
    """Blocks taking the structure from before to after; rollback=True drops what the migration added."""
    blocks = []
    for table in sorted(after.keys() - before.keys()):
        blocks.append((f'{"recreate the structure of" if rollback else "new"} table {table}', create_table(table, after[table])))
    for table in sorted(after.keys() & before.keys()):
        if before[table] != after[table]:
            blocks += table_changes(table, before[table], after[table], rollback)
    for table in sorted(before.keys() - after.keys()):
        blocks.append((f'drop table {table}', f'DROP TABLE IF EXISTS {quoted(table)};\n'))
    return blocks


def draft_text(before, after):
    """The migration: the way forward, then the way back below "-- Rollback:". Only two header lines: the change's
    fingerprint (the same change refreshes its draft) and the body's checksum (tells an edited draft)."""
    fingerprint = hashlib.sha256(json.dumps([before, after], sort_keys=True).encode()).hexdigest()
    body = ''.join(f'\n-- {title}\n{sql}' for title, sql in changes(before, after))
    body += '\n-- Rollback:\n' + ''.join(f'\n-- {title}\n{sql}' for title, sql in changes(after, before, rollback=True))
    return f'{MARKER}{fingerprint}\n{CHECKSUM}' + hashlib.sha256(body.encode()).hexdigest() + '\n' + body


def split_header(text):
    """A draft's checksum and body: what decides whether an untouched draft needs the current wording."""
    match = re.search(f'^{CHECKSUM}', text, re.M)
    return text[match.start():] if match else text


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


def committed_migrations(project, reference='HEAD'):
    """Migration paths (relative to the migrations directory) that the reference commit has."""
    directory = migrations_directory(project)
    root, prefix = schema_repository(directory)
    try:
        return set(os.fsdecode(name)[len(prefix):] for name in
                   git(root, 'ls-tree', '-r', '-z', '--name-only', reference, '--', ':(literal)' + prefix).split(b'\0') if name)
    except ValueError:
        return set()


def new_migrations(project, folder, reference='HEAD'):
    """Migration files of one folder in the working tree that the reference commit does not have."""
    if not folder.is_dir():
        return []
    committed = committed_migrations(project, reference)
    directory = migrations_directory(project)
    return sorted(path for path in folder.glob('*.sql') if path.relative_to(directory).as_posix() not in committed)


def schema_tables(definitions, schema, qualified):
    """One schema's tables; unqualified names when its migration runs in that schema."""
    return {(table if qualified else split_name(table)[1]): sql for table, sql in definitions.items()
            if split_name(table)[0] == schema}


def write_drafts(project, actual, name='schema'):
    """Draft the migrations for the change from the committed snapshot to actual, one per changed schema
    (each in its own directory when there are extra schemas); returns their paths."""
    from database_migrations import definitions
    before = committed_definitions(project)
    if before is None:
        return []
    after = baselined(before, definitions(actual))
    if before == after:
        return []
    from database_registry import remove_entries
    removed = remove_entries(project, before.keys() - after.keys())  # Dropped since the commit, even if exported already.
    if removed:
        print(f"Removed the table registry lines of {len(removed)} dropped tables: {', '.join(sorted(removed)[:5])}"
              f"{' …' if len(removed) > 5 else ''}")
    flat = main_directory(project) is None
    paths = []
    for schema in [None, *extra_schemas(project.settings)]:
        old, new = schema_tables(before, schema, flat), schema_tables(after, schema, flat)
        if old == new:
            continue
        path = write_draft(project, migration_folder(project, schema), draft_text(old, new), name)
        if path:
            paths.append(path)
    return paths


def write_draft(project, folder, text, name='schema'):
    marker = re.search(f'^{MARKER}.*$', text, re.M)[0]
    pending = new_migrations(project, folder)
    for path in pending:
        current = path.read_text()
        if marker in current:
            if untouched_draft(current) and split_header(current) != split_header(text):
                path.write_text(text)  # Same change, newer draft wording: nobody edited it, so refresh it.
            return path
    drafts = [path for path in pending if untouched_draft(path.read_text())]
    if pending and not drafts:
        print(f'The DB changed beyond the edited migration {pending[-1].name}; extend it by hand.')
        return None
    target = drafts[-1] if drafts else folder / f'{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{name}.sql'
    if target.is_symlink() or target.parent.is_symlink():
        raise ValueError('Migration files must not be symlinks')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def staged_migrations(project):
    """Staged migration files that HEAD does not have, with their staged contents (paths relative to migrations/)."""
    directory = migrations_directory(project)
    root, prefix = schema_repository(directory)
    committed = committed_migrations(project)
    files = {}
    for record in git(root, 'ls-files', '--stage', '-z', '--', ':(literal)' + prefix).split(b'\0'):
        if not record:
            continue
        metadata, pathname = record.split(b'\t', 1)
        name = os.fsdecode(pathname)[len(prefix):]
        if name in committed:
            continue
        files[name] = git(root, 'cat-file', 'blob', metadata.decode().split()[1]).decode()
    return files


def changed_schemas(before, after):
    """The schemas (None for the main database) whose tables differ between two definition sets."""
    return {split_name(table)[0] for table in before.keys() | after.keys() if before.get(table) != after.get(table)}


def commit_problems(project, staged):
    """What still keeps a commit with this staged snapshot from passing."""
    from database_migrations import definitions
    from database_registry import registry_file
    from database_schema import normalize_ddl
    problems = []
    if migrations_enabled(project):
        before = committed_definitions(project)
        migrations = staged_migrations(project)
        for name, text in sorted(migrations.items()):
            folder, _, base = name.rpartition('/')
            if not NAME.match(base) or '/' in folder:
                problems.append(f'migration file name must look like [<schema>/]20260101120000-short-name.sql: {name}')
            if OPEN.search(text):
                problems.append(f'TODO left in migration {name}')
        if before is not None and staged:
            after = baselined(before, {t: normalize_ddl(sql) for t, sql in definitions(staged).items()})
            covered = {folder_schema(project, name) for name in migrations}
            for schema in sorted(changed_schemas(before, after) - covered, key=lambda name: name or ''):
                where = migration_folder(project, schema).relative_to(migrations_directory(project).parent).as_posix()
                problems.append(f'the schema of {schema or "the main database"} changed but no new migration is staged in {where}/')
    registry = registry_file(project)
    root, _ = schema_repository(registry.parent)
    relative = registry.relative_to(root).as_posix()
    unstaged = registry.exists() and any(line[1:2] != ' ' for line in git(
        root, 'status', '--porcelain', '--', ':(literal)' + relative).decode().splitlines())
    if unstaged:
        problems.append(f'stage the table registry: git add {relative}')
    return problems


def applied_migrations(project):
    project.database.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} (name varchar(255) NOT NULL PRIMARY KEY, '
                             'checksum char(64) NOT NULL, applied_at datetime NOT NULL) ENGINE=InnoDB;\n')
    return dict(project.database.query(f'SELECT name, checksum FROM {TABLE};\n'))


def pending_migrations(project):
    """([(identity, schema, path, checksum)] not applied yet, [identities changed since they were applied])."""
    files = migration_files(project)
    applied = applied_migrations(project)
    pending, changed = [], []
    for identity, schema, path in files:
        checksum = migration_checksum(path.read_text())
        if identity not in applied:
            pending.append((identity, schema, path, checksum))
        elif applied[identity] not in (checksum, hashlib.sha256(path.read_bytes()).hexdigest()):
            changed.append(identity)
    return pending, changed


def unapplied_committed(project):
    """Committed migrations this DB has not applied (pulled from others): an export now would undo them."""
    directory = migrations_directory(project)
    committed = committed_migrations(project)
    if not any(path.relative_to(directory).as_posix() in committed for _, _, path in migration_files(project)):
        return []  # Nothing committed to apply: the database is not asked.
    return [identity for identity, _, path, _ in pending_migrations(project)[0]
            if path.relative_to(directory).as_posix() in committed]


def require_applied(project):
    """Stop while committed migrations (pulled from others) are not applied here: an export would undo them."""
    behind = unapplied_committed(project)
    if behind:
        raise ValueError(f"{len(behind)} committed migrations are not applied to this database yet: "
                         f"{', '.join(behind[:5])}{' …' if len(behind) > 5 else ''}\n"
                         "Run make db-migrate first, then try again (nothing was exported).")


def draft_migrations(project, name='schema'):
    """make migration-draft: export the schema and draft the migrations from the last commit to this DB,
    one per changed database, without a commit and whatever is staged. Returns the draft paths."""
    from database_schema import export_schema, read_schema
    require_enabled(project)
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise ValueError('name= is lowercase letters, digits and dashes, e.g. name=drop-oneoff-tables')
    require_applied(project)
    actual = read_schema(project, require_classified=True)
    export_schema(project, actual)
    if committed_definitions(project) is None:
        print('No committed snapshot yet: commit this export as the baseline; it needs no migration.')
        return []
    paths = write_drafts(project, actual, name)
    if not paths:
        print('The database matches the last commit: no migration to draft.')
        return []
    for path in paths:
        text = path.read_text()
        todos = len(OPEN.findall(text))
        print(f'Migration draft: {path}' + (f' ({todos} TODO to finish by hand)' if todos else ''))
    print(f'Review it (the way back is below "-- Rollback:"), then git add -A {migrations_directory(project).parent} and commit.')
    return paths


def record_applied(project, names):
    """Record the staged new migrations of a passing commit as applied here: this DB already has their change."""
    if not names:
        return
    directory = migrations_directory(project)
    applied = applied_migrations(project)
    rows = []
    for name in names:
        path = directory / name
        schema = folder_schema(project, name)
        identity = f'{schema}/{path.name}' if schema else path.name
        if identity not in applied and path.is_file():
            rows.append(f'({literal(identity)}, {literal(migration_checksum(path.read_text()))}, NOW())')
    if rows:
        project.database.execute(f'INSERT INTO {TABLE} (name, checksum, applied_at) VALUES {", ".join(rows)};\n')


def run_in(project, schema, sql):
    """SQL in a schema's context, then back in the main database for the setup_migrations bookkeeping."""
    if not schema:
        return sql
    return use(schema) + sql + '\n' + use(project.settings['DATABASE_NAME'])


def require_enabled(project):
    if not migrations_enabled(project):
        raise ValueError('Setup migrations are off (DATABASE_MIGRATIONS=off): the project migrates its own database')


def migrate(project, dry=False):
    """Apply pending migrations in order; stop at the first failure. Returns the applied identities."""
    require_enabled(project)
    pending, changed = pending_migrations(project)
    for name in changed:
        print(f'Warning: {name} changed after it was applied here; it is not rerun.')
    if not pending:
        print('No pending migrations.')
        return []
    for identity, schema, path, checksum in pending:
        sql = split_rollback(path.read_text())[0]
        if OPEN.search(sql):
            raise ValueError(f'{identity} still has a TODO; finish it before applying')
        if dry:
            print(f'-- Would apply {identity}' + (f' in {schema}' if schema else '') + '\n' + sql)
            continue
        print(f'Applying {identity}...', flush=True)
        try:
            project.database.execute(run_in(project, schema, sql) + f'\nINSERT INTO {TABLE} (name, checksum, applied_at) '
                                     f'VALUES ({literal(identity)}, {literal(checksum)}, NOW());\n')
        except ValueError as error:
            raise ValueError(f'Migration {identity} failed ({error}); it is not recorded. Fix it and rerun make db-migrate.') from error
    print(f'{"Would apply" if dry else "Applied"} {len(pending)} migrations.')
    return [] if dry else [identity for identity, *_ in pending]


def rollback(project, steps=1, to=None, dry=False):
    """Run the rollback part of the latest applied migrations, newest first, and forget them.

    to=<identity> rolls back everything applied after that migration (it stays); otherwise the last steps."""
    require_enabled(project)
    applied_migrations(project)
    rows = [row[0] for row in project.database.query(f'SELECT name FROM {TABLE} ORDER BY applied_at DESC, name DESC;\n')]
    if to:
        if to not in rows:
            raise ValueError(f'{to} is not applied here; applied: {", ".join(rows[:5]) or "none"}')
        targets = rows[:rows.index(to)]
    else:
        if steps < 1:
            raise ValueError('steps must be at least 1')
        targets = rows[:steps]
    if not targets:
        print('Nothing to roll back.')
        return []
    files = {identity: (schema, path) for identity, schema, path in migration_files(project)}
    plans = []
    for identity in targets:  # Check every one before changing anything.
        if identity not in files:
            raise ValueError(f'{identity} is applied but its file is missing; check out the branch that has it')
        schema, path = files[identity]
        sql = split_rollback(path.read_text())[1]
        if sql is None or not sql.strip():
            raise ValueError(f'{identity} has no "-- Rollback:" part; write one (guarded, safe to rerun) or restore a backup')
        if OPEN.search(sql):
            raise ValueError(f'{identity} has a TODO in its rollback; finish it first')
        plans.append((identity, schema, sql))
    for identity, schema, sql in plans:
        if dry:
            print(f'-- Would roll back {identity}' + (f' in {schema}' if schema else '') + '\n' + sql)
            continue
        print(f'Rolling back {identity}...', flush=True)
        try:
            project.database.execute(run_in(project, schema, sql) + f'\nDELETE FROM {TABLE} WHERE name = {literal(identity)};\n')
        except ValueError as error:
            raise ValueError(f'Rollback of {identity} failed ({error}); it stays recorded as applied. Fix its rollback and '
                             'rerun.') from error
    print(f'{"Would roll back" if dry else "Rolled back"} {len(plans)} migrations.')
    return [] if dry else [identity for identity, *_ in plans]
