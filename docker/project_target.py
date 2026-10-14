"""The Deploy target: where a release's files go, this machine or a server reached over ssh (DEPLOY_SSH).

The containers' Docker daemon is a separate setting (DEPLOY_CONTEXT) and not handled here. Both targets
answer the same operations; paths are the target's own."""

from pathlib import Path
import re
import shlex
import subprocess

HOST = re.compile(r'[A-Za-z0-9._-]+@[A-Za-z0-9.-]+')
# A password prompt or an unreachable host fails at once instead of holding a pipeline. Host keys are checked
# as ssh is configured (known_hosts), never accepted blindly: the target receives production code.
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15']
RSYNC_SSH = ['-e', shlex.join(['ssh', *SSH_OPTIONS])]


def deploy_target(project, run=subprocess.run):
    """This project's Deploy target: an SshTarget with DEPLOY_SSH=user@host, else a LocalTarget."""
    host = project.settings.get('DEPLOY_SSH') or ''
    if not host:
        return LocalTarget(run)
    if not HOST.fullmatch(host):
        raise ValueError('DEPLOY_SSH must look like user@host')
    return SshTarget(host, run)


def listeners(output):
    """{(address, port)} of `ss -Hltn` lines; IPv6 addresses without their brackets."""
    found = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 4 and ':' in fields[3]:
            address, port = fields[3].rsplit(':', 1)
            found.add((address.strip('[]'), port))
    return found


class LocalTarget:
    """The release's files stay on this machine (a build server deploying to itself, or a local directory)."""
    local = True
    label = ''

    def __init__(self, run=subprocess.run):
        self.run = run

    def mkdir(self, paths):
        for path in paths:
            Path(path).mkdir(parents=True, exist_ok=True)

    def push(self, source, path, *options):
        """rsync source (a local path; a trailing / copies a directory's contents) to path on the target."""
        self.run(['rsync', *options, str(source), path], check=True)

    def pull(self, path, local, *options):
        self.run(['rsync', *options, path, str(local)], check=True)

    def directories(self, path):
        folder = Path(path)
        return {child.name for child in folder.iterdir() if child.is_dir()} if folder.is_dir() else set()

    def remove(self, path, names):
        for name in names:
            (Path(path) / name).unlink(missing_ok=True)

    def listening(self):
        """The listening TCP addresses, or None when they cannot be read."""
        try:
            result = self.run(['ss', '-Hltn'], capture_output=True, text=True)
        except OSError:
            return None
        return None if result.returncode else listeners(result.stdout)


class SshTarget:
    """The release's files go to user@host; every remote command's arguments are quoted for its shell."""
    local = False

    def __init__(self, host, run=subprocess.run):
        self.host, self.run = host, run
        self.label = host + ':'

    def ssh(self, *command):
        return ['ssh', *SSH_OPTIONS, self.host, shlex.join(command)]

    def mkdir(self, paths):
        if paths:
            self.run(self.ssh('mkdir', '-p', '--', *paths), check=True)

    def push(self, source, path, *options):
        self.run(['rsync', *RSYNC_SSH, *options, str(source), f'{self.host}:{path}'], check=True)

    def pull(self, path, local, *options):
        self.run(['rsync', *RSYNC_SSH, *options, f'{self.host}:{path}', str(local)], check=True)

    def directories(self, path):
        result = self.run(self.ssh('find', path, '-mindepth', '1', '-maxdepth', '1', '-type', 'd', '-printf', '%f\\n'),
                          capture_output=True, text=True)
        return set(result.stdout.split('\n')) - {''} if result.returncode == 0 else set()

    def remove(self, path, names):
        if names:
            listing = ''.join(f'{name}\0' for name in names).encode()
            self.run(['ssh', *SSH_OPTIONS, self.host, f'cd {shlex.quote(path)} && xargs -0 rm -f --'], input=listing,
                     check=True)

    def listening(self):
        result = self.run(self.ssh('ss', '-Hltn'), capture_output=True, text=True)
        return None if result.returncode else listeners(result.stdout)
