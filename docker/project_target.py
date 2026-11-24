"""The Deploy target: where a release's files go, this machine or a server reached over ssh (DEPLOY_SSH).

The containers' Docker daemon is a separate setting (DEPLOY_CONTEXT) and not handled here. Both targets
answer the same operations; paths are the target's own.

DEPLOY_MODE (setup-server's target file, like the rest of the target): docker (or empty, the default) runs the
release's containers on the target; files sends the code only, to a server without Docker (or a directory of this
machine): no Docker context, no container, no database setup manages there (files_only)."""

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
# The removal helper (remove_inside.py), run by SshTarget.remove on the server with python3 -c. Trusted content only:
# see its docstring for the threat model.
REMOVE_HELPER = (Path(__file__).resolve().parent / 'remove_inside.py').read_text()
PYTHON_CHECK = "import sys; sys.exit(0 if sys.version_info >= (3, 3) else 1)"
DEPLOY_MODES = ('docker', 'files')
# What every database step of a files target says (make db-backup, make deploy backup=1, setup-server's backups).
NO_DATABASE = 'files deploy target: no database managed here'


def deploy_mode(settings):
    """DEPLOY_MODE, checked: docker (also when empty) or files."""
    mode = str(settings.get('DEPLOY_MODE') or 'docker')
    if mode not in DEPLOY_MODES:
        raise ValueError(f'DEPLOY_MODE must be docker or files, not {mode!r} (setup-server writes it into target.env)')
    return mode


def files_only(settings):
    """Whether the target takes the code files only (DEPLOY_MODE=files): nothing there runs in a container."""
    return deploy_mode(settings) == 'files'


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
        """Removes code tree files the new release no longer ships with remove_inside (trusted content: a name is
        kept when, resolved, it lies outside path, is the new release's (keep) or protected). Returns the names still
        to remove (pending: the next deploy tries them again)."""
        from remove_inside import remove_inside
        pending, _ = remove_inside(path, [os.fsencode(name) for name in names], [os.fsencode(name) for name in keep],
                                   [os.fsencode(pattern) for pattern in protected], err=sys.stdout)
        return [os.fsdecode(name) for name in pending]

    def script(self, path, text):
        """Runs a shell script (sh -e, read from standard input) in the directory path; its failure raises
        CalledProcessError. DEPLOY_MODE=files: what a container would run otherwise (the hook, the cache clear)."""
        self.run(['sh', '-e', '-s'], cwd=path, input=text.encode(), check=True)

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
        """As LocalTarget.remove, on the server with its python3 3.3+ (asked once); without one nothing is removed and
        every name stays pending. Names, the new release's files and the protected patterns travel on standard input
        as the hex of their bytes; the reply is remove_inside's framed block on file descriptor 3 (the server's own
        output goes to standard error), and only names sent are taken from it."""
        from remove_inside import unframe
        if not names:
            return []
        if self.python is None:
            self.python = self.run(self.ssh('python3', '-c', PYTHON_CHECK), capture_output=True).returncode == 0
        if not self.python:
            print(f'Warning: {self.host} has no python3 3.3+: nothing was removed; {len(names)} files stay pending '
                  'until a deploy finds one there.')
            return list(names)
        request = {'remove': [os.fsencode(name).hex() for name in names],
                   'keep': [os.fsencode(name).hex() for name in keep],
                   'protected': [os.fsencode(pattern).hex() for pattern in protected]}
        command = ['ssh', *SSH_OPTIONS, self.host,
                   f'cd {shlex.quote(path)} && python3 -c {shlex.quote(REMOVE_HELPER)} . 3 3>&1 1>&2']
        result = self.run(command, input=json.dumps(request).encode(), capture_output=True)
        if result.stderr:
            print(os.fsdecode(result.stderr).rstrip())
        if result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        reply = unframe(result.stdout or b'')
        if reply is None:
            print(f'Warning: {self.host} gave no removal reply: {len(names)} files stay pending.')
            return list(names)
        sent = {os.fsencode(name): name for name in names}
        return [sent[name] for name in reply[0] if name in sent]

    def script(self, path, text):
        """As LocalTarget.script, on the server: ssh … 'cd PATH && sh -e -s', the script on standard input (so it is
        quoted for no shell and never shows on a command line)."""
        self.run(['ssh', *SSH_OPTIONS, self.host, f'cd {shlex.quote(path)} && sh -e -s'], input=text.encode(), check=True)

    def listening(self):
        result = self.run(self.ssh('ss', '-Hltn'), capture_output=True, text=True)
        return None if result.returncode else listeners(result.stdout)
