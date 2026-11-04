"""Remove code tree files a new release no longer ships, inside the deploy target's code root only.

    python3 remove_inside.py ROOT [FD] < {"remove": [hex], "keep": [hex], "protected": [hex]}
    (SshTarget runs this source on the server with python3 -c)

Threat model: the release and the target hold TRUSTED content. Untrusted (agent) stage deploys are refused while setup
does not offer release-hardened-v1; protection against a hostile tree that races or plants links during the removal
is future work (setup's copy and removal in an isolated container), not this helper's.

Everything is bytes, so the server's locale never matters: names, the new release's files ("keep") and the protected
patterns ("protected", rsync-style, anchored at the code root as DEPLOY_PROTECTED_PATHS) travel as the hex of their
bytes. Each name must be a plain relative code tree name (no ., .. or empty part, no control character). Its directory
is resolved (os.path.realpath; the last part stays as it is, so a stale link is removed itself, as rm did), and the name
is kept on purpose when that entry lies outside the resolved root, is a file the new release ships or a directory one
lies in (each resolved the same way), or lies under a protected path. Otherwise a file or link is unlinked; a missing
one counts as done; anything else (a directory there, any OSError) stays pending for the next deploy.

The reply is one framed block on FD (default 1): a start line, the length of the JSON that follows, the JSON
({"pending": [hex], "kept": [hex]}) and an end line, so output around it (a login banner) cannot be mistaken for it.
Python 3.5 and the standard library only: hosting servers may have nothing newer.
"""

import binascii
from fnmatch import fnmatch
import json
import os
import stat
import sys

FRAME_START, FRAME_END = b'--setup-remove-inside-begin--', b'--setup-remove-inside-end--'


def plain(name):
    """A relative code tree name: no ., .. or empty part, no control character."""
    parts = name.split(b'/')
    return bool(name) and not name.startswith(b'/') and not any(part in (b'', b'.', b'..') for part in parts) and \
        not any(byte < 32 or byte == 127 for byte in bytearray(name))


def entry(root, name):
    """The resolved path of the entry name: its directory through os.path.realpath, its last part as it is."""
    directory, last = os.path.split(os.path.join(root, name))
    return os.path.join(os.path.realpath(directory), last)


def shipped_paths(root, keep):
    """The resolved entries the new release ships and every directory they lie in, below the root."""
    paths = set()
    for name in keep:
        for path in (entry(root, name), os.path.realpath(os.path.join(root, name))):
            while path.startswith(root + b'/'):
                paths.add(path)
                path = os.path.dirname(path)
    return paths


def protected(patterns, relative):
    """As project_deploy.is_protected, on bytes: a code tree path that is a protected path or lies inside one."""
    path = b'/' + relative
    return any(fnmatch(path, pattern.rstrip(b'/')) or fnmatch(path, pattern + b'*') for pattern in patterns)


def remove_inside(root, names, keep=(), patterns=(), err=None):
    """Removes names (bytes) under root (bytes or str). Returns (pending, kept): the names not removed yet, which the
    next deploy tries again, and the names kept on purpose."""
    err = err or sys.stderr
    root = os.path.realpath(os.fsencode(root))
    ships = shipped_paths(root, keep)
    pending, kept = [], []
    for name in names:
        if not name:
            continue
        if not plain(name):
            print('setup: not removing %r: not a plain code tree name' % (name,), file=err)
            continue
        path = entry(root, name)
        relative = path[len(root) + 1:]
        if not path.startswith(root + b'/') or path in ships or protected(patterns, relative):
            print('setup: keeping %r: outside the code root, the new release\'s or protected' % (name,), file=err)
            kept.append(name)
            continue
        try:
            if stat.S_ISDIR(os.lstat(path).st_mode):
                print('setup: not removing %r: a directory there; it stays pending' % (name,), file=err)
                pending.append(name)
                continue
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            print('setup: cannot remove %r: %s; it stays pending' % (name, error.strerror), file=err)
            pending.append(name)
    return pending, kept


def frame(pending, kept):
    """The reply block: start line, JSON length, JSON, end line."""
    body = json.dumps({'pending': [binascii.hexlify(name).decode('ascii') for name in pending],
                       'kept': [binascii.hexlify(name).decode('ascii') for name in kept]}).encode('ascii')
    return FRAME_START + b'\n' + str(len(body)).encode('ascii') + b'\n' + body + b'\n' + FRAME_END + b'\n'


def unframe(output):
    """The reply in output, whatever surrounds it: (pending, kept) as bytes names, or None without a whole block."""
    start = output.find(FRAME_START + b'\n')
    if start < 0:
        return None
    rest = output[start + len(FRAME_START) + 1:]
    length, _, rest = rest.partition(b'\n')
    if not length.isdigit() or rest[int(length):int(length) + len(FRAME_END) + 2] != b'\n' + FRAME_END + b'\n':
        return None
    reply = json.loads(rest[:int(length)].decode('ascii'))
    return ([binascii.unhexlify(name) for name in reply['pending']], [binascii.unhexlify(name) for name in reply['kept']])


def main(arguments):
    request = json.loads(sys.stdin.buffer.read().decode('ascii'))
    names = [binascii.unhexlify(name) for name in request['remove']]
    keep = [binascii.unhexlify(name) for name in request.get('keep', [])]
    patterns = [binascii.unhexlify(pattern) for pattern in request.get('protected', [])]
    pending, kept = remove_inside(arguments[0], names, keep, patterns)
    output = int(arguments[1]) if len(arguments) > 1 else 1
    data = frame(pending, kept)
    while data:
        data = data[os.write(output, data):]
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
