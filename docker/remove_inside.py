"""Remove code tree files of a deploy target without ever leaving its root, even while the application there changes it.

    python3 remove_inside.py ROOT < NUL-separated names    (SshTarget runs this source on the server with python3 -c)

Each name is walked from a descriptor of ROOT one directory at a time, every directory opened with O_NOFOLLOW relative
to the one before, and the file is unlinked relative to the last: a name never resolves through a link, and a directory
renamed or swapped for a link meanwhile changes nothing, because the descriptors held are the real directories. A name
that is not a plain relative code tree name (absolute, ., .., empty part, control character) or that meets a link or a
file where a directory should be is left and named. Exit status 1 when a removal failed; every name is tried.
Only the standard library: a server needs python3 and nothing else.
"""

import errno
import os
import sys


def plain(name):
    """A relative code tree name: no ., .. or empty part, no control character."""
    parts = name.split(b'/')
    return bool(name) and not name.startswith(b'/') and not any(part in (b'', b'.', b'..') for part in parts) and \
        not any(byte < 32 or byte == 127 for byte in name)


def remove_inside(root, names, err=None):
    """Removes names (bytes) under root; returns the number of failed removals (left names are no failure)."""
    err = err or sys.stderr
    failures = 0
    top = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in names:
            if not name:
                continue
            if not plain(name):
                print(f'setup: not removing {name!r}: not a plain code tree name', file=err)
                continue
            parts = name.split(b'/')
            current = os.dup(top)
            try:
                for part in parts[:-1]:
                    try:
                        following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                            dir_fd=current)
                    except FileNotFoundError:
                        break
                    except OSError as error:
                        if error.errno in (errno.ELOOP, errno.ENOTDIR):
                            print(f'setup: not removing {name!r}: {part!r} is no directory of the code tree', file=err)
                        else:
                            print(f'setup: cannot remove {name!r}: {error.strerror}', file=err)
                            failures += 1
                        break
                    os.close(current)
                    current = following
                else:
                    try:
                        os.unlink(parts[-1], dir_fd=current)
                    except FileNotFoundError:
                        pass
                    except IsADirectoryError:
                        print(f'setup: not removing {name!r}: a directory now', file=err)
                    except OSError as error:
                        print(f'setup: cannot remove {name!r}: {error.strerror}', file=err)
                        failures += 1
            finally:
                os.close(current)
    finally:
        os.close(top)
    return failures


if __name__ == '__main__':
    sys.exit(1 if remove_inside(sys.argv[1], sys.stdin.buffer.read().split(b'\0')) else 0)
