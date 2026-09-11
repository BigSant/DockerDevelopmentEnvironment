"""Prepare a new local project; start it only with an explicit --start."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

SETUP = Path(__file__).resolve().parent
sys.path.insert(0, str(SETUP / 'docker'))

from prepare_project import minimal_files
from project_bootstrap import set_env_values
from project_ide import checked_path, replace_file

MARKER = 'app/.generated/create-project.json'


def normalize_name(value):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*(?:[_-][A-Za-z0-9]+)*', value):
        raise ValueError('Name must start with a letter; use A-Z, a-z, 0-9, and single underscores or hyphens.')
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1-\2', value)
    name = re.sub(r'([a-z0-9])([A-Z])', r'\1-\2', name).lower().replace('_', '-')
    if len(name) > 32:
        raise ValueError('Normalized project name must be at most 32 characters.')
    return name


def ensure_domain_available(parent, domain):
    for pattern in ('*/app/env/local.env', '*/docker/env/local.env', '*/app/docker/env/local.env',
                    '*/app/docker/.env.local', '*/docker/.env.local'):
        for path in Path(parent).glob(pattern):
            if not path.is_file():
                continue
            match = re.search(r'^DOMAIN\s*=\s*[\'\"]?([^\s\'\"#]+)', path.read_text(), re.M)
            if match and match[1].lower() == domain:
                raise ValueError(f'Domain {domain} is already used: {path}. Choose another project name.')


def prepare_project_name(app, name):
    """Only the display name is needed before opening a newly created project."""
    path = checked_path(SimpleNamespace(directory=app), '.idea/.name')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x') as output:
            output.write(name + '\n')


def scaffold(name, parent):
    display_name = name
    name = normalize_name(name)
    root = Path(parent).resolve() / name
    marker = root / MARKER
    if root.is_symlink():
        raise ValueError(f'Project directory must not be a symlink: {root}')
    if root.exists():
        if any(p.is_symlink() for p in (root / 'app', marker.parent, marker)):
            raise ValueError(f'Existing project creation marker must not be a symlink: {root}')
        saved = json.loads(marker.read_text()) if marker.is_file() else {}
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in
                {'creator': 'create-project', 'version': 1, 'name': name}.items()):
            raise ValueError(f'Directory already exists and is not managed by this command: {root}. Choose another name; existing files were preserved.')
        original = saved.get('display_name', name)
        if not isinstance(original, str) or normalize_name(original) != name:
            raise ValueError('Project marker contains an invalid name; files were preserved.')
        prepare_project_name(root / 'app', original)
        print(f'Project is already prepared; existing files and credentials were preserved: {root}', flush=True)
        return root / 'app'

    domain = name.replace('_', '-') + '.local'
    ensure_domain_available(root.parent, domain)
    app, files = minimal_files(root)
    # Claim only a new project directory. Never merge a scaffold into someone else's files.
    root.mkdir()
    for path, contents in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(contents)
    public = app / 'public'
    public.mkdir()
    (public / 'index.php').write_text(
        "<?php\nheader('Content-Type: text/plain; charset=utf-8');\n"
        f"echo \"Project {name} is running.\\n\";\n"
        "echo 'PHP: ' . PHP_VERSION . \"\\n\";\n"
        "echo 'Cache: ' . getenv('CACHE_MODE') . \"\\n\";\n")
    import secrets
    set_env_values(app / 'env/local.env', {
        'DOMAIN': domain,
        'DATABASE_USER': name.replace('-', '_'), 'DATABASE_NAME': name.replace('-', '_'),
        'DATABASE_PASSWORD': secrets.token_hex(24),
    })
    prepare_project_name(app, display_name)
    # The marker is written last: retries may resume a complete scaffold, never guess
    # whether a pre-existing directory (or an interrupted file copy) belongs to us.
    replace_file(marker, json.dumps({'creator': 'create-project', 'version': 1, 'name': name,
                                    'display_name': display_name}).encode(), private=True)
    print(f'Created project files: {app}', flush=True)
    return app


def prerequisites():
    missing = [name for name in ('make', 'docker', 'openssl', 'mkcert') if not shutil.which(name)]
    if missing:
        raise ValueError('Missing programs: ' + ', '.join(missing) + '. Install them and retry.')
    result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError('Docker is not running. Start Docker and retry.')
    result = subprocess.run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=20)
    version = re.search(r'(\d+)\.(\d+)\.(\d+)', result.stdout)
    if result.returncode or not version or tuple(map(int, version.groups())) < (2, 24, 4):
        raise ValueError('Docker Compose 2.24.4 or newer is required.')


def start(app):
    for action, description in (('bootstrap', 'Preparing ports, local domain, TLS and PhpStorm'),
                                ('check', 'Checking configuration'),
                                ('build', 'Building Docker images'),
                                ('up', 'Starting services and waiting for container healthchecks')):
        print(f'\n{description}…', flush=True)
        subprocess.run(['make', '--no-print-directory', '-C', str(app), f'SETUP_DIRECTORY={SETUP}', action], check=True)
    from project import Project
    project = Project(app)
    print(f'\nProject started: http://{project.settings["DOMAIN"]}/\nOpen in PhpStorm: {app}\nApplication code: {app / "public"}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='create-project', description='Prepare a new project next to the shared setup without starting its environment.')
    parser.add_argument('name', metavar='name', help='For example: Melga, MelgaMCP or GameroomAkeneo')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--start', action='store_true', help='Also prepare the host, TLS and IDE, build images and start the environment')
    mode.add_argument('--no-start', action='store_true', help=argparse.SUPPRESS)  # Previous spelling remains harmless.
    args = parser.parse_args(argv)
    try:
        if args.start:
            prerequisites()
        app = scaffold(args.name, SETUP.parent)
        if args.start:
            start(app)
        else:
            print(f'Files prepared; environment has not been started.\nOpen in PhpStorm: {app}\n'
                  f'To start: {SETUP / "create-project"} {args.name} --start')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Error: {error}\nResolve the error and retry the same create-project command; prepared files are preserved.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nInterrupted. Prepared files were preserved; you can retry the same command.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
