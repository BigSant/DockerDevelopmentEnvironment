"""Compatibility reporting and namespacing of locally built shared images."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
API_VERSION = '1'


def image_suffix(profile, build_arguments=None):
    digest = hashlib.sha256(profile.encode())
    digest.update(json.dumps(build_arguments or {}, sort_keys=True).encode())
    base = ROOT / 'docker'
    paths = [base / 'Dockerfile', base / '.env']
    for directory in (base / 'docker', base / 'profile'):
        paths += [p for p in directory.rglob('*') if p.is_file() and p.name != 'AGENTS.md']
    for path in sorted(paths):
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes())
    return '-s' + digest.hexdigest()[:12]


def setup_info():
    version = (ROOT / 'VERSION').read_text().strip()
    commit = subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], text=True).strip()
    dirty = bool(subprocess.check_output(['git', '-C', str(ROOT), 'status', '--porcelain'], text=True).strip())
    print(f'Shared setup {version}; API {API_VERSION}; commit {commit}' + (' (working changes)' if dirty else ''))
