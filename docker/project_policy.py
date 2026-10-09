"""Resolve independent runtime switches; environment identity never follows cache mode."""
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

# Defaults are resolved once, before service Compose interpolation.
DEFAULTS = {
    'CACHE_MODE': 'auto', 'PHP_OPCACHE': 'auto', 'PHP_APCU': 'auto',
    'PHP_OPCACHE_VALIDATE_TIMESTAMPS': 'auto', 'PHP_OPCACHE_REVALIDATE_FREQ': '0',
    'PS_SMARTY_CACHE': 'auto', 'PS_SMARTY_COMPILE': 'auto', 'PS_ASSET_CACHE': 'auto',
    'PS_OBJECT_CACHE': 'auto', 'MAIL_MODE': 'auto', 'HTTP_FORCE_HTTPS': 'auto',
    # After a DB import: shop SSL flag, and the external integrations left enabled (local/test/stage).
    'PS_SSL': 'on', 'INTEGRATIONS_ENABLED': '',
    'PS_DEBUG_MODE': '', 'PS_DEBUG_IPS': '',
    # One time zone for PHP, the database, web servers and cron.
    'TZ': 'Europe/Vilnius',
    # Xdebug 3 mode (local/test images only); empty resolves to debug there.
    'XDEBUG_MODE': '',
    'CONTAINER_LOG_MAX_SIZE': '10m', 'CONTAINER_LOG_MAX_FILES': '3',
    'MIN_FREE_DISK_MB': '1024', 'BACKUP_KEEP_LAST': '5',
    # Empty pool sizes are derived from PHP_MEMORY; explicit values win.
    'PHP_FPM_MAX_CHILDREN': '',
    'PHP_FPM_START_SERVERS': '',
    'PHP_FPM_MIN_SPARE_SERVERS': '',
    'PHP_FPM_MAX_SPARE_SERVERS': '',
    'PHP_FPM_MAX_REQUESTS': '500',
    'PHP_FPM_PROCESS_MEMORY': '128m',
    'DATABASE_BUFFER_POOL': '',
    # Query statistics for the DB monitor; off saves several hundred MB of database memory.
    'DATABASE_PERFORMANCE_SCHEMA': 'off',
    'MEMCACHED_CACHE_MB': '',
    'REDIS_MAXMEMORY_MB': '',
    # Where nginx-proxy publishes its ports: loopback behind DirectAdmin / the host Nginx,
    # or one interface address (e.g. a VPN) that clients reach directly.
    'PUBLISH_ADDRESS': '127.0.0.1',
}

# <SERVICE>_MEMORY / <SERVICE>_CPUS with local-machine defaults; stage/prod set them per server.
RESOURCES = {
    'PHP': ('1g', '2'), 'DATABASE': ('2g', '2'), 'WEBSERVER': ('256m', '1'), 'PROXY': ('128m', '0.5'),
    'CRON': ('512m', '1'), 'MEMCACHED': ('256m', '0.5'), 'REDIS': ('256m', '0.5'), 'PMA': ('256m', '0.5'),
    'MAILPIT': ('256m', '0.5'), 'DBMONITOR': ('128m', '0.25'), 'METRICS': ('128m', '0.25'),
}
REMOTE_REQUIRED = ('PHP', 'DATABASE', 'WEBSERVER', 'PROXY')
# Previous shared names remain accepted as fallbacks during the transition.
MEMORY_ALIASES = {
    'PHP': 'PHP_CONTAINER_MEMORY', 'CRON': 'PHP_CONTAINER_MEMORY', 'PMA': 'PHP_CONTAINER_MEMORY',
    'DATABASE': 'DB_CONTAINER_MEMORY', 'WEBSERVER': 'WEB_CONTAINER_MEMORY', 'PROXY': 'WEB_CONTAINER_MEMORY',
    'MAILPIT': 'WEB_CONTAINER_MEMORY', 'MEMCACHED': 'MEMCACHED_CONTAINER_MEMORY',
}
LEGACY_RESOURCE_KEYS = ('PHP_CONTAINER_MEMORY', 'DB_CONTAINER_MEMORY', 'WEB_CONTAINER_MEMORY',
                        'MEMCACHED_CONTAINER_MEMORY', 'CONTAINER_CPUS')
for _name, (_memory, _cpus) in RESOURCES.items():
    DEFAULTS[f'{_name}_MEMORY'] = ''
    DEFAULTS[f'{_name}_CPUS'] = ''


class MissingLimits(ValueError):
    def __init__(self, environment, missing):
        self.environment, self.missing = environment, missing
        super().__init__(f'{environment} requires explicit container limits: {", ".join(missing)}')


def megabytes(size):
    match = re.fullmatch(r'([1-9]\d*)([kmg])', size)
    if not match:
        raise ValueError(f'Invalid size {size!r}; use a value such as 256m or 2g')
    return int(match[1]) * {'k': 1 / 1024, 'm': 1, 'g': 1024}[match[2]]


def resolve_resources(settings, values, environment):
    """Container limits per service and the internal sizes derived from them."""
    get = lambda key: str(settings.get(key) or '')
    missing = []
    legacy_cache = ''
    for name, (memory, cpus) in RESOURCES.items():
        explicit_memory = get(f'{name}_MEMORY') or get(MEMORY_ALIASES.get(name, ''))
        if name == 'MEMCACHED' and re.fullmatch(r'\d+', explicit_memory):
            # The former MEMCACHED_MEMORY was the cache size in MB, not a container limit.
            legacy_cache, explicit_memory = explicit_memory, get('MEMCACHED_CONTAINER_MEMORY')
        explicit_cpus = get(f'{name}_CPUS') or get('CONTAINER_CPUS')
        if environment in ('stage', 'prod') and name in REMOTE_REQUIRED:
            missing += [f'{name}_{kind}' for kind, value in (('MEMORY', explicit_memory), ('CPUS', explicit_cpus)) if not value]
        values[f'{name}_MEMORY'] = explicit_memory or memory
        values[f'{name}_CPUS'] = explicit_cpus or cpus
    if missing:
        raise MissingLimits(environment, missing)
    for name in RESOURCES:
        megabytes(values[f'{name}_MEMORY'])
        cpus = values[f'{name}_CPUS']
        if not re.fullmatch(r'\d+(?:\.\d+)?', cpus) or not 0 < float(cpus) <= 1024:
            raise ValueError(f'{name}_CPUS must be greater than 0 and at most 1024')
    if not values['DATABASE_BUFFER_POOL']:
        values['DATABASE_BUFFER_POOL'] = f"{max(64, int(megabytes(values['DATABASE_MEMORY']) * 0.6))}M"
    if not re.fullmatch(r'[1-9]\d*[KMG]?', values['DATABASE_BUFFER_POOL']):
        raise ValueError('DATABASE_BUFFER_POOL must be a size such as 1228M or 12G')
    values['MEMCACHED_CACHE_MB'] = values['MEMCACHED_CACHE_MB'] or legacy_cache or str(
        max(16, int(megabytes(values['MEMCACHED_MEMORY']) * 0.9)))
    if not re.fullmatch(r'[1-9]\d*', values['MEMCACHED_CACHE_MB']):
        raise ValueError('MEMCACHED_CACHE_MB must be a positive number of megabytes')
    values['REDIS_MAXMEMORY_MB'] = values['REDIS_MAXMEMORY_MB'] or str(max(16, int(megabytes(values['REDIS_MEMORY']) * 0.9)))
    if not re.fullmatch(r'[1-9]\d*', values['REDIS_MAXMEMORY_MB']):
        raise ValueError('REDIS_MAXMEMORY_MB must be a positive number of megabytes')
    process = megabytes(values['PHP_FPM_PROCESS_MEMORY'])
    children = values['PHP_FPM_MAX_CHILDREN'] or str(
        min(256, max(2, int((megabytes(values['PHP_MEMORY']) - 128) // process))))
    if re.fullmatch(r'\d+', children):
        count = int(children)
        spare = max(1, count // 4)
        defaults = {'PHP_FPM_MAX_CHILDREN': children, 'PHP_FPM_START_SERVERS': str(spare),
                    'PHP_FPM_MIN_SPARE_SERVERS': str(spare), 'PHP_FPM_MAX_SPARE_SERVERS': str(max(spare, count // 2))}
        for key, value in defaults.items():
            values[key] = values[key] or value
    return values


def suggested_limits(total_bytes, cpus):
    """Starting limits for a server dedicated to one project (divide when shared)."""
    total = total_bytes / 1024 ** 2
    size = lambda share, floor: f'{max(floor, int(total * share // 64 * 64))}m'
    cpu = lambda share, floor: f'{max(floor, round(cpus * share * 2) / 2):g}'
    return {'PHP_MEMORY': size(0.30, 512), 'PHP_CPUS': cpu(0.5, 1),
            'DATABASE_MEMORY': size(0.45, 1024), 'DATABASE_CPUS': cpu(0.5, 1),
            'WEBSERVER_MEMORY': size(0.04, 256), 'WEBSERVER_CPUS': cpu(0.25, 0.5),
            'PROXY_MEMORY': size(0.02, 128), 'PROXY_CPUS': cpu(0.125, 0.5)}


def choice(settings, name, allowed):
    value = settings[name]
    if value not in allowed:
        raise ValueError(f'{name} must be one of: {", ".join(allowed)}')
    return value


def publish_address(value):
    """'proxy' for loopback (a host proxy forwards clients), 'direct' for an interface clients reach themselves."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is None or address.is_unspecified or '%' in value:
        raise ValueError(f'PUBLISH_ADDRESS must be one IPv4 or IPv6 address of this host such as 127.0.0.1 or '
                         f'a VPN interface address, without port, CIDR or zone (0.0.0.0/:: would publish on '
                         f'every interface); got {value!r}')
    return 'proxy' if address.is_loopback else 'direct'


def resolve_policy(settings, environment):
    # An empty setting means 'not set': the default applies.
    values = {key: str(settings.get(key) or default) for key, default in DEFAULTS.items()}
    development = environment in ('local', 'test')
    cache = choice(values, 'CACHE_MODE', ('auto', 'off', 'on'))
    values['CACHE_MODE'] = ('off' if development else 'on') if cache == 'auto' else cache
    for name in ('PHP_OPCACHE', 'PHP_APCU', 'PS_SMARTY_CACHE', 'PS_ASSET_CACHE'):
        if choice(values, name, ('auto', 'off', 'on')) == 'auto':
            values[name] = values['CACHE_MODE']
    if choice(values, 'PHP_OPCACHE_VALIDATE_TIMESTAMPS', ('auto', 'off', 'on')) == 'auto':
        values['PHP_OPCACHE_VALIDATE_TIMESTAMPS'] = 'on' if development else 'off'
    if choice(values, 'PS_SMARTY_COMPILE', ('auto', 'never', 'check', 'always')) == 'auto':
        values['PS_SMARTY_COMPILE'] = 'check' if values['CACHE_MODE'] == 'off' else 'never'
    if choice(values, 'PS_OBJECT_CACHE', ('auto', 'preserve', 'off', 'on')) == 'auto':
        values['PS_OBJECT_CACHE'] = 'off' if values['CACHE_MODE'] == 'off' else 'preserve'
    if choice(values, 'MAIL_MODE', ('auto', 'off', 'preserve', 'mailpit')) == 'auto':
        mailpit = 'mailpit' in str(settings.get('SERVICES') or '').split()
        values['MAIL_MODE'] = 'preserve' if environment == 'prod' else 'mailpit' if mailpit else 'off'
    elif values['MAIL_MODE'] == 'mailpit' and 'mailpit' not in str(settings.get('SERVICES') or '').split():
        raise ValueError('MAIL_MODE=mailpit requires mailpit in SERVICES')
    choice(values, 'PS_SSL', ('on', 'off'))
    choice(values, 'DATABASE_PERFORMANCE_SCHEMA', ('on', 'off'))
    if not re.fullmatch(r'[a-z0-9_-]*(?: [a-z0-9_-]+)*', ' '.join(values['INTEGRATIONS_ENABLED'].split())):
        raise ValueError('INTEGRATIONS_ENABLED lists integration names (letters, digits, - and _) separated by spaces')
    if choice(values, 'HTTP_FORCE_HTTPS', ('auto', 'off', 'on')) == 'auto':
        values['HTTP_FORCE_HTTPS'] = 'off' if development else 'on'
    for name in ('PHP_OPCACHE_REVALIDATE_FREQ', 'MIN_FREE_DISK_MB', 'BACKUP_KEEP_LAST', 'CONTAINER_LOG_MAX_FILES'):
        if not re.fullmatch(r'\d+', values[name]) or int(values[name]) > 1000000:
            raise ValueError(f'{name} must be an integer between 0 and 1000000')
    if int(values['BACKUP_KEEP_LAST']) < 1 or int(values['CONTAINER_LOG_MAX_FILES']) < 1:
        raise ValueError('BACKUP_KEEP_LAST and CONTAINER_LOG_MAX_FILES must be positive')
    if not re.fullmatch(r'[1-9]\d*[kmg]', values['CONTAINER_LOG_MAX_SIZE']):
        raise ValueError('CONTAINER_LOG_MAX_SIZE must be a positive size such as 10m')
    for name in LEGACY_RESOURCE_KEYS[:-1]:
        if settings.get(name):
            megabytes(str(settings[name]))
    if settings.get('CONTAINER_CPUS') and (not re.fullmatch(r'\d+(?:\.\d+)?', str(settings['CONTAINER_CPUS']))
                                           or not 0 < float(settings['CONTAINER_CPUS']) <= 1024):
        raise ValueError('CONTAINER_CPUS must be greater than 0 and at most 1024')
    values['SETUP_DIRECT_CLIENTS'] = '1' if publish_address(values['PUBLISH_ADDRESS']) == 'direct' else '0'
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+)*', values['TZ']):
        raise ValueError('TZ must be a zone name such as Europe/Vilnius or UTC')
    if development:
        values['XDEBUG_MODE'] = values['XDEBUG_MODE'] or 'debug'
        modes = values['XDEBUG_MODE'].split(',')
        allowed = {'off', 'develop', 'coverage', 'debug', 'gcstats', 'profile', 'trace'}
        if not set(modes) <= allowed or ('off' in modes and len(modes) > 1):
            raise ValueError('XDEBUG_MODE must be off or a comma list of: develop, coverage, debug, gcstats, profile, trace')
    elif values['XDEBUG_MODE']:
        raise ValueError('XDEBUG_MODE applies to local/test only; stage/prod images have no Xdebug')
    resolve_resources(settings, values, environment)
    for name in ('PHP_FPM_MAX_CHILDREN', 'PHP_FPM_START_SERVERS', 'PHP_FPM_MIN_SPARE_SERVERS',
                 'PHP_FPM_MAX_SPARE_SERVERS', 'PHP_FPM_MAX_REQUESTS'):
        if not re.fullmatch(r'\d+', values[name]) or not 0 <= int(values[name]) <= 100000:
            raise ValueError(f'{name} must be an integer between 0 and 100000')
        if name != 'PHP_FPM_MAX_REQUESTS' and int(values[name]) == 0:
            raise ValueError(f'{name} must be positive')
    minimum, start, maximum, children = (int(values[name]) for name in (
        'PHP_FPM_MIN_SPARE_SERVERS', 'PHP_FPM_START_SERVERS',
        'PHP_FPM_MAX_SPARE_SERVERS', 'PHP_FPM_MAX_CHILDREN'))
    if not minimum <= start <= maximum <= children:
        raise ValueError('FPM requires MIN_SPARE_SERVERS <= START_SERVERS <= MAX_SPARE_SERVERS <= MAX_CHILDREN')
    if settings.get('PROFILE') in ('ps', 'prestashop'):
        mode = choice(values, 'PS_DEBUG_MODE', ('', 'off', 'on', 'ip'))
        ips = values['PS_DEBUG_IPS'].strip()
        if mode:
            try:
                for ip in ips.split(',') if ips else []:
                    if '%' in ip: raise ValueError()
                    ipaddress.ip_address(ip.strip())
            except ValueError:
                raise ValueError('PS_DEBUG_IPS requires comma-separated IPv4/IPv6 addresses without ports or CIDR') from None
            if mode == 'ip' and not ips:
                raise ValueError('PS_DEBUG_MODE=ip requires PS_DEBUG_IPS')
    return values


def validate_configuration(project):
    """Read-only checks, including optional project-owned service env contracts."""
    model = json.loads(project.capture(['config', '--format', 'json']))
    services = model['services']
    php = services.get('php-fpm', {}).get('environment', {})
    if project.settings.get('PROFILE') in ('ps', 'prestashop'):
        for name in ('DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD'):
            if not php.get(name): raise ValueError(f'PHP runtime requires {name}')
        if not str(php['DATABASE_PORT']).isdigit() or not 1 <= int(php['DATABASE_PORT']) <= 65535:
            raise ValueError('APP_DATABASE_PORT must be between 1 and 65535')
        root = project.web_directory
        version = project.settings.get('PS_VERSION', '')
        if not version:
            raise ValueError('Set PS_VERSION in env/common.env (the PrestaShop version of the shop code)')
        from project_prestashop import config_file, required_keys
        if project.settings.get('SETUP_COMPATIBILITY_CHECK', '') != 'off':
            from compatibility import check
            engine = 'mariadb' if services.get('database', {}).get('image', '').startswith('mariadb-') else 'mysql'
            errors, warnings = check(version, project.settings['PHP_VERSION'], engine,
                                     project.settings.get('MARIADB_VERSION' if engine == 'mariadb' else 'MYSQL_VERSION', ''))
            for warning in warnings:
                print(f'Warning: {warning}')
            if errors:
                raise ValueError('; '.join(errors) + ' (SETUP_COMPATIBILITY_CHECK=off skips this for forks)')
        if not config_file(root, version).is_file():
            missing = [key for key in required_keys(version) if not php.get(key)]
            if missing:
                raise ValueError(f"No {config_file(root, version).relative_to(root)} yet and no keys to create it: "
                                 f"set {', '.join(missing)} (make bootstrap generates them locally)")
        if php.get('PS_DEBUG_MODE') and not (root / 'config/defines.inc.php').is_file():
            raise ValueError('Restore config/defines.inc.php before setting PS_DEBUG_MODE')
    file = project.directory / 'config/environment.json'
    if file.is_symlink(): raise ValueError('config/environment.json must not be a symlink')
    if file.exists():
        contracts = json.loads(file.read_text())
        if not isinstance(contracts, dict): raise ValueError('Environment contract must be an object of service names')
        for service, rules in contracts.items():
            if service not in services: continue  # Optional profiles are validated when selected.
            if not isinstance(rules, dict): raise ValueError('Environment service contract must be an object')
            for name, rule in rules.items():
                if not isinstance(rule, dict) or set(rule) - {'required', 'type', 'choices'}:
                    raise ValueError(f'Invalid environment contract rule: {service}/{name}')
                if ('required' in rule and not isinstance(rule['required'], bool)) or ('choices' in rule and
                        (not isinstance(rule['choices'], list) or any(not isinstance(v, str) for v in rule['choices']))):
                    raise ValueError(f'Invalid environment contract rule: {service}/{name}')
                kind = rule.get('type', 'string')
                if kind not in ('string', 'integer', 'boolean', 'url'):
                    raise ValueError(f'Unknown environment contract type: {service}/{name}')
                value = services[service].get('environment', {}).get(name)
                if value in (None, ''):
                    if rule.get('required', False): raise ValueError(f'Missing required environment key: {service}/{name}')
                    continue
                value = str(value)
                valid = kind == 'string' or (kind == 'integer' and re.fullmatch(r'-?\d+', value))
                valid = valid or (kind == 'boolean' and value in ('0', '1', 'true', 'false'))
                if kind == 'url':
                    parsed = urlsplit(value)
                    valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname)
                if not valid or ('choices' in rule and value not in rule['choices']):
                    raise ValueError(f'Invalid environment value: {service}/{name}')
    return model


def preflight_php(project):
    validate_configuration(project)
    if project.settings.get('PROFILE') in ('ps', 'prestashop'):
        project.capture(['run', '--rm', '--pull', 'never', '--no-deps', '--entrypoint', 'php', 'php-fpm',
                         '/opt/setup/runtime/prestashop-command.php', 'validate'])
