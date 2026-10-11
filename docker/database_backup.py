"""Atomic private logical backups, optionally compressed, using the running DB."""

from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from database_client import DUMP, extra_schemas


def keep_existing(structure):
    """Structure-only dump that restores without dropping or emptying tables that already exist."""
    return re.sub(rb'^CREATE TABLE `', b'CREATE TABLE IF NOT EXISTS `', structure, flags=re.M)


def registry_passes(project, database):
    """(schema, mysqldump options) passes: everything without the data of skip tables, then their structure.

    Restoring such a backup must not wipe the skip tables on the server (payment tokens, API
    keys), so their structure carries no DROP TABLE and creates a table only when it is missing.
    The extra schemas' skip tables (schema.table in the registry) get a structure pass of their own.
    """
    from database_import import skip_rule
    from database_schema import database_tables, extra_tables
    rule = skip_rule(project)
    if not rule:
        return [(database, [])]
    skipped = [table for table in database_tables(project) if rule(table)]
    extra = [name for name in extra_tables(project) if rule(name)]
    passes = [(database, [f'--ignore-table={database}.{table}' for table in skipped] + [f'--ignore-table={name}' for name in extra])]
    structure = ['--no-data', '--skip-add-drop-table', '--skip-routines', '--skip-events']
    if skipped:
        passes.append((database, [*structure, *skipped]))
    by_schema = {}
    for name in extra:
        schema, _, table = name.partition('.')
        by_schema.setdefault(schema, []).append(table)
    passes += [(schema, [*structure, *tables]) for schema, tables in by_schema.items()]
    return passes


def backup_database(project, destination=None, registry=False):
    database = project.settings['DATABASE_NAME']
    if not database:
        raise ValueError('DATABASE_NAME is required for backup')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    target = Path(destination).expanduser().absolute() if destination else (
        project.directory / '.generated/backups' / f'{project.name}-{stamp}.sql.gz')
    if not (target.name.endswith('.sql') or target.name.endswith('.sql.gz')):
        raise ValueError('Backup destination must end in .sql or .sql.gz')
    if target.exists() or target.is_symlink():
        raise ValueError('Backup destination already exists; choose a new file')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    from project_storage import require_space
    require_space(project, target.parent)
    # registry=True: structure of every table, data only of tables the registry imports from LIVE.
    passes = registry_passes(project, database) if registry else [(database, [])]
    extras = extra_schemas(project.settings)
    if extras:
        # One dump run reads the main and the extra schemas in a single transaction (a consistent copy)
        # and names each one (CREATE DATABASE IF NOT EXISTS + USE), so a restore recreates every schema.
        # The run ends inside the last schema, so the header-less structure pass of the main
        # database's skipped tables goes first. The registry covers the main database only.
        (_, data), *structure = passes
        passes = [*structure, (database, [*data, '--databases', *extras])]
    commands = [(schema, project.command + ['exec', '-T', 'database', 'sh', '-c', DUMP, 'database-backup', schema, *options])
                for schema, options in passes]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as raw:
            temporary = Path(raw.name)
            # Drain output while the client runs. Errors stay private and bounded
            # in a temporary descriptor; failed/truncated output is never published.
            writer = gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) if target.name.endswith('.gz') else raw
            try:
                for schema, command in commands:
                    with tempfile.TemporaryFile(dir=target.parent) as errors:
                        process = subprocess.Popen(command, env=project.child_env(), stdout=subprocess.PIPE,
                                                   stderr=errors)
                        try:
                            count = 0
                            if '--no-data' in command:
                                # Structure only: small enough to rewrite in memory.
                                block = keep_existing(process.stdout.read())
                                if schema != database:
                                    # An extra schema's skip tables: its structure dump names no schema.
                                    block = b'\nUSE `' + schema.encode() + b'`;\n' + block
                                writer.write(block)
                                count = len(block)
                            while True:
                                block = process.stdout.read(1024 * 1024)
                                if not block:
                                    break
                                writer.write(block)
                                count += len(block)
                            if process.wait() or count == 0:
                                raise ValueError('Database backup failed; check service/permissions. No backup published.')
                        finally:
                            process.stdout.close()
                            if process.poll() is None:
                                process.terminate()
                                process.wait()
            finally:
                if writer is not raw:
                    writer.close()
            raw.flush()
            os.fsync(raw.fileno())
        # Link publishes exclusively, unlike replace: concurrent backups cannot
        # overwrite an existing destination after the initial existence check.
        os.link(temporary, target)
        print(f'Database backup: {target}')
        record_unregistered(project, target)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_unregistered(project, backup):
    """After a backup: the tables the registry does not know, printed and kept for /metrics.

    data/<env>/table-registry/status.json holds table names only (no data). A failed check never fails
    the backup it follows."""
    if not project.settings.get('SCHEMA_DIRECTORY'):
        return None
    from database_registry import load, unknown_tables
    from database_schema import registry_tables
    try:
        tables = registry_tables(project)
        unknown = sorted(unknown_tables(load(project), tables))
    except ValueError as error:
        print(f'Warning: the table registry was not checked after the backup: {error}')
        return None
    status = {'checked': int(time.time()), 'backup': backup.name, 'tables': len(tables), 'unregistered': unknown}
    directory = project.data_directory / 'table-registry'
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=directory, delete=False, suffix='.tmp') as handle:
        json.dump(status, handle, indent=1)
        handle.write('\n')
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, directory / 'status.json')
    if unknown:
        shown = ', '.join(unknown[:10]) + (' …' if len(unknown) > 10 else '')
        print(f'Warning: {len(unknown)} tables are not in the table registry ({shown}); their data is in this backup. '
              'Classify them in the backup UI or with make schema-resolve.')
    return status
