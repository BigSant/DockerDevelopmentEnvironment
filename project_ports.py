#!/usr/bin/env python3
"""Read reserved host ports from all supported project source locations."""

import argparse
from pathlib import Path
import re
import sys


def read_ports(env_file):
    values = {}
    for line in env_file.read_text().splitlines():
        match = re.fullmatch(r'\s*(LOCALHOST_PORT(?:_SSL)?)\s*=\s*["\']?([0-9]+)["\']?\s*(?:#.*)?', line)
        if match:
            values[match[1]] = int(match[2])
    pair = tuple(values.get(key, 0) for key in ("LOCALHOST_PORT", "LOCALHOST_PORT_SSL"))
    if any(port < 1 or port > 65535 for port in pair) or pair[0] == pair[1]:
        raise ValueError(f"Set two distinct valid host ports in {env_file}")
    return pair


def allocate(project):
    project = Path(project).resolve()
    suffixes = ("docker/.env.local", "app/docker/.env.local", "app/.env.local",
                "docker/env/local.env", "app/docker/env/local.env", "app/env/local.env")
    existing = [project / suffix for suffix in suffixes
                if (project / suffix).is_file()]
    if existing:
        pairs = [read_ports(env_file) for env_file in existing]
        if len(set(pairs)) != 1:
            raise ValueError(f"Conflicting legacy and root Docker ports in {project}")
        return pairs[0]
    used = set()
    for suffix in suffixes:
        for env_file in project.parent.glob(f"*/{suffix}"):
            used.update(read_ports(env_file))
    for port in range(3001, 65535, 2):
        if port not in used and port + 1 not in used:
            return port, port + 1
    raise ValueError("No free configured port pair remains")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    try:
        print(*allocate(args.project))
    except (ValueError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
