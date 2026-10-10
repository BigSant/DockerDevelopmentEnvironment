"""Pass the application's own settings (APP_ENVIRONMENT) into the containers that run its code.

Env files feed Compose interpolation only; a value reaches a container when a service lists it.
APP_ENVIRONMENT names those values once (names, never values) and setup lists each as
`NAME: ${NAME-}` in every service that runs the application. Compose fills in the value from the
env files or, for a name they do not set, the process environment (Bitbucket Deployment variables),
so the generated overlay holds references only and the image fingerprints do not change.
"""

import hashlib
import re

from project_ide import checked_path, replace_file

# The web PHP (PHPUnit runs in it too, through exec) and the cron jobs; cron only when enabled.
APPLICATION_SERVICES = ('php-fpm', 'cron')
NAME = re.compile(r'[A-Z][A-Z0-9_]*')
# Setup sets these in the application containers or computes them for Compose; a copy from
# APP_ENVIRONMENT would replace setup's value without a word. Every setup setting
# (project-settings.yaml) is refused as well, see reserved_names().
RESERVED_NAMES = frozenset({
    'ENV', 'PROFILE', 'TZ', 'DOMAIN', 'MAIL_MODE', 'CACHE_MODE', 'NPM_CONFIG_CACHE', 'COMPOSER_HOME',
    'ROOT_DIRECTORY', 'DOCKERFILE_DIRECTORY', 'BUILD_ENV', 'IMAGE_ENV', 'HOST_UID', 'HOST_GID',
    'BUILDX_NO_DEFAULT_ATTESTATIONS', 'BUILDX_METADATA_PROVENANCE',
    # Process basics every container already has.
    'PATH', 'HOME', 'HOSTNAME', 'PWD', 'USER', 'SHELL',
})
RESERVED_PREFIXES = ('DATABASE_', 'APP_DATABASE_', 'MYSQL_', 'MARIADB_', 'PS_', 'PHP_', 'XDEBUG_', 'CRON_',
                     'PROJECT_', 'SETUP_', 'COMPOSE_', 'DOCKER_')


def reserved(name, settings):
    return name in RESERVED_NAMES or name.startswith(RESERVED_PREFIXES) or name in settings


def application_names(settings):
    """The validated APP_ENVIRONMENT names in their first-listed order."""
    names = list(dict.fromkeys(str(settings.get('APP_ENVIRONMENT') or '').split()))
    invalid = [name for name in names if not NAME.fullmatch(name)]
    if invalid:
        raise ValueError(f"APP_ENVIRONMENT lists variable names only (A-Z, 0-9, _; starting with a letter): "
                         f"{', '.join(invalid)}")
    taken = [name for name in names if reserved(name, settings)]
    if taken:
        raise ValueError(f"APP_ENVIRONMENT cannot pass {', '.join(taken)}: setup sets or computes "
                         "these itself, and a copy would replace its value. Remove them from the list "
                         "(pass one by hand in compose/ only if the application really needs it)")
    return names


def application_services(services):
    return [name for name in APPLICATION_SERVICES if name == 'php-fpm' or name in services]


def application_environment(project, settings, services):
    """Compose arguments for the APP_ENVIRONMENT overlay; none when the setting is empty."""
    names = application_names(settings)
    if not names:
        return []
    lines = ['# Generated from APP_ENVIRONMENT: references only, Compose fills in the values.', 'services:']
    for service in application_services(services):
        lines += [f'  {service}:', '    environment:']
        lines += [f'      {name}: ${{{name}-}}' for name in names]
    contents = ('\n'.join(lines) + '\n').encode()
    digest = hashlib.sha256(contents).hexdigest()[:20]
    path = checked_path(project, f'.generated/app-environment-{digest}.yaml')
    # Content-addressed files keep concurrent runner/IDE invocations independent.
    replace_file(path, contents, private=True)
    return ['-f', str(path)]
