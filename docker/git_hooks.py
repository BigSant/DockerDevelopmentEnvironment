"""Git hooks setup writes into the project's repository (make setup on ENV=local, make git-hooks).

pre-commit runs setup's checks (a PrestaShop shop's one .gitignore, gitignore_check.py; the schema, database as
code) and then the project's own bin/pre-commit; pre-push runs the project's bin/pre-push. A shop in a repository of
its own (app/public/.git) gets a pre-commit there that runs the .gitignore check alone. A clone does not carry .git/hooks, so a project that keeps its checks in bin/
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


def shop_gitignore(project, root):
    """Where the shop's .gitignore check runs: ('own', shop root) for a PrestaShop shop with its own repository,
    ('inside', prefix) for one that is a folder of the environment repository, None otherwise."""
    from project_prestashop import is_prestashop
    web = getattr(project, 'web_directory', None)
    if not web or not Path(web).is_dir() or not is_prestashop(project):
        return None
    web = Path(web).resolve()
    if (web / '.git').exists():
        return 'own', web
    if web.is_relative_to(root):
        return 'inside', f'{web.relative_to(root).as_posix()}/' if web != root else ''
    return None


def gitignore_line(prefix):
    command = [sys.executable, str(Path(__file__).with_name('gitignore_check.py'))] + (['--prefix', prefix] if prefix else [])
    return shlex.join(command) + ' || exit $?'


def hook_text(project, name, schema, gitignore=None, script=True):
    """The hook: setup's part (the .gitignore and schema checks before a commit), then the project's bin/<name>.
    gitignore: the shop folder prefix the check takes ('' for the repository root), None for no check."""
    lines = ['#!/bin/sh', f'{MARKER} ({name})',
             f'# Written by setup (make setup, make git-hooks) and replaced by it; the project\'s checks go in bin/{name}.'
             if script else '# Written by setup (make setup, make git-hooks) and replaced by it: the shop keeps one .gitignore.']
    if gitignore is not None:
        lines.append(gitignore_line(gitignore))
    if schema:
        command = [sys.executable, str(Path(__file__).with_name('project.py')),
                   '--docker-directory', str(project.directory), '--project-directory', str(project.root),
                   '--env', project.environment, 'schema-commit']
        lines.append(shlex.join(command) + ' || exit $?')
    if script:
        lines += [f'script={shlex.quote(str(project_script(project, name)))}',
                  'if [ -x "$script" ]; then exec "$script" "$@"; fi']
    lines.append('exit 0')
    return '\n'.join(lines) + '\n'


def wanted(project, names=HOOKS, gitignore=None):
    """{hook: (runs the schema check, the .gitignore check prefix or None)}, for the hooks this project has
    something to run in."""
    schema = bool(project.settings.get('SCHEMA_DIRECTORY'))
    hooks = {}
    for name in names:
        part = schema and name == 'pre-commit'
        check = gitignore if name == 'pre-commit' else None
        if part or check is not None or project_script(project, name).is_file():
            hooks[name] = (part, check)
    return hooks


def write_hook(hook, contents, installed, preserved, label):
    """Write one setup hook unless somebody else's (or a symlink) is there."""
    if hook.is_symlink() or (hook.exists() and not any(m in hook.read_text(errors='replace')
                                                      for m in (MARKER, *PREVIOUS_MARKERS))):
        preserved.append(str(hook))
        return
    if not hook.exists() or hook.read_text() != contents:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(contents)
    hook.chmod(0o755)
    installed.append(label)


def install_git_hooks(project, names=HOOKS):
    """Write the hooks this project needs; returns their names. Raises (after writing the others) when a hook
    somebody else owns is in the way."""
    root = repository(project)
    directory = hooks_directory(root)
    shop = shop_gitignore(project, root)
    installed, preserved = [], []
    for name, (schema, gitignore) in wanted(project, names, shop[1] if shop and shop[0] == 'inside' else None).items():
        write_hook(directory / name, hook_text(project, name, schema, gitignore), installed, preserved, name)
    if installed:
        print(f'Git hooks in {root}: ' + ', '.join(installed) + '.')
    if shop and shop[0] == 'own' and 'pre-commit' in names:
        # The shop repository's own pre-commit: the .gitignore check, nothing of the environment repository.
        shop_installed = []
        write_hook(hooks_directory(shop[1]) / 'pre-commit', hook_text(project, 'pre-commit', False, '', script=False),
                   shop_installed, preserved, 'pre-commit')
        if shop_installed:
            print(f'Git hooks in {shop[1]}: pre-commit (one .gitignore).')
            installed.append('shop pre-commit')
    if preserved:
        raise ValueError('Existing hook preserved: ' + ', '.join(preserved)
                         + '; remove it (setup writes its own) or call the setup part from it')
    return installed
