"""Disk headroom checks and explicitly requested pruning of generated backups."""
from datetime import datetime
from pathlib import Path
import re
import shutil


def require_space(project, path, extra=0):
    path = Path(path)
    while not path.exists(): path = path.parent
    reserve = int(project.settings.get('MIN_FREE_DISK_MB', '0')) * 1024 * 1024
    free = shutil.disk_usage(path).free
    required = reserve + extra
    if free < required:
        raise ValueError(f'Not enough free disk space at {path}: need about {required // (1024*1024)} MiB, available {free // (1024*1024)} MiB (including MIN_FREE_DISK_MB reserve)')


def prune_backups(project, apply=False):
    directory = project.directory / '.generated/backups'
    if directory.is_symlink() or directory.parent.is_symlink() or not directory.resolve().is_relative_to(project.directory.resolve()):
        raise ValueError('Backup directory must stay inside the project without symlinks')
    pattern = re.compile(re.escape(project.name) + r'-(\d{8}T\d{12})\.sql\.gz$')
    candidates = []
    for path in directory.glob('*.sql.gz'):
        match = pattern.fullmatch(path.name)
        if path.is_symlink() or not path.is_file() or not match: continue
        try: datetime.strptime(match[1], '%Y%m%dT%H%M%S%f')
        except ValueError: continue
        candidates.append(path)
    keep = int(project.settings['BACKUP_KEEP_LAST'])
    for path in sorted(candidates, reverse=True)[keep:]:
        print(('Remove: ' if apply else 'Would remove: ') + str(path))
        if apply: path.unlink()
    if not apply: print('Preview only; use apply=1 to remove only these generated backups.')
