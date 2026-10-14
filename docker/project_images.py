"""Preview and remove setup-built images that no container uses and no project expects."""

import json
from pathlib import Path
import re
import subprocess

# Images built before labels existed are recognised by their setup naming scheme.
LEGACY_NAME = re.compile(
    r'^(php-fpm-base-|mysql-|mariadb-|webserver-apache-|nginx-proxy-|pma-|mailpit-|memcached-|'
    r'playwright-|php-phpstan-|php-cs-|php-doctrine-migrations-|[a-z0-9][a-z0-9_-]*-php-fpm-(local|stage|prod|test))')


def build_images(project):
    """Build this project's images, locally built parents first: the PHP base, then the CORE layer on it (both
    only in the build_only profile, which nothing starts), then every other image, the project's PHP among them."""
    project.run(['build', 'php-fpm-base'], profiles='build_only')
    project.run(['build', 'php-fpm-core'], profiles='build_only')
    project.run(['build'])


def _docker(*arguments):
    result = subprocess.run(['docker', *arguments], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'docker {arguments[0]} failed; is Docker running?')
    return result.stdout


def setup_images():
    """Setup images as {id: {'names': [...], 'size': str}}; foreign images are never included."""
    images = {}
    for line in _docker('images', '--no-trunc', '--format', '{{json .}}').splitlines():
        row = json.loads(line)
        name = row['Repository'] if row['Tag'] in ('latest', '<none>') else f"{row['Repository']}:{row['Tag']}"
        labels = row.get('Labels') or ''
        managed = 'setup.managed=1' in labels.split(',')
        if not managed and not LEGACY_NAME.match(row['Repository']):
            continue
        entry = images.setdefault(row['ID'], {'names': [], 'size': row.get('Size', '')})
        entry['names'].append(name)
    return images


def used_image_ids():
    ids = _docker('ps', '-a', '-q').split()
    if not ids:
        return set()
    return {container['Image'] for container in json.loads(_docker('inspect', *ids))}


def expected_image_names(project):
    """Images the current sources of every grouped project beside this one would use."""
    names = set()
    from project import Project
    for directory in sorted(project.root.parent.glob('*/app')) + [project.directory]:
        if not (directory / 'env/common.env').is_file() or not (directory / 'env/local.env').is_file():
            continue
        try:
            current = project if directory == project.directory else Project(directory)
            names.update(s['image'] for s in current.model()['services'].values() if s.get('image'))
        except (ValueError, OSError):
            continue
    return names


def released_image_ids(project):
    """Image ids that a stage/prod release lock of any grouped project still needs (deploy, rollback)."""
    ids = set()
    for lock in sorted(project.root.parent.glob('*/app/.generated/release/*/images.lock.json')) + \
            sorted(project.directory.glob('.generated/release/*/images.lock.json')):
        try:
            ids.update(json.loads(lock.read_text()).get('images', {}).values())
        except (ValueError, OSError):
            continue
    return ids


def plan_prune(images, used, expected):
    keep = lambda entry: any(n in expected or n.split(':')[0] in expected for n in entry['names'])
    return {image_id: entry for image_id, entry in images.items()
            if image_id not in used and not keep(entry)}


def prune_images(project, apply=False, cache=False):
    plan = plan_prune(setup_images(), used_image_ids() | released_image_ids(project), expected_image_names(project))
    if not plan:
        print('No unused outdated setup images.')
    for image_id, entry in sorted(plan.items(), key=lambda item: item[1]['names']):
        print(f"{', '.join(sorted(entry['names']))}\t{entry['size']}")
    if plan and not apply:
        print(f'{len(plan)} images can be removed; run again with apply=1 to remove them.')
    for image_id in plan if apply else ():
        result = subprocess.run(['docker', 'rmi', image_id], capture_output=True, text=True)
        if result.returncode:
            print(f'Kept {image_id[:19]}: {result.stderr.strip()}')
    if cache:
        if apply:
            subprocess.run(['docker', 'builder', 'prune', '-f'], check=True)
        else:
            print(_docker('system', 'df').strip())
            print('cache=1 apply=1 clears the whole Docker build cache, including other projects\' layers.')
