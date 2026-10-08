"""Compatibility reporting and namespacing of locally built shared images."""

import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
API_VERSION = '1'


# A built image whose Dockerfile starts FROM another locally built image.
SUFFIX_DEPENDENCIES = {'php-fpm-core': 'php-fpm-base', 'php-fpm': 'php-fpm-core'}
IGNORED_NAMES = {'AGENTS.md', '__pycache__', '.DS_Store'}


def suffix_variable(service):
    """Compose variable carrying one service's image fingerprint."""
    return 'SETUP_IMAGE_SUFFIX_' + re.sub(r'[^A-Z0-9]', '_', service.upper())


def _instructions(text):
    joined = re.sub(r'\\\n', ' ', text)
    for line in joined.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            yield line


def dockerfile_sources(text, arguments):
    """Context-relative COPY/ADD sources; multi-stage --from copies are not context files."""
    values = {}
    for line in _instructions(text):
        if line.upper().startswith('ARG '):
            name, _, default = line[4:].strip().partition('=')
            values.setdefault(name.strip(), default.strip().strip('"\''))
    values.update({k: str(v) for k, v in (arguments or {}).items()})
    expand = lambda value: re.sub(r'\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?', lambda m: values.get(m.group(1), ''), value)
    sources = []
    for line in _instructions(text):
        keyword = line.split(None, 1)[0].upper()
        if keyword not in ('COPY', 'ADD'):
            continue
        parts = shlex.split(line)[1:]
        if any(p.startswith('--from') for p in parts):
            continue
        parts = [p for p in parts if not p.startswith('--')]
        sources += [expand(p) for p in parts[:-1]]
    return sources


def _hash_path(digest, path, base):
    if path.is_dir():
        for child in sorted(path.rglob('*')):
            if child.is_file() and not IGNORED_NAMES.intersection(child.parts):
                _hash_path(digest, child, base)
        return
    if path.is_file():
        digest.update(path.relative_to(base).as_posix().encode() if path.is_relative_to(base) else str(path).encode())
        digest.update(path.read_bytes())


def build_fingerprint(service, build, profile, dependency=''):
    """Hash only the inputs this one image is built from."""
    digest = hashlib.sha256()
    arguments = dict(build.get('args') or {})
    # A dependency's fingerprint replaces its placeholder in the arguments.
    for placeholder in ('SETUP_IMAGE_SUFFIX', 'SETUP_PHP_IMAGE'):
        arguments.pop(placeholder, None)
    digest.update(json.dumps([API_VERSION, service, profile, dependency, build.get('target'),
                              sorted(arguments.items())]).encode())
    context = Path(build['context'])
    dockerfile = Path(build.get('dockerfile') or 'Dockerfile')
    dockerfile = dockerfile if dockerfile.is_absolute() else context / dockerfile
    text = dockerfile.read_text()
    digest.update(text.encode())
    for source in dockerfile_sources(text, arguments):
        matches = sorted(context.glob(source.lstrip('/'))) if any(c in source for c in '*?[') else [context / source.lstrip('/')]
        for path in matches:
            resolved = path.resolve()
            if resolved.is_relative_to(context.resolve()):
                _hash_path(digest, resolved, context.resolve())
    for name, location in sorted((build.get('additional_contexts') or {}).items()):
        path = Path(str(location))
        if path.is_dir():
            digest.update(name.encode())
            _hash_path(digest, path.resolve(), path.resolve())
    return '-s' + digest.hexdigest()[:12]


def image_suffixes(profile, services):
    """Fingerprint per built service, dependencies first."""
    built = {name: service['build'] for name, service in services.items() if service.get('build')}
    result = {}

    def resolve(name):
        if name not in result:
            dependency = SUFFIX_DEPENDENCIES.get(name)
            prefix = resolve(dependency) if dependency in built else ''
            result[name] = build_fingerprint(name, built[name], profile, prefix)
        return result[name]

    for name in sorted(built):
        resolve(name)
    return result


def setup_info():
    version = (ROOT / 'VERSION').read_text().strip()
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip())
    print(f'Shared setup {version}; API {API_VERSION}; commit {commit}' + (' (working changes)' if dirty else ''))
