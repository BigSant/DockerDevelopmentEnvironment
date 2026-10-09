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


def scaffold(name, parent, options=None):
    """Project files for a new project; options come from the wizard (profile, versions, engine, services)."""
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
    if options:
        apply_options(app, options)
    public = app / 'public'
    if options and options.get('profile') == 'prestashop':
        # The shop code comes from its own repository: git clone it into app/public.
        public = None
    else:
        public.mkdir()
    if public is not None:
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
        'DATABASE_ROOT_PASSWORD': secrets.token_hex(24),
    })
    prepare_project_name(app, display_name)
    # The marker is written last: retries may resume a complete scaffold, never guess
    # whether a pre-existing directory (or an interrupted file copy) belongs to us.
    replace_file(marker, json.dumps({'creator': 'create-project', 'version': 1, 'name': name,
                                    'display_name': display_name}).encode(), private=True)
    print(f'Created project files: {app}', flush=True)
    return app


PROFILES = ('prestashop', 'generic')
ENGINES = ('mysql', 'mariadb')
DEFAULT_PS = '9.0.2'
DEFAULT_SERVICES = 'pma mailpit'


def php_versions():
    """PHP versions setup has images for, oldest first."""
    found = [path.name for path in (SETUP / 'docker/docker/php-fpm').iterdir() if re.fullmatch(r'\d+\.\d+', path.name)]
    return sorted(found, key=lambda version: tuple(map(int, version.split('.'))))


def choices_for(options):
    """Allowed PHP and database versions for the chosen profile and PrestaShop version."""
    import compatibility
    if options.get('profile') != 'prestashop':
        return php_versions(), {'mysql': ['5.7', '8.0', '8.4'], 'mariadb': ['10.6', '10.11', '11.4']}
    line = compatibility.DATA[compatibility.release_line(options['ps_version'])]
    php = [version for version in php_versions() if version in line['php']]
    return php, {'mysql': line['mysql'], 'mariadb': line['mariadb']}


def complete_options(options):
    """Fill defaults and validate one set of answers; raises before anything is written."""
    from project import SERVICE_CATALOGUE
    options = dict(options)
    options.setdefault('profile', 'prestashop')
    if options['profile'] not in PROFILES:
        raise ValueError(f"Profile must be one of: {', '.join(PROFILES)}")
    if options['profile'] == 'prestashop':
        options.setdefault('ps_version', DEFAULT_PS)
        if not re.fullmatch(r'\d+(\.\d+){1,3}', str(options['ps_version'])):
            raise ValueError('PrestaShop version looks like 9.0.2 or 1.7.8.11')
    else:
        options.pop('ps_version', None)
    php, databases = choices_for(options)
    if not php:
        raise ValueError(f"No PHP image in setup supports PrestaShop {options['ps_version']}")
    options.setdefault('php_version', php[-1])
    if options['php_version'] not in php:
        raise ValueError(f"PHP {options['php_version']} is not compatible here; choose one of: {', '.join(php)}")
    options.setdefault('database', 'mysql')
    if options['database'] not in ENGINES:
        raise ValueError(f"Database must be one of: {', '.join(ENGINES)}")
    listed = databases[options['database']]
    options.setdefault('database_version', listed[-1])
    if not re.fullmatch(r'\d+\.\d+(\.\d+)?', str(options['database_version'])):
        raise ValueError('Database version looks like 8.4 or 10.11')
    services = str(options.get('services', DEFAULT_SERVICES)).split()
    unknown = [name for name in services if name not in SERVICE_CATALOGUE]
    if unknown:
        raise ValueError(f"Unknown services: {', '.join(unknown)}; available: {' '.join(SERVICE_CATALOGUE)}")
    options['services'] = ' '.join(services)
    return options


def apply_options(app, options):
    """Write the answers into the new project's settings and Compose sources."""
    lines = {'PROFILE': options['profile'], 'PHP_VERSION': options['php_version'],
             ('MARIADB_VERSION' if options['database'] == 'mariadb' else 'MYSQL_VERSION'): options['database_version']}
    if options.get('ps_version'):
        lines['PS_VERSION'] = options['ps_version']
    if options['services']:
        lines['SERVICES'] = options['services']
    if options['database'] == 'mariadb':
        lines['DATABASE_ENGINE'] = 'mariadb'
    set_env_values(app / 'env/common.env', lines)


def ask_options(name, given, ask=input):
    """Interactive wizard: every question has a default (Enter) and only compatible choices."""
    def question(text, default, allowed=None):
        while True:
            hint = f" ({'/'.join(allowed)})" if allowed and len(allowed) <= 8 else ''
            answer = ask(f'{text}{hint} [{default}]: ').strip() or default
            if allowed is None or answer in allowed:
                return answer
            print(f'  Choose one of: {", ".join(allowed)}')
    options = dict(given)
    if not name:
        while True:
            name = ask('Project name (e.g. Melga, GameroomAkeneo): ').strip()
            try:
                normalize_name(name)
                break
            except ValueError as error:
                print(f'  {error}')
    if 'profile' not in options:
        options['profile'] = question('Profile', 'prestashop', PROFILES)
    if options['profile'] == 'prestashop' and 'ps_version' not in options:
        options['ps_version'] = question('PrestaShop version', DEFAULT_PS)
    php, databases = choices_for({'profile': options['profile'], 'ps_version': options.get('ps_version')})
    if 'php_version' not in options:
        options['php_version'] = question('PHP version', php[-1], php)
    if 'database' not in options:
        options['database'] = question('Database', 'mysql', ENGINES)
    if 'database_version' not in options:
        listed = databases[options['database']]
        options['database_version'] = question(f"{options['database']} version", listed[-1], listed)
    if 'services' not in options:
        from project import SERVICE_CATALOGUE
        print(f"  Services: {' '.join(SERVICE_CATALOGUE)}")
        options['services'] = question('Services (space separated, - for none)', DEFAULT_SERVICES)
        if options['services'] == '-':
            options['services'] = ''
    return name, options


def summary(name, options):
    domain = normalize_name(name).replace('_', '-') + '.local'
    rows = [('Project', f'{name} ({normalize_name(name)})'), ('Domain', domain), ('Profile', options['profile'])]
    if options.get('ps_version'):
        rows.append(('PrestaShop', options['ps_version']))
    rows += [('PHP', options['php_version']), ('Database', f"{options['database']} {options['database_version']}"),
             ('Services', options['services'] or '(none)'), ('Time zone', 'Europe/Vilnius')]
    return '\n'.join(f'  {label:<11} {value}' for label, value in rows)


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


def main(argv=None, ask=input, interactive=None):
    parser = argparse.ArgumentParser(prog='create-project', description='Prepare a new project next to the shared setup '
                                     'without starting its environment. Without a name it asks every question.')
    parser.add_argument('name', metavar='name', nargs='?', help='For example: Melga, MelgaMCP or GameroomAkeneo')
    parser.add_argument('--profile', choices=PROFILES)
    parser.add_argument('--ps-version', help=f'PrestaShop version (default {DEFAULT_PS})')
    parser.add_argument('--php-version', help='PHP version; only versions the PrestaShop line supports')
    parser.add_argument('--database', choices=ENGINES)
    parser.add_argument('--database-version', help='e.g. 8.4 or 10.11')
    parser.add_argument('--services', help=f'catalogue services, space separated (default "{DEFAULT_SERVICES}"; "" for none)')
    parser.add_argument('--yes', action='store_true', help='write without the confirmation question')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--start', action='store_true', help='Also prepare the host, TLS and IDE, build images and start the environment')
    mode.add_argument('--no-start', action='store_true', help=argparse.SUPPRESS)  # Previous spelling remains harmless.
    args = parser.parse_args(argv)
    given = {key: value for key, value in (('profile', args.profile), ('ps_version', args.ps_version),
                                           ('php_version', args.php_version), ('database', args.database),
                                           ('database_version', args.database_version), ('services', args.services))
             if value is not None}
    interactive = sys.stdin.isatty() if interactive is None else interactive
    try:
        name = args.name
        if not name and not interactive:
            raise ValueError('Name the project, or run create-project in a terminal to be asked')
        # In a terminal every answer not given as a parameter is asked; all parameters ask nothing.
        asked = interactive and not args.yes and (not name or len(given) < 6)
        if asked:
            name, given = ask_options(name, given, ask)
        options = complete_options(given) if (given or asked) else None
        if options:
            print('\nNew project:\n' + summary(name, options))
            if interactive and not args.yes and ask('Create these files? [Y/n]: ').strip().lower() not in ('', 'y', 'yes', 't', 'taip'):
                print('Cancelled; nothing was created.')
                return 1
        if args.start:
            prerequisites()
        app = scaffold(name, SETUP.parent, options)
        if args.start:
            start(app)
        else:
            clone = '\nNext: git clone <shop repository> ' + str(app / 'public') + ' && make -C ' + str(app) + ' setup' \
                if options and options['profile'] == 'prestashop' else ''
            print(f'Files prepared; environment has not been started.\nOpen in PhpStorm: {app}{clone}\n'
                  f'To start: {SETUP / "create-project"} {name} --start')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Error: {error}\nResolve the error and retry the same create-project command; prepared files are preserved.', file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print('\nInterrupted. Nothing new was written by the questions; you can retry the same command.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
