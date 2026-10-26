"""Files backup: the code tree paths BACKUP_FILE_PATHS lists, copied from the Deploy target into a tar.gz here.

make files-backup ENV=stage|prod|<extra> [file=PATH.tar.gz]
Files the shop writes on the target that its repository ignores (e.g. modules/*/translations/) have no copy in
git; the setup-server worker runs this every night and keeps files-<env>-<UTC time>Z.tar.gz in its backups. rsync
keeps a mirror of the listed paths beside the archive (.files-<env>/), so a run transfers only what changed and
descends only into the directories a pattern names. The archive is written to a temporary file and renamed when
complete: a failed run publishes nothing. Nothing on the target is written; no secret or container is needed.
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

from project_environments import is_server

SETTING = 'BACKUP_FILE_PATHS'
VANISHED = 24  # rsync: some source files vanished while it ran (the shop wrote meanwhile); the rest is copied


def include_filters(patterns):
    """rsync filters that copy only the anchored patterns: every parent directory on the way is included, so rsync
    descends only there, a directory pattern brings everything under it, the rest is excluded."""
    includes = []
    for pattern in patterns:
        parts = pattern.strip('/').split('/')
        steps = ['/' + '/'.join(parts[:depth]) + '/' for depth in range(1, len(parts))]
        steps.append('/' + '/'.join(parts) + ('/***' if pattern.endswith('/') else ''))
        includes += [step for step in steps if step not in includes]
    return [*(f'--include={step}' for step in includes), '--exclude=*']


def unmatched(mirror, patterns):
    """The patterns nothing in the mirror matches (a directory pattern needs a directory)."""
    missing = []
    for pattern in patterns:
        found = [path for path in Path(mirror).glob(pattern.strip('/')) if not pattern.endswith('/') or path.is_dir()]
        if not found:
            missing.append(pattern.lstrip('/'))
    return missing


def write_archive(mirror, target):
    """gzip'ed tar of the mirror, members named from the code root (no ./), sorted, symlinks kept as links; renamed
    into place when complete. (files, bytes of the archive)."""
    mirror = Path(mirror)
    names = []
    for directory, folders, entries in os.walk(mirror):  # links to directories are listed, never followed
        names += [(Path(directory) / name).relative_to(mirror).as_posix() for name in (*folders, *entries)]
    names.sort()
    files = 0
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='tmp', delete=False) as raw:
            temporary = Path(raw.name)
            with tarfile.open(fileobj=raw, mode='w:gz') as archive:
                for name in names:
                    archive.add(mirror / name, arcname=name, recursive=False)
                    files += not (mirror / name).is_dir() or (mirror / name).is_symlink()
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return files, target.stat().st_size


def size_text(value):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return ''


def backup_files(project, destination=None):
    from project_deploy import code_path, code_tree_patterns
    from project_release import release_settings
    from project_storage import require_space
    from project_target import deploy_target
    environment = project.environment
    if not is_server(environment):
        raise ValueError('files-backup copies a server environment\'s files: ENV=stage, prod or one STAGE_ENVIRONMENTS lists')
    patterns = code_tree_patterns(project, SETTING)
    if not patterns:
        raise ValueError(f'{SETTING} is empty: nothing to back up (e.g. {SETTING}="modules/*/translations/" in env/common.env)')
    _, deploy_path = release_settings(project, remote=False)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    target = Path(destination).expanduser().absolute() if destination else (
        project.directory / '.generated/backups' / f'files-{environment}-{stamp}Z.tar.gz')
    if not target.name.endswith('.tar.gz'):
        raise ValueError('Files backup destination must end in .tar.gz')
    if target.exists() or target.is_symlink():
        raise ValueError('Backup destination already exists; choose a new file')
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require_space(project, target.parent)
    mirror = target.parent / f'.files-{environment}'
    if mirror.is_symlink():
        raise ValueError(f'{mirror} is a symlink; remove it')
    mirror.mkdir(mode=0o700, exist_ok=True)
    source = deploy_target(project)
    path = code_path(project, deploy_path)
    print(f'==> copying {" ".join(pattern.lstrip("/") for pattern in patterns)} from {source.label}{path}', flush=True)
    try:
        source.pull(f'{path}/', f'{mirror}/', '-a', '--delete', '--delete-excluded', '--prune-empty-dirs',
                    *include_filters(patterns))
    except subprocess.CalledProcessError as error:
        if error.returncode != VANISHED:
            raise ValueError(f'rsync from {source.label}{path} failed (exit {error.returncode}); no backup written') from None
        print('warning: some files vanished on the target while they were copied; the others are in the backup')
    finally:
        os.chmod(mirror, 0o700)  # rsync -a gives it the code root's mode
    for pattern in unmatched(mirror, patterns):
        print(f'warning: {SETTING} entry {pattern} matched nothing on the target')
    files, size = write_archive(mirror, target)
    print(f'Backed up {files} files ({size_text(size)}) of {SETTING} into {target}')
    return target
