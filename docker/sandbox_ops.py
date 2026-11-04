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
  push                 rsync: standard input is JSON {"source": "/release/...", "dest": "/target/...", "options": [...]}
  remove               remove_inside on /target: its JSON request on standard input, its framed reply on standard output
Python 3.5+ and the standard library only.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RELEASE, TARGET = '/release', '/target'


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
    request = json.loads(sys.stdin.read())
    source, dest = inside(request['source'], RELEASE), inside(request['dest'], TARGET)
    return subprocess.call(['rsync'] + list(request['options']) + [source, dest])


def remove():
    import remove_inside
    return remove_inside.main([TARGET])


def main(arguments):
    operation, rest = arguments[0], arguments[1:]
    operations = {'extract': extract, 'write': write, 'manifest': manifest, 'directories': directories,
                  'push': push, 'remove': remove}
    if operation not in operations:
        raise SystemExit('sandbox: unknown operation %r' % (operation,))
    return operations[operation](*rest)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
