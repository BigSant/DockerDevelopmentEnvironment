"""Generate reviewable Doctrine migrations using Git DDL and an isolated DB clone."""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import uuid
from types import SimpleNamespace

from database_schema import (MANIFEST, git, managed_file, mysql_query, read_schema,
                             schema_directory, schema_repository, table_file)
from project_ide import checked_path


def committed_schema(directory, reference):
    root, prefix = schema_repository(directory)
    commit = git(root, 'rev-parse', '--verify', '--end-of-options', reference + '^{commit}').decode().strip()
    files = {}
    for record in git(root, 'ls-tree', '-r', '-z', commit, '--', ':(literal)' + prefix).split(b'\0'):
        if not record:
            continue
        metadata, pathname = record.split(b'\t', 1)
        mode, kind, oid = metadata.decode().split()
        name = os.fsdecode(pathname)[len(prefix):]
        if '/' in name or not managed_file(name):
            continue
        if mode != '100644' or kind != 'blob':
            raise ValueError('Committed schema files must be regular files')
        files[name] = git(root, 'cat-file', 'blob', oid)
    return root, commit, definitions(files)


def definitions(files):
    if MANIFEST not in files:
        raise ValueError('Baseline schema is missing. Export and commit the initial schema before changing the DB.')
    manifest = json.loads(files[MANIFEST])
    if (not isinstance(manifest, dict) or manifest.get('format') != 1 or manifest.get('dialect') != 'mysql'
            or not isinstance(manifest.get('tables'), list)):
        raise ValueError('Unsupported schema manifest')
    tables = {}
    expected = {MANIFEST}
    for table in manifest['tables']:
        if (not isinstance(table, dict) or not isinstance(table.get('name'), str)
                or table.get('file') != table_file(table['name']) or table['name'] in tables):
            raise ValueError('Invalid schema manifest table')
        name = table['file']
        if name not in files:
            raise ValueError('Schema manifest references a missing table file')
        sql = files[name].decode()
        if not sql.startswith('CREATE TABLE ') or '\0' in sql:
            raise ValueError('Expected a CREATE TABLE schema export')
        tables[table['name']] = sql
        expected.add(name)
    if set(files) != expected:
        raise ValueError('Schema files do not match the manifest')
    return tables


def docker(arguments, *, env=None, input=None, timeout=120):
    try:
        result = subprocess.run(['docker', *arguments], input=input, capture_output=True,
                                text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ValueError('Doctrine diff Docker operation timed out') from error
    if result.returncode:
        # Worker reports only a phase; Docker/SQL diagnostics may contain private settings.
        if result.stderr.startswith('Schema diff failed while '):
            raise ValueError(result.stderr.strip())
        raise ValueError('Doctrine diff Docker operation failed; check Docker and build the Doctrine image with make doctrine-build')
    return result.stdout


def compare_in_container(database_image, doctrine_image, config, payload):
    name = 'setup-schema-diff-' + uuid.uuid4().hex
    password = secrets.token_hex(24)
    env = {**os.environ, 'MYSQL_ROOT_PASSWORD': password}
    try:
        # Reuse the exact running DB image but none of its data, init scripts, network or credentials.
        docker(['run', '-d', '--name', name, '--network', 'none', '--pull', 'never',
                '--memory', '512m', '--cpus', '1', '--tmpfs', '/var/lib/mysql:rw,size=512m',
                '--tmpfs', '/docker-entrypoint-initdb.d', '-e', 'MYSQL_ROOT_PASSWORD',
                database_image, '--innodb-buffer-pool-size=64M', '--performance-schema=OFF',
                '--skip-log-bin', '--bind-address=127.0.0.1'], env=env)
        result = docker(['run', '--rm', '-i', '--name', name + '-worker', '--pull', 'never',
                         '--network', 'container:' + name, '--memory', '512m', '--cpus', '1',
                         '--mount', f'type=bind,source={config / "migrations.php"},target=/input/config/migrations.php,readonly',
                         '-e', 'MYSQL_ROOT_PASSWORD', '--entrypoint', 'php', doctrine_image,
                         '/opt/doctrine/schema-diff.php'], env=env, input=json.dumps(payload), timeout=180)
        return json.loads(result)
    finally:
        # Both names are unique to this invocation. No project container is ever removed.
        for container in (name + '-worker', name):
            try:
                result = subprocess.run(['docker', 'rm', '-f', '--volumes', container], capture_output=True, timeout=30)
                removed = result.returncode == 0 or b'No such container' in result.stderr
            except (OSError, subprocess.TimeoutExpired):
                removed = False
            if not removed:
                print(f'Could not remove temporary container {container}; check Docker and remove it manually.')


def doctrine_config(project, model):
    service = model['services'].get('php-doctrine-migrations')
    if not service:
        raise ValueError('Add compose/doctrine.yaml and database/doctrine configuration before using doctrine-diff')
    mounts = [mount for mount in service.get('volumes', [])
              if mount.get('target', '').rstrip('/') == '/tmp/doctrine-migrations/config']
    if len(mounts) != 1 or mounts[0].get('type') != 'bind':
        raise ValueError('Doctrine configuration must be a project bind directory')
    config = Path(mounts[0]['source'])
    if not config.is_absolute() or not config.is_relative_to(project.root):
        raise ValueError('Doctrine configuration must stay inside the project')
    checked_path(SimpleNamespace(directory=project.root), (config / 'migrations.php').relative_to(project.root))
    if not (config / 'migrations.php').is_file():
        raise ValueError('Create database/doctrine/migrations.php first')
    if any(path.is_symlink() for path in config.rglob('*')):
        raise ValueError('Doctrine configuration and migration files must not contain symlinks')
    return config, service['image']


def generate_migration(project, reference='HEAD'):
    if project.environment != 'local':
        raise ValueError('Generate migrations from ENV=local; deploy reviewed files to stage/prod separately')
    lock = checked_path(project, '.generated/doctrine-diff.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Doctrine diff is already running for this project') from error
        return _generate_migration(project, reference)


def _generate_migration(project, reference):
    directory = schema_directory(project)
    repository, commit, before = committed_schema(directory, reference)
    model = project.model()
    config, image = doctrine_config(project, model)
    config_repository, _ = schema_repository(config)
    if config_repository != repository:
        raise ValueError('Schema and migrations must belong to the same Git repository')
    # Resolve immutable images before any disposable container is created.
    doctrine_image = docker(['image', 'inspect', '--format', '{{.Id}}', image]).strip()
    container = project.capture(['ps', '-q', 'database']).strip()
    if not container or len(container.splitlines()) != 1:
        raise ValueError('Start the local database before generating a migration')
    database_image = docker(['inspect', '--format', '{{.Image}}', container]).strip()
    after = definitions(read_schema(project))
    character = mysql_query(project, 'SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=DATABASE();\n').strip().split('\t')
    if len(character) != 2 or any(not re.fullmatch(r'[a-zA-Z0-9_]+', value) for value in character):
        raise ValueError('Cannot determine database character settings')
    fingerprint = hashlib.sha256(json.dumps([commit, before, after], sort_keys=True).encode()).hexdigest()
    marker = '// Schema diff: ' + fingerprint
    for path in config.rglob('Version*.php'):
        if marker in path.read_text():
            print(f'Migration for this schema change already exists: {path}')
            return path
    classname = 'Version' + datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    print(f'Comparing Git schema {commit[:12]} with the local DB in a disposable container... ', flush=True)
    result = compare_in_container(database_image, doctrine_image, config,
                                  {'before': before, 'after': after, 'charset': character[0], 'collation': character[1],
                                   'class': classname, 'fingerprint': fingerprint})
    relative = Path(result['directory'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Migration directory must stay inside Doctrine configuration')
    versions = config / relative
    target = checked_path(SimpleNamespace(directory=project.root), (versions / (classname + '.php')).relative_to(project.root))
    # Do not generate the same uncommitted change twice or overlap another pending migration.
    prefix = versions.relative_to(repository).as_posix()
    pending = git(repository, 'status', '--porcelain', '--untracked-files=all', '--', ':(literal)' + prefix)
    changed = git(repository, 'diff', '--name-only', commit, '--', ':(literal)' + prefix)
    if pending or changed:
        raise ValueError('Migration files differ from the baseline. Commit migrations and their updated schema together, or remove the reviewed draft before regenerating it.')
    if not result['sql_count']:
        print('No supported table schema changes; no migration file created.')
        return None
    # Do not accept a result gathered while the developer was changing the DB or baseline.
    if definitions(read_schema(project)) != after:
        raise ValueError('Local schema changed during generation; retry')
    if committed_schema(directory, reference)[1] != commit:
        raise ValueError('Git baseline changed during generation; retry')
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x') as output:
        output.write(result['code'])
    print(f'Generated {target} ({result["sql_count"]} SQL statements). Review it, run make schema-export, and commit code, schema and migration together.')
    print('Project DB unchanged. Test on a database copy before deployment; automatic data-safe rollback is not inferred.')
    return target
