"""make db-pull: download the newest stage/prod backup that the setup-server keeps, over SSH from the VPN.

The server side is setup-server's bin/add-backup-key: the developer's key may only run its backup command
(`latest PROJECT ENV` names the newest backup, `get PROJECT ENV FILE` sends it). Nothing here reaches LIVE.
The file lands in .generated/backups (0600); make db-import loads it, or `make db-pull import=1` does both.
"""

import gzip
import os
from pathlib import Path
import re
import subprocess
import tempfile

TARGET = re.compile(r'[a-z_][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.:-]*')
NAME = re.compile(r'[a-z0-9][a-z0-9_-]*')
# The nightly series of setup-server and the pre-deploy backups of make db-backup (the server's naming).
BACKUP = re.compile(r'(?:worker-(stage|prod)-\d{8}T\d{6}Z|[a-z0-9][a-z0-9_-]*-(stage|prod)-\d{8}T\d{6}\d*)\.sql\.gz')
ENVIRONMENTS = ('stage', 'prod')


def ssh_command(project):
    target = project.settings.get('BACKUP_SSH', '')
    if not target:
        raise ValueError('Set BACKUP_SSH in env/common.env: the setup-server account on the VPN, e.g. setup@10.8.0.14')
    if not TARGET.fullmatch(target):
        raise ValueError('BACKUP_SSH must be user@host, e.g. setup@10.8.0.14')
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=15']
    key = project.settings.get('BACKUP_SSH_KEY', '')
    if key:
        command += ['-i', str(Path(key).expanduser()), '-o', 'IdentitiesOnly=yes']
    return command + [target]


def server_project(project):
    name = project.settings.get('BACKUP_SERVER_PROJECT') or project.settings.get('PROJECT_NAME', '')
    if not NAME.fullmatch(name):
        raise ValueError('BACKUP_SERVER_PROJECT must be the project\'s directory name on the server (projects/<name>)')
    return name


def refusal(result):
    message = (result.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
    message = message[-1] if message else 'no answer'
    if result.returncode == 255:  # ssh itself failed: no route, no key, host key
        return (f'Cannot reach the backup server ({message}). Check the VPN, BACKUP_SSH, and that your public key '
                'was added on the server (setup-server: sudo bin/add-backup-key).')
    return f'The backup server refused: {message}'


def pull(project, source='prod', run=subprocess.run):
    """The local path of the newest backup of ENV `source`; downloads it unless it is already here."""
    if project.environment != 'local':
        raise ValueError('make db-pull downloads into a developer copy: run it with ENV=local')
    if source not in ENVIRONMENTS:
        raise ValueError('from= must be stage or prod')
    name, ssh = server_project(project), ssh_command(project)
    latest = run(ssh + [f'latest {name} {source}'], capture_output=True, timeout=60)
    if latest.returncode:
        raise ValueError(refusal(latest))
    file = latest.stdout.decode('utf-8', 'replace').strip()
    match = BACKUP.fullmatch(file)
    if not match or source not in match.groups():
        raise ValueError('The backup server named no usable backup')
    directory = project.directory / '.generated/backups'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / file
    if target.is_file():
        print(f'Already downloaded: {target}')
    else:
        from project_storage import require_space
        require_space(project, directory)
        print(f'Downloading the newest {source} backup of {name}: {file}')
        handle = tempfile.NamedTemporaryFile(dir=directory, prefix='.pull-', delete=False)
        partial = Path(handle.name)
        try:
            with handle:
                os.chmod(partial, 0o600)
                result = run(ssh + [f'get {name} {source} {file}'], stdout=handle, stderr=subprocess.PIPE, timeout=4 * 3600)
            if result.returncode:
                raise ValueError(refusal(result))
            try:
                with gzip.open(partial) as data:  # a cut download fails here, never as a half import
                    while data.read(1024 * 1024):
                        pass
            except (OSError, EOFError) as error:
                raise ValueError(f'The download is not a complete .sql.gz ({error}); nothing was kept') from None
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
    print(f'Backup: {target} ({target.stat().st_size / 1e6:.1f} MB)')
    print('Warning: this is the server\'s copy with real customer data. It stays private (0600) in .generated/backups; '
          'delete it when you no longer need it.')
    return target
