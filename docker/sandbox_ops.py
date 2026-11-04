"""Setup's operations on an untrusted release copy or a target's code tree, run INSIDE the release sandbox container.

    python3 /setup/sandbox_ops.py OPERATION [ARGUMENTS]    (docker/release_sandbox.py starts it; never run on a host)

The container mounts only /release (the release copy), /target (the target's code tree, when the operation needs it)
and /setup (this file and remove_inside.py, read only); it has no network, a read-only root, no capabilities and runs
as an unprivileged user. Whatever links, hard links, odd names or renames the release or the target hold, a path here
can only reach those two mounts: a link to a host path leads into this container's own read-only file system.

Operations:
  extract              a tar archive on standard input into /release (GNU tar)
  write NAME           standard input into /release/NAME: a new file created exclusively, renamed over the entry
                       (a planted link or hard link is replaced, never written through)
  manifest             the JSON list of /release's files and links (directories that are links included), walked
                       without following links; names starting with .setup- are setup's own and left out
  directories PATH     the JSON list of the directory names in /target/PATH (not following links)
  push                 standard input is JSON {"source": "/release/...", "dest": "/target/...", "options": [...],
                       "ignore_existing": bool}: rsync's selection into a staging directory of the target, then each
                       entry renamed into place (new inodes; an existing file is never written, chmod-ed or touched)
  modules              the JSON {module: version} of /release/modules/<m>/<m>.php (regular files only, size-capped)
  release-directories PATH   the JSON list of the directory names in /release/PATH (not following links)
  remove               remove_inside on /target: its JSON request on standard input, its framed reply on standard output
Python 3.5+ and the standard library only.
"""

import json
import os
import re
import shutil
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RELEASE, TARGET = '/release', '/target'
STAGING_PREFIX = '.setup-staging-'
MODULE_VERSION = re.compile(rb"\$this->version\s*=\s*['\"]([^'\"]+)['\"]")
MODULE_READ_LIMIT = 1024 * 1024


def check_mounts():
    """The directories mounted are the ones setup checked (their inode, SETUP_EXPECT_*): a directory swapped for a
    link between that check and Docker's mount is refused here, before anything is read or written."""
    for root, variable in ((RELEASE, 'SETUP_EXPECT_RELEASE'), (TARGET, 'SETUP_EXPECT_TARGET')):
        expected = os.environ.get(variable)
        if expected:
            status = os.stat(root)
            if '%d:%d' % (status.st_dev, status.st_ino) != expected:
                raise SystemExit('sandbox: %s is not the directory setup checked (%s): refused' % (root, expected))


def inside(path, root):
    """path, which must be root or below it once normalized (no .. out of it)."""
    normal = os.path.normpath(path)
    if normal != root and not normal.startswith(root + '/'):
        raise SystemExit('sandbox: %r is not below %s' % (path, root))
    return path


def extract():
    return subprocess.call(['tar', '-x', '--no-same-owner', '-C', RELEASE])


def write(name):
    if '/' in name or name in ('', '.', '..'):
        raise SystemExit('sandbox: not a file name of the release copy: %r' % (name,))
    data = sys.stdin.buffer.read()
    directory = os.open(RELEASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = '.setup-write-%s' % (os.urandom(8).hex(),)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        try:
            os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        except OSError:
            os.unlink(temporary, dir_fd=directory)
            raise SystemExit('sandbox: the release holds a directory or something setup cannot replace at %s' % name)
    finally:
        os.close(directory)
    return 0


def manifest():
    files = []
    for directory, folders, names in os.walk(RELEASE):
        for name in names + [folder for folder in folders if os.path.islink(os.path.join(directory, folder))]:
            if not name.startswith('.setup-') and not (directory == RELEASE and name.startswith(STAGING_PREFIX)):
                files.append(os.path.relpath(os.path.join(directory, name), RELEASE))
    files.sort()
    # Names may hold any byte but / and NUL: kept as the file system's bytes (surrogate escapes), checked by the host.
    sys.stdout.write(json.dumps(files))
    return 0


def directories(path):
    folder = inside(os.path.join(TARGET, path), TARGET)
    names = []
    if os.path.isdir(folder) and not os.path.islink(folder):
        names = sorted(entry.name for entry in os.scandir(folder) if entry.is_dir(follow_symlinks=False))
    sys.stdout.write(json.dumps(names))
    return 0


def push():
    """rsync stages what differs from the target (--compare-dest: a file identical there, content and attributes, is
    left alone) in a fresh directory of the target's file system; each staged entry is then renamed over its place.
    A published file is always a new inode, so a hard link the target holds to another file never changes that file,
    whatever the application does meanwhile; only directories (which have no hard links) get their mode and times."""
    request = json.loads(sys.stdin.read())
    source, dest = inside(request['source'], RELEASE), inside(request['dest'], TARGET).rstrip('/') or TARGET
    for leftover in os.listdir(TARGET):
        if leftover.startswith(STAGING_PREFIX):
            shutil.rmtree(os.path.join(TARGET, leftover))
    staging = os.path.join(TARGET, STAGING_PREFIX + os.urandom(6).hex())
    os.mkdir(staging, 0o700)
    try:
        compare = ['--compare-dest=' + dest + '/'] if os.path.isdir(dest) else []
        status = subprocess.call(['rsync'] + list(request['options']) + compare + [source, staging + '/'])
        if status:
            return status
        publish(staging, dest, request.get('ignore_existing'))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return 0


def publish(staging, dest, ignore_existing):
    if not os.path.lexists(dest):
        os.makedirs(dest)
    directories = []
    for directory, folders, names in os.walk(staging):
        relative = os.path.relpath(directory, staging)
        here = dest if relative == '.' else os.path.join(dest, relative)
        for name in folders:
            path, staged = os.path.join(here, name), os.path.join(directory, name)
            if os.path.islink(staged):
                names.append(name)  # a link to a directory is an entry like a file
                continue
            if os.path.lexists(path) and not (os.path.isdir(path) and not os.path.islink(path)):
                os.unlink(path)  # a file or link where the release has a directory, as rsync replaces it
            if not os.path.lexists(path):
                os.mkdir(path, 0o700)
            directories.append((staged, path))
        folders[:] = [name for name in folders if not os.path.islink(os.path.join(directory, name))]
        for name in names:
            path = os.path.join(here, name)
            if ignore_existing and os.path.lexists(path):
                continue
            os.rename(os.path.join(directory, name), path)
    for staged, path in reversed(directories + [(staging, dest)]):
        status = os.stat(staged)
        os.chmod(path, stat.S_IMODE(status.st_mode))
        os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns))


def modules():
    """PrestaShop's module versions: modules/<m>/<m>.php, regular files only (opened without following a link, a FIFO
    not waited for), at most 1 MiB each."""
    versions = {}
    root = os.path.join(RELEASE, 'modules')
    if not os.path.isdir(root) or os.path.islink(root):
        sys.stdout.write('{}')
        return 0
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not re.match(r'^[A-Za-z0-9_]{1,64}$', name) or os.path.islink(folder) or not os.path.isdir(folder):
            continue
        try:
            fd = os.open(os.path.join(folder, name + '.php'), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError:
            continue
        with os.fdopen(fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                continue
            match = MODULE_VERSION.search(handle.read(MODULE_READ_LIMIT))
        if match:
            versions[name] = match.group(1).decode('utf-8', 'replace')
    sys.stdout.write(json.dumps(versions))
    return 0


def release_directories(path):
    folder = inside(os.path.join(RELEASE, path), RELEASE)
    names = []
    if os.path.isdir(folder) and not os.path.islink(folder):
        names = sorted(entry.name for entry in os.scandir(folder) if entry.is_dir(follow_symlinks=False))
    sys.stdout.write(json.dumps(names))
    return 0


def remove():
    import remove_inside
    return remove_inside.main([TARGET])


def main(arguments):
    operation, rest = arguments[0], arguments[1:]
    check_mounts()
    operations = {'extract': extract, 'write': write, 'manifest': manifest, 'directories': directories,
                  'push': push, 'remove': remove, 'modules': modules, 'release-directories': release_directories}
    if operation not in operations:
        raise SystemExit('sandbox: unknown operation %r' % (operation,))
    return operations[operation](*rest)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
