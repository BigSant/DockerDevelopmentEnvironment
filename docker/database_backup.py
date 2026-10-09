"""Atomic private logical backups, optionally compressed, using the running DB."""

from datetime import datetime, timezone
import gzip
import os
from pathlib import Path
import subprocess
import tempfile

from database_client import DUMP


def registry_passes(project, database):
    """mysqldump option sets: everything without the data of skip tables, then their structure only."""
    from database_import import skip_rule
    from database_schema import database_tables
    rule = skip_rule(project)
    skipped = [table for table in database_tables(project) if rule(table)] if rule else []
    if not skipped:
        return [[]]
    return [[f'--ignore-table={database}.{table}' for table in skipped],
            ['--no-data', '--skip-routines', '--skip-events', *skipped]]


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
    passes = registry_passes(project, database) if registry else [[]]
    commands = [project.command + ['exec', '-T', 'database', 'sh', '-c', DUMP, 'database-backup', database, *options]
                for options in passes]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as raw:
            temporary = Path(raw.name)
            # Drain output while the client runs. Errors stay private and bounded
            # in a temporary descriptor; failed/truncated output is never published.
            writer = gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) if target.name.endswith('.gz') else raw
            try:
                for command in commands:
                    with tempfile.TemporaryFile(dir=target.parent) as errors:
                        process = subprocess.Popen(command, env=project.child_env(), stdout=subprocess.PIPE,
                                                   stderr=errors)
                        try:
                            count = 0
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
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
