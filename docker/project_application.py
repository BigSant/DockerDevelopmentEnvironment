"""The application preparation step of make setup: profile-specific, never automatic at start."""
import re
import shlex
import shutil
import subprocess

from project_environments import is_server
from project_prestashop import is_prestashop

PATH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')


def plan_application(project, tracked=True):
    """Shell commands for the PHP container and whether composer runs; raises on unsafe settings. tracked=False: no
    refusal for paths the shop repository tracks (a test session's copy, where the release wins)."""
    settings = project.settings
    if not is_prestashop(settings):
        return None
    version, paths = settings['PS_VERSION'], settings['PS_RELEASE_PATHS'].split()
    if paths and settings['PS_RELEASE'] != 'on':
        raise ValueError('PS_RELEASE_PATHS requires PS_RELEASE=on')
    composer = (project.directory / 'config/composer/composer.json').is_file()
    admin = settings['PS_ADMIN_DIR']
    commands = []
    for path in paths:
        if not PATH.fullmatch(path) or '..' in path.split('/'):
            raise ValueError(f'PS_RELEASE_PATHS entry is not a plain relative path: {path}')
        if path.split('/')[0] == 'install':
            raise ValueError('The installer directory is never copied into a shop')
        if composer and path.split('/')[0] == 'vendor':
            continue  # the project's own composer.lock defines vendor/
        target = admin if path == 'admin' and admin else path
        if not PATH.fullmatch(target):
            raise ValueError(f'PS_ADMIN_DIR is not a plain directory name: {target}')
        files = tracked_files(project.web_directory, target) if tracked else []
        if files:
            raise ValueError(f'{target} contains files tracked by the shop repository ({files[0]} …); '
                             'the release never overwrites tracked code')
        source = shlex.quote(f'/opt/prestashop/release/{version}/{path}')
        destination = shlex.quote(f'/var/www/html/{target}')
        commands.append(f'test -e {source} || {{ echo "Release has no {path}" >&2; exit 1; }}; '
                        f'rm -rf {destination} && mkdir -p "$(dirname {destination})" && '
                        f'cp -R --preserve=mode,timestamps {source} {destination} && echo "Release: {path} -> {target}"')
    if settings['PS_RELEASE'] == 'on':
        commands += [seed_command(version, directory) for directory in seeded_directories()]
    return commands, composer


def seeded_directories():
    """The protected directories (setup's PrestaShop writable paths without a wildcard) that take the files the release
    ships there and the shop lacks: cache/.htaccess, the index.php guards, img/ defaults. The shop writes the rest."""
    from project_deploy import PRESTASHOP_WRITABLE
    return [path.strip('/') for path in PRESTASHOP_WRITABLE if path.endswith('/') and '*' not in path]


def seed_command(version, directory, releases='/opt/prestashop/release', shop='/var/www/html'):
    """Copy the release's files of one directory that the shop lacks; an existing file, the shop's, is never replaced.
    tar --skip-old-files rather than cp -n, whose exit status differs between coreutils versions."""
    source = shlex.quote(f'{releases}/{version}/{directory}')
    destination = shlex.quote(f'{shop}/{directory}')
    return (f'if [ -d {source} ]; then mkdir -p {destination} && tar -C {source} -cf - . | '
            f'tar -C {destination} --skip-old-files -xf - && echo "Release: missing files of {directory}/ added"; fi')


def tracked_files(web_directory, relative):
    if not (web_directory / '.git').exists():
        return []
    result = subprocess.run(['git', '-C', str(web_directory), 'ls-files', '--', relative],
                            capture_output=True, text=True)
    return result.stdout.split() if result.returncode == 0 else []


def prepare_application(project):
    plan = plan_application(project)
    if plan is None:
        print('No application preparation for this profile.')
        return
    commands, composer = plan
    if composer:
        for name in ('composer.json', 'composer.lock'):
            source = project.directory / 'config/composer' / name
            if source.is_file():
                shutil.copy2(source, project.web_directory / name)
                print(f'Composer: {name} from config/composer')
    if commands:
        project.run(['run', '--rm', '--no-deps', '--entrypoint', 'sh', 'php-fpm', '-c', ' && '.join(commands)])
    if composer:
        arguments = ['install', '--no-interaction', '--working-dir=/var/www/html']
        if is_server(project.environment):
            arguments += ['--no-dev', '--optimize-autoloader']
        project.run(['run', '--rm', '--no-deps', '--entrypoint', 'composer', 'php-fpm', *arguments])
    if not commands and not composer:
        print('Nothing to prepare: PS_RELEASE_PATHS is empty and config/composer has no composer.json.')


def release_into_test(project, test):
    """A test session (TEST_SESSION=on) of a PrestaShop project whose core comes from the official release: the
    PS_RELEASE_PATHS of PS_VERSION (and the release's files of the seeded directories) copied into ENV=test's copy of
    the shop, as make app-prepare does for ENV=local. The release is the one in this session's php-fpm image (built by
    make build from setup's Dockerfile and the official image or zip of PS_VERSION, its PS_RELEASE_SHA256 checked);
    the copy runs in a one-off confined ENV=test container, without a network, and replaces whatever the caller's shop
    code had at those paths. Nothing for another profile or without PS_RELEASE=on."""
    if not getattr(project, 'session', None):
        return False
    plan = plan_application(project, tracked=False)
    if plan is None or project.settings.get('PS_RELEASE') != 'on':
        return False
    commands, composer = plan
    if composer:
        raise ValueError('config/composer/composer.json: composer install needs the network, which a test session\'s '
                         'containers do not have; such a project cannot get a test session yet')
    if commands:
        print(f'==> PrestaShop {project.settings["PS_VERSION"]} release paths into the test copy', flush=True)
        test.run(['run', '--rm', '--no-deps', '--entrypoint', 'sh', 'php-fpm', '-c', ' && '.join(commands)])
    return True
