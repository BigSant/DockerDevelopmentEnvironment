"""Daily operations with argument arrays and explicit application adapters."""
import json
import shlex
from pathlib import PurePosixPath
from project_policy import preflight_php


def selected_service(project, service):
    if service and service not in json.loads(project.capture(['config', '--format', 'json']))['services']:
        raise ValueError('Service is not enabled in this environment: ' + service)
    return [service] if service else []


def restart(project, service=None, timeout=90):
    selected = selected_service(project, service)
    preflight_php(project)
    project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout),
                 '--force-recreate'] + (['--no-deps'] if selected else []) + selected)


def logs(project, service=None, follow=False, tail=100):
    selected = selected_service(project, service)
    project.run(['logs', '--tail', str(tail)] + (['--follow'] if follow else []) + selected)


def package_command(project, tool, command=None, directory='.'):
    selected_service(project, 'php-fpm')
    relative = PurePosixPath(directory)
    if relative.is_absolute() or '..' in relative.parts or '\x00' in directory:
        raise ValueError('dir must be relative to the application source directory, without ..')
    options = ['--workdir', str(PurePosixPath('/var/www/html') / relative)]
    project.run(['exec', '-T', *options, 'php-fpm', tool] + shlex.split(command or '--version'))


def composer(project, command=None, directory='.'):
    package_command(project, 'composer', command, directory)


def npm(project, command=None, directory='.'):
    package_command(project, 'npm', command, directory)


def cache_clear(project):
    selected_service(project, 'php-fpm')
    if project.settings['PROFILE'] in ('ps', 'prestashop'):
        command = ['php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear']
    else:
        command = shlex.split(project.settings.get('CACHE_CLEAR_COMMAND', ''))
        if not command: raise ValueError('Set project CACHE_CLEAR_COMMAND for this application profile')
    project.run(['exec', '-T', 'php-fpm'] + command)
    # OPcache/APCu belong to FPM processes, not the CLI cache-clearing process.
    project.run(['restart', 'php-fpm'])
