"""Playwright beyond the bare make e2e: the specs' own npm dependencies (make e2e-install), a caller's selection
(grep, grep_invert, files), a JSON report, Playwright storage states, and runs against a real address (url=) outside
ENV=test.

The specs (tests/playwright/, which may be a git submodule) and the configuration (config/playwright/) stay read-only
in every run. make e2e-install alone writes tests/playwright/node_modules, from package-lock.json only (npm ci, no
install scripts), and only when the lock file changed since the last install. The image's @playwright/test is the
one every run uses (it matches the browsers in the image), so the project's own copy is removed after the install:
a spec's import of @playwright/test resolves to /e2e/node_modules, the other packages to tests/playwright/node_modules.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess

TESTS = 'tests/playwright'
INSTALL_MARKER = '.setup-e2e-install'
# The packages the image brings: two copies of @playwright/test in one run fail ("Requiring @playwright/test second time").
IMAGE_PACKAGES = ('@playwright/test', 'playwright', 'playwright-core')
# A spec file filter (Playwright takes it as a pattern on the file path), optionally :LINE.
SPEC_FILTER = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.@/-]{0,199}(?::[0-9]{1,6})?')
PATTERN_LIMIT = 1000
STORAGE_ROLE = re.compile(r'[a-z][a-z0-9_-]{0,31}')
CONTAINER_NAME = re.compile(r'[a-z0-9][a-z0-9_.-]{0,99}')
EXTERNAL_URL = re.compile(r'https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?(?:/[A-Za-z0-9._~%/-]*)?')
AUTH_DIRECTORY = '/e2e/auth'
REPORT_FILE = '/e2e/data/report.json'


def tests_directory(project):
    return Path(project.process_env['PROJECT_APP_DIRECTORY']) / TESTS


def config_directory(project):
    return Path(project.process_env['PROJECT_CONFIG_DIRECTORY']) / 'playwright'


def playwright_data(project, data=None):
    """The host directory mounted as /e2e/data: data= (an absolute directory) or the environment's data/playwright."""
    if data:
        path = Path(data)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('data= must be an absolute directory without ..')
    else:
        path = Path(project.process_env['PROJECT_DATA_DIRECTORY']) / 'playwright'
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_digest(lock, version):
    return hashlib.sha256(lock.read_bytes() + b'\0' + str(version).encode()).hexdigest()


def install(project):
    """make e2e-install: npm ci of tests/playwright from its package-lock.json, skipped while the lock file (and
    PLAYWRIGHT_VERSION) is what the last install used. Returns True when it installed."""
    if project.environment != 'local':
        raise ValueError('Run make e2e-install from ENV=local: it installs into tests/playwright, which every '
                         'environment\'s Playwright mounts read-only')
    directory = tests_directory(project)
    if not (directory / 'package.json').is_file():
        print(f'{TESTS} has no package.json: nothing to install')
        return False
    lock = directory / 'package-lock.json'
    if not lock.is_file():
        raise ValueError(f'{TESTS}/package-lock.json is missing: make e2e-install installs from the lock file only (npm ci)')
    modules = directory / 'node_modules'
    if modules.is_symlink():
        raise ValueError(f'{TESTS}/node_modules must not be a symlink')
    digest = install_digest(lock, project.settings.get('PLAYWRIGHT_VERSION') or project.process_env.get('PLAYWRIGHT_VERSION', ''))
    marker = modules / INSTALL_MARKER
    if marker.is_file() and marker.read_text().strip() == digest:
        print(f'{TESTS}/node_modules is up to date with package-lock.json')
        return False
    data = playwright_data(project)
    print(f'==> npm ci in {TESTS} (package-lock.json {digest[:12]})', flush=True)
    project.run(['run', '--rm', '--no-deps', '--user', f'{os.getuid()}:{os.getgid()}',
                 '--volume', f'{directory}:/e2e/install', '--workdir', '/e2e/install',
                 '--env', 'HOME=/tmp', '--env', 'npm_config_cache=/e2e/data/.npm-cache',
                 '--env', 'npm_config_update_notifier=false', '--entrypoint', 'npm', 'playwright',
                 'ci', '--no-audit', '--no-fund', '--ignore-scripts'], profiles='playwright')
    for package in IMAGE_PACKAGES:
        shutil.rmtree(modules / package, ignore_errors=True)
    marker.write_text(digest + '\n')
    print(f'Installed {TESTS}/node_modules (the image\'s @playwright/test is used; cache {data / ".npm-cache"})')
    return True


def selection(grep=None, grep_invert=None, files=None):
    """Playwright arguments of a caller's selection: --grep, --grep-invert, then the spec file filters."""
    arguments = []
    for option, pattern in (('--grep', grep), ('--grep-invert', grep_invert)):
        if pattern:
            if len(pattern) > PATTERN_LIMIT or any(ord(character) < 32 for character in pattern):
                raise ValueError(f'{option[2:].replace("-", "_")}: at most {PATTERN_LIMIT} characters, no control characters')
            arguments += [option, pattern]
    for name in (files or '').split():
        if not SPEC_FILTER.fullmatch(name) or '..' in PurePosixPath(name.split(':')[0]).parts:
            raise ValueError(f'files: {name!r} is not a spec path inside {TESTS} (letters, digits, . _ @ / -, optional :LINE)')
        arguments.append(name)
    return arguments


def storage_states(text):
    """{role: host path} of storage_states="ROLE=/absolute/file.json ..." (Playwright storage state files)."""
    states = {}
    for item in (text or '').split():
        role, _, path = item.partition('=')
        if not STORAGE_ROLE.fullmatch(role) or not path.startswith('/') or '..' in PurePosixPath(path).parts \
                or not path.endswith('.json'):
            raise ValueError(f'storage_states: {item!r} is not ROLE=/absolute/path.json')
        if role in states:
            raise ValueError(f'storage_states: the role {role} is given twice')
        states[role] = path
    return states


def state_options(states):
    """docker (compose) run options mounting each storage state read-only as /e2e/auth/<role>.json, with
    E2E_STORAGE_STATE_<ROLE> and E2E_STORAGE_STATES (JSON) naming them for the specs."""
    options, names = [], {}
    for role, path in sorted(states.items()):
        inside = f'{AUTH_DIRECTORY}/{role}.json'
        names[role] = inside
        options += ['--volume', f'{path}:{inside}:ro',
                    '--env', f'E2E_STORAGE_STATE_{role.upper().replace("-", "_")}={inside}']
    if names:
        options += ['--env', 'E2E_STORAGE_STATES=' + json.dumps(names, separators=(',', ':'))]
    return options


def report_options(json_report):
    """With json_report: Playwright's JSON report as data/playwright/report.json, next to the HTML report
    (data/playwright/report/), and the list on the console. Without it the project's own reporters."""
    if not json_report:
        return [], []
    return (['--env', f'PLAYWRIGHT_JSON_OUTPUT_FILE={REPORT_FILE}', '--env', 'PLAYWRIGHT_HTML_OUTPUT_DIR=/e2e/data/report',
             '--env', 'PLAYWRIGHT_HTML_OPEN=never'], ['--reporter=list,html,json'])


def command_arguments(project, command):
    arguments = shlex.split(command or project.settings['PLAYWRIGHT_COMMAND'])
    if not arguments or not arguments[0]:
        raise ValueError('QA command cannot be empty')
    return arguments


def run(project, command=None, grep=None, grep_invert=None, files=None, json_report=False, url=None, states=None,
        data=None, name=None):
    """make e2e [cmd=] [grep=] [grep_invert=] [files=] [json=1] [storage_states=] [name=] in ENV=test, or with url=
    against that address. Nothing given: exactly the old make e2e."""
    extra = selection(grep, grep_invert, files)
    env_options, reporter = report_options(json_report)
    mounts = state_options(storage_states(states) if isinstance(states, str) else (states or {}))
    if name and not CONTAINER_NAME.fullmatch(name):
        raise ValueError('name: a container name (lower-case letters, digits, . _ -)')
    if url:
        return run_external(project, url, command, extra + reporter, env_options + mounts, data, name)
    if data:
        raise ValueError('data= goes with url=: ENV=test keeps its results in its own data directory')
    from project_testing import test_project
    arguments = command_arguments(project, command)  # The setting of the environment make was run in.
    test = test_project(project)
    options = ['--entrypoint', arguments.pop(0), *(['--name', name] if name else []), *env_options, *mounts]
    # The shared Playwright service otherwise starts `tail -f /dev/null`.
    test.run(['run', '--rm', *options, 'playwright', *arguments, *extra, *reporter], profiles='playwright')


def playwright_image(project):
    """The Playwright image of this project and environment, built when it is missing."""
    image = project.model()['services']['playwright']['image']
    found = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image], capture_output=True,
                           env=project.child_env())
    if found.returncode:
        project.run(['build', 'playwright'], profiles='playwright')
    return image


def run_external(project, url, command, extra, options, data, name):
    """Playwright against a real address (a STAGE or LIVE site): the same image, mounts and command as make e2e, with
    BASE_URL set to url, on Docker's default bridge network (nothing of a project's network), as this account."""
    if project.environment == 'test':
        raise ValueError('url= runs against a real address; ENV=test has its own (make e2e without url=)')
    if not EXTERNAL_URL.fullmatch(url or ''):
        raise ValueError('url: http(s)://HOST[:PORT][/PATH]')
    arguments = command_arguments(project, command)
    for directory in (tests_directory(project), config_directory(project)):
        if not directory.is_dir():
            raise ValueError(f'{directory} is missing: url= runs the project\'s own specs and configuration')
    output = playwright_data(project, data)
    image = playwright_image(project)
    docker = ['docker', 'run', '--rm', '--network', 'bridge', '--user', f'{os.getuid()}:{os.getgid()}',
              '--label', 'setup.managed=1', '--label', 'setup.service=playwright-external',
              '--label', f'setup.project={project.settings["PROJECT_NAME"]}',
              *(['--name', name] if name else []),
              '--env', 'HOME=/tmp', '--env', 'npm_config_update_notifier=false', '--env', f'BASE_URL={url.rstrip("/")}',
              '--env', 'PLAYWRIGHT_TEST_DIR=/e2e/tests',
              '--env', 'PLAYWRIGHT_DATA_DIR=/e2e/data', '--env', 'E2E_EXTERNAL=1',
              '--volume', f'{config_directory(project)}:/e2e/config:ro', '--volume', f'{tests_directory(project)}:/e2e/tests:ro',
              '--volume', f'{output}:/e2e/data', *options, '--workdir', '/e2e', '--entrypoint', arguments.pop(0),
              image, *arguments, *extra]
    environment = project.child_env()
    environment.pop('DOCKER_CONTEXT', None)  # Playwright runs here, whatever the environment's target is.
    print(f'==> Playwright against {url} (results in {output})', flush=True)
    subprocess.run(docker, env=environment, check=True)
