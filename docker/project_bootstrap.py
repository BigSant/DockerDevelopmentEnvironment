"""Idempotent local onboarding and a separate test application/data tree."""

import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile

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
        # Generated values are simple hostnames, ports, identifiers and hex tokens.
        if not re.fullmatch(r'[A-Za-z0-9_.:/-]*', str(value)):
            raise ValueError('Invalid generated environment value')
        line = f'{key}={value}'
        pattern = rf'^{re.escape(key)}=.*$'
        contents = re.sub(pattern, lambda m: line, contents, flags=re.M) if re.search(pattern, contents, re.M) else contents.rstrip()+'\n'+line+'\n'
    replace_file(path, contents.encode(), private=True)


def prepare_host(project):
    domain = project.settings.get('DOMAIN', '')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain):
        raise ValueError('Set DOMAIN to a hostname without a port')
    try:
        addresses = {r[4][0] for r in socket.getaddrinfo(domain, None)}
    except socket.gaierror:
        addresses = set()
    if not addresses:
        helper = Path(__file__).with_name('host_entry.py')
        result = subprocess.run(['sudo', '-n', sys.executable, str(helper), domain], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f'Local DNS entry is missing. Run: sudo {sys.executable} {helper} {domain}')
    elif not addresses.issubset({'127.0.0.1', '::1'}):
        raise ValueError('DOMAIN resolves outside localhost; use a local development hostname')
    ssl = project.data_directory / 'ssl'
    ssl.mkdir(mode=0o700, parents=True, exist_ok=True)
    certificate, key = ssl/'domain.crt', ssl/'domain.key'
    valid = False
    if certificate.is_file() and key.is_file():
        checks = [subprocess.run(['openssl', 'x509', '-noout', '-in', str(certificate), *args], capture_output=True)
                  for args in (['-checkend', '2592000'], ['-checkhost', domain])]
        valid = all(r.returncode == 0 for r in checks)
    if not valid:
        if not shutil.which('mkcert'):
            raise ValueError('Install mkcert, then rerun make bootstrap to create local TLS certificates')
        installed = subprocess.run(['mkcert', '-install'], stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        if installed.returncode:
            raise ValueError('Run mkcert -install once to install the local CA, then rerun make bootstrap')
        with tempfile.TemporaryDirectory(dir=ssl) as temporary:
            crt, private = Path(temporary)/'domain.crt', Path(temporary)/'domain.key'
            result = subprocess.run(['mkcert', '-cert-file', str(crt), '-key-file', str(private), domain, '*.'+domain], capture_output=True)
            if result.returncode: raise ValueError('mkcert failed to generate the local certificate')
            replace_file(certificate, crt.read_bytes())
            replace_file(key, private.read_bytes(), private=True)
    print(f'Local hostname and TLS prepared: {domain}')


def bootstrap(project):
    from project import Project
    if project.environment not in ('local', 'test'):
        raise ValueError('bootstrap provisions local/test environments only; production settings must be supplied explicitly')
    values = {}
    private = project.env_files[-1]
    raw = private.read_text()
    if not project.settings['DOMAIN'] or project.settings['DOMAIN'] == 'example.local':
        values['DOMAIN'] = project.settings['PROJECT_NAME'] + ('.test.localhost' if project.environment == 'test' else '.localhost')
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
    print('Bootstrap complete. Use make doctor to check images, then make build / make up as needed.')
    return project


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
    env_file = project.directory / ('env/test.env' if (project.directory/'env/common.env').exists() else '.env.test')
    if not env_file.exists():
        ports = available_ports(project)
        domain = project.settings['PROJECT_NAME']+'.test.localhost'
        set_env_values(env_file, {'DOMAIN':domain, 'LOCALHOST_PORT':str(ports[0]), 'LOCALHOST_PORT_SSL':str(ports[1]),
                                 'DATABASE_NAME':project.settings['DATABASE_NAME']+'_test', 'DATABASE_USER':'test',
                                 'DATABASE_PASSWORD':secrets.token_hex(24), 'COMPOSE_PROFILES':'',
                                 'APP_SOURCE_DIRECTORY':'', 'DATA_DIRECTORY':'',
                                 'SMOKE_URL':f'http://{domain}:{ports[0]}/', 'SQL_DOMAIN':f'{domain}:{ports[0]}'})
    if not destination.exists() or refresh:
        if not shutil.which('rsync'): raise ValueError('Install rsync to create the isolated test application copy')
        if refresh:
            test = Project(project.directory, 'test', project.root)
            test.run(['stop'])
        command = ['rsync', '-a', '--safe-links', '--delete', '--exclude=.git', '--exclude=node_modules',
                   '--exclude=/var/cache', '--exclude=/var/logs', '--exclude=/cache', str(source)+'/', str(destination)+'/']
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
        for relative in ('var/cache', 'var/logs', 'cache'):
            (destination/relative).mkdir(parents=True, exist_ok=True)
        replace_file(marker, b'Isolated test application managed by shared setup.\n', private=True)
        print(f'Prepared isolated test application: {destination}')
    else:
        print('Test checkout preserved; use make test-init refresh=1 to refresh it from local source.')
    test = Project(project.directory, 'test', project.root)
    initialize_directories(test)
    prepare_host(test)
    print('Test environment prepared. Run make db-prepare ENV=test, import a test dump/fixtures, then make up ENV=test.')
    return test
