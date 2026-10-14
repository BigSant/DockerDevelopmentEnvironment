"""D model: build images on this (build) machine, carry them to the target, run them there.

The target needs only Docker, the code, data and env: images carry the setup runtime and CORE
configuration, and the release compose model maps project paths to DEPLOY_PATH and refers to no
setup file. The target is reached through a Docker context (ssh://user@host for servers). The
model carries env values (passwords), so it is rendered at deploy time and piped, never written.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from project_target import deploy_target

DOCKER_ROOT = Path(__file__).resolve().parent
SETUP_ROOT = DOCKER_ROOT.parent
LOCK_NAME = 'images.lock.json'
# Setup files that images now contain; their local mounts are dropped from a release.
BAKED_MOUNTS = {'/opt/setup/runtime', '/opt/setup/database-profile'}
# Host system paths that exist on every Linux target; kept read-only in a release (the metrics service and its
# Docker collector, which has no network and only reads the Docker API).
HOST_MOUNTS = {('/proc', '/host/proc'), ('/var/run/docker.sock', '/var/run/docker.sock')}


def release_directory(project):
    return project.directory / '.generated/release' / project.environment


def setup_version(allow_dirty=False, tagged=False):
    """The setup checkout the release is built from; never uncommitted, and for prod exactly a release tag."""
    result = subprocess.run(['git', '-C', str(SETUP_ROOT), 'describe', '--tags', '--always', '--dirty'],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Cannot determine the setup version; build releases from a setup git checkout')
    version = result.stdout.strip()
    if version.endswith('-dirty') and not allow_dirty:
        raise ValueError(f'Setup has uncommitted changes ({version}); commit or tag it before a release')
    if tagged and not allow_dirty and not re.fullmatch(r'v\d+\.\d+\.\d+', version):
        raise ValueError(f'ENV=prod releases build from a setup release tag; setup is at {version}. '
                         'Check out the tag (git -C setup checkout vX.Y.Z) first')
    return version


def release_settings(project, remote=True):
    """DEPLOY_CONTEXT, DEPLOY_PATH and the server account; all described in env, none tied to this machine."""
    if project.environment not in ('stage', 'prod'):
        raise ValueError('Releases are built for ENV=stage or ENV=prod')
    settings = project.settings
    missing = [name for name in ('APP_UID', 'APP_GID', 'DEPLOY_PATH') + (('DEPLOY_CONTEXT',) if remote else ())
               if not settings.get(name)]
    if missing:
        raise ValueError(f'ENV={project.environment} release needs {", ".join(missing)} in env/{project.environment}.env '
                         '(APP_UID/APP_GID: the hosting account on the server)')
    path = settings['DEPLOY_PATH']
    if not path.startswith('/') or '..' in path.split('/'):
        raise ValueError('DEPLOY_PATH must be the absolute project root on the target (the directory holding app/ and data/)')
    if remote and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', settings['DEPLOY_CONTEXT']):
        raise ValueError('DEPLOY_CONTEXT must name a Docker context (docker context create NAME --docker host=ssh://...)')
    return settings.get('DEPLOY_CONTEXT'), path.rstrip('/') or '/'


def release_model(project, deploy_path):
    """The active services as a self-contained compose model for the target."""
    model = project.active_model()
    local_root = project.root.resolve()
    for name, service in model['services'].items():
        service.pop('build', None)
        # The model already holds the selected services (SERVICES, COMPOSE_PROFILES); compose up on the target
        # runs without COMPOSE_PROFILES and would skip cron, backup or a project's profiled service.
        service.pop('profiles', None)
        volumes = []
        for volume in service.get('volumes', []):
            if volume.get('type') != 'bind':
                volumes.append(volume)
                continue
            source = Path(volume['source']).resolve()
            if (volume['source'], volume.get('target')) in HOST_MOUNTS and volume.get('read_only'):
                volumes.append(volume)
            elif source.is_relative_to(local_root):
                volume['source'] = deploy_path + '/' + source.relative_to(local_root).as_posix()
                volumes.append(volume)
            elif source.is_relative_to(SETUP_ROOT.resolve()) and volume.get('target', '').rstrip('/') in BAKED_MOUNTS:
                continue
            elif source.is_relative_to(SETUP_ROOT.resolve()):
                raise ValueError(f'{name} mounts a setup file ({source}); the target has no setup checkout')
            else:
                raise ValueError(f'{name} mounts {source}, outside the project; a release maps only project paths')
        if 'volumes' in service:
            service['volumes'] = volumes
    model['name'] = project.name
    return model


def environment_mounts(project, model, root=None):
    """The environment repository's files the containers mount (config/, the schema directory, ...), relative
    to the project root: what a target needs besides the code (rsynced by make deploy) and its own data.
    root: where the model's sources live (the project root, or DEPLOY_PATH in a release model).
    .generated/ (backups, build state) never leaves this machine."""
    local_root = project.root.resolve()
    root = Path(root) if root else local_root
    inside = lambda path: root / Path(path).resolve().relative_to(local_root)
    environment = inside(project.directory)
    skipped = [inside(project.web_directory), inside(project.data_directory), environment / '.generated']
    found = set()
    for service in model['services'].values():
        for volume in service.get('volumes', []):
            if volume.get('type') != 'bind':
                continue
            source = Path(volume['source'])
            if source != environment and source.is_relative_to(environment) \
                    and not any(source.is_relative_to(path) for path in skipped):
                found.add(source)
    kept = [path for path in sorted(found) if not any(path != other and path.is_relative_to(other) for other in found)]
    return [path.relative_to(root).as_posix() for path in kept]


def sync_environment(project, deploy_path, model=None):
    """Copy the mounted environment files to DEPLOY_PATH when the target is not this checkout itself.

    On the build server's own build root (DEPLOY_PATH = the project root, no DEPLOY_SSH) the containers
    mount the checkout directly. Anywhere else, a local directory or a server, they mount DEPLOY_PATH's copy,
    which follows the checked-out commit on every deploy-images and deploy (--delete: a file removed from
    the repository goes on the target too)."""
    target = deploy_target(project)
    if target.local and Path(deploy_path).resolve() == project.root.resolve():
        return []
    if model is None:
        paths = environment_mounts(project, project.active_model())
    else:  # a release model: its sources are already DEPLOY_PATH's
        paths = environment_mounts(project, model, deploy_path)
    if not paths:
        return []
    target.mkdir(sorted({str(Path(deploy_path, path).parent) for path in paths}))
    for path in paths:
        source = project.root / path
        directory = source.is_dir()
        target.push(f'{source}/' if directory else str(source), f'{deploy_path}/{path}' + ('/' if directory else ''),
                    '-a', '--checksum', *(['--delete'] if directory else []))
    print(f'==> environment files to {target.label}{deploy_path}: {" ".join(paths)}', flush=True)
    return paths


def runtime_directories(project, model, deploy_path):
    """The release model's bind sources inside the code and data directories on DEPLOY_PATH: what the
    containers write. Configuration is not among them (sync_environment copies it)."""
    local_root = project.root.resolve()
    on_target = lambda path: Path(deploy_path, Path(path).resolve().relative_to(local_root))
    bases = [on_target(project.web_directory), on_target(project.data_directory)]
    found = set()
    for service in model['services'].values():
        for volume in service.get('volumes', []):
            source = Path(volume['source']) if volume.get('type') == 'bind' else None
            if source is None or not any(source == base or source.is_relative_to(base) for base in bases):
                continue
            local = local_root / source.relative_to(deploy_path)
            # File mounts are supplied by the project, like initialize_directories leaves them.
            if local.is_file() or (local.suffix and not local.is_dir()):
                continue
            found.add(str(source))
    return sorted(found)


def prepare_runtime_directories(project, model, deploy_path):
    """Create those directories as the deploying account before the containers start. Docker creates a missing
    bind source as root, and the containers run as APP_UID: on a first deploy php-fpm could not write its code
    directory nor MySQL its data, and both restarted until compose gave up on them."""
    paths = runtime_directories(project, model, deploy_path)
    target = deploy_target(project)
    target.mkdir(paths)
    if paths and target.local:
        locked = [path for path in paths if not os.access(path, os.W_OK)]
        if locked:
            raise ValueError('These directories are not writable by this account (Docker created them as root on an '
                             'earlier deploy?): ' + ' '.join(locked) + f'. Give them to it once: sudo chown -R '
                             f'{os.getuid()}:{os.getgid()} ' + ' '.join(locked))
    return paths


def published_ports(model):
    """(host address, port) of every published port in the model; '' for all addresses."""
    return sorted({(str(port.get('host_ip') or ''), str(port['published'])) for service in model['services'].values()
                   for port in service.get('ports', []) if isinstance(port, dict) and port.get('published')})


def check_ports(project, context, compose_project, model):
    """Stop before anything starts when another process holds a published port on the target.

    Started anyway, Docker gives up on that container's networking halfway: it is left on some of its networks
    only, and later deploys restart it unchanged. Only a target this machine can look at is checked (its own
    Docker, or DEPLOY_SSH); ports this project's containers publish already are theirs."""
    target = deploy_target(project)
    if target.local and context != 'default':
        return
    wanted = published_ports(model)
    if not wanted:
        return
    listeners = target.listening()
    if listeners is None:
        return
    own = subprocess.run(['docker', '--context', context, 'ps', '--filter', f'label=com.docker.compose.project={compose_project}',
                          '--format', '{{.Ports}}'], capture_output=True, text=True).stdout
    owned = set(re.findall(r'(?:^|[\s,])\[?([0-9a-fA-F.:]*?)\]?:(\d+)->', own))
    owned_ports = {port for _, port in owned}
    wildcard = {'', '0.0.0.0', '*', '::'}
    taken = [f'{address or "*"}:{port}' for address, port in wanted if port not in owned_ports and any(
        port == held_port and (held in wildcard or address in wildcard or held == address) for held, held_port in listeners)]
    if taken:
        raise ValueError(f'Port {", ".join(taken)} is already in use on the target by another process (a host web server, '
                         'another stack); free it first, nothing was started')


def recreate_detached(project, context, compose_project, model):
    """Remove this project's containers that miss one of the networks the model gives them; compose up recreates them.

    A start that failed (a taken port) can leave a container on some of its networks only; compose sees an
    unchanged config and just restarts it, so it never reaches its upstreams ("host not found")."""
    ids = subprocess.run(['docker', '--context', context, 'ps', '-aq', '--filter', f'label=com.docker.compose.project={compose_project}'],
                         capture_output=True, text=True).stdout.split()
    if not ids:
        return []
    found = subprocess.run(['docker', '--context', context, 'inspect', *ids], capture_output=True, text=True)
    if found.returncode:
        return []
    names = {key: (value or {}).get('name', key) for key, value in (model.get('networks') or {}).items()}
    removed = []
    for container in json.loads(found.stdout):
        service = model['services'].get((container.get('Config', {}).get('Labels') or {}).get('com.docker.compose.service', ''))
        if not service or not service.get('networks'):
            continue
        attached = set((container.get('NetworkSettings') or {}).get('Networks') or {})
        missing = sorted(names.get(key, key) for key in service['networks'] if names.get(key, key) not in attached)
        if missing:
            name = container['Name'].lstrip('/')
            print(f'==> {name} is not on {", ".join(missing)} (an earlier start failed halfway): recreating it', flush=True)
            subprocess.run(['docker', '--context', context, 'rm', '-f', name], check=True, capture_output=True)
            removed.append(name)
    return removed


def image_names(model):
    return sorted({service['image'] for service in model['services'].values() if service.get('image')})


def external_images(project, model):
    """Images of active services that no service builds (e.g. getmeili/meilisearch from a project compose file)."""
    full = project.model()['services']
    built = {service.get('image') for service in full.values() if service.get('build')}
    return sorted({service['image'] for name, service in model['services'].items()
                   if service.get('image') and not full.get(name, {}).get('build') and service['image'] not in built})


def image_id(name):
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ''


def pull_external(images):
    """Pull the external images this machine lacks, so docker save can ship them with the built ones."""
    for image in images:
        if image_id(image):
            continue
        print(f'==> pulling {image}', flush=True)
        result = subprocess.run(['docker', 'pull', image], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f'Cannot pull {image} for the release: {result.stderr.strip()[-300:]}')


def base_digests(project):
    """Digests of the external images each built image starts from, as pulled on this machine."""
    full = project.model()
    references = set()
    for service in full['services'].values():
        build = service.get('build')
        if not build:
            continue
        context = Path(build['context'])
        dockerfile = Path(build.get('dockerfile') or 'Dockerfile')
        text = (dockerfile if dockerfile.is_absolute() else context / dockerfile).read_text()
        arguments = {**{m[1]: m[2] for m in re.finditer(r'(?m)^ARG\s+(\w+)=["\']?([^"\'\s]*)', text)},
                     **{k: str(v) for k, v in (build.get('args') or {}).items()}}
        for match in re.finditer(r'(?im)^FROM\s+(\S+)', text):
            reference = re.sub(r'\$\{?(\w+)\}?', lambda m: arguments.get(m[1], ''), match[1])
            local = {s.get('image') for s in full['services'].values()}
            if reference and reference not in local and not reference.startswith(('php-fpm-base-', project.settings['PROJECT_NAME'] + '-')):
                references.add(reference)
    return repo_digests(references)


def repo_digests(references):
    """Registry digest of each image as pulled on this machine (None: built or loaded locally)."""
    digests = {}
    for reference in sorted(references):
        result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{json .RepoDigests}}', reference],
                                capture_output=True, text=True)
        found = json.loads(result.stdout or 'null') if result.returncode == 0 else None
        digests[reference] = found[0] if found else None
    return digests


def trivy_report(images):
    """Critical vulnerabilities per image; a warning only, never a failed release."""
    trivy = shutil.which('trivy')
    if not trivy:
        print('Warning: trivy is not installed; the vulnerability check was skipped (https://trivy.dev).')
        return None
    report = {}
    for image in images:
        result = subprocess.run([trivy, 'image', '--quiet', '--severity', 'CRITICAL', '--format', 'json', image],
                                capture_output=True, text=True)
        try:
            findings = [v for r in json.loads(result.stdout).get('Results', []) for v in r.get('Vulnerabilities') or []]
        except ValueError:
            print(f'Warning: trivy could not scan {image}')
            continue
        report[image] = len(findings)
        print(f'Trivy {image}: {len(findings)} critical' + (' — review before deploying' if findings else ''))
    return report


def build_release(project, allow_dirty=False):
    """Build the images for ENV=stage/prod here and write the lock file and release compose model."""
    _, deploy_path = release_settings(project, remote=False)
    version = setup_version(allow_dirty, tagged=project.environment == 'prod')
    print(f'==> building {project.environment} images from setup {version}', flush=True)
    from project_images import build_images
    build_images(project)
    model = release_model(project, deploy_path)
    images = image_names(model)
    external = external_images(project, model)
    pull_external(external)
    ids = {name: image_id(name) for name in images}
    missing = [name for name, found in ids.items() if not found]
    if missing:
        raise ValueError('Release images are not on this machine: ' + ', '.join(missing) + '; the transfer would fail')
    lock = {'setup': version, 'environment': project.environment, 'project': project.name, 'images': ids,
            'bases': base_digests(project), 'external': repo_digests(external)}
    directory = release_directory(project)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # The lock holds no secrets; the compose model (with passwords) is rendered at deploy time only.
    (directory / LOCK_NAME).write_text(json.dumps(lock, indent=2, sort_keys=True) + '\n')
    missing = [reference for reference, digest in lock['bases'].items() if not digest]
    if missing:
        print('Warning: no registry digest for ' + ', '.join(missing) + ' (built or loaded locally)')
    trivy_report(images)
    print(f'Release {project.environment}: {len(images)} images, lock {directory / LOCK_NAME}')
    return lock


def deploy_images(project, transfer=True):
    """Load the release images into the target's Docker and start the release model there."""
    context, deploy_path = release_settings(project)
    directory = release_directory(project)
    if not (directory / LOCK_NAME).is_file():
        raise ValueError(f'Build the release first: make release ENV={project.environment}')
    lock = json.loads((directory / LOCK_NAME).read_text())
    images = sorted(lock['images'])
    if transfer:
        print(f'==> sending {len(images)} images to Docker context {context}', flush=True)
        save = subprocess.Popen(['docker', 'save', *images], stdout=subprocess.PIPE)
        load = subprocess.run(['docker', '--context', context, 'load'], stdin=save.stdout, capture_output=True, text=True)
        save.stdout.close()
        if save.wait() or load.returncode:
            raise ValueError(f'Image transfer to {context} failed: {load.stderr.strip()[-300:]}')
    print(f'==> starting {lock["project"]} on {context}', flush=True)
    # Rendered now from env (secrets may come from the pipeline's environment) and piped, never stored.
    model = release_model(project, deploy_path)
    # The containers mount the environment files from DEPLOY_PATH: they are there before anything starts.
    sync_environment(project, deploy_path, model)
    prepare_runtime_directories(project, model, deploy_path)
    check_ports(project, context, lock['project'], model)
    recreate_detached(project, context, lock['project'], model)
    subprocess.run(['docker', '--context', context, 'compose', '-p', lock['project'], '-f', '-',
                    'up', '-d', '--no-build', '--pull', 'never', '--remove-orphans'],
                   input=json.dumps(model), text=True, check=True)
    return images
