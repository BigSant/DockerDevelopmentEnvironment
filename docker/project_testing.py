"""The isolated ENV=test: prepare it with the mock data set and run PHPUnit suites only there.

Tests never start on their own: make test-prepare and make phpunit are the only entry points, and
both always act on ENV=test, whose database, containers and application copy are separate from
local. The application copy is refreshed incrementally before each run.
"""

from pathlib import Path
import re
import shutil
import subprocess
import urllib.request

SUITE_NAMES = ('phpunit.xml', 'phpunit.xml.dist')
# Setup's PHPUnit when neither the module nor the shop brings one: the newest line for the PHP version.
PHPUNIT_FOR_PHP = (((8, 1), '10.5'), ((7, 3), '9.6'), ((7, 2), '8.5'), ((7, 1), '7.5'), ((7, 0), '6.5'), ((5, 6), '5.7'))


def test_project(project):
    from project import Project
    if project.environment == 'test':
        return project
    if project.environment != 'local':
        raise ValueError('Tests run only in the isolated ENV=test, prepared from ENV=local')
    if not (project.directory / 'env/test.env').is_file():
        raise ValueError('ENV=test is not prepared; run make test-prepare first')
    test = Project(project.directory, 'test', project.root)
    if test.settings['DATABASE_NAME'] == project.settings['DATABASE_NAME']:
        raise ValueError('ENV=test must use its own database; refusing to run tests against the local one')
    return test


def prepare_tests(project, dataset='dev'):
    """Copy or refresh the application, load the mock data set into the test DB and start ENV=test."""
    from database_load import plan_load, run_load
    from project_bootstrap import initialize_test
    from project_health import start_project
    if project.environment != 'local':
        raise ValueError('Run make test-prepare from ENV=local')
    initialize_test(project, refresh=(project.directory / '.generated/test/initialized').is_file())
    test = test_project(project)
    print('==> starting the test database', flush=True)
    test.run(['up', '-d', '--wait', 'database'])
    print(f'==> loading data set {dataset} into {test.settings["DATABASE_NAME"]}', flush=True)
    run_load(test, plan_load(test, dataset=dataset, replace=True))
    print('==> starting ENV=test', flush=True)
    start_project(test, 180)
    print(f'ENV=test is ready on https://{test.settings["DOMAIN"]}:{test.settings.get("LOCALHOST_PORT_SSL", "")}/ '
          '— run make phpunit [module=NAME] [filter=PATTERN].')
    return test


# The environment repository's tests/ (outside the document root) arrive in the ENV=test copy here.
PROJECT_SUITE = '.setup-tests/project'


def find_suites(web_directory, prestashop):
    """PHPUnit configurations of the application (`shop` for PrestaShop, else `app`), of PrestaShop modules
    (module or module/tests) and of the project. modules/ is an ordinary folder of a generic application."""
    suites = {}
    modules = sorted(path for path in (web_directory / 'modules').glob('*') if path.is_dir()) if prestashop else []
    for base in [web_directory, *modules]:
        for folder in (base, base / 'tests'):
            names = [name for name in SUITE_NAMES if (folder / name).is_file()]
            if names:
                key = ('shop' if prestashop else 'app') if base == web_directory else base.name
                suites.setdefault(key, folder / names[0])  # phpunit.xml overrides the .dist default
                break
    names = [name for name in SUITE_NAMES if (web_directory / PROJECT_SUITE / name).is_file()]
    if names:
        suites['project'] = web_directory / PROJECT_SUITE / names[0]
    return suites


def copy_project_suite(project, destination):
    """Bring the environment repository's PHPUnit suite (tests/, without e2e specs) into the test copy."""
    source = project.directory / 'tests'
    target = destination / PROJECT_SUITE
    if not any((source / name).is_file() for name in SUITE_NAMES):
        if target.exists():
            shutil.rmtree(target)
        return
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rsync', '-a', '--safe-links', '--delete', '--exclude=/playwright', f'{source}/', f'{target}/'],
                   check=True)


def phpunit_binary(project, config, web_directory):
    """Container path of the PHPUnit to use: the suite's own, the module's, the shop's, else setup's."""
    module = config.parent.parent if config.parent.name == 'tests' else config.parent
    for candidate in (config.parent / '.tools/phpunit.phar', module / '.tools/phpunit.phar',
                      module / 'vendor/bin/phpunit', web_directory / 'vendor/bin/phpunit'):
        if candidate.is_file():
            return '/var/www/html/' + candidate.relative_to(web_directory).as_posix()
    return setup_phpunit(project, web_directory)


def setup_phpunit(project, web_directory):
    match = re.match(r'(\d+)\.(\d+)', str(project.settings.get('PHP_VERSION', '')))
    if not match:
        raise ValueError('Set PHP_VERSION to choose a PHPUnit version')
    php = (int(match[1]), int(match[2]))
    version = next((phpunit for minimum, phpunit in PHPUNIT_FOR_PHP if php >= minimum), None)
    if not version:
        raise ValueError(f'No PHPUnit version is known for PHP {match[0]}')
    target = web_directory / f'.setup-tools/phpunit-{version}.phar'
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f'https://phar.phpunit.de/phpunit-{version}.phar'
        print(f'Downloading PHPUnit {version} from {url}', flush=True)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        if not data.startswith(b'#!/usr/bin/env php'):
            raise ValueError(f'Unexpected PHPUnit download from {url}')
        target.write_bytes(data)
    return '/var/www/html/' + target.relative_to(web_directory).as_posix()


def run_phpunit(project, module=None, test_filter=None, sync=True):
    from project_bootstrap import initialize_test
    local = project
    test = test_project(project)
    if sync and local.environment == 'local':
        initialize_test(local, refresh=True)
    from project_prestashop import is_prestashop
    suites = find_suites(test.web_directory, is_prestashop(test))
    if module:
        if module not in suites:
            raise ValueError(f'No PHPUnit suite for {module}; found: {", ".join(sorted(suites)) or "none"}')
        suites = {module: suites[module]}
    if not suites:
        print('No PHPUnit suites found (phpunit.xml or phpunit.xml.dist in the application, the environment '
              'repository tests/' + (', a module or its tests/ folder' if is_prestashop(test) else '') + ').')
        return []
    failed = []
    for name, config in sorted(suites.items()):
        binary = phpunit_binary(test, config, test.web_directory)
        # Symfony's PHPUnit bridge (loaded by the shop autoloader) fails a green run over deprecation
        # notices; they are still reported, but the exit status follows the tests.
        command = ['exec', '-T', '-w', '/var/www/html', '-e', 'SYMFONY_DEPRECATIONS_HELPER=weak', 'php-fpm', 'php', binary,
                   '-c', '/var/www/html/' + config.relative_to(test.web_directory).as_posix()]
        if test_filter:
            command += ['--filter', test_filter]
        print(f'==> PHPUnit {name}: {config.relative_to(test.web_directory)} ({binary.rsplit("/", 1)[-1]})', flush=True)
        try:
            test.run(command)
        except subprocess.CalledProcessError:
            failed.append(name)
    if failed:
        raise ValueError(f'PHPUnit failed: {", ".join(failed)}')
    print(f'PHPUnit passed: {", ".join(sorted(suites))}')
    return sorted(suites)
