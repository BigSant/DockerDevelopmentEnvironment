"""Atomic private logical backups, optionally compressed, using the running DB."""

from datetime import datetime, timezone
import gzip
import os
from pathlib import Path
import subprocess
import tempfile

from database_client import DUMP


def backup_database(project, destination=None):
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
    command = project.command + ['exec', '-T', 'database', 'sh', '-c', DUMP, 'database-backup', database]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as raw:
            temporary = Path(raw.name)
            # Drain output while the client runs. Errors stay private and bounded
            # in a temporary descriptor; failed/truncated output is never published.
            with tempfile.TemporaryFile(dir=target.parent) as errors:
                process = subprocess.Popen(command, env=project.child_env(), stdout=subprocess.PIPE, stderr=errors)
                try:
                    if target.name.endswith('.gz'):
                        writer = gzip.GzipFile(fileobj=raw, mode='wb', mtime=0)
                    else:
                        writer = raw
                    count = 0
                    try:
                        while True:
                            block = process.stdout.read(1024 * 1024)
                            if not block:
                                break
                            writer.write(block)
                            count += len(block)
                    finally:
                        if writer is not raw:
                            writer.close()
                    if process.wait() or count == 0:
                        raise ValueError('Database backup failed; check service/permissions. No backup published.')
                finally:
                    process.stdout.close()
                    if process.poll() is None:
                        process.terminate()
                        process.wait()
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
