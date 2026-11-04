"""Remove code tree files of a deploy target without ever leaving its root, even while the application there changes it.

    python3 remove_inside.py ROOT [FD] < {"remove": [hex], "keep": [hex], "protected": [pattern]}
    (SshTarget runs this source on the server with python3 -c; names travel as the hex of their bytes, so the
    server's locale never matters; the names left pending are written to FD, NUL-separated, never mixed with output)

Each name is walked from ROOT one directory at a time, every directory opened O_PATH|O_NOFOLLOW relative to the one
before (no read permission needed: a 0300 directory is walked as a plain unlink always did; where O_PATH is missing, a
directory that cannot be opened is entered by path after lstat shows it is a real directory), and the file is unlinked
relative to the last. A link on the way is followed only while its relative target stays inside ROOT. No path is
resolved twice by descriptor, and a directory renamed or swapped for a link meanwhile changes nothing.

A name is removed only if its fully resolved entry is not what the new release ships ("keep": its files, and every
directory they lie in, each resolved the same way) and not under a protected path ("protected": rsync-style patterns
anchored at the code root, as DEPLOY_PROTECTED_PATHS); such a name is kept on purpose. A name that is not a plain
relative code tree name is refused. A name whose way leads out of ROOT, or meets a file where a directory belongs, is
left: not removed, so not done, and reported pending. Exit status 1 when a removal failed; every name is tried.
Python 3.5 and the standard library only: hosting servers may have nothing newer.
"""

import errno
from fnmatch import fnmatch
import json
import os
import stat
import sys

# O_PATH (Linux): a descriptor to walk and unlink in, which needs no read permission on the directory.
PATH_FLAG = getattr(os, 'O_PATH', 0)
LINK_HOPS = 40


class LeavesRoot(Exception):
    """The way leads out of the root (an absolute link, .. above it, too many links) or meets a non-directory."""


def plain(name):
    """A relative code tree name: no ., .. or empty part, no control character."""
    parts = name.split(b'/')
    return bool(name) and not name.startswith(b'/') and not any(part in (b'', b'.', b'..') for part in parts) and \
        not any(byte < 32 or byte == 127 for byte in bytearray(name))


def protected(patterns, name):
    """As project_deploy.is_protected: a code tree path (relative) that is a protected path or lies inside one."""
    path = '/' + os.fsdecode(name)
    return any(fnmatch(path, pattern.rstrip('/')) or fnmatch(path, pattern + '*') for pattern in patterns)


def flags():
    return (PATH_FLAG or os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Directory(object):
    """A directory of the walk: a descriptor, or (no O_PATH, no read permission) its path, checked by lstat."""

    def __init__(self, fd, path):
        self.fd, self.path = fd, path

    def close(self):
        if self.fd is not None:
            os.close(self.fd)

    def copy(self):
        return Directory(None if self.fd is None else os.dup(self.fd), self.path)

    def child(self, part):
        path = os.path.join(self.path, part)
        if self.fd is not None:
            try:
                return Directory(os.open(part, flags(), dir_fd=self.fd), path)
            except OSError as error:
                if error.errno != errno.EACCES or PATH_FLAG:
                    raise
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            raise OSError(errno.ELOOP, 'a link', path)
        if not stat.S_ISDIR(mode):
            raise OSError(errno.ENOTDIR, 'not a directory', path)
        try:
            return Directory(os.open(path, flags()), path)
        except OSError as error:
            if error.errno != errno.EACCES:
                raise
            return Directory(None, path)

    def lstat(self, name):
        if self.fd is None:
            return os.lstat(os.path.join(self.path, name))
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def readlink(self, name):
        if self.fd is None:
            return os.readlink(os.path.join(self.path, name))
        return os.readlink(name, dir_fd=self.fd)

    def unlink(self, name):
        if self.fd is None:
            os.unlink(os.path.join(self.path, name))
        else:
            os.unlink(name, dir_fd=self.fd)


def open_root(root):
    path = os.path.abspath(os.fsencode(root))
    try:
        return Directory(os.open(path, flags() & ~os.O_NOFOLLOW), path)
    except OSError as error:
        if error.errno != errno.EACCES or PATH_FLAG:
            raise
        return Directory(None, path)


def walk(top, parts):
    """(the directory `parts` names below top, its resolved parts from the root). A link is followed only while its
    relative target stays below top; FileNotFoundError when a part is missing, LeavesRoot otherwise."""
    stack, resolved = [top.copy()], []
    pending = list(parts)
    hops = LINK_HOPS
    try:
        while pending:
            part = pending.pop(0)
            if part in (b'', b'.'):
                continue
            if part == b'..':
                if not resolved:
                    raise LeavesRoot('.. above the root')
                stack.pop().close()
                resolved.pop()
                continue
            try:
                stack.append(stack[-1].child(part))
                resolved.append(part)
                continue
            except OSError as error:
                if error.errno == errno.ENOENT:
                    raise
                if error.errno not in (errno.ELOOP, errno.ENOTDIR):
                    raise
            # A link (O_PATH with O_NOFOLLOW and O_DIRECTORY gives ENOTDIR for one) or a file.
            if not stat.S_ISLNK(stack[-1].lstat(part).st_mode):
                raise LeavesRoot('not a directory')
            hops -= 1
            target = stack[-1].readlink(part)
            if hops < 0 or target.startswith(b'/'):
                raise LeavesRoot('a link out of the root')
            pending = target.split(b'/') + pending
        return stack.pop(), resolved
    finally:
        for directory in stack:
            directory.close()


def resolved_entry(top, name, directories):
    """The resolved path of the entry name, or None when its way is missing or leaves the root; directories caches
    the resolved parts of each directory."""
    parts = name.split(b'/')
    parent = b'/'.join(parts[:-1])
    if parent not in directories:
        try:
            directory, resolved = walk(top, parts[:-1])
            directory.close()
            directories[parent] = resolved
        except (OSError, LeavesRoot):
            directories[parent] = None
    resolved = directories[parent]
    return None if resolved is None else b'/'.join(resolved + [parts[-1]])


def shipped(top, keep):
    """Every entry the new release ships and every directory they lie in, literally and resolved."""
    entries, directories = set(), {}
    for name in keep:
        for candidate in (name, resolved_entry(top, name, directories)):
            if candidate:
                parts = candidate.split(b'/')
                entries.update(b'/'.join(parts[:index]) for index in range(1, len(parts) + 1))
    return entries


def remove_inside(root, names, keep=(), patterns=(), err=None):
    """Removes names (bytes) under root. Returns (failed removals, names left pending because their way leaves the
    root, names kept on purpose: what the new release ships or protects, once resolved)."""
    err = err or sys.stderr
    failures, left, kept = 0, [], []
    top = open_root(root)
    try:
        ships = shipped(top, keep) if keep else set()
        for name in names:
            if not name:
                continue
            if not plain(name):
                print('setup: not removing %r: not a plain code tree name' % (name,), file=err)
                continue
            parts = name.split(b'/')
            try:
                directory, resolved = walk(top, parts[:-1])
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
                entry = b'/'.join(resolved + [parts[-1]])
                if entry in ships or protected(patterns, entry):
                    print('setup: keeping %r: as %r it is the new release\'s or protected' % (name, entry), file=err)
                    kept.append(name)
                    continue
                directory.unlink(parts[-1])
            except FileNotFoundError:
                pass
            except OSError as error:
                if error.errno in (errno.EISDIR, errno.EPERM) and stat.S_ISDIR(directory.lstat(parts[-1]).st_mode):
                    print('setup: not removing %r: a directory now; it stays pending' % (name,), file=err)
                    left.append(name)
                else:
                    print('setup: cannot remove %r: %s' % (name, error.strerror), file=err)
                    failures += 1
            finally:
                directory.close()
    finally:
        top.close()
    return failures, left, kept


def main(arguments):
    request = json.loads(sys.stdin.buffer.read().decode('ascii'))
    failed, left, _ = remove_inside(arguments[0], [bytes.fromhex(name) for name in request['remove']],
                                    [bytes.fromhex(name) for name in request.get('keep', [])],
                                    request.get('protected', []))
    output = int(arguments[1]) if len(arguments) > 1 else 1
    data = b''.join(name + b'\0' for name in left)
    while data:
        data = data[os.write(output, data):]
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
