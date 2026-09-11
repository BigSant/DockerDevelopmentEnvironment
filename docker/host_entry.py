#!/usr/bin/env python3
"""Privileged helper: append one validated loopback host without changing other entries."""
import fcntl
import re
import sys

if len(sys.argv) != 2 or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]*[a-zA-Z0-9]', sys.argv[1]):
    raise SystemExit('Provide one local hostname')
host = sys.argv[1]
with open('/etc/hosts', 'r+') as file:
    fcntl.flock(file, fcntl.LOCK_EX)
    contents = file.read()
    for line in contents.splitlines():
        fields = line.split('#', 1)[0].split()
        if host in fields[1:]:
            if fields[0] not in ('127.0.0.1', '::1'): raise SystemExit('Existing non-local host entry preserved')
            raise SystemExit(0)
    file.write('\n127.0.0.1 ' + host + ' # shared-setup\n')
    file.flush()
