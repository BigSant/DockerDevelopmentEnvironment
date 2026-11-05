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

RELEASE, TARGET, DEPLOY = '/release', '/target', '/deploy'
STAGING_PREFIX = '.setup-staging-'
MODULE_VERSION = re.compile(rb"\$this->version\s*=\s*['\"]([^'\"]+)['\"]")
MODULE_READ_LIMIT = 1024 * 1024


def check_mounts():
    """The directories mounted are the ones setup checked (their inode, SETUP_EXPECT_*): a directory swapped for a
    link between that check and Docker's mount is refused here, before anything is read or written."""
    for root, variable in ((RELEASE, 'SETUP_EXPECT_RELEASE'), (TARGET, 'SETUP_EXPECT_TARGET'),
                           (DEPLOY, 'SETUP_EXPECT_DEPLOY')):
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
            if not name.startswith('.setup-'):
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
    """Publishes the release (or one directory of it) into the code tree, which the application can change at any
    time, without ever giving it setup's files in progress or a path to follow:
      * /deploy is the code tree's parent (setup-server's, mounted into no container of the project); the code tree is
        /deploy/SETUP_CODE_NAME, opened without following a link and checked against its inode;
      * rsync stages what differs from the code tree (--compare-dest: identical files, content and attributes, are left
        alone) in a fresh directory of /deploy, with the release's modes and times already on the staged files;
      * each staged entry is renamed into the code tree from held directory descriptors, every directory there opened
        without following a link (a link or file where the release has a directory is replaced, as rsync does);
        --ignore-existing publishes by hard link, which never replaces what the code tree has. Only directories get
        their mode and times, through their descriptors; no file there is written, chmod-ed or touched in place."""
    request = json.loads(sys.stdin.read())
    source = inside(request['source'], RELEASE)
    deploy = os.open(DEPLOY, os.O_RDONLY | os.O_DIRECTORY)
    try:
        code = os.open(os.environ['SETUP_CODE_NAME'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=deploy)
    except OSError:
        raise SystemExit('sandbox: the code tree is a link or no directory: refused') from None
    status = os.fstat(code)
    if '%d:%d' % (status.st_dev, status.st_ino) != os.environ['SETUP_EXPECT_CODE']:
        raise SystemExit('sandbox: the code tree is not the directory setup checked: refused')
    dest = destination(code, request['dest'])
    for leftover in os.listdir(deploy):
        if leftover.startswith(STAGING_PREFIX):
            shutil.rmtree(leftover, dir_fd=deploy)
    staging = STAGING_PREFIX + os.urandom(6).hex()
    os.mkdir(staging, 0o700, dir_fd=deploy)
    try:
        # rsync reads the code tree for its comparison only, through the held descriptor (inherited).
        result = subprocess.call(['rsync'] + list(request['options']) +
                                 ['--compare-dest=/proc/self/fd/%d/' % dest, source, os.path.join(DEPLOY, staging) + '/'],
                                 pass_fds=(dest,))
        if result:
            return result
        staged = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=deploy)
        try:
            publish(staged, dest, request.get('ignore_existing'))
        finally:
            os.close(staged)
    finally:
        shutil.rmtree(staging, dir_fd=deploy, ignore_errors=True)
        os.close(dest)
        os.close(code)
        os.close(deploy)
    return 0


def open_directory(name, parent):
    """name in parent as a directory descriptor, never through a link; O_PATH where it cannot be read (0300)."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    except PermissionError:
        return os.open(name, getattr(os, 'O_PATH', os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)


def destination(code, relative, hops=40):
    """The descriptor of relative below the code tree (created when missing). A link on the way is followed only while
    its relative target stays inside the code tree (cache -> var/cache), as rsync follows its destination path."""
    stack = [os.dup(code)]
    pending = [part for part in relative.split('/') if part]
    try:
        while pending:
            part = pending.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                if len(stack) == 1:
                    raise SystemExit('sandbox: the destination leaves the code tree')
                os.close(stack.pop())
                continue
            try:
                stack.append(open_directory(part, stack[-1]))
                continue
            except FileNotFoundError:
                os.mkdir(part, 0o755, dir_fd=stack[-1])
                stack.append(open_directory(part, stack[-1]))
                continue
            except OSError:
                pass
            if not stat.S_ISLNK(os.stat(part, dir_fd=stack[-1], follow_symlinks=False).st_mode):
                raise SystemExit('sandbox: %s in the code tree is no directory' % part)
            target = os.readlink(part, dir_fd=stack[-1])
            hops -= 1
            if hops < 0 or target.startswith('/'):
                raise SystemExit('sandbox: %s leads out of the code tree' % part)
            pending = target.split('/') + pending
        return stack.pop()
    finally:
        for fd in stack:
            os.close(fd)


def publish(staged, dest, ignore_existing):
    for entry in sorted(os.scandir(staged), key=lambda entry: entry.name):
        name = entry.name
        if entry.is_dir(follow_symlinks=False):
            try:
                child = open_directory(name, dest)
            except FileNotFoundError:
                os.mkdir(name, 0o700, dir_fd=dest)
                child = open_directory(name, dest)
            except OSError:
                os.unlink(name, dir_fd=dest)  # a link or file where the release has a directory, as rsync replaces it
                os.mkdir(name, 0o700, dir_fd=dest)
                child = open_directory(name, dest)
            source = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=staged)
            try:
                publish(source, child, ignore_existing)
            finally:
                os.close(source)
                os.close(child)
        elif ignore_existing:
            try:
                os.link(name, name, src_dir_fd=staged, dst_dir_fd=dest, follow_symlinks=False)
            except FileExistsError:
                pass  # the code tree has its own: it stays
        else:
            os.rename(name, name, src_dir_fd=staged, dst_dir_fd=dest)
    attributes(staged, dest)


def attributes(source, dest):
    """A directory's mode and times, through its descriptor (a directory has no hard links)."""
    status = os.fstat(source)
    try:
        os.chmod(dest, stat.S_IMODE(status.st_mode))
        os.utime(dest, ns=(status.st_atime_ns, status.st_mtime_ns))
    except OSError:
        pass  # an O_PATH descriptor (no read permission): left as it is


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
