"""Omit absent shared config directories without hiding explicit project mounts."""
import hashlib
import json
from pathlib import Path
from project_ide import checked_path, replace_file


def attach_optional_config_mounts(project):
    root = project.directory / 'config'
    pairs = {
        ('php-fpm', '/opt/setup/project'): [root],
    }
    for service, component, common, selected in (
        ('php-fpm', 'php', '/usr/local/etc/php/project.d', '/usr/local/etc/php/project.d.env'),
        ('cron', 'php', '/usr/local/etc/php/project.d', '/usr/local/etc/php/project.d.env'),
        ('webserver', 'apache', '/usr/local/apache2/conf/project', '/usr/local/apache2/conf/project.env'),
        ('nginx-proxy', 'nginx-proxy', '/etc/nginx/project.d', '/etc/nginx/project.d.env'),
        ('database', 'mysql', '/etc/mysql/project.d', '/etc/mysql/project.d.env'),
        ('database', 'mariadb', '/etc/mysql/project.d', '/etc/mysql/project.d.env'),
    ):
        pairs.setdefault((service, common), []).append(root / component)
        pairs.setdefault((service, selected), []).append(root / component / project.environment)
    lines = ['services:']
    for service, config in project.model()['services'].items():
        volumes = config.get('volumes', [])
        kept = []
        for volume in volumes:
            source = Path(volume.get('source', ''))
            optional = (volume.get('type') == 'bind' and volume.get('read_only') is True
                        and source in pairs.get((service, volume['target']), []))
            if optional and not source.exists() and not source.is_symlink():
                continue
            kept.append(volume)
        if len(kept) != len(volumes):
            # Compose interpolates again when reading this overlay; preserve literal dollars.
            lines += [f'  {json.dumps(service)}:',
                      '    volumes: !override ' + json.dumps(kept).replace('$', '$$')]
    if len(lines) == 1:
        return
    contents = ('\n'.join(lines) + '\n').encode()
    digest = hashlib.sha256(contents).hexdigest()[:20]
    path = checked_path(project, f'.generated/optional-mounts-{digest}.yaml')
    # Content-addressed files keep concurrent runner/IDE invocations independent.
    replace_file(path, contents, private=True)
    project.command += ['-f', str(path)]
