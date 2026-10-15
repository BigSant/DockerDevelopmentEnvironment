"""Daily operations with argument arrays and explicit application adapters."""
import shlex
from pathlib import PurePosixPath
from project_policy import preflight_php
from project_prestashop import is_prestashop


def selected_service(project, service):
    if service and service not in project.active_model()['services']:
        raise ValueError('Service is not enabled in this environment: ' + service)
    return [service] if service else []


def restart(project, service=None, timeout=90):
    selected = selected_service(project, service)
    preflight_php(project)
    project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout),
                 '--force-recreate'] + (['--no-deps'] if selected else []) + selected)
    from project_prestashop import ensure_htaccess
    ensure_htaccess(project)


def logs(project, service=None, follow=False, tail=100, day=None):
    """docker logs of the running containers; with day (YYYY-MM-DD, today, yesterday) the log collector's files of
    that day instead, which outlive the containers (SERVICES=metrics)."""
    selected = selected_service(project, service)
    if not day:
        project.run(['logs', '--tail', str(tail)] + (['--follow'] if follow else []) + selected)
        return
    if 'metrics-logs' not in project.active_model()['services']:
        raise ValueError('make logs day=... reads the log collector, which runs with SERVICES=metrics')
    if follow:
        raise ValueError('make logs day=... prints a day; follow=1 is for docker logs')
    project.run(['exec', '-T', 'metrics-logs', 'php', '/opt/setup/runtime/metrics/log-collector.php', '--print', day] + selected)


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
    if is_prestashop(project):
        command = ['php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear']
    else:
        command = shlex.split(project.settings.get('CACHE_CLEAR_COMMAND', ''))
        if not command: raise ValueError('Set project CACHE_CLEAR_COMMAND for this application profile')
    project.run(['exec', '-T', 'php-fpm'] + command)
    # OPcache/APCu belong to FPM processes, not the CLI cache-clearing process.
    project.run(['restart', 'php-fpm'])


# QA tools: (service, profile, entrypoint, default command); e2e's entrypoint is the command's first word.
QA_TOOLS = {'phpstan': ('php-phpstan', 'phpstan', 'make', 'report'), 'phpcs': ('php-cs', 'phpcs', 'make', 'check'),
            'e2e': ('playwright', 'playwright', None, None)}


def run_qa(project, action, command=None):
    """make phpstan / phpcs / e2e [command=...]: the tool's container, its default command unless one is given.
    End-to-end tests write orders and customers, so e2e only ever runs against ENV=test."""
    service, profile, entrypoint, default = QA_TOOLS[action]
    if action == 'e2e':
        from project_testing import test_project
        default = project.settings['PLAYWRIGHT_COMMAND']  # The setting of the environment make was run in.
        project = test_project(project)
    arguments = shlex.split(command or default)
    if not arguments or not arguments[0]:
        raise ValueError('QA command cannot be empty')
    if entrypoint:
        options = ['--no-deps', '--entrypoint', entrypoint]
    else:
        # The shared Playwright service otherwise starts `tail -f /dev/null`.
        options = ['--entrypoint', arguments.pop(0)]
    project.run(['run', '--rm', *options, service, *arguments], profiles=profile)
