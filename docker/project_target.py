"""The Deploy target: where a release's files go, this machine or a server reached over ssh (DEPLOY_SSH).

The containers' Docker daemon is a separate setting (DEPLOY_CONTEXT) and not handled here. Both targets
answer the same operations; paths are the target's own."""

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

HOST = re.compile(r'[A-Za-z0-9._-]+@[A-Za-z0-9.-]+')
# A password prompt or an unreachable host fails at once instead of holding a pipeline. Host keys are checked
# as ssh is configured (known_hosts), never accepted blindly: the target receives production code.
SSH_OPTIONS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15']
RSYNC_SSH = ['-e', shlex.join(['ssh', *SSH_OPTIONS])]
# The removal helper (remove_inside.py: descriptors, never a path resolved twice), run by SshTarget.remove on the
# server with python3. A server without python3 gets REMOVE_INSIDE_SH: each name's directory resolved (cd -P) and
# removed only inside the root, every failure counted; it checks paths, so it is not proof against a target that
# changes its own tree meanwhile.
REMOVE_HELPER = (Path(__file__).resolve().parent / 'remove_inside.py').read_text()
# Without python3 3.3+ on the server: each name's directory resolved (cd -P); a removal only where the way holds no
# link (removing through one needs the helper's checks against the new release), inside the root. A directory gone
# holds nothing; one that cannot be entered, a way out of the root or a failed rm stays pending (written to fd 3, never
# mixed with output) and a cd or rm failure makes the exit status non-zero.
REMOVE_INSIDE_SH = ('root=$1; shift; status=0; for name do '
                    'parent=$(dirname -- "$name"); '
                    'if [ ! -e "$parent" ] && [ ! -L "$parent" ]; then continue; fi; '
                    'if ! dir=$(cd -P -- "$parent" 2>/dev/null && pwd -P); then '
                    'echo "setup: cannot enter the directory of $name; it stays pending" >&2; '
                    'printf \'%s\\0\' "$name" >&3; status=1; continue; fi; '
                    'if [ "$parent" = . ]; then expected=$root; else expected=$root/$parent; fi; '
                    'if [ "$dir" != "$expected" ]; then case "$dir/" in '
                    '"$root"/*) echo "setup: keeping $name: its way holds a link, which only the server\'s python3 '
                    '3.3+ removes through" >&2;; '
                    '*) echo "setup: not removing $name: outside the code tree; it stays pending" >&2; '
                    'printf \'%s\\0\' "$name" >&3;; esac; continue; fi; '
                    'if ! rm -f -- "$dir/${name##*/}"; then printf \'%s\\0\' "$name" >&3; status=1; fi; '
                    'done; exit $status')
PYTHON_CHECK = "import sys; sys.exit(0 if sys.version_info >= (3, 3) else 1)"


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

    def remove(self, path, names, keep=(), protected=()):
        """Removes code tree files with the descriptor walk of remove_inside: a name never leaves path, through a
        link (one inside path is followed) or a directory the application swaps meanwhile, and never removes what the
        new release ships (keep) or protects, once resolved. Returns the names left because their way leaves path
        (not removed: the caller keeps them pending); a failed removal raises."""
        from remove_inside import remove_inside
        failed, left, _ = remove_inside(path, [os.fsencode(name) for name in names],
                                        [os.fsencode(name) for name in keep], list(protected), err=sys.stdout)
        if failed:
            raise OSError(f'{failed} files of the code tree could not be removed (above)')
        return [os.fsdecode(name) for name in left]

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
        self.python = None  # whether the server has python3 3.3+ for the removal helper, once asked

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

    def remove(self, path, names, keep=(), protected=()):
        """As LocalTarget.remove, on the server: the helper with the server's python3 3.3+ (asked once), else the
        shell loop of REMOVE_INSIDE_SH. Names travel on standard input only; the names left pending come back on
        file descriptor 3 (the server's own output goes to standard error), and only names sent are taken."""
        if not names:
            return []
        if self.python is None:
            self.python = self.run(self.ssh('python3', '-c', PYTHON_CHECK), capture_output=True).returncode == 0
        if self.python:
            request = {'remove': [os.fsencode(name).hex() for name in names],
                       'keep': [os.fsencode(name).hex() for name in keep], 'protected': list(protected)}
            listing = json.dumps(request).encode()
            remote = f'cd {shlex.quote(path)} && python3 -c {shlex.quote(REMOVE_HELPER)} . 3 3>&1 1>&2'
        else:
            listing = ''.join(f'{name}\0' for name in names).encode()
            remote = (f'cd {shlex.quote(path)} && echo "setup: no python3 3.3+ on the server: removals checked by path" >&2'
                      f' && root=$(pwd -P) && xargs -0 sh -c {shlex.quote(REMOVE_INSIDE_SH)} sh "$root" 3>&1 1>&2')
        command = ['ssh', *SSH_OPTIONS, self.host, remote]
        result = self.run(command, input=listing, capture_output=True)
        if result.stderr:
            print(os.fsdecode(result.stderr).rstrip())
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        sent = set(names)
        returned = [os.fsdecode(name) for name in (result.stdout or b'').split(b'\0') if name]
        return [name for name in returned if name in sent]

    def listening(self):
        result = self.run(self.ssh('ss', '-Hltn'), capture_output=True, text=True)
        return None if result.returncode else listeners(result.stdout)
