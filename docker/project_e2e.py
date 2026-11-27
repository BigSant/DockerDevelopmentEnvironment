"""Playwright beyond the bare make e2e: the specs' own npm dependencies (make e2e-install), a caller's selection
(grep, grep_invert, files), a JSON report, Playwright storage states, and runs against a real address (url=) outside
ENV=test.

The specs (tests/playwright/, which may be a git submodule) and the configuration (config/playwright/) stay read-only
in every run. make e2e-install alone writes the specs' npm dependencies, from package.json, package-lock.json and
.npmrc only (npm ci --ignore-scripts, in the Playwright image), into .generated/e2e/ of the project, outside the specs,
and only when one of those files changed since the last install; every run mounts that node_modules read-only as
/e2e/tests/node_modules, over whatever tests/playwright/node_modules holds. The image's @playwright/test is the one
every run uses (it matches the browsers in the image), so the project's own copy is removed after the install: a
spec's import of @playwright/test resolves to /e2e/node_modules, the other packages to /e2e/tests/node_modules.

Every Playwright container (ENV=test and url=) runs as the account that started it, with PLAYWRIGHT_MEMORY,
PLAYWRIGHT_CPUS and at most 1024 processes, no capabilities and no-new-privileges: the specs are code from a branch.
"""

import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess

TESTS = 'tests/playwright'
INSTALL_MARKER = '.setup-e2e-install'
# What an install is made of: the files copied from tests/playwright into the install directory.
INSTALL_FILES = ('package.json', 'package-lock.json', '.npmrc')
PIDS_LIMIT = '1024'
TMPFS = '/tmp:size=256m,mode=1777'
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
    """tests/playwright, or a test session's specs directory (TEST_SESSION=on)."""
    given = project.process_env.get('PROJECT_E2E_TESTS_DIRECTORY')
    return Path(given) if given else Path(project.process_env['PROJECT_APP_DIRECTORY']) / TESTS


def config_directory(project):
    given = project.process_env.get('PROJECT_E2E_CONFIG_DIRECTORY')
    return Path(given) if given else Path(project.process_env['PROJECT_CONFIG_DIRECTORY']) / 'playwright'


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


def install_directory(project):
    """.generated/e2e of the project: the install's package files and node_modules, outside the specs."""
    return Path(project.process_env['PROJECT_E2E_MODULES_DIRECTORY']).parent


def regular_file(path, label):
    """The bytes of a regular file that is not a symlink (None when it does not exist)."""
    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f'{label} must be a regular file, not a symlink or anything else')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(descriptor, 'rb') as handle:
        return handle.read()


def real_directory(path, label):
    """path as a directory that is not a symlink, made when it is missing."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError(f'{label} must be a directory, not a symlink')
    path.mkdir(parents=True, exist_ok=True)
    return path


def install_digest(files, version):
    """The cache key of an install: every install file (its name and bytes, or its absence) and PLAYWRIGHT_VERSION."""
    digest = hashlib.sha256()
    for name in INSTALL_FILES:
        content = files.get(name)
        digest.update(name.encode() + b'\0' + (b'-' if content is None else b'+' + hashlib.sha256(content).digest()))
    digest.update(b'\0' + str(version).encode())
    return digest.hexdigest()


def install(project):
    """make e2e-install: npm ci of tests/playwright's package.json and package-lock.json (and .npmrc), copied into
    .generated/e2e, skipped while those files (and PLAYWRIGHT_VERSION) are what the last install used. Returns True
    when it installed."""
    if project.environment != 'local':
        raise ValueError('Run make e2e-install from ENV=local: it installs into .generated/e2e, which every '
                         'environment\'s Playwright mounts read-only')
    directory = tests_directory(project)
    files = {name: regular_file(directory / name, f'{TESTS}/{name}') for name in INSTALL_FILES}
    if files['package.json'] is None:
        # No manifest: no dependencies, not the modules an earlier commit installed.
        target = install_directory(project)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target / 'node_modules', ignore_errors=True)
            for name in (INSTALL_MARKER, *INSTALL_FILES):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(target / name)
            real_directory(target / 'node_modules', '.generated/e2e/node_modules')
        print(f'{TESTS} has no package.json: nothing to install')
        return False
    if files['package-lock.json'] is None:
        raise ValueError(f'{TESTS}/package-lock.json is missing: make e2e-install installs from the lock file only (npm ci)')
    target = real_directory(install_directory(project), '.generated/e2e')
    modules = target / 'node_modules'
    if modules.is_symlink():
        raise ValueError('.generated/e2e/node_modules must not be a symlink')
    digest = install_digest(files, project.settings.get('PLAYWRIGHT_VERSION') or project.process_env.get('PLAYWRIGHT_VERSION', ''))
    marker = target / INSTALL_MARKER
    if (regular_file(marker, f'.generated/e2e/{INSTALL_MARKER}') or b'').decode(errors='replace').strip() == digest \
            and modules.is_dir():
        print(f'.generated/e2e/node_modules is up to date with {TESTS}/package-lock.json')
        return False
    with contextlib.suppress(FileNotFoundError):
        os.unlink(marker)
    shutil.rmtree(modules, ignore_errors=True)
    for name, content in files.items():
        with contextlib.suppress(FileNotFoundError):
            os.unlink(target / name)
        if content is not None:
            descriptor = os.open(target / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(content)
    cache = real_directory(target / '.npm-cache', '.generated/e2e/.npm-cache')
    mount_points(project)
    print(f'==> npm ci of {TESTS} into .generated/e2e (install files {digest[:12]})', flush=True)
    # A one-off container of the image (no Compose project, so no network or orphan is left), bounded and unprivileged;
    # the bridge network reaches the npm registry.
    memory = limit(project, 'PLAYWRIGHT_MEMORY', '2g')
    command = ['docker', 'run', '--rm', '--network', 'bridge', '--user', f'{os.getuid()}:{os.getgid()}',
               *confinement(memory, limit(project, 'PLAYWRIGHT_CPUS', '2')),
               '--label', 'setup.managed=1', '--label', 'setup.service=playwright-install',
               '--volume', f'{target}:/e2e/install', '--workdir', '/e2e/install',
               '--env', 'HOME=/tmp', '--env', 'npm_config_cache=/e2e/install/.npm-cache',
               '--env', 'npm_config_update_notifier=false', '--entrypoint', 'npm', playwright_image(project),
               'ci', '--no-audit', '--no-fund', '--ignore-scripts']
    environment = project.child_env()
    environment.pop('DOCKER_CONTEXT', None)
    subprocess.run(command, env=environment, check=True)
    shutil.rmtree(cache, ignore_errors=True)
    for package in IMAGE_PACKAGES:
        shutil.rmtree(modules / package, ignore_errors=True)
    real_directory(modules, '.generated/e2e/node_modules')
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o644)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(digest + '\n')
    print(f'Installed .generated/e2e/node_modules (the image\'s @playwright/test is used)')
    return True


def limit(project, key, default):
    return project.settings.get(key) or project.process_env.get(key) or default


def confinement(memory, cpus):
    """docker run options of every Playwright container: limits, no capabilities, a read-only root with a sized /tmp."""
    return ['--memory', memory, '--memory-swap', memory, '--cpus', cpus, '--pids-limit', PIDS_LIMIT,
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '--read-only', '--tmpfs', TMPFS]


def mount_points(project):
    """The directories every run mounts, made (as this account) when missing, so Docker never makes one as root: the
    data directory, the install's node_modules (empty before the first make e2e-install) and its mount point in
    tests/playwright."""
    modules = Path(project.process_env['PROJECT_E2E_MODULES_DIRECTORY'])
    playwright_data(project)  # the service's /e2e/data
    real_directory(modules.parent, '.generated/e2e')
    real_directory(modules, '.generated/e2e/node_modules')
    tests = tests_directory(project)
    if tests.is_dir() and not tests.is_symlink():
        real_directory(tests / 'node_modules', f'{TESTS}/node_modules')
    return modules


def selection(grep=None, grep_invert=None, files=None):
    """Playwright arguments of a caller's selection: --grep, --grep-invert, then the spec file filters."""
    arguments = []
    for option, pattern in (('--grep', grep), ('--grep-invert', grep_invert)):
        if pattern:
            if len(pattern) > PATTERN_LIMIT or any(ord(character) < 32 for character in pattern):
                raise ValueError(f'{option[2:].replace("-", "_")}: at most {PATTERN_LIMIT} characters, no control characters')
            arguments.append(f'{option}={pattern}')  # one argument: a pattern such as -x is never an option
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
    mount_points(test)
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
    modules = mount_points(project)
    docker = ['docker', 'run', '--rm', '--network', 'bridge', '--user', f'{os.getuid()}:{os.getgid()}',
              *confinement(limit(project, 'PLAYWRIGHT_MEMORY', '2g'), limit(project, 'PLAYWRIGHT_CPUS', '2')),
              '--label', 'setup.managed=1', '--label', 'setup.service=playwright-external',
              '--label', f'setup.project={project.settings["PROJECT_NAME"]}',
              *(['--name', name] if name else []),
              '--env', 'HOME=/tmp', '--env', 'npm_config_update_notifier=false', '--env', f'BASE_URL={url.rstrip("/")}',
              '--env', 'PLAYWRIGHT_TEST_DIR=/e2e/tests',
              '--env', 'PLAYWRIGHT_DATA_DIR=/e2e/data', '--env', 'E2E_EXTERNAL=1',
              '--volume', f'{config_directory(project)}:/e2e/config:ro', '--volume', f'{tests_directory(project)}:/e2e/tests:ro',
              '--volume', f'{modules}:/e2e/tests/node_modules:ro',
              '--volume', f'{output}:/e2e/data', *options, '--workdir', '/e2e', '--entrypoint', arguments.pop(0),
              image, *arguments, *extra]
    environment = project.child_env()
    environment.pop('DOCKER_CONTEXT', None)  # Playwright runs here, whatever the environment's target is.
    print(f'==> Playwright against {url} (results in {output})', flush=True)
    subprocess.run(docker, env=environment, check=True)
