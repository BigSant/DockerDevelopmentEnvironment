"""The stage/prod secret store: a stack keeps the Deployment variables it was deployed with.

The Deployment variables (Bitbucket) or environment secrets (GitHub) exist only in a pipeline step.
Commands that run on the server outside a pipeline — the worker's nightly backup, "backup now", a
`make db-import` by hand — need the same values, and a copy in a file would be a second source to
keep in step. The stack carries them instead: the `secrets` service lists every Deployment variable
as `NAME: ${NAME-}` (references only, like APP_ENVIRONMENT), so each deploy gives it the pipeline's
values. Outside a pipeline (CI is not "true") a stage/prod command takes a name its process lacks
from that container. The container only sleeps, without network or mounts; its values sit in
Docker's container configuration like every other container's environment (the database's
passwords already do).
"""

import hashlib
import json
import os
import subprocess

from project_env_files import file_settings
from project_ide import checked_path, replace_file

SERVICE = 'secrets'
ENVIRONMENTS = ('stage', 'prod')


def store_names(directory, environment):
    """The names the store keeps: the environment's Deployment variables; none for local/test."""
    if environment not in ENVIRONMENTS:
        return []
    from project_pipeline import deployment_variables
    return list(deployment_variables(directory, environment))


def container_name(project_name, environment):
    return f'{project_name}-{environment}-{SERVICE}'


def secret_store(project, names):
    """Compose arguments for the store overlay; none without names."""
    if not names:
        return []
    lines = ['# Generated: the Deployment variables this stack was deployed with (references only).',
             'services:',
             f'  {SERVICE}:',
             '    image: ${PROJECT_NAME}-php-fpm-${IMAGE_ENV:-${ENV}}${SETUP_IMAGE_SUFFIX_PHP_FPM:-}',
             '    container_name: ${PROJECT_NAME}-${ENV}-' + SERVICE,
             '    user: "${HOST_UID:-1000}:${HOST_GID:-1000}"',
             '    entrypoint: ["sleep", "infinity"]',
             '    network_mode: none',
             '    read_only: true',
             '    stop_signal: SIGKILL',
             '    mem_limit: 16m',
             '    cpus: 0.05',
             '    restart: unless-stopped',
             '    environment:']
    lines += [f'      {name}: ${{{name}-}}' for name in names]
    contents = ('\n'.join(lines) + '\n').encode()
    digest = hashlib.sha256(contents).hexdigest()[:20]
    path = checked_path(project, f'.generated/secret-store-{digest}.yaml')
    # Content-addressed like the APP_ENVIRONMENT overlay: concurrent invocations stay independent.
    replace_file(path, contents, private=True)
    return ['-f', str(path)]


def read_store(name, context=None, run=subprocess.run):
    """{NAME: value} of the running store container; {} when there is none."""
    command = ['docker', *(['--context', context] if context else []), 'inspect', '--type', 'container',
               '--format', '{{json .Config.Env}}', name]
    try:
        result = run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode:
        return {}
    return dict(entry.split('=', 1) for entry in json.loads(result.stdout or '[]') if '=' in entry)


def fill_from_store(directory, environment, process_env, run=subprocess.run):
    """Give process_env the Deployment variables it lacks from the stack's store; the names filled.

    Only outside a pipeline: there the pipeline's own values are the truth, and a variable removed in
    Bitbucket must not come back from the running stack.
    """
    if environment not in ENVIRONMENTS or os.environ.get('CI') == 'true':
        return []
    names = [name for name in store_names(directory, environment) if not process_env.get(name)]
    if not names:
        return []
    values, _ = file_settings(directory, environment)
    store = read_store(container_name(values.get('PROJECT_NAME', ''), environment),
                       values.get('DEPLOY_CONTEXT') or None, run)
    filled = [name for name in names if store.get(name)]
    process_env.update({name: store[name] for name in filled})
    return filled
