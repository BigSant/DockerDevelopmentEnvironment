"""Remove code tree files of a deploy target without ever leaving its root, even while the application there changes it.

    python3 remove_inside.py ROOT < NUL-separated names    (SshTarget runs this source on the server with python3 -c)

Each name is walked from a descriptor of ROOT one directory at a time, every directory opened with O_NOFOLLOW relative
to the one before (O_PATH: a directory without read permission, such as 0300, is walked as a plain unlink always did),
and the file is unlinked relative to the last. A link on the way is followed only while it stays inside ROOT (a relative
target, resolved by the same walk): no path is ever resolved twice, and a directory renamed or swapped for a link
meanwhile changes nothing, because the descriptors held are the real directories.

A name that is not a plain relative code tree name (absolute, ., .., empty part, control character) is refused. A name
whose way leads out of ROOT or meets a file where a directory belongs is left: not removed, so not done; the script
prints it to standard output (NUL-separated) for the caller to keep pending. Exit status 1 when a removal failed;
every name is tried. Python 3.5 and the standard library only: hosting servers may have nothing newer.
"""

import errno
import os
import stat
import sys

# O_PATH (Linux): a descriptor to walk and unlink in, which needs no read permission on the directory.
DIRECTORY_FLAGS = (getattr(os, 'O_PATH', 0) or os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
LINK_HOPS = 40


class LeavesRoot(Exception):
    """The way leads out of the root (an absolute link, .. above it, too many links) or meets a non-directory."""


def plain(name):
    """A relative code tree name: no ., .. or empty part, no control character."""
    parts = name.split(b'/')
    return bool(name) and not name.startswith(b'/') and not any(part in (b'', b'.', b'..') for part in parts) and \
        not any(byte < 32 or byte == 127 for byte in bytearray(name))


def open_root(root):
    return os.open(root, DIRECTORY_FLAGS & ~os.O_NOFOLLOW)


def walk(top, parts):
    """A new descriptor of the directory `parts` names below the root descriptor top. A link is followed only while
    its relative target stays below top; FileNotFoundError when a part is missing, LeavesRoot otherwise."""
    stack = [os.dup(top)]
    pending = [part for part in parts]
    hops = LINK_HOPS
    try:
        while pending:
            part = pending.pop(0)
            if part in (b'', b'.'):
                continue
            if part == b'..':
                if len(stack) == 1:
                    raise LeavesRoot('.. above the root')
                os.close(stack.pop())
                continue
            try:
                stack.append(os.open(part, DIRECTORY_FLAGS, dir_fd=stack[-1]))
                continue
            except OSError as error:
                if error.errno == errno.ENOENT:
                    raise
                if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
            # A link (O_PATH with O_NOFOLLOW and O_DIRECTORY gives ENOTDIR for one) or a file.
            if not stat.S_ISLNK(os.stat(part, dir_fd=stack[-1], follow_symlinks=False).st_mode):
                raise LeavesRoot('not a directory')
            hops -= 1
            target = os.readlink(part, dir_fd=stack[-1])
            if hops < 0 or target.startswith(b'/'):
                raise LeavesRoot('a link out of the root')
            pending = target.split(b'/') + pending
        result = stack.pop()
        return result
    finally:
        for fd in stack:
            os.close(fd)


def remove_inside(root, names, err=None):
    """Removes names (bytes) under root. Returns (failed removals, names left because their way leaves the root)."""
    err = err or sys.stderr
    failures, left = 0, []
    top = open_root(root)
    try:
        for name in names:
            if not name:
                continue
            if not plain(name):
                print('setup: not removing %r: not a plain code tree name' % (name,), file=err)
                continue
            parts = name.split(b'/')
            try:
                directory = walk(top, parts[:-1])
            except FileNotFoundError:
                continue  # its directory is gone: so is the file
            except LeavesRoot as reason:
                print('setup: not removing %r: its way leaves the code tree (%s); it stays pending' % (name, reason),
                      file=err)
                left.append(name)
                continue
            except OSError as error:
                print('setup: cannot remove %r: %s' % (name, error.strerror), file=err)
                failures += 1
                continue
            try:
                os.unlink(parts[-1], dir_fd=directory)
            except FileNotFoundError:
                pass
            except OSError as error:
                if error.errno in (errno.EISDIR, errno.EPERM) and \
                        stat.S_ISDIR(os.stat(parts[-1], dir_fd=directory, follow_symlinks=False).st_mode):
                    print('setup: not removing %r: a directory now; it stays pending' % (name,), file=err)
                    left.append(name)
                else:
                    print('setup: cannot remove %r: %s' % (name, error.strerror), file=err)
                    failures += 1
            finally:
                os.close(directory)
    finally:
        os.close(top)
    return failures, left


if __name__ == '__main__':
    failed, kept = remove_inside(sys.argv[1], sys.stdin.buffer.read().split(b'\0'))
    sys.stdout.buffer.write(b''.join(name + b'\0' for name in kept))
    sys.exit(1 if failed else 0)
