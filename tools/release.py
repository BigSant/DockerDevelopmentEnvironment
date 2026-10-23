#!/usr/bin/env python3
"""Turn doc/RELEASES.md's "## Unreleased" section into the next release: VERSION and the section's heading.

Every merge to master is released (.github/workflows/release.yml runs this after the merge's CI passed, then
commits "Release X.Y.Z" and tags vX.Y.Z), so the newest release tag is always master and a server that takes the
newest release runs what master has. The heading says how far the version moves:
  ## Unreleased            a patch release (2.14.1 -> 2.14.2): fixes, documentation
  ## Unreleased (minor)    a minor release (2.14.1 -> 2.15.0): something new, nothing to change in projects
  ## Unreleased (major)    a major release (2.14.1 -> 3.0.0): projects must change (say how in the section)
A section without entries still releases, as a patch with one line saying nothing changes for projects.
Afterwards the file starts again with an empty "## Unreleased". Prints the new version; --check changes nothing.
"""

import argparse
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r'## Unreleased(?: \((patch|minor|major)\))?')
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)')
EMPTY_NOTE = 'Internal changes only (tests, tooling, documentation); nothing to change in projects.'


def next_version(current, step):
    match = VERSION.fullmatch(current)
    if not match:
        raise ValueError(f'VERSION is not MAJOR.MINOR.PATCH: {current!r}')
    major, minor, patch = (int(part) for part in match.groups())
    if step == 'major':
        return f'{major + 1}.0.0'
    if step == 'minor':
        return f'{major}.{minor + 1}.0'
    return f'{major}.{minor}.{patch + 1}'


def release(text, current):
    """(new RELEASES.md text, new version) for the RELEASES.md text and the current VERSION."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if HEADING.fullmatch(line.strip())]
    if len(starts) != 1:
        raise ValueError('doc/RELEASES.md must have exactly one "## Unreleased" heading')
    start = starts[0]
    end = next((index for index in range(start + 1, len(lines)) if lines[index].startswith('## ')), len(lines))
    body = '\n'.join(lines[start + 1:end]).strip()
    step = HEADING.fullmatch(lines[start].strip())[1] or 'patch'
    version = next_version(current, step)
    section = ['## Unreleased', '', f'## {version}', '', body or EMPTY_NOTE, '']
    new = lines[:start] + section + lines[end:]
    return '\n'.join(new).rstrip('\n') + '\n', version


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--check', action='store_true', help='print the next version; change nothing')
    parser.add_argument('--root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(arguments)
    version_file, notes = args.root / 'VERSION', args.root / 'doc/RELEASES.md'
    try:
        text, version = release(notes.read_text(), version_file.read_text().strip())
    except (OSError, ValueError) as error:
        print(f'release: {error}', file=sys.stderr)
        return 65
    if not args.check:
        notes.write_text(text)
        version_file.write_text(version + '\n')
    print(version)
    return 0


if __name__ == '__main__':
    sys.exit(main())
