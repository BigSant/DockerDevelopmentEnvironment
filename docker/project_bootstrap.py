"""Idempotent local onboarding and a separate test application/data tree."""

import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import tempfile

from env_layout import insert
from project_environment import initialize_directories
from project_ide import initialize_ide, replace_file


def available_ports(project):
    reserved = set()
    parent = project.root.parent
    for pattern in ('*/app/env/*.env', '*/docker/env/*.env', '*/app/docker/env/*.env', '*/app/docker/.env.*', '*/docker/.env.*'):
        for file in parent.glob(pattern):
            if file.is_file():
                reserved.update(int(v) for v in re.findall(r'^LOCALHOST_PORT(?:_SSL)?\s*=\s*[\'\"]?(\d+)', file.read_text(), re.M))
    seed = int(hashlib.sha256(project.name.encode()).hexdigest()[:4], 16)
    result = []
    for offset in range(10000):
        port = 30000 + (seed + offset) % 10000
        if port in reserved: continue
        with socket.socket() as probe:
            try: probe.bind(('0.0.0.0', port))
            except OSError: continue
        result.append(port)
        if len(result) == 2: return result
    raise ValueError('No free local port pair found')


def set_env_values(path, values):
    if path.is_symlink(): raise ValueError('Private env must not be a symlink')
    contents = path.read_text() if path.exists() else ''
    for key, value in values.items():
        value = str(value)
        # Plain hostnames, ports, identifiers, hex and base64 stay unquoted; anything else is
        # single-quoted, which dotenv keeps literally (no ${...} interpolation).
        if re.fullmatch(r'[A-Za-z0-9_.:/+=-]*', value):
            line = f'{key}={value}'
        elif "'" not in value and '\n' not in value:
            line = f"{key}='{value}'"
        else:
            raise ValueError(f'Cannot store {key} safely in an env file')
        pattern = rf'^{re.escape(key)}=.*$'
        contents = re.sub(pattern, lambda m: line, contents, flags=re.M) if re.search(pattern, contents, re.M) else insert(contents, key, line)
    replace_file(path, contents.encode(), private=True)


def ensure_generated_secrets(directory, environment):
    """Local/test only: create missing private secrets that nobody has to choose by hand."""
    if environment not in ('local', 'test'):
        return
    private = Path(directory) / f'env/{environment}.env'
    if not private.is_file():
        return
    from project_env_files import project_root, read_dotenv
    from project_prestashop import is_prestashop, local_key_values
    own = read_dotenv(private)
    common = Path(directory) / 'env/common.env'
    # As Compose reads them: common.env, then the private file; a blank value is not set.
    settings = {**(read_dotenv(common) if common.is_file() else {}), **own}
    values = {}
    if not own.get('DATABASE_ROOT_PASSWORD'):
        values['DATABASE_ROOT_PASSWORD'] = secrets.token_hex(24)
    if is_prestashop(settings) and settings.get('PS_VERSION'):
        present = {key for key, value in own.items() if key.startswith('PS_') and value}
        web = project_root(Path(directory).resolve()) / (settings.get('APP_SOURCE_DIRECTORY') or 'app/public')
        values.update(local_key_values(web, settings['PS_VERSION'], present))
    if values:
        set_env_values(private, values)


def certificate_is_valid(certificate, domain):
    expiry = subprocess.run(['openssl', 'x509', '-noout', '-in', str(certificate), '-checkend', '2592000'],
                            capture_output=True)
    hostname = subprocess.run(['openssl', 'x509', '-noout', '-in', str(certificate), '-checkhost', domain],
                              capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
    # OpenSSL can exit successfully even when -checkhost reports a mismatch.
    return (expiry.returncode == 0 and hostname.returncode == 0 and
            hostname.stdout.strip() == f'Hostname {domain} does match certificate')


INSTALLED_HELPERS = Path('/usr/local/lib/setup')
# The root-owned system interpreter: sudoers names it, so a user-writable venv/pyenv python never runs as root.
HOST_PYTHON = '/usr/bin/python3'


def host_helper(name):
    """The root-owned copy installed by setup/install-host (sudoers allows only that copy)."""
    installed, source = INSTALLED_HELPERS / name, Path(__file__).with_name(name)
    if installed.is_file():
        if installed.read_bytes() != source.read_bytes():
            raise ValueError('The host helpers changed with this setup version; rerun setup/install-host (needs sudo once)')
        return installed
    return source


def resolves_locally(host):
    try:
        addresses = {r[4][0] for r in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    return bool(addresses) and addresses.issubset({'127.0.0.1', '::1'})


SERVICE_HOSTS = ('pma', 'mailpit', 'cron', 'dbmonitor', 'backup', 'metrics')


def route_exists(domain, port, nginx=Path('/etc/nginx'), hosts=()):
    """None, 'partial' (the site but not every service host name) or 'complete' for the host Nginx route.

    hosts: the project's other hostnames; a route without one of them does not serve that site."""
    site = nginx / 'sites-enabled' / f'{domain}.conf'
    try:
        text = site.read_text()
    except OSError:
        return None
    names = set(' '.join(re.findall(r'server_name([^;]*);', text)).split())
    if not {domain, *hosts} <= names or f'proxy_pass http://127.0.0.1:{port};' not in text:
        return None
    return 'complete' if {f'{host}.{domain}' for host in SERVICE_HOSTS} <= names else 'partial'


def prepare_host(project):
    domain = project.settings.get('DOMAIN', '')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain):
        raise ValueError('Set DOMAIN to a hostname without a port')
    from project_domains import extra_hosts
    hosts = extra_hosts(project.settings)
    for name in (domain, *hosts):
        try:
            addresses = {r[4][0] for r in socket.getaddrinfo(name, None)}
        except socket.gaierror:
            addresses = set()
        if not addresses:
            helper = host_helper('host_entry.py')
            result = subprocess.run(['sudo', '-n', HOST_PYTHON, str(helper), name], capture_output=True, text=True)
            if result.returncode:
                raise ValueError(f'Local DNS entry is missing. Run setup/install-host once, or: sudo {HOST_PYTHON} {helper} {name}')
        elif not addresses.issubset({'127.0.0.1', '::1'}):
            raise ValueError(f'{name} (DOMAIN) resolves outside localhost; use a local development hostname')
    ssl = project.data_directory / 'ssl'
    ssl.mkdir(mode=0o700, parents=True, exist_ok=True)
    certificate, key = ssl/'domain.crt', ssl/'domain.key'
    valid = False
    if certificate.is_file() and key.is_file():
        valid = all(certificate_is_valid(certificate, name) for name in (domain, *hosts))
    if not valid:
        if not shutil.which('mkcert'):
            raise ValueError('Install mkcert, then rerun make bootstrap to create local TLS certificates')
        installed = subprocess.run(['mkcert', '-install'], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        if installed.returncode:
            raise ValueError('Run mkcert -install once to install the local CA, then rerun make bootstrap')
        with tempfile.TemporaryDirectory(dir=ssl) as temporary:
            crt, private = Path(temporary)/'domain.crt', Path(temporary)/'domain.key'
            result = subprocess.run(['mkcert', '-cert-file', str(crt), '-key-file', str(private), domain, '*.'+domain, *hosts], capture_output=True)
            if result.returncode: raise ValueError('mkcert failed to generate the local certificate')
            replace_file(certificate, crt.read_bytes())
            replace_file(key, private.read_bytes(), private=True)
    print(f"Local hostname and TLS prepared: {' '.join((domain, *hosts))}")
    entry_helper = host_helper('host_entry.py')
    for host in (f'{name}.{domain}' for name in SERVICE_HOSTS):
        if resolves_locally(host):
            continue
        command = ['sudo', '-n', HOST_PYTHON, str(entry_helper), host]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            import shlex
            raise ValueError('Local service hostname could not be prepared. Run setup/install-host once, or: ' +
                             shlex.join(['sudo', HOST_PYTHON, str(entry_helper), host]))
    route = route_exists(domain, project.settings['LOCALHOST_PORT'], hosts=hosts)
    if route == 'complete':
        print(f'Local Nginx route already in place: http://{domain}/')
        return
    helper = host_helper('host_proxy.py')
    args = [HOST_PYTHON, str(helper), domain, project.settings['LOCALHOST_PORT'], str(ssl), *hosts]
    result = subprocess.run(['sudo', '-n', *args], capture_output=True, text=True)
    if result.returncode:
        import shlex
        if route == 'partial':
            # The site works; only newer service host names (e.g. cron., dbmonitor.) are not routed yet.
            print(f'Warning: the Nginx route for {domain} lacks some service host names. Run setup/install-host '
                  'once, or: ' + shlex.join(['sudo', *args]))
            return
        raise ValueError('Local Nginx route could not be prepared. ' + result.stderr.strip() +
                         '\nRun setup/install-host once, or: ' + shlex.join(['sudo', *args]))
    print(f'Local Nginx route prepared: http://{domain}/')


def bootstrap(project):
    from project import Project
    if project.environment not in ('local', 'test'):
        raise ValueError('bootstrap provisions local/test environments only; production settings must be supplied explicitly')
    values = {}
    private = project.env_files[-1]
    raw = private.read_text()
    if not project.settings['DOMAIN'] or project.settings['DOMAIN'] == 'example.local':
        values['DOMAIN'] = project.settings['PROJECT_NAME'].replace('_', '-') + ('.test.local' if project.environment == 'test' else '.local')
    if any(project.settings[key] in ('', '0') for key in ('LOCALHOST_PORT', 'LOCALHOST_PORT_SSL')):
        ports = available_ports(project)
        for key, port in zip(('LOCALHOST_PORT', 'LOCALHOST_PORT_SSL'), ports):
            if project.settings[key] in ('', '0'): values[key] = str(port)
    for key in ('DATABASE_NAME', 'DATABASE_USER'):
        if re.search(rf'^{key}=example\s*$', raw, re.M): values[key] = project.settings['PROJECT_NAME'].replace('-', '_')
    if re.search(r"^DATABASE_PASSWORD=['\"]?replace-with-local-password['\"]?\s*$", raw, re.M):
        values['DATABASE_PASSWORD'] = secrets.token_hex(24)
    if values: set_env_values(private, values)
    project = Project(project.directory, project.environment, project.root, project.profiles)
    initialize_directories(project)
    if not project.web_directory.is_dir():
        raise ValueError(f'Application checkout is missing: {project.web_directory}')
    prepare_host(project)
    active = project.model()['services']
    if 'php-fpm' in active:
        if project.environment == 'local': initialize_ide(project)
        else:
            from project_ide import refresh_ide
            refresh_ide(project)  # Do not switch the developer's default IDE environment.
    print('Bootstrap complete. make setup runs the remaining steps (build, app-prepare, up).')
    return project


PRESTASHOP_CACHE_FOLDERS = ('var/cache', 'var/logs', 'cache')


def initialize_test(project, refresh=False):
    from project import Project
    if project.environment != 'local':
        raise ValueError('Run make test-init from ENV=local; it prepares ENV=test separately')
    root = project.directory / '.generated/test'
    if root.is_symlink() or not root.resolve().is_relative_to(project.directory):
        raise ValueError('Test directory must stay inside the project and not be a symlink')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    source = project.web_directory
    destination = root / 'app'
    if not source.is_dir(): raise ValueError('The local application checkout is missing')
    if destination.is_symlink(): raise ValueError('Test application must not be a symlink')
    marker = root / 'initialized'
    if destination.exists() and not marker.exists(): raise ValueError('Unmanaged test checkout exists; preserved')
    env_file = project.directory / 'env/test.env'
    if not env_file.exists():
        ports = available_ports(project)
        domain = project.settings['PROJECT_NAME'].replace('_', '-')+'.test.local'
        set_env_values(env_file, {'DOMAIN':domain, 'LOCALHOST_PORT':str(ports[0]), 'LOCALHOST_PORT_SSL':str(ports[1]),
                                 'DATABASE_NAME':project.settings['DATABASE_NAME']+'_test', 'DATABASE_USER':'test',
                                 'DATABASE_PASSWORD':secrets.token_hex(24),
                                 'DATABASE_ROOT_PASSWORD':secrets.token_hex(24),
                                 'APP_SOURCE_DIRECTORY':'', 'DATA_DIRECTORY':'',
                                 'SQL_DOMAIN':domain})
    if not destination.exists() or refresh:
        if not shutil.which('rsync'): raise ValueError('Install rsync to create the isolated test application copy')
        # Tool worktrees and setup's own test tools never come from the local checkout; projects add
        # large non-code folders (feed caches, media) with TEST_COPY_EXCLUDE. The cache folders are
        # PrestaShop's: the copy starts with them empty; a generic application's tree is copied as it is.
        from project_prestashop import is_prestashop
        cache_folders = PRESTASHOP_CACHE_FOLDERS if is_prestashop(project) else ()
        excludes = ['.git', 'node_modules', *('/' + folder for folder in cache_folders), '/.claude', '/.setup-tools',
                    '/.setup-tests', *str(project.settings.get('TEST_COPY_EXCLUDE') or '').split()]
        for pattern in excludes:
            if pattern.startswith('-') or '..' in pattern.split('/'):
                raise ValueError(f'TEST_COPY_EXCLUDE entries are paths inside the shop: {pattern}')
        command = ['rsync', '-a', '--safe-links', '--delete', *[f'--exclude={pattern}' for pattern in excludes],
                   str(source)+'/', str(destination)+'/']
        from project_storage import require_space
        preview = subprocess.run(command[:1] + ['--dry-run', '--stats'] + command[1:],
                                 env={**os.environ, 'LC_ALL': 'C'}, capture_output=True, text=True, check=True)
        size = re.search(r'^Total transferred file size: ([\d,]+) bytes', preview.stdout, re.M)
        if not size: raise ValueError('Cannot estimate test checkout disk requirements from rsync')
        require_space(project, destination, int(size[1].replace(',', '')))
        destination.mkdir(exist_ok=True)
        try:
            subprocess.run(command, check=True)
        except BaseException:
            if not marker.exists(): shutil.rmtree(destination)
            raise
        for relative in cache_folders:
            (destination/relative).mkdir(parents=True, exist_ok=True)
        from project_testing import copy_project_suite
        copy_project_suite(project, destination)
        replace_file(marker, b'Isolated test application managed by shared setup.\n', private=True)
        print(f'{"Refreshed" if refresh else "Prepared"} isolated test application: {destination}')
    else:
        print('Test checkout preserved; make test-sync (or make test-prepare) refreshes it from local source.')
    test = Project(project.directory, 'test', project.root)
    initialize_directories(test)
    prepare_host(test)
    print('Test environment prepared. make test-prepare loads the data set and starts it; make phpunit runs the tests.')
    return test
