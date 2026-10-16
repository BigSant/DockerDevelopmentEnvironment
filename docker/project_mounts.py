"""Omit absent project configuration directories instead of letting Compose create them."""
import json
from pathlib import Path
from project_ide import write_overlay


def attach_optional_config_mounts(project):
    """CORE configuration mounts are optional: a missing source is not mounted.

    Compose would otherwise create the absent source as an empty root-owned
    directory. Only the CORE default (service, target, source) mounts qualify;
    a mount a project adds explicitly stays required even when its source is
    missing. Data, application and setup runtime mounts are never touched.
    """
    root = project.directory / 'config'
    application = project.root / 'app/config'
    defaults = {
        ('php-fpm', '/opt/setup/project'): {root},
        ('cron', '/etc/cron.d'): {application / 'cron'},
        ('metrics', '/host/cron-config'): {application / 'cron'},
        ('metrics', '/host/metrics-config'): {application / 'metrics'},
        ('backup', '/opt/setup/project-database'): {Path(project.process_env['PROJECT_DATABASE_DIRECTORY'])},
        ('php-phpstan', '/tmp/phpstan/config'): {application / 'phpstan'},
        ('php-cs', '/tmp/php-cs-fixer/config'): {application / 'php-cs'},
        ('playwright', '/e2e/config'): {application / 'playwright'},
        ('pma', '/etc/phpmyadmin/conf.d'): {root / 'pma'},
    }
    for service, component, common, selected in (
        ('php-fpm', 'php', '/usr/local/etc/php/project.d', '/usr/local/etc/php/project.d.env'),
        ('cron', 'php', '/usr/local/etc/php/project.d', '/usr/local/etc/php/project.d.env'),
        ('webserver', 'apache', '/usr/local/apache2/conf/project', '/usr/local/apache2/conf/project.env'),
        ('nginx-proxy', 'nginx-proxy', '/etc/nginx/project.d', '/etc/nginx/project.d.env'),
        ('database', 'mysql', '/etc/mysql/project.d', '/etc/mysql/project.d.env'),
        ('database', 'mariadb', '/etc/mysql/project.d', '/etc/mysql/project.d.env'),
        ('redis', 'redis', '/usr/local/etc/redis/project.d', '/usr/local/etc/redis/project.d.env'),
    ):
        defaults.setdefault((service, common), set()).add(root / component)
        defaults.setdefault((service, selected), set()).add(root / component / project.environment)
    optional = lambda service, volume: Path(volume.get('source', '')) in defaults.get((service, volume.get('target')), ())
    lines = ['services:']
    for service, config in project.model()['services'].items():
        volumes = config.get('volumes', [])
        kept = []
        for volume in volumes:
            source = Path(volume.get('source', ''))
            if (volume.get('type') == 'bind' and optional(service, volume)
                    and not source.exists() and not source.is_symlink()):
                continue
            kept.append(volume)
        if len(kept) != len(volumes):
            # Compose interpolates again when reading this overlay; preserve literal dollars.
            lines += [f'  {json.dumps(service)}:',
                      '    volumes: !override ' + json.dumps(kept).replace('$', '$$')]
    if len(lines) == 1:
        return
    project.command += ['-f', str(write_overlay(project, 'optional-mounts', ('\n'.join(lines) + '\n').encode()))]
