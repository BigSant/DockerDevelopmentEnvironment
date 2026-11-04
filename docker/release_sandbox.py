"""Release isolation (RELEASE_ISOLATION=container): setup's work on an untrusted release, in a throwaway container.

A stage deploy of an agent branch builds a release whose code, and whatever its Composer scripts and build.sh create, is
hostile: it may hold links to host files, hard links, odd names, and the target's application may rename and plant
while setup works. With RELEASE_ISOLATION=container (a project setting, or the job's environment), every operation of
make deploy on the release copy or the target's code tree runs in a container of the release-sandbox image
(docker/docker/release-sandbox) that mounts only those directories and setup's helpers (sandbox_ops.py, remove_inside.py)
read only, with --network none, a read-only root file system, no capabilities, no new privileges and an unprivileged
user: the release copy's owner for the release, the project's APP_UID:APP_GID for the target. A path there can reach
nothing else of this machine. Covered: exporting the code into the release, writing setup's files into it, the
release manifest, copying the release and its protected directories to the target, listing the target's module
directories, and removals. This machine's Python only reads trusted things (history, attempt files) and the manifest
the container prints, checked (plain relative names, size-capped).

Only for a target on this machine (no DEPLOY_SSH): the release and the target must be on the daemon's host. The
project's release build (Composer, config/deploy/build.sh) already runs in the release PHP image; with isolation its
network must not be the host's (RELEASE_BUILD_NETWORK: bridge by default, host refused). Without isolation make deploy
works on this machine as before.
"""

import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

DOCKER_DIRECTORY = Path(__file__).resolve().parent
DOCKERFILE = DOCKER_DIRECTORY / 'docker/release-sandbox/Dockerfile'
HELPERS = ('sandbox_ops.py', 'remove_inside.py')
IMAGE = 'setup-release-sandbox'
ISOLATION_VALUES = ('none', 'container')
MANIFEST_LIMIT = 64 * 1024 * 1024
MANIFEST_NAMES = 500000
CAPABILITY = 'release-hardened-v1'
# What every setup of this version offers (docker/project.mk SETUP_CAPABILITIES says the same).
BASE_CAPABILITIES = ('deploy-attempt-v1', 'deploy-health-v1', 'deploy-status-v1', 'release-build-network-v1')


# The job's request for a hardened deploy: a workflow that probed release-hardened-v1 passes it, and make deploy then
# isolates whatever the project's files say, or refuses (check) before anything changes.
REQUIRE_VARIABLE = 'DEPLOY_REQUIRE_ISOLATION'


def isolation(project):
    """Whether make deploy isolates its work on the release: RELEASE_ISOLATION=container in the project's settings or
    in the job's environment, or DEPLOY_REQUIRE_ISOLATION=1 from the job (both can only switch it on)."""
    value = str(project.settings.get('RELEASE_ISOLATION') or 'none')
    if value not in ISOLATION_VALUES:
        raise ValueError(f'RELEASE_ISOLATION must be none or container, not {value!r}')
    return value == 'container' or os.environ.get('RELEASE_ISOLATION') == 'container' or \
        os.environ.get(REQUIRE_VARIABLE) == '1'


def refusal(settings):
    """Why isolation cannot apply to these settings (None: it can)."""
    if settings.get('DEPLOY_SSH'):
        return ('RELEASE_ISOLATION=container needs a deploy target on this machine (no DEPLOY_SSH): the sandbox '
                'container mounts the release and the target code here. Untrusted deploys to another server stay refused')
    if not shutil.rmtree.avoids_symlink_attacks:
        return ('RELEASE_ISOLATION=container needs a Python whose shutil.rmtree is safe from link attacks (it prunes '
                'old release copies)')
    if str(settings.get('RELEASE_BUILD_NETWORK') or 'bridge') == 'host':
        return ('RELEASE_ISOLATION=container refuses RELEASE_BUILD_NETWORK=host: the untrusted release build would '
                'have this machine\'s network')
    return None


def check(project):
    """Refuses (ValueError) a project isolation cannot cover, before anything changes."""
    reason = refusal(project.settings)
    if reason:
        raise ValueError(reason)


def image_tag():
    return f'{IMAGE}:{hashlib.sha256(DOCKERFILE.read_bytes()).hexdigest()[:16]}'


def ensure_image(run=subprocess.run):
    """The sandbox image, built here once per Dockerfile version (its base pinned by digest)."""
    tag = image_tag()
    if run(['docker', 'image', 'inspect', tag], capture_output=True).returncode == 0:
        return tag
    print(f'==> building the release sandbox image {tag}', flush=True)
    result = run(['docker', 'build', '-q', '-t', tag, '-f', str(DOCKERFILE), str(DOCKERFILE.parent)],
                 capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'The release sandbox image {tag} could not be built: '
                         f'{(result.stderr or result.stdout).strip()[-400:]}')
    return tag


def docker_available(run=subprocess.run):
    try:
        return run(['docker', 'version', '--format', '{{.Server.Version}}'], capture_output=True).returncode == 0
    except OSError:
        return False


def pinned(root, relative, create=False):
    """(path, "dev:ino") of root/relative for a bind mount. root is setup's or setup-server's own (DEPLOY_PATH, the
    project directory) and is resolved; every part below it is opened from a held descriptor without following links
    (created when missing, with create), so a link there is refused. The container compares the inode it got mounted
    with this one (sandbox_ops: SETUP_EXPECT_*): a directory swapped for a link between this check and Docker's mount
    is refused there."""
    base = os.path.realpath(root)
    parts = [part for part in str(relative).split('/') if part not in ('', '.')]
    if '..' in parts:
        raise ValueError(f'{relative} leaves {root}')
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts:
            try:
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, 0o755, dir_fd=fd)
                following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(f'{os.path.join(base, *parts)}: {part} is a link or no directory; the release '
                                     'sandbox mounts only real directories') from None
                raise
            os.close(fd)
            fd = following
        status = os.fstat(fd)
        return os.path.join(base, *parts), f'{status.st_dev}:{status.st_ino}'
    finally:
        os.close(fd)


def mount(source, target, readonly):
    source = os.path.abspath(source)
    if ',' in source or '\n' in source:
        raise ValueError(f'Cannot mount {source} into the release sandbox: its path holds a comma or newline')
    return ['--mount', f'type=bind,source={source},target={target}' + (',readonly' if readonly else '')]


def plain(name):
    parts = name.split('/')
    return bool(name) and not name.startswith('/') and not any(part in ('', '.', '..') for part in parts) and \
        not any(ord(character) < 32 or ord(character) == 127 for character in name)


class Sandbox:
    """The release sandbox for one release copy and, for the target operations, one code tree on this machine. It
    answers what make deploy asks of a deploy target (push, directories, remove) for paths below those two."""
    local = True
    label = ''

    def __init__(self, project, release, target=None, run=subprocess.run):
        """release: the release copy below the project directory; target: (DEPLOY_PATH, the code tree below it)."""
        self.project, self.run = project, run
        self.image = ensure_image(run)
        directory = Path(project.directory)
        release, self.release_inode = pinned(directory, Path(os.path.abspath(release)).relative_to(
            os.path.abspath(directory)))
        self.release = Path(release)
        self.code = self.target_inode = None
        if target:
            # The code tree below DEPLOY_PATH (setup-server's), created when missing, never through a link.
            self.code, self.target_inode = pinned(target[0], target[1], create=True)
        settings = project.settings
        self.target_user = f"{settings.get('APP_UID') or os.getuid()}:{settings.get('APP_GID') or os.getgid()}"
        self.release_user = f'{os.getuid()}:{os.getgid()}'

    def command(self, operation, *arguments, target=False, release_writable=False):
        helpers = []
        for name in HELPERS:
            helpers += mount(DOCKER_DIRECTORY / name, f'/setup/{name}', True)
        mounts = mount(self.release, '/release', not release_writable)
        if target:
            mounts += mount(self.code, '/target', False)
        return ['docker', 'run', '--rm', '-i', '--network', 'none', '--read-only',
                '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--pids-limit', '256', '--memory', '1g',
                '--user', self.target_user if target else self.release_user, '-e', 'HOME=/tmp',
                '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', f'SETUP_EXPECT_RELEASE={self.release_inode}',
                *(['-e', f'SETUP_EXPECT_TARGET={self.target_inode}'] if target else []), *helpers, *mounts, '--entrypoint', 'python3', self.image,
                '/setup/sandbox_ops.py', operation, *arguments]

    def call(self, operation, *arguments, data=None, capture=False, **options):
        result = self.run(self.command(operation, *arguments, **options), input=data,
                          stdout=subprocess.PIPE if capture else None)
        if result.returncode:
            raise ValueError(f'The release sandbox could not {operation} (exit {result.returncode})')
        return result.stdout

    # The release copy (the release's owner).
    def extract(self, archive):
        """A tar archive (bytes or an open stream) into the release copy."""
        result = self.run(self.command('extract', release_writable=True), stdin=archive)
        if result.returncode:
            raise ValueError(f'The release sandbox could not extract the code (exit {result.returncode})')

    def write(self, name, data):
        self.call('write', name, data=data, release_writable=True)

    def manifest(self):
        """The release's file names, checked: a JSON list of plain relative names, size-capped. A name the host would
        not trust (absolute, .., control characters) is left out, so no removal ever follows it."""
        output = self.call('manifest', capture=True)
        if len(output) > MANIFEST_LIMIT:
            raise ValueError(f'The release manifest is larger than {MANIFEST_LIMIT} bytes: refused')
        names = json.loads(output.decode('utf-8', 'surrogateescape'))
        if not isinstance(names, list) or len(names) > MANIFEST_NAMES or not all(isinstance(name, str) for name in names):
            raise ValueError('The release sandbox gave no list of file names: refused')
        refused = [name for name in names if not plain(name)]
        if refused:
            print(f'Warning: {len(refused)} files of the release have names setup never removes later (absolute, .., '
                  f'control characters): {", ".join(repr(name) for name in refused[:5])}')
        return sorted(name for name in names if plain(name))

    # The target's code tree (the project's APP_UID:APP_GID).
    def inner(self, path, host, root):
        path, host = str(path).rstrip('/'), str(host).rstrip('/')
        if path != host and not path.startswith(host + '/'):
            raise ValueError(f'{path} is not below {host}: the release sandbox mounts nothing else')
        return root + path[len(host):]

    def push(self, source, dest, *options):
        """rsync's selection (options, --checksum, the excludes) into a staging directory of the target, then each
        file renamed into place: a published file is a new inode, and an existing file is never written, chmod-ed or
        touched (sandbox_ops.push), so a hard link the target holds to any other file never changes that file."""
        request = {'source': self.inner(source, self.release, '/release') + ('/' if str(source).endswith('/') else ''),
                   'dest': self.inner(dest, self.code, '/target') + ('/' if str(dest).endswith('/') else ''),
                   'options': [option for option in options if option != '--ignore-existing'],
                   'ignore_existing': '--ignore-existing' in options}
        self.call('push', data=json.dumps(request).encode(), target=True)

    def module_versions(self):
        """{module: version} of the release's PrestaShop modules, read in the sandbox (no link followed, regular files
        only, size-capped) and checked here."""
        versions = json.loads(self.call('modules', capture=True) or b'{}')
        if not isinstance(versions, dict):
            raise ValueError('The release sandbox gave no module list: refused')
        return {name: version for name, version in versions.items()
                if isinstance(name, str) and isinstance(version, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', name)
                and re.fullmatch(r'[A-Za-z0-9._+-]{1,32}', version)}

    def release_directories(self, path):
        """The directory names in the release's path (not following links), checked."""
        names = json.loads(self.call('release-directories', path, capture=True) or b'[]')
        return {name for name in names if isinstance(name, str) and plain(name) and '/' not in name}

    def directories(self, path):
        return set(json.loads(self.call('directories', self.inner(path, self.code, '')[1:] or '.', capture=True,
                                        target=True)))

    def remove(self, path, names, keep=(), protected=()):
        """As LocalTarget.remove, in the sandbox: the names still to remove (pending)."""
        from remove_inside import unframe
        if not names:
            return []
        if self.inner(path, self.code, '/target') != '/target':
            raise ValueError(f'Removals run in the code tree {self.code} only')
        request = {'remove': [os.fsencode(name).hex() for name in names],
                   'keep': [os.fsencode(name).hex() for name in keep],
                   'protected': [os.fsencode(pattern).hex() for pattern in protected]}
        reply = unframe(self.call('remove', data=json.dumps(request).encode(), capture=True, target=True) or b'')
        if reply is None:
            print(f'Warning: the release sandbox gave no removal reply: {len(names)} files stay pending.')
            return list(names)
        sent = {os.fsencode(name): name for name in names}
        return [sent[name] for name in reply[0] if name in sent]


def effective_settings(directory, environment):
    """The settings make deploy of ENV reads: env/common.env, then env/<env>.env when it exists (what make deploy
    loads; make pipeline-deploy writes it from the deployed commit's example), else the example, then setup-server's
    target file (target.<env>.env or target.env)."""
    from project_env_files import read_dotenv, target_settings
    directory = Path(directory)
    values = {}
    for path in (directory / 'env/common.env', directory / f'env/{environment}.env'
                 if (directory / f'env/{environment}.env').is_file() else directory / f'env/{environment}.env.example'):
        if path.is_file():
            values.update(read_dotenv(path))
    values.update(target_settings(directory, environment))
    return values


def capabilities(directory, environment):
    """setup-capabilities for one project environment: release-hardened-v1 when setup isolates this environment's
    deploy on request: its target (in the settings make deploy reads) is on this machine and Docker answers here.
    The workflow then asks with DEPLOY_REQUIRE_ISOLATION=1, and make deploy isolates or refuses: the probe and the
    deploy cannot disagree."""
    from project_environments import is_server
    if not is_server(environment):
        return False, f'ENV={environment} is no server environment'
    values = effective_settings(directory, environment)
    reason = refusal(values)
    if reason:
        return False, reason
    if not docker_available():
        return False, 'Docker does not answer on this machine'
    return True, None
