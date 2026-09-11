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
    'PS_DEBUG_MODE': '', 'PS_DEBUG_IPS': '',
    'PHP_CONTAINER_MEMORY': '1g', 'DB_CONTAINER_MEMORY': '2g',
    'WEB_CONTAINER_MEMORY': '256m', 'CONTAINER_CPUS': '2',
    'CONTAINER_LOG_MAX_SIZE': '10m', 'CONTAINER_LOG_MAX_FILES': '3',
    'MIN_FREE_DISK_MB': '1024', 'BACKUP_KEEP_LAST': '5',
}


def choice(settings, name, allowed):
    value = settings[name]
    if value not in allowed:
        raise ValueError(f'{name} must be one of: {", ".join(allowed)}')
    return value


def resolve_policy(settings, environment):
    values = {key: str(settings.get(key, default)) for key, default in DEFAULTS.items()}
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
    if choice(values, 'MAIL_MODE', ('auto', 'off', 'preserve')) == 'auto':
        values['MAIL_MODE'] = 'preserve' if environment == 'prod' else 'off'
    if choice(values, 'HTTP_FORCE_HTTPS', ('auto', 'off', 'on')) == 'auto':
        values['HTTP_FORCE_HTTPS'] = 'off' if development else 'on'
    for name in ('PHP_OPCACHE_REVALIDATE_FREQ', 'MIN_FREE_DISK_MB', 'BACKUP_KEEP_LAST', 'CONTAINER_LOG_MAX_FILES'):
        if not re.fullmatch(r'\d+', values[name]) or int(values[name]) > 1000000:
            raise ValueError(f'{name} must be an integer between 0 and 1000000')
    if int(values['BACKUP_KEEP_LAST']) < 1 or int(values['CONTAINER_LOG_MAX_FILES']) < 1:
        raise ValueError('BACKUP_KEEP_LAST and CONTAINER_LOG_MAX_FILES must be positive')
    for name in ('PHP_CONTAINER_MEMORY', 'DB_CONTAINER_MEMORY', 'WEB_CONTAINER_MEMORY', 'CONTAINER_LOG_MAX_SIZE'):
        if not re.fullmatch(r'[1-9]\d*[kmg]', values[name]):
            raise ValueError(f'{name} must be a positive size such as 256m or 2g')
    if not re.fullmatch(r'\d+(?:\.\d+)?', values['CONTAINER_CPUS']) or not 0 < float(values['CONTAINER_CPUS']) <= 1024:
        raise ValueError('CONTAINER_CPUS must be greater than 0 and at most 1024')
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
        if not any((root / path).is_file() for path in ('app/config/parameters.php', 'config/settings.inc.php')):
            raise ValueError('Restore the PS shop configuration and keys before starting: parameters.php or PS 1.6 settings.inc.php')
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
