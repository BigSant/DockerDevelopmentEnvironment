"""Reject test overrides that reconnect the isolated stack to shared state."""

from pathlib import Path

from project_prestashop import is_prestashop


def validate_test_model(project, model):
    root = (project.directory / '.generated/test').resolve()
    if not root.is_relative_to(project.directory):
        raise ValueError('The test directory must resolve inside the project')
    prefixes = (project.name + '-', project.name + '_')
    for name, service in model.get('services', {}).items():
        container = service.get('container_name', '')
        if container and not container.startswith(prefixes):
            raise ValueError(f'Test service {name}: container_name must use the test project prefix')
        if service.get('network_mode') not in (None, 'none'):
            raise ValueError(f'Test service {name}: use an isolated Compose network')
        if service.get('volumes_from'):
            raise ValueError(f'Test service {name}: shared container volumes are not allowed')
        for volume in service.get('volumes', []):
            if volume.get('type') == 'bind' and not volume.get('read_only', False):
                source = Path(volume['source']).resolve()
                if not source.is_relative_to(root):
                    raise ValueError(f'Test service {name}: writable mount {volume["target"]} must stay inside .generated/test; use :ro for shared configuration/tests')
    for kind in ('networks', 'volumes'):
        for resource in model.get(kind, {}).values():
            if resource.get('external') or not resource.get('name', '').startswith(prefixes):
                raise ValueError(f'Test {kind} must use the test project prefix and cannot be external')
    if getattr(project, 'session', None):
        validate_session_model(project, model)
    php = model.get('services', {}).get('php-fpm', {}).get('environment', {})
    if is_prestashop(project) and php:
        database = model.get('services', {}).get('database', {}).get('environment', {})
        if php.get('DATABASE_HOST') != 'database' or php.get('DATABASE_NAME') != database.get('MYSQL_DATABASE'):
            raise ValueError('Test PrestaShop must connect to its own database service and database name')


# --- setup-server's test sessions (TEST_SESSION=on) ------------------------------------------------------------------
# Host-side code (Makefile, env/, compose files, Dockerfiles) is this tree's, a trusted commit; what the caller chose
# (specs, their configuration, the shop code) lives in absolute data directories outside it. ENV=test is confined from
# its rendered model: every network internal, no published port, no extra host, no Docker socket, every service with
# few capabilities, no-new-privileges and memory, swap and process limits; Playwright with a read-only root.
SESSION_KEYS = {'tests': 'E2E_TESTS_DIRECTORY', 'config': 'E2E_CONFIG_DIRECTORY', 'app': 'TEST_APP_SOURCE'}
# What the services' entrypoints need as root before they drop to their own user (chown, gosu, binding :80).
SESSION_CAPABILITIES = ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'FSETID', 'KILL', 'NET_BIND_SERVICE', 'SETGID', 'SETUID')
SESSION_PIDS, PLAYWRIGHT_PIDS = 512, 1024
SESSION_DEFAULT_MEMORY = 512 * 1024 * 1024
PLAYWRIGHT_TMPFS = '/tmp:size=256m,mode=1777'


def session_directories(project, settings, environment):
    """{tests, config, app: Path} of a test session (TEST_SESSION=on in ENV=local/test), else None. Each an absolute
    real directory outside the project directory (so outside every build context and compose file of the tree)."""
    flag = str(settings.get('TEST_SESSION') or '')
    if flag in ('', 'off'):
        return None
    if flag != 'on':
        raise ValueError('TEST_SESSION must be on or off')
    if environment not in ('local', 'test'):
        raise ValueError('TEST_SESSION=on is for a test session\'s ENV=local and ENV=test only')
    found = {}
    for name, key in SESSION_KEYS.items():
        value = str(settings.get(key) or '')
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or path.is_symlink() or not path.is_dir():
            raise ValueError(f'TEST_SESSION=on: {key} must be an existing absolute directory, not a symlink')
        path = path.resolve()
        if path.is_relative_to(project.directory) or project.directory.is_relative_to(path):
            raise ValueError(f'TEST_SESSION=on: {key} must lie outside {project.directory}')
        found[name] = path
    return found


def client_directories(project):
    """Where a session's caller-chosen content is: its data directories and ENV=test's copy of the shop."""
    return [*project.session.values(), (project.directory / '.generated/test').resolve()]


def check_sources(project):
    """No compose file of a session comes from a client-writable directory."""
    command = project.command
    for index, item in enumerate(command):
        if item == '-f' and index + 1 < len(command):
            path = Path(command[index + 1]).resolve()
            generated = path.parent == (project.directory / '.generated').resolve() or \
                path == (project.directory / '.generated/test/session.yml').resolve()
            if any(path.is_relative_to(place) for place in client_directories(project)) and not generated:
                raise ValueError(f'TEST_SESSION=on: compose file {path} lies in a client-writable directory')


def memory_bytes(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def session_overlay(project, model):
    """.generated/test/session.yml: the confinement of every service and network of the rendered ENV=test model."""
    lines = ['# Generated by setup for a test session (TEST_SESSION=on) from the rendered model; do not edit.', 'services:']
    for name, service in sorted(model.get('services', {}).items()):
        memory = memory_bytes(service.get('mem_limit')) or SESSION_DEFAULT_MEMORY
        playwright = name == 'playwright'
        lines += [f'  {json_name(name)}:', '    ports: !reset []', '    extra_hosts: !reset []', '    privileged: false',
                  '    cap_drop: !override ["ALL"]',
                  '    cap_add: ' + ('!reset []' if playwright else '!override ' + list_text(SESSION_CAPABILITIES)),
                  '    security_opt: !override ["no-new-privileges:true"]',
                  f'    pids_limit: {PLAYWRIGHT_PIDS if playwright else SESSION_PIDS}',
                  f'    mem_limit: {memory}', f'    memswap_limit: {memory}']
        if playwright:
            lines += ['    read_only: true', f'    tmpfs: !override ["{PLAYWRIGHT_TMPFS}"]']
    networks = sorted(model.get('networks', {}))
    if networks:
        lines.append('networks:')
        for name in networks:
            lines += [f'  {json_name(name)}:', '    internal: true']
    target = project.directory / '.generated/test/session.yml'
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = target.with_name('.session.yml.tmp')
    temporary.write_text('\n'.join(lines) + '\n')
    temporary.replace(target)
    return target


def json_name(name):
    import json
    return json.dumps(name)


def list_text(items):
    import json
    return json.dumps(list(items))


def validate_session_model(project, model):
    """A test session's rendered ENV=test model, refused unless confined (see session_overlay)."""
    allowed = set(SESSION_CAPABILITIES)
    clients = client_directories(project)
    trusted = [project.root.resolve(), Path(project.process_env['ROOT_DIRECTORY']).resolve().parent, *project.session.values()]
    for name, network in model.get('networks', {}).items():
        if network.get('internal') is not True:
            raise ValueError(f'Test session: network {name} must be internal')
    for name, service in model.get('services', {}).items():
        where = f'Test session service {name}'
        if service.get('ports'):
            raise ValueError(f'{where}: publishes ports')
        if service.get('privileged'):
            raise ValueError(f'{where}: privileged')
        for key in ('pid', 'ipc', 'userns_mode', 'uts', 'cgroup'):
            if service.get(key) == 'host':
                raise ValueError(f'{where}: {key}: host')
        if service.get('devices') or service.get('device_cgroup_rules'):
            raise ValueError(f'{where}: devices')
        if 'ALL' not in (service.get('cap_drop') or []) or not set(service.get('cap_add') or []) <= allowed:
            raise ValueError(f'{where}: capabilities beyond {" ".join(sorted(allowed))}')
        options = service.get('security_opt') or []
        if 'no-new-privileges:true' not in options or any('unconfined' in item for item in options):
            raise ValueError(f'{where}: needs no-new-privileges and its default confinement')
        if not service.get('pids_limit') or not service.get('mem_limit') or \
                str(service.get('memswap_limit')) != str(service.get('mem_limit')):
            raise ValueError(f'{where}: needs pids, memory and swap limits')
        hosts = service.get('extra_hosts') or {}
        if any('host-gateway' in str(item) for item in (hosts.values() if isinstance(hosts, dict) else hosts)):
            raise ValueError(f'{where}: host-gateway')
        if name == 'playwright' and service.get('read_only') is not True:
            raise ValueError(f'{where}: needs a read-only root')
        build = service.get('build') or {}
        context = build.get('context') if isinstance(build, dict) else None
        if context and any(Path(context).resolve().is_relative_to(place) for place in clients):
            raise ValueError(f'{where}: builds from a client-writable directory')
        for item in service.get('env_file') or []:
            path = Path(item.get('path') if isinstance(item, dict) else item).resolve()
            if any(path.is_relative_to(place) for place in clients):
                raise ValueError(f'{where}: env_file from a client-writable directory')
        for volume in service.get('volumes', []):
            if volume.get('type') != 'bind':
                continue
            source = Path(volume['source']).resolve()
            if 'docker.sock' in source.name or not any(source.is_relative_to(place) for place in trusted):
                raise ValueError(f'Test session service {name}: mounts {source} from outside the session')
            if any(source.is_relative_to(place) for place in project.session.values()) and not volume.get('read_only'):
                raise ValueError(f'Test session service {name}: a session data directory is mounted writable')
