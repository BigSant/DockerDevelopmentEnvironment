"""Git hooks setup writes into the project's repository (make setup on ENV=local, make git-hooks).

pre-commit runs setup's schema check (database as code) and then the project's own bin/pre-commit; pre-push
runs the project's bin/pre-push. A clone does not carry .git/hooks, so a project that keeps its checks in bin/
gets them in every clone without anyone linking them by hand. The hook looks the project script up when it
runs, so a script added or changed later needs no reinstall; a hook that has neither part is not written.
A hook somebody else wrote, a symlink, or a core.hooksPath hook manager is left alone and reported."""

import os
from pathlib import Path
import shlex
import subprocess
import sys

MARKER = '# Shared setup git hook v1'
# Hooks earlier setup versions wrote: setup owns them and replaces them.
PREVIOUS_MARKERS = ('# Shared setup schema pre-commit hook v1', '# Shared setup schema pre-commit hook v2')
HOOKS = ('pre-commit', 'pre-push')


def _git(directory, *args):
    from database_schema import git_env
    return subprocess.run(['git', '-C', str(directory), *args], capture_output=True, env=git_env())


def repository(project):
    """The work tree the project directory belongs to (with a separate code repository: the environment's)."""
    result = _git(project.directory, 'rev-parse', '--show-toplevel')
    if result.returncode:
        raise ValueError(f'{project.directory} is not inside a Git repository')
    return Path(os.fsdecode(result.stdout).strip()).resolve()


def hooks_directory(root):
    """.git/hooks of the repository, unless core.hooksPath hands the hooks to a manager."""
    custom = _git(root, 'config', '--get', 'core.hooksPath')
    common = Path(os.fsdecode(_git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir').stdout).strip())
    # A hooksPath that names the repository's own hooks directory is the default, not a hook manager.
    default = custom.returncode == 0 and (root / os.fsdecode(custom.stdout).strip()).resolve() == (common / 'hooks').resolve()
    if custom.returncode != 1 and not default:
        raise ValueError('core.hooksPath is configured or unreadable; add the setup hooks to the existing hook manager')
    hooks = _git(root, 'rev-parse', '--path-format=absolute', '--git-path', 'hooks')
    return Path(os.fsdecode(hooks.stdout).strip())


def project_script(project, name):
    return Path(project.directory) / 'bin' / name


def hook_text(project, name, schema):
    """The hook: setup's part (the schema check before a commit), then the project's bin/<name>."""
    lines = ['#!/bin/sh', f'{MARKER} ({name})',
             f'# Written by setup (make setup, make git-hooks) and replaced by it; the project\'s checks go in bin/{name}.']
    if schema:
        command = [sys.executable, str(Path(__file__).with_name('project.py')),
                   '--docker-directory', str(project.directory), '--project-directory', str(project.root),
                   '--env', project.environment, 'schema-commit']
        lines.append(shlex.join(command) + ' || exit $?')
    lines += [f'script={shlex.quote(str(project_script(project, name)))}',
              'if [ -x "$script" ]; then exec "$script" "$@"; fi',
              'exit 0']
    return '\n'.join(lines) + '\n'


def wanted(project, names=HOOKS):
    """{hook: runs the schema check}, for the hooks this project has something to run in."""
    schema = bool(project.settings.get('SCHEMA_DIRECTORY'))
    hooks = {}
    for name in names:
        part = schema and name == 'pre-commit'
        if part or project_script(project, name).is_file():
            hooks[name] = part
    return hooks


def install_git_hooks(project, names=HOOKS):
    """Write the hooks this project needs; returns their names. Raises (after writing the others) when a hook
    somebody else owns is in the way."""
    root = repository(project)
    directory = hooks_directory(root)
    installed, preserved = [], []
    for name, schema in wanted(project, names).items():
        hook = directory / name
        contents = hook_text(project, name, schema)
        if hook.is_symlink() or (hook.exists() and not any(m in hook.read_text(errors='replace')
                                                          for m in (MARKER, *PREVIOUS_MARKERS))):
            preserved.append(str(hook))
            continue
        if not hook.exists() or hook.read_text() != contents:
            directory.mkdir(parents=True, exist_ok=True)
            hook.write_text(contents)
        hook.chmod(0o755)
        installed.append(name)
    if installed:
        print(f'Git hooks in {root}: ' + ', '.join(installed) + '.')
    if preserved:
        raise ValueError('Existing hook preserved: ' + ', '.join(preserved)
                         + '; remove it (setup writes its own) or call the setup part from it')
    return installed
