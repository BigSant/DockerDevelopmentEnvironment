"""The application preparation step of make setup: profile-specific, never automatic at start."""
import re
import shlex
import shutil
import subprocess

PATH = re.compile(r'[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*')


def plan_application(project):
    """Shell commands for the PHP container and whether composer runs; raises on unsafe settings."""
    settings = project.settings
    if settings.get('PROFILE') not in ('ps', 'prestashop'):
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
        tracked = tracked_files(project.web_directory, target)
        if tracked:
            raise ValueError(f'{target} contains files tracked by the shop repository ({tracked[0]} …); '
                             'the release never overwrites tracked code')
        source = shlex.quote(f'/opt/prestashop/release/{version}/{path}')
        destination = shlex.quote(f'/var/www/html/{target}')
        commands.append(f'test -e {source} || {{ echo "Release has no {path}" >&2; exit 1; }}; '
                        f'rm -rf {destination} && mkdir -p "$(dirname {destination})" && '
                        f'cp -R --preserve=mode,timestamps {source} {destination} && echo "Release: {path} -> {target}"')
    return commands, composer


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
        if project.environment in ('stage', 'prod'):
            arguments += ['--no-dev', '--optimize-autoloader']
        project.run(['run', '--rm', '--no-deps', '--entrypoint', 'composer', 'php-fpm', *arguments])
    if not commands and not composer:
        print('Nothing to prepare: PS_RELEASE_PATHS is empty and config/composer has no composer.json.')
