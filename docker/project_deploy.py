"""Code deploy: build a release of the application on this machine and carry it to the target with rsync.

make deploy ENV=stage|prod [ref=TAG] [backup=1] [rollback=1]
  1. release: git archive of the code ref, composer install --no-dev (and PS_RELEASE_PATHS for
     PrestaShop) in the release PHP image, the project's config/deploy/build.sh (assets) if present;
  2. with backup=1, a DB backup of the target to this machine, following the table registry (off by default);
  3. rsync of the code without --delete: files that exist only on the target (protected paths,
     vendors installed there, PrestaShop modules not in the code) stay; a file is removed only when
     the previously deployed release had it and this one does not (release manifests);
  4. the project's config/deploy/after-rsync.sh in the PHP container (its failure stops the deploy),
     PrestaShop module upgrades, pending setup migrations, a schema report, a cache clear and a
     graceful php-fpm reload so OPcache serves the new code (DEPLOY_PHP_RELOAD=off skips it).
Every step that knows an application follows PROFILE: an empty (generic) profile reads and protects
nothing of PrestaShop, so .htaccess, robots.txt and modules/ are code like any other tracked file.
rollback=1 deploys the previous release again; the database comes back from the backup by hand.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from project_prestashop import is_prestashop
from project_target import deploy_target

# PrestaShop paths written by the shop itself; a PrestaShop deploy never overwrites or deletes them.
# Paths the shop repository tracks (mails/, translations/, config/xml/) are code like any other file.
PRESTASHOP_WRITABLE = ('/var/', '/img/', '/upload/', '/download/', '/cache/', '/config/themes/',
                       '/app/config/parameters.php', '/config/settings.inc.php',
                       '/app/Resources/translations/', '/themes/*/cache/', '/themes/*/assets/cache/', '/modules/*/cache/',
                       '/modules/*/var/', '/.htaccess', '/robots.txt', '/sitemap*.xml')
# Checkouts and tool folders, never part of a release, for every profile.
TOOL_PATHS = ('/.setup-tools/', '/.git/', '/.claude/', '/.idea/')
PROTECTED_PATH = re.compile(r'/?[A-Za-z0-9._*-]+(/[A-Za-z0-9._*-]+)*/?')
AFTER_RSYNC_HOOK = 'config/deploy/after-rsync.sh'
KEEP_RELEASES = 3
MODULE_VERSION = re.compile(r"\$this->version\s*=\s*['\"]([^'\"]+)['\"]")


def protected_paths(project):
    """Paths of the code tree a deploy never overwrites or deletes: the profile's, the tools', the project's."""
    patterns = [*(PRESTASHOP_WRITABLE if is_prestashop(project) else ()), *TOOL_PATHS]
    for entry in (project.settings.get('DEPLOY_PROTECTED_PATHS') or '').split():
        parts = entry.strip('/').split('/')
        if not PROTECTED_PATH.fullmatch(entry) or any(part in ('.', '..') for part in parts) or not parts[0].strip('*'):
            raise ValueError(f'DEPLOY_PROTECTED_PATHS entry is not a plain path inside the code tree: {entry!r} '
                             '(e.g. var/data/ for a directory, config/local.php for a file)')
        patterns.append('/' + entry.lstrip('/'))
    return patterns


def deploy_directory(project):
    from project_release import release_directory
    return release_directory(project)


def history(project):
    path = deploy_directory(project) / 'deployments.json'
    return json.loads(path.read_text()) if path.is_file() else []


def rollback_entry(entries):
    """The release rollback=1 returns to: the latest other completed deploy before the last one."""
    completed = [entry for entry in entries[:-1] if not entry.get('failed') and entry['commit'] != entries[-1]['commit']]
    return completed[-1] if completed else None


def record(project, entry):
    entries = history(project) + [entry]
    path = deploy_directory(project) / 'deployments.json'
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, indent=2) + '\n')


def shop_checkout(project):
    """The shop's git checkout: the code directory itself, or the local one beside a server env."""
    return project.web_directory if (project.web_directory / '.git').exists() else project.root / 'app/public'


def shop_commit(project, ref):
    shop = shop_checkout(project)
    result = subprocess.run(['git', '-C', str(shop), 'rev-parse', '--verify', '--end-of-options', ref + '^{commit}'],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'The shop repository has no commit {ref!r}; deploy a tag or commit of {shop}')
    return shop, result.stdout.strip()


def php_image(project):
    image = project.model()['services']['php-fpm']['image']
    if subprocess.run(['docker', 'image', 'inspect', image], capture_output=True).returncode:
        raise ValueError(f'The release image {image} is missing; run make release ENV={project.environment} first')
    return image


def in_release_image(project, release, command):
    image = php_image(project)
    # The hosting account has no home directory in the image: npm/Composer caches go to /tmp.
    subprocess.run(['docker', 'run', '--rm', '--network', 'host', '--user', f'{project.process_env["HOST_UID"]}:'
                    f'{project.process_env["HOST_GID"]}', '-e', 'HOME=/tmp', '-e', 'NPM_CONFIG_CACHE=/tmp/setup-npm-cache',
                    '-e', 'COMPOSER_HOME=/tmp/composer', '-v', f'{release}:/var/www/html',
                    '-w', '/var/www/html', '--entrypoint', 'sh', image, '-c', command], check=True)


def build_code(project, ref):
    """The release directory for one shop commit, built once and reused (rollback, redeploy)."""
    shop, commit = shop_commit(project, ref)
    release = deploy_directory(project) / 'code' / commit[:12]
    if (release / '.setup-release').is_file():
        print(f'Release {commit[:12]} already built: {release}')
        return release, commit
    if release.exists():
        shutil.rmtree(release)
    release.mkdir(parents=True)
    print(f'==> exporting {ref} ({commit[:12]}) from {shop}', flush=True)
    archive = subprocess.Popen(['git', '-C', str(shop), 'archive', '--format=tar', commit], stdout=subprocess.PIPE)
    subprocess.run(['tar', '-x', '-C', str(release)], stdin=archive.stdout, check=True)
    archive.stdout.close()
    if archive.wait():
        raise ValueError(f'git archive of {commit[:12]} failed')
    composer = project.directory / 'config/composer'
    if (composer / 'composer.json').is_file():
        for name in ('composer.json', 'composer.lock'):
            if (composer / name).is_file():
                shutil.copy2(composer / name, release / name)
    if (release / 'composer.json').is_file():
        print('==> composer install --no-dev', flush=True)
        in_release_image(project, release, 'composer install --no-dev --optimize-autoloader --no-interaction --no-progress')
    from project_application import plan_application
    plan = plan_application(project)  # None: the profile copies nothing into the code
    for command in plan[0] if plan else []:
        in_release_image(project, release, command)
    hook = project.directory / 'config/deploy/build.sh'
    if hook.is_file():
        print('==> config/deploy/build.sh', flush=True)
        _run_hook(project, release, hook)
    write_manifest(project, release, commit)
    (release / '.setup-release').write_text(json.dumps({'ref': ref, 'commit': commit}) + '\n')
    prune_releases(project, keep=release)
    return release, commit


def manifest_path(project, commit):
    return deploy_directory(project) / 'manifests' / f'{commit[:12]}.txt'


def write_manifest(project, release, commit):
    """Every file the release ships; the next deploy removes what it drops from this list only."""
    files = sorted(path.relative_to(release).as_posix() for path in release.rglob('*')
                   if (path.is_file() or path.is_symlink()) and not path.name.startswith('.setup-'))
    target = manifest_path(project, commit)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(''.join(f'{name}\n' for name in files))
    return files


def removed_files(project, previous, current):
    """Files of the previously deployed release that the new one no longer ships (never protected paths)."""
    from fnmatch import fnmatch
    if not previous or not manifest_path(project, previous).is_file():
        return []
    before = set(manifest_path(project, previous).read_text().split('\n')) - {''}
    after = set(manifest_path(project, current).read_text().split('\n')) - {''}
    patterns = protected_paths(project)
    protected = lambda name: any(fnmatch('/' + name, pattern.rstrip('/')) or fnmatch('/' + name, pattern + '*')
                                 for pattern in patterns)
    return sorted(name for name in before - after if not protected(name))


def _run_hook(project, release, hook):
    """Project build step (assets, translations) inside the release image, on the release copy."""
    script = release / '.setup-build.sh'
    shutil.copy2(hook, script)
    try:
        in_release_image(project, release, 'sh /var/www/html/.setup-build.sh')
    finally:
        script.unlink(missing_ok=True)


def prune_releases(project, keep):
    releases = sorted((path for path in (deploy_directory(project) / 'code').iterdir() if path.is_dir()),
                      key=lambda path: path.stat().st_mtime, reverse=True)
    used = {entry['commit'][:12] for entry in history(project)[-KEEP_RELEASES:]} | {keep.name}
    for path in releases[KEEP_RELEASES:]:
        if path.name not in used:
            shutil.rmtree(path)


def code_path(project, deploy_path):
    """The shop code's directory on the Deploy target."""
    return f'{deploy_path}/{project.web_directory.relative_to(project.root).as_posix()}'


def code_rsync_options(protected):
    """protected: rsync patterns anchored at the code root (protected_paths plus target-only modules)."""
    excludes = [*protected, '/.setup-release']
    # --checksum: archive mtimes are commit times, so size + mtime cannot tell two releases apart.
    # No --delete: the target holds files the code does not track (module vendors, built assets).
    return ['-a', '--checksum', '--human-readable', '--stats', *(f'--exclude={pattern}' for pattern in excludes)]


def module_versions(release):
    versions = {}
    for main in sorted((release / 'modules').glob('*/*.php')):
        if main.stem != main.parent.name:
            continue
        match = MODULE_VERSION.search(main.read_text(errors='replace'))
        if match:
            versions[main.stem] = match[1]
    return versions


def after_rsync(project):
    """The project's hook in the PHP container on the target, once the new code is there (e.g. Doctrine migrations)."""
    hook = project.directory / AFTER_RSYNC_HOOK
    if not hook.is_file():
        return False
    print(f'==> {AFTER_RSYNC_HOOK} in php-fpm', flush=True)
    try:
        # The script is committed project code, not a secret; sh -e stops at its first failing command.
        project.run(['exec', '-T', 'php-fpm', 'sh', '-e', '-c', hook.read_text()])
    except subprocess.CalledProcessError as error:
        raise ValueError(f'{AFTER_RSYNC_HOOK} failed (exit {error.returncode}); the deploy stopped: the new code is on '
                         'the target (recorded as failed), but migrations and the cache clear did not run. '
                         'Fix it and deploy again, or deploy rollback=1') from None
    return True


def clear_cache(project):
    if is_prestashop(project):
        project.run(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'cache-clear'])
        return
    command = shlex.split(project.settings.get('CACHE_CLEAR_COMMAND') or '')
    if not command:
        print('No CACHE_CLEAR_COMMAND for this profile; no application cache was cleared.')
        return
    project.run(['exec', '-T', 'php-fpm', *command])


def php_reload(project):
    """DEPLOY_PHP_RELOAD (on by default): whether a deploy ends with a php-fpm reload."""
    value = project.settings.get('DEPLOY_PHP_RELOAD') or 'on'
    if value not in ('on', 'off'):
        raise ValueError('DEPLOY_PHP_RELOAD must be on or off')
    return value == 'on'


def reload_php(project):
    """USR2 to the php-fpm master (PID 1): new workers with an empty OPcache, so the new code is served.
    Workers finish their requests within process_control_timeout (set in the CORE image)."""
    if not php_reload(project):
        print('DEPLOY_PHP_RELOAD=off: php-fpm was not reloaded; OPcache may serve the previous code until it restarts.')
        return
    # kill is the shell builtin: the PHP images have no procps.
    project.run(['exec', '-T', 'php-fpm', 'sh', '-c', 'kill -USR2 1'])
    print('php-fpm reloaded (OPcache emptied).')


def upgrade_modules(project, release):
    """Run the upgrade of every installed module whose code version is newer than the database's."""
    if str(project.settings.get('PS_VERSION', '')).startswith('1.6'):
        print('PrestaShop 1.6 has no module upgrade command; upgrade changed modules from the back office.')
        return []
    prefix = project.settings.get('PS_DATABASE_PREFIX') or 'ps_'
    installed = dict(row for row in project.database.query(f'SELECT name, version FROM `{prefix}module`;\n') if len(row) == 2)
    changed = [name for name, version in module_versions(release).items()
               if name in installed and newer(version, installed[name])]
    for name in changed:
        print(f'==> upgrading module {name} {installed[name]} -> {module_versions(release)[name]}', flush=True)
        project.run(['exec', '-T', 'php-fpm', 'php', 'bin/console', 'prestashop:module', 'upgrade', name])
    return changed


def newer(candidate, current):
    parse = lambda value: [int(part) if part.isdigit() else part for part in re.split(r'[.\-]', value)]
    try:
        return parse(candidate) > parse(current)
    except TypeError:
        return candidate != current


def deploy_code(project, ref=None, backup=False, rollback=False):
    from project_release import release_settings
    _, deploy_path = release_settings(project)
    php_reload(project)  # a bad value stops the deploy before anything changes
    entries = history(project)
    if rollback:
        # The latest other release whose deploy completed; a failed one is never a rollback target.
        previous = rollback_entry(entries)
        if not previous:
            raise ValueError('No previous deployment to roll back to')
        release = deploy_directory(project) / 'code' / previous['commit'][:12]
        if not (release / '.setup-release').is_file():
            raise ValueError(f'The previous release {previous["commit"][:12]} is no longer on this machine; deploy ref={previous["ref"]}')
        ref, commit = previous['ref'], previous['commit']
        print(f'==> rolling back to {ref} ({commit[:12]}); restore the database from the pre-deploy backup if needed')
        applied = entries[-1].get('migrations') or []
        if applied:
            print(f'    The release rolled back applied {len(applied)} migrations ({", ".join(applied)}); the database keeps them.\n'
                  f'    Undo them with: make db-rollback ENV={project.environment} steps={len(applied)} '
                  '(dry=1 shows the SQL first)')
    else:
        if not ref:
            raise ValueError('Name the shop release to deploy: make deploy ENV=... ref=TAG')
        release, commit = build_code(project, ref)
    target, path = deploy_target(project), code_path(project, deploy_path)
    prestashop = is_prestashop(project)
    excludes = protected_paths(project)
    # PrestaShop modules installed only on the target stay; for other profiles modules/ is plain code.
    protected = sorted(target.directories(path + '/modules') - {child.name for child in (release / 'modules').iterdir() if child.is_dir()}) \
        if prestashop and (release / 'modules').is_dir() else []
    backup_file = None
    if backup:
        from database_backup import backup_database
        print('==> backing up the target database (table registry)', flush=True)
        backup_file = backup_database(project, registry=True)
    print(f'==> rsync {release} -> {target.label}{path}', flush=True)
    target.push(f'{release}/', f'{path}/', *code_rsync_options(excludes + [f'/modules/{name}/' for name in protected]))
    if not manifest_path(project, commit).is_file():
        write_manifest(project, release, commit)
    removed = removed_files(project, entries[-1]['commit'] if entries else None, commit)
    target.remove(path, removed)
    if removed:
        print(f'Removed {len(removed)} files that the previous release shipped and this one does not.')
    elif not entries:
        print('First deploy: nothing removed on the target (files it already had stay).')
    for name in protected:
        print(f'Warning: module {name} exists only on the target and was left as is; '
              f'make module-pull ENV={project.environment} name={name} brings it into the code.')
    # The environment files the containers mount follow the deployed commit too (DEPLOY_PATH elsewhere).
    from project_release import sync_environment
    sync_environment(project, deploy_path)
    entry = {'ref': ref, 'commit': commit, 'at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
             'rollback': rollback, 'backup': str(backup_file) if backup_file else None}
    try:
        after_rsync(project)
    except ValueError:
        # The history tells which code the target holds: the next deploy removes files against it,
        # and rollback=1 returns to the release before it.
        record(project, {**entry, 'failed': 'after-rsync'})
        raise
    if prestashop:
        upgrade_modules(project, release)
    from database_migrations_guarded import migrate, migrations_directory, migrations_enabled
    if migrations_enabled(project) and migrations_directory(project).is_dir():
        # Recorded with the deploy: rollback=1 names them for make db-rollback.
        entry['migrations'] = migrate(project)
    if project.settings.get('SCHEMA_DIRECTORY'):
        from database_schema import schema_report
        schema_report(project)
    clear_cache(project)
    reload_php(project)
    record(project, entry)
    print(f'Deployed {ref} ({commit[:12]}) to ENV={project.environment}.')
    return commit


def module_pull(project, name):
    """Copy a module that exists only on the target into the local shop checkout."""
    from project_release import release_settings
    if not is_prestashop(project):
        raise ValueError('module-pull copies PrestaShop modules; it needs PROFILE=prestashop')
    if not re.fullmatch(r'[A-Za-z0-9_]+', name or ''):
        raise ValueError('Name the module: make module-pull ENV=... name=MODULE')
    _, deploy_path = release_settings(project)
    local = shop_checkout(project) / 'modules' / name
    if local.exists():
        raise ValueError(f'{local} already exists; the code copy wins')
    deploy_target(project).pull(f'{code_path(project, deploy_path)}/modules/{name}/', f'{local}/', '-a', '--exclude=/cache/',
                                '--exclude=/var/')
    print(f'Pulled module {name} into {local}; review and commit it to the shop repository.')
    return local
