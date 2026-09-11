"""Reject test overrides that reconnect the isolated stack to shared state."""

from pathlib import Path


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
    php = model.get('services', {}).get('php-fpm', {}).get('environment', {})
    if project.settings.get('PROFILE') in ('ps', 'prestashop') and php:
        database = model.get('services', {}).get('database', {}).get('environment', {})
        if php.get('DATABASE_HOST') != 'database' or php.get('DATABASE_NAME') != database.get('MYSQL_DATABASE'):
            raise ValueError('Test PrestaShop must connect to its own database service and database name')
