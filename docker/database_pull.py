"""make db-pull: download a stage/prod backup that the setup-server keeps, over SSH from the VPN.

The server side is setup-server's bin/add-backup-key: the developer's key may only run its backup command
(`info PROJECT ENV [full]` names the newest backup and its size, `get PROJECT ENV FILE` sends it, and with
--allow-fresh `fresh PROJECT ENV [full]` makes a new one first). Nothing here reaches LIVE. The file lands in
.generated/backups (0600); make db-import loads it, or `make db-pull import=1` does both.

Which backup: the newest registry backup (setup's: structure of every table, the import tables' data; nightly,
fresh or pre-deploy), `full=1` the newest full one (every table's data; weekly), `fresh=1` a new one made now
(with full=1 a new full one). Each step shows a progress bar on a terminal: the backup on the server (fresh), the
download (checked and read on the way, so the import does not read it again), and with import=1 the unpacking,
the import, after-import and the migrations.

Where to ask: the project's BACKUP_SSH / BACKUP_SSH_KEY, else this machine's answer in setup's docker/.env.backup,
which the first run in a terminal asks for (configure=1 asks again) and every project then uses. Which project:
BACKUP_SERVER_PROJECT, else the git repository's name (the server's build directory is named after it), else
PROJECT_NAME; when the server knows neither, a terminal run asks and keeps the answer in the project's env/local.env.
"""

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zlib

from project_remote import origin_url

TARGET = re.compile(r'[a-z_][a-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.:-]*')
NAME = re.compile(r'[a-z0-9][a-z0-9_-]*')
# The nightly series of setup-server and the pre-deploy backups of make db-backup (the server's naming).
# setup-server's series (nightly worker-, weekly full-, fresh-, fresh-full-) and setup's pre-deploy backups.
BACKUP = re.compile(r'(?:(?:worker|full|fresh|fresh-full)-(stage|prod)-\d{8}T\d{6}Z'
                    r'|[a-z0-9][a-z0-9_-]*-(stage|prod)-\d{8}T\d{6}\d*)\.sql\.gz')
FULL_SERIES = re.compile(r'(?:full|fresh-full)-(stage|prod)-\d{8}T\d{6}Z\.sql\.gz')
PROGRESS = re.compile(r'progress backup (\d+) (\d+)')
CHUNK = 1024 * 1024
ENVIRONMENTS = ('stage', 'prod')
# This machine's backup server, shared by every project (ignored by git: .env.*).
SHARED = Path(__file__).with_name('.env.backup')


def read_shared(path=None):
    path = SHARED if path is None else path
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.fullmatch(r"([A-Z_]+)=(.*)", line.strip())
            if match:
                values[match[1]] = match[2].strip().strip("'\"")
    return values


def configure(project, ask=input, path=None):
    """Ask for this machine's backup server and key; stored for every project."""
    from project_bootstrap import set_env_values
    path = SHARED if path is None else path
    current = read_shared(path)
    default = current.get('BACKUP_SSH', '')
    target = ask('setup-server SSH account (user@host, e.g. setup@10.8.0.14)'
                 + (f' [{default}]' if default else '') + ': ').strip() or default
    if not TARGET.fullmatch(target):
        raise ValueError('The backup server must be user@host, e.g. setup@10.8.0.14; nothing was saved')
    default = current.get('BACKUP_SSH_KEY', '')
    key = ask('Private SSH key (empty: ssh\'s default' + (f'; - for none, [{default}]' if default else '') + '): ').strip()
    key = '' if key == '-' else key or default
    if key and not Path(key).expanduser().is_file():
        raise ValueError(f'No key file {key}; nothing was saved')
    set_env_values(path, {'BACKUP_SSH': target, 'BACKUP_SSH_KEY': key})
    print(f'Saved in {path}: every project on this machine uses it (a project\'s own BACKUP_SSH wins).')
    if project.settings.get('BACKUP_SSH'):
        print(f'Note: this project sets BACKUP_SSH={project.settings["BACKUP_SSH"]} itself, so it keeps using that.')
    return {'BACKUP_SSH': target, 'BACKUP_SSH_KEY': key}


def connection(project, reconfigure=False, ask=input, interactive=None, path=None):
    """(user@host, key) of the backup server: the project's settings, else this machine's shared answer."""
    shared = read_shared(path)
    if reconfigure or not (project.settings.get('BACKUP_SSH') or shared.get('BACKUP_SSH')):
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            raise ValueError('Set BACKUP_SSH: run make db-pull once in a terminal (it asks and keeps the answer for '
                             'every project on this machine) or put BACKUP_SSH=setup@10.8.0.14 in env/common.env')
        shared = configure(project, ask, path)
    target = project.settings.get('BACKUP_SSH') or shared.get('BACKUP_SSH', '')
    if not TARGET.fullmatch(target):
        raise ValueError('BACKUP_SSH must be user@host, e.g. setup@10.8.0.14')
    return target, project.settings.get('BACKUP_SSH_KEY') or shared.get('BACKUP_SSH_KEY', '')


def ssh_command(target, key=''):
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=15']
    if key:
        command += ['-i', str(Path(key).expanduser()), '-o', 'IdentitiesOnly=yes']
    return command + [target]


def git_name(directory):
    """The repository name of the origin remote, as the server names build directories (lower case); '' unless
    the directory is the root of its own repository (the environment repository the pipeline deploys)."""
    top = subprocess.run(['git', '-C', str(directory), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if top.returncode or Path(top.stdout.strip()).resolve() != Path(directory).resolve():
        return ''
    url = origin_url(directory)
    if not url:
        return ''
    name = re.split(r'[/:]', url.rstrip('/'))[-1]
    return name[:-4].lower() if name.endswith('.git') else name.lower()


def server_projects(project):
    """The names to ask the server for, in order: BACKUP_SERVER_PROJECT alone, else the git name, then PROJECT_NAME."""
    configured = project.settings.get('BACKUP_SERVER_PROJECT', '')
    if configured:
        if not NAME.fullmatch(configured):
            raise ValueError('BACKUP_SERVER_PROJECT must be the project\'s directory name on the server (projects/<name>)')
        return [configured]
    names = []
    for name in (git_name(project.directory), project.settings.get('PROJECT_NAME', '')):
        if NAME.fullmatch(name) and name not in names:
            names.append(name)
    return names


def refusal(result):
    message = (result.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
    message = message[-1] if message else 'no answer'
    if result.returncode == 255:  # ssh itself failed: no route, no key, host key
        return (f'Cannot reach the backup server ({message}). Check the VPN, BACKUP_SSH, and that your public key '
                'was added on the server (setup-server: sudo bin/add-backup-key).')
    return f'The backup server refused: {message}'


def unknown_project(result, name):
    return result.returncode == 65 and f'no project {name} on this server'.encode() in (result.stderr or b'')


def old_server(result):
    """setup-server before info/fresh answers its usage line to them."""
    return result.returncode == 65 and b'expected: latest PROJECT ENV | get PROJECT ENV FILE' in (result.stderr or b'')


def request_words(name, source, full):
    return f'{name} {source}' + (' full' if full else '')


def newest(project, ssh, source, run, ask, interactive, full=False):
    """(server project name, the server's answer to `info`); asks for the name when the server knows none tried.

    The answer of a server without `info` (setup-server before the weekly full backups) is `latest`'s."""
    tried = []

    def info(name):
        answer = run(ssh + [f'info {request_words(name, source, full)}'], capture_output=True, timeout=60)
        if old_server(answer):
            if full:
                raise ValueError('The backup server is too old for full=1 (update setup-server: bin/update)')
            answer = run(ssh + [f'latest {name} {source}'], capture_output=True, timeout=60)
        return answer

    for name in server_projects(project):
        answer = info(name)
        if not unknown_project(answer, name):
            return name, answer
        tried.append(name)
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if project.settings.get('BACKUP_SERVER_PROJECT') or not interactive:
        raise ValueError(f'The backup server has no project named {" or ".join(tried) or "(no name)"}; set '
                         'BACKUP_SERVER_PROJECT to its directory name there (projects/<name>) in env/local.env')
    name = ask(f'The backup server has no project {" or ".join(tried) or "(no name)"}. '
               'Its directory name there (projects/<name>): ').strip()
    if not NAME.fullmatch(name):
        raise ValueError('A server project name is lower case letters, digits, _ and -; nothing was saved')
    answer = info(name)
    if unknown_project(answer, name):
        raise ValueError(f'The backup server has no project {name} either; nothing was saved')
    from project_bootstrap import set_env_values
    local = Path(project.directory) / f'env/{project.environment}.env'
    set_env_values(local, {'BACKUP_SERVER_PROJECT': name})
    print(f'Saved BACKUP_SERVER_PROJECT={name} in {local}.')
    return name, answer


def named_backup(answer, source, full):
    """(file, size or 0) from a server answer "<file> [<size>]"; refuses anything else."""
    words = answer.decode('utf-8', 'replace').strip().split()
    file = words[0] if words else ''
    size = int(words[1]) if len(words) == 2 and words[1].isdigit() else 0
    match = BACKUP.fullmatch(file)
    if not match or source not in match.groups() or len(words) > 2 or bool(FULL_SERIES.fullmatch(file)) != full:
        raise ValueError('The backup server named no usable backup')
    return file, size


def fresh_backup(ssh, name, source, full, popen=subprocess.Popen, meter=None):
    """Ask the server for a new backup and follow its progress; (file, size) of the result."""
    from progress import Bar
    meter = meter or Bar
    process = popen(ssh + [f'fresh {request_words(name, source, full)}'], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE)
    bar, said = None, []
    for raw in process.stderr:
        line = raw.decode('utf-8', 'replace').rstrip()
        match = PROGRESS.fullmatch(line)
        if match:
            if bar is None:
                bar = meter('Backup on the server', int(match[2]))
            bar.update(int(match[1]), int(match[2]) or None)
            continue
        said.append(line)
        if line.startswith('==>') or line.startswith('    '):
            # The server's own steps (waiting for a running backup, reusing a recent one) are worth reading.
            if bar is not None:
                sys.stderr.write('\n')
            print(line, file=sys.stderr, flush=True)
    answer = process.stdout.read()
    status = process.wait()
    if bar is not None:
        bar.finish('failed' if status else '')
    if status:
        message = next((line for line in reversed(said) if line.strip()), 'no answer')
        if status == 255:
            raise ValueError(f'Cannot reach the backup server ({message}). Check the VPN, BACKUP_SSH, and that your '
                             'public key was added on the server (setup-server: sudo bin/add-backup-key).')
        if 'expected: latest PROJECT ENV | get PROJECT ENV FILE' in message:
            message = 'it cannot make fresh backups yet (update setup-server: bin/update)'
        raise ValueError(f'The backup server refused: {message}')
    return named_backup(answer, source, full)


class Checked:
    """Writes a download to its file while checking the gzip stream and reading the dump inside it."""

    def __init__(self, handle, scanner):
        self.handle, self.scanner = handle, scanner
        self.inflate, self.fed, self.members = zlib.decompressobj(31), False, 0

    def write(self, data):
        self.handle.write(data)
        while data:
            self.fed = True
            self.scanner.feed(self.inflate.decompress(data))
            if self.inflate.eof:  # gzip allows several members one after another
                data = self.inflate.unused_data
                self.inflate, self.fed = zlib.decompressobj(31), False
                self.members += 1
            else:
                data = b''

    def complete(self):
        if self.fed or not self.members:  # cut in the middle of a member, or nothing at all
            raise zlib.error('the gzip stream ends in the middle' if self.fed else 'nothing was sent')


def download(project, ssh, name, source, file, size, target, popen=subprocess.Popen, meter=None):
    """Download into a private temporary file, check and read it on the way, and keep it only when complete."""
    from database_import import DumpScanner, main_schema, remember_scan
    from progress import Bar
    meter = meter or Bar
    directory = target.parent
    handle = tempfile.NamedTemporaryFile(dir=directory, prefix='.pull-', delete=False)
    partial = Path(handle.name)
    scanner = DumpScanner(main_schema(project) if project.settings.get('DATABASE_NAME') else None)
    try:
        with handle:
            os.chmod(partial, 0o600)
            checked = Checked(handle, scanner)
            process = popen(ssh + [f'get {name} {source} {file}'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            bar = meter('Download', size)
            try:
                while block := process.stdout.read(CHUNK):
                    checked.write(block)
                    bar.advance(len(block))
                checked.complete()
            except zlib.error as error:
                process.kill()
                process.wait()
                bar.finish('failed')
                raise ValueError(f'The download is not a complete .sql.gz ({error}); nothing was kept') from None
            errors = process.stderr.read()
            status = process.wait()
            bar.finish('failed' if status else '')
            if status:
                raise ValueError(refusal(subprocess.CompletedProcess([], status, b'', errors)))
            if size and bar.done != size:
                raise ValueError(f'The download has {bar.done} of {size} bytes; nothing was kept')
        os.replace(partial, target)
        remember_scan(target, scanner.main, scanner.result())
    finally:
        partial.unlink(missing_ok=True)


def pull(project, source='prod', run=subprocess.run, reconfigure=False, ask=input, interactive=None, full=False,
         fresh=False, popen=subprocess.Popen, steps=None):
    """The local path of the newest (or, with fresh, a new) backup of ENV `source`; downloads it unless it is here.

    steps: (first step number, number of steps) for the step headings when the caller goes on (import=1)."""
    from progress import amount, step
    if project.environment != 'local':
        raise ValueError('make db-pull downloads into a developer copy: run it with ENV=local')
    if source not in ENVIRONMENTS:
        raise ValueError('from= must be stage or prod')
    first, count = steps or (1, 2 if fresh else 1)
    ssh = ssh_command(*connection(project, reconfigure, ask, interactive))
    name, latest = newest(project, ssh, source, run, ask, interactive, full)
    kind = ('a new full' if full else 'a new') if fresh else ('the newest full' if full else 'the newest')
    if fresh:
        if latest.returncode and b'backup of' not in (latest.stderr or b''):  # "no prod backup of X yet" is fine
            raise ValueError(refusal(latest))
        step(first, count, f'{kind[0].upper() + kind[1:]} {source} backup of {name} on the server')
        file, size = fresh_backup(ssh, name, source, full, popen)
        first += 1
    else:
        if latest.returncode:
            raise ValueError(refusal(latest))
        file, size = named_backup(latest.stdout, source, full)
    directory = project.directory / '.generated/backups'
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = directory / file
    step(first, count, f'Download {file}' + (f' ({amount(size)})' if size else ''))
    if target.is_file() and (not size or target.stat().st_size == size):
        print(f'Already downloaded: {target}')
    else:
        from project_storage import require_space
        require_space(project, directory, size)
        download(project, ssh, name, source, file, size, target, popen)
    print(f'Backup: {target} ({amount(target.stat().st_size)})')
    print('Warning: this is the server\'s copy with real customer data. It stays private (0600) in .generated/backups; '
          'delete it when you no longer need it.')
    return target
