"""Files backups: the code tree paths BACKUP_FILE_PATHS lists, copied from the Deploy target into a tar.gz here, and
make files-pull, which downloads the newest one setup-server keeps (as make db-pull does a database backup).

make files-backup ENV=stage|prod|<extra> [file=PATH.tar.gz]
Files the shop writes on the target that its repository ignores (e.g. modules/*/translations/) have no copy in
git; the setup-server worker runs this every night and keeps files-<env>-<UTC time>Z.tar.gz in its backups. rsync
keeps a mirror of the listed paths beside the archive (.files-<env>/), so a run transfers only what changed and
descends only into the directories a pattern names. The archive is written to a temporary file and renamed when
complete: a failed run publishes nothing. Nothing on the target is written; no secret or container is needed.

make files-pull [from=prod|stage|<extra>] [fresh=1] [extract=1]   (ENV=local)
The same server, key and grants as make db-pull (setup-server: db downloads, fresh asks for a new one): `info
PROJECT ENV files`, `get …`, `fresh PROJECT ENV files`. The archive lands in .generated/backups (0600) and is read to
its end on the way; extract=1 writes its regular files that BACKUP_FILE_PATHS of this checkout names into the local
code tree (they replace what is there; nothing else is touched or deleted, links are skipped).
"""

from datetime import datetime, timezone
from fnmatch import fnmatchcase
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile

from project_environments import is_server

SETTING = 'BACKUP_FILE_PATHS'
VANISHED = 24  # rsync: some source files vanished while it ran (the shop wrote meanwhile); the rest is copied
CHUNK = 1024 * 1024


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


def archive_name(source):
    """setup-server's files backups of one server environment."""
    return re.compile(rf'files-{source}-\d{{8}}T\d{{6}}Z\.tar\.gz')


def named_archive(answer, source):
    """(file, size or 0) from a server answer "<file> [<size>]"; refuses anything else."""
    words = answer.decode('utf-8', 'replace').strip().split()
    file = words[0] if words else ''
    if not archive_name(source).fullmatch(file) or len(words) > 2 or (len(words) == 2 and not words[1].isdigit()):
        raise ValueError('The backup server named no usable files backup')
    return file, int(words[1]) if len(words) == 2 else 0


def no_files_server(answer):
    """setup-server before files backups refuses the request's last word."""
    return answer.returncode == 65 and b'the last word may only be full' in (answer.stderr or b'')


def download_archive(ssh, name, source, file, size, target, popen=subprocess.Popen, meter=None):
    """Download into a private temporary file, read the archive to its end, and keep it only when complete."""
    from database_pull import refusal
    from progress import Bar
    meter = meter or Bar
    handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pull-', delete=False)
    partial = Path(handle.name)
    try:
        with handle:
            os.chmod(partial, 0o600)
            with popen(ssh + [f'get {name} {source} {file}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                bar, done = meter('Download', size), 0
                while block := process.stdout.read(CHUNK):
                    handle.write(block)
                    done += len(block)
                    bar.advance(len(block))
                errors = process.stderr.read()
                status = process.wait()
            bar.finish('failed' if status or (size and done != size) else '')
            if status:
                raise ValueError(refusal(subprocess.CompletedProcess([], status, b'', errors)))
            if size and done != size:
                raise ValueError(f'The download has {done} of {size} bytes; nothing was kept')
        try:
            with tarfile.open(partial, 'r:gz') as archive:
                for _ in archive:
                    pass
        except (tarfile.TarError, EOFError, OSError) as error:
            raise ValueError(f'The download is not a complete .tar.gz ({error}); nothing was kept') from None
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def listed(name, patterns):
    """Whether a code tree path is one BACKUP_FILE_PATHS names: under a directory pattern, or a file pattern itself."""
    parts = name.split('/')
    for pattern in patterns:
        wanted = pattern.strip('/').split('/')
        directory = pattern.endswith('/')
        if (len(parts) > len(wanted) if directory else len(parts) == len(wanted)) and \
                all(fnmatchcase(part, want) for part, want in zip(parts, wanted)):
            return True
    return False


def extract_archive(project, path):
    """Write the archive's regular files that this checkout's BACKUP_FILE_PATHS names into the local code tree."""
    from project_deploy import code_tree_patterns
    patterns = code_tree_patterns(project, SETTING)
    if not patterns:
        raise ValueError(f'{SETTING} is empty in this checkout: set it as on the server (env/common.env) to extract')
    root = Path(project.web_directory).resolve()
    written = skipped = 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            name = member.name.strip('/')
            parts = name.split('/')
            if member.isdir():
                continue
            if not member.isfile() or not name or member.name.startswith('/') or '..' in parts or '.' in parts \
                    or not listed(name, patterns):
                skipped += 1
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(root) or target.is_symlink():
                skipped += 1
                continue
            with archive.extractfile(member) as source, tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pull-',
                                                                                    delete=False) as handle:
                while block := source.read(CHUNK):
                    handle.write(block)
            os.chmod(handle.name, 0o644)
            os.utime(handle.name, (member.mtime, member.mtime))
            os.replace(handle.name, target)
            written += 1
    print(f'Extracted {written} files into {root}' + (f'; {skipped} skipped (links, or not in {SETTING})' if skipped else '')
          + '; nothing else was touched')
    return written


def pull_files(project, source='prod', fresh=False, extract=False, run=subprocess.run, popen=subprocess.Popen,
               reconfigure=False, ask=input, interactive=None):
    """The local path of the newest (or, with fresh, a new) files backup of ENV `source`; downloads it unless it is
    here, and with extract writes its files into the local code tree."""
    from database_pull import connection, fresh_backup, newest, refusal, ssh_command
    from progress import amount, step
    from project_environments import server_environments
    if project.environment != 'local':
        raise ValueError('make files-pull downloads into a developer copy: run it with ENV=local')
    if source not in server_environments(project.directory):
        raise ValueError('from= must be stage, prod or an environment STAGE_ENVIRONMENTS lists')
    first, count = 1, 1 + int(fresh) + int(extract)
    ssh = ssh_command(*connection(project, reconfigure, ask, interactive))
    name, latest = newest(project, ssh, source, run, ask, interactive, word='files')
    if no_files_server(latest):
        raise ValueError('The backup server has no files backups yet (update setup-server: bin/update)')
    if fresh:
        if latest.returncode and b'backup of' not in (latest.stderr or b''):  # "no prod files backup of X yet" is fine
            raise ValueError(refusal(latest))
        step(first, count, f'A new {source} files backup of {name} on the server')
        file, size = fresh_backup(ssh, name, source, False, popen, word='files',
                                  named=lambda answer: named_archive(answer, source))
        first += 1
    else:
        if latest.returncode:
            raise ValueError(refusal(latest))
        file, size = named_archive(latest.stdout, source)
    directory = project.directory / '.generated/backups'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / file
    step(first, count, f'Download {file}' + (f' ({amount(size)})' if size else ''))
    if target.is_file() and (not size or target.stat().st_size == size):
        print(f'Already downloaded: {target}')
    else:
        from project_storage import require_space
        require_space(project, directory, size)
        download_archive(ssh, name, source, file, size, target, popen)
    print(f'Files backup: {target} ({amount(target.stat().st_size)})')
    if extract:
        step(count, count, f'Extract into the code tree ({SETTING})')
        extract_archive(project, target)
    return target
