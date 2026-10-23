"""make ssh: a terminal or a command on a stage/prod server, through the setup-server worker that deploys it.

The developer never reaches the server itself: the key's forced command on the worker (setup-server's
bin/add-backup-key --grant PROJECT:ENV=LEVEL, the same key and BACKUP_SSH as make db-pull) goes on with the
worker's own access, at the level granted for that project environment:
  phpfpm-readonly   a terminal or command in the php-fpm container as another account (reads, does not change files)
  phpfpm            the same as the application account, or root=1 as root in the container
  docker            the project's containers: ps, logs, exec, start, stop, restart, top, stats, inspect
  full              the server itself as the deploy account, in the project directory (DEPLOY_PATH)
A level includes the ones before it. The requests are the gate's: `access`, `shell PROJECT ENV [root|readonly]`,
`exec PROJECT ENV [root|readonly] -- CMD…`, `host PROJECT ENV [-- CMD…]` and `docker PROJECT ENV VERB …`; cmd=
and docker= are split into words as the shell does (no shell runs them: cmd="sh -c '…'" for pipes).

Which project: as make db-pull (BACKUP_SERVER_PROJECT, else the git repository's name, else PROJECT_NAME); the
server's `access` answer picks the first of them this key may reach.
"""

import shlex
import subprocess
import sys

from database_pull import connection, server_projects, ssh_command
from project_environments import server_environments

# Refused requests exit 65 (EX_DATAERR) with the gate's reason on stderr, as setup-server-backup-pull does.
REFUSED = 65


def request(name, source, root=False, readonly=False, host=False, command='', docker=''):
    """The gate's request words for make ssh's options; refuses combinations the gate has no answer for."""
    if root and readonly:
        raise ValueError('root=1 and readonly=1 exclude each other')
    if docker:
        if root or readonly or host or command:
            raise ValueError('docker= is a request of its own: leave out root=, readonly=, host= and cmd=')
        words = shlex.split(docker)
        return ['docker', name, source, *words]
    words = shlex.split(command) if command else []
    if command and not words:
        raise ValueError('cmd= is empty')
    if host:
        if root or readonly:
            raise ValueError('host=1 is the server itself: root= and readonly= are for the php-fpm container')
        return ['host', name, source, *(['--', *words] if words else [])]
    mode = ['root'] if root else ['readonly'] if readonly else []
    return ['exec', name, source, *mode, '--', *words] if words else ['shell', name, source, *mode]


def terminal():
    """A terminal on both ends: ssh then asks for one (-t), so shells, prompts and colours work; never for a pipe."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def old_server(result):
    """setup-server before the access gate: its backup command answers every other request with its usage line."""
    return result.returncode == REFUSED and b'expected: latest' in (result.stderr or b'')


def reachable(ssh, run=subprocess.run):
    """[(project, env, level)] the server's `access` answer lists for this key."""
    result = run(ssh + ['access'], capture_output=True, timeout=60)
    if old_server(result):
        raise ValueError('The server has no access gate yet: update setup-server (bin/update) and grant your key a '
                         'level there (sudo bin/add-backup-key … --grant PROJECT:ENV=LEVEL)')
    if result.returncode:
        message = (result.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
        message = message[-1] if message else 'no answer'
        if result.returncode == 255:  # ssh itself failed: no route, no key, host key
            raise ValueError(f'Cannot reach the server ({message}). Check the VPN, BACKUP_SSH, and that your public '
                             'key was added there (setup-server: sudo bin/add-backup-key).')
        raise ValueError(f'The server refused: {message}')
    return [tuple(line.split()) for line in result.stdout.decode('utf-8', 'replace').splitlines()
            if len(line.split()) == 3]


def server_project(project, source, grants):
    """The first of make db-pull's names the key may reach in ENV `source` (else in any ENV, for the gate's reason)."""
    names = server_projects(project)
    for wanted in (lambda grant: grant[1] == source, lambda grant: True):
        for name in names:
            if any(grant[0] == name and wanted(grant) for grant in grants):
                return name
    raise ValueError(f'Your key may reach no project named {" or ".join(names) or "(no name)"} on the server; set '
                     'BACKUP_SERVER_PROJECT to its directory name there (projects/<name>) or ask for a grant '
                     '(setup-server: sudo bin/add-backup-key … --grant PROJECT:ENV=LEVEL)')


def connect(project, source='prod', root=False, readonly=False, host=False, command='', docker='', access=False,
            run=subprocess.run, ask=input, interactive=None, tty=None):
    """Run the request through the worker with this terminal; the exit status is the remote command's (65 refused)."""
    if source not in server_environments(project.directory):
        raise ValueError('from= must be stage, prod or an environment STAGE_ENVIRONMENTS lists')
    if not access:
        request('', source, root, readonly, host, command, docker)  # a wrong combination stops before connecting
    ssh = ssh_command(*connection(project, ask=ask, interactive=interactive))
    grants = reachable(ssh, run)
    if access:
        for grant in grants:
            print(' '.join(grant))
        if not grants:
            print('Your key may reach no server yet (setup-server: sudo bin/add-backup-key … --grant PROJECT:ENV=LEVEL).')
        return 0
    words = request(server_project(project, source, grants), source, root, readonly, host, command, docker)
    if terminal() if tty is None else tty:
        ssh = ssh[:-1] + ['-t'] + ssh[-1:]
    return run(ssh + [shlex.join(words)]).returncode
