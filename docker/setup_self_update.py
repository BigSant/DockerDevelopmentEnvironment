"""Bring this setup checkout up to the release a project needs, on a build server (make pipeline-deploy).

A project names the oldest setup it runs with (SETUP_MIN_VERSION). On a setup-server worker the shared
checkout belongs to the runner's own account, so a deploy that needs a newer setup does not have to stop:
it fetches the tags and checks out the newest release tag of the same MAJOR version that is new enough
(a MAJOR version changes the project contract: that stays a person's decision), then starts again on it.
Only release tags (vX.Y.Z), only a clean checkout, never on a developer's machine.
"""

import re
import subprocess

from project_env_files import DOCKER_ROOT, version_tuple

RELEASE_TAG = re.compile(r'v(\d+\.\d+\.\d+)')


def git(root, *args, run=subprocess.run):
    return run(['git', '-C', str(root), *args], capture_output=True, text=True, check=True).stdout


def newest_release(tags, current, wanted):
    """The newest vX.Y.Z of current's MAJOR that is at least wanted; None when there is none."""
    major = version_tuple(current)[0]
    fitting = [(version_tuple(match[1]), tag) for tag in tags if (match := RELEASE_TAG.fullmatch(tag))
               and version_tuple(match[1])[0] == major and version_tuple(match[1]) >= version_tuple(wanted)]
    return max(fitting)[1] if fitting else None


def update_setup(wanted, root=None, run=subprocess.run):
    """Check out the release tag a project needs; returns (previous version, tag). ValueError when it cannot."""
    root = root or DOCKER_ROOT.parent
    if git(root, 'status', '--porcelain', '--untracked-files=no', run=run).strip():
        raise ValueError(f'{root} has local changes; setup is not updated over them')
    current = (root / 'VERSION').read_text().strip()
    if version_tuple(current) is None:
        raise ValueError(f'{root}/VERSION is not a version ({current!r})')
    if version_tuple(wanted)[0] > version_tuple(current)[0]:
        raise ValueError(f'The project needs setup {wanted}, a new MAJOR version (the setup here is {current}): '
                         f'update it by hand (setup-server: bin/update --setup-ref v{wanted})')
    git(root, 'fetch', '--quiet', '--tags', 'origin', run=run)
    tag = newest_release(git(root, 'tag', '--list', 'v*', run=run).split(), current, wanted)
    if not tag:
        raise ValueError(f'No setup release tag v{version_tuple(current)[0]}.x is {wanted} or newer at origin: '
                         'tag one first')
    git(root, 'checkout', '--quiet', '--detach', tag, run=run)
    return current, tag
