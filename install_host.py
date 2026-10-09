#!/usr/bin/env python3
"""Prepare this machine once for every setup project: tools, local CA, host Nginx, sudo helpers.

Run as your normal user; sudo asks for the password once. Afterwards make setup / bootstrap run
without sudo prompts. Rerun after updating setup when bootstrap asks for it.
"""

import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
HELPERS = ('host_entry.py', 'host_proxy.py')
INSTALLED = Path('/usr/local/lib/setup')
# Root-owned interpreter named in sudoers (the runner calls the helpers with it); never a user venv.
HOST_PYTHON = '/usr/bin/python3'
SUDOERS = Path('/etc/sudoers.d/shared-setup')
# command -> Debian/Ubuntu package
PACKAGES = {'make': 'make', 'openssl': 'openssl', 'mkcert': 'mkcert', 'nginx': 'nginx', 'rsync': 'rsync',
            'git': 'git', 'curl': 'curl'}


def helper_digest(directory):
    digest = hashlib.sha256()
    for name in HELPERS:
        digest.update((Path(directory) / name).read_bytes())
    return digest.hexdigest()


def installed_helpers_current(installed=INSTALLED, source=ROOT / 'docker'):
    """True when the root-owned helper copies match this setup checkout."""
    try:
        return helper_digest(installed) == helper_digest(source)
    except OSError:
        return False


def missing_tools(which=shutil.which):
    return [command for command in (*PACKAGES, 'docker') if not which(command)]


def compose_problem(run=subprocess.run):
    try:
        version = run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return 'Docker with the Compose plugin is required'
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', version.stdout)
    if version.returncode or not match or tuple(map(int, match.groups())) < (2, 24, 4):
        return 'Docker Compose 2.24.4 or newer is required'
    return None


def sudoers_rule(user, python=HOST_PYTHON, installed=INSTALLED):
    helpers = ', '.join(f'{python} {installed / name} *' for name in HELPERS)
    return (f'# Managed by setup/install-host: local DNS and Nginx routes for development projects.\n'
            f'{user} ALL=(root) NOPASSWD: {helpers}\n')


def plan(user, which=shutil.which, run=subprocess.run):
    """Privileged steps (as argument lists) and problems that need a person."""
    problems, steps = [], []
    missing = missing_tools(which)
    if 'docker' in missing:
        problems.append('Install Docker Engine with the Compose plugin: https://docs.docker.com/engine/install/')
    packages = [PACKAGES[c] for c in missing if c in PACKAGES]
    if packages:
        steps.append(['apt-get', 'install', '-y', *packages])
    if 'docker' not in missing:
        problem = compose_problem(run)
        if problem:
            problems.append(problem)
    if sys.version_info < (3, 10):
        problems.append('Python 3.10 or newer is required')
    steps.append(['install', '-d', '-o', 'root', '-g', 'root', '-m', '0755', str(INSTALLED)])
    for name in HELPERS:
        steps.append(['install', '-o', 'root', '-g', 'root', '-m', '0755', str(ROOT / 'docker' / name), str(INSTALLED / name)])
    return steps, problems


def write_sudoers(user, sudo):
    rule = sudoers_rule(user)
    check = subprocess.run([*sudo, 'visudo', '-cf', '-'], input=rule, text=True, capture_output=True)
    if check.returncode:
        raise SystemExit('The generated sudoers rule did not validate; nothing was installed:\n' + check.stderr)
    subprocess.run([*sudo, 'install', '-o', 'root', '-g', 'root', '-m', '0440', '/dev/stdin', str(SUDOERS)],
                   input=rule, text=True, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='Only report what is missing; change nothing')
    args = parser.parse_args()
    user = os.environ.get('SUDO_USER') or os.environ.get('USER') or 'root'
    steps, problems = plan(user)
    for problem in problems:
        print(f'Problem: {problem}')
    if args.check:
        current = installed_helpers_current()
        print('Host helpers:', 'installed and current' if current else 'missing or outdated')
        print('Sudo rule:', 'present' if SUDOERS.exists() else 'missing')
        for step in steps:
            print('Would run: sudo ' + ' '.join(step))
        return 1 if problems or not current else 0
    if problems:
        print('Resolve the problems above, then rerun setup/install-host.')
        return 1
    sudo = [] if os.geteuid() == 0 else ['sudo']
    for step in steps:
        print('$ sudo ' + ' '.join(step))
        subprocess.run([*sudo, *step], check=True)
    write_sudoers(user, sudo)
    # The local CA lives in the user's profile; mkcert asks for sudo to trust it system-wide.
    subprocess.run(['mkcert', '-install'], check=True)
    if subprocess.run([*sudo, 'systemctl', 'is-active', '--quiet', 'nginx']).returncode:
        subprocess.run([*sudo, 'systemctl', 'enable', '--now', 'nginx'], check=True)
    print('This machine is ready: run make setup in a project.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
