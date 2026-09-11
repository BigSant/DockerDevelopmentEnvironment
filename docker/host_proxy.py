#!/usr/bin/env python3
"""Install one local host Nginx route without replacing unrelated configurations."""
import argparse
import fcntl
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import os

MARKER = '# Managed by shared setup host_proxy\n'


def configuration(domain, port, ssl):
    if not re.fullmatch(r'[a-z][a-z0-9-]*(?:\.[a-z0-9][a-z0-9-]*)+', domain):
        raise ValueError('Invalid local hostname')
    if not str(port).isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError('Expected a project HTTP port between 1024 and 65535')
    ssl = Path(ssl).resolve()
    if re.search(r'[\x00-\x1f"$\\;{}]', str(ssl)):
        raise ValueError('Unsupported certificate path')
    if not all((ssl / name).is_file() for name in ('domain.crt', 'domain.key')):
        raise ValueError('Prepare the project TLS certificate before its Nginx route')
    variable = 'setup_connection_' + domain.replace('.', '_').replace('-', '_')
    result = MARKER + f'''map $http_upgrade ${variable} {{
    default upgrade;
    '' close;
}}
'''
    for tls in (False, True):
        listen = ('listen 443 ssl http2;\n    listen [::]:443 ssl http2;\n'
                  f'    ssl_certificate "{ssl}/domain.crt";\n'
                  f'    ssl_certificate_key "{ssl}/domain.key";') if tls else 'listen 80;\n    listen [::]:80;'
        result += f'''server {{
    {listen}
    server_name {domain} pma.{domain} mailpit.{domain};
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection ${variable};
        proxy_buffering off;
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }}
}}
'''
    return result.encode()


def write(path, contents):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(contents)
    try:
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install(domain, port, ssl, nginx=Path('/etc/nginx'), run=subprocess.run):
    contents = configuration(domain, port, ssl)
    available, enabled = nginx / 'sites-available', nginx / 'sites-enabled'
    if not available.is_dir() or not enabled.is_dir():
        raise ValueError('Install host Nginx with sites-available/sites-enabled first')
    target, link = available / (domain + '.conf'), enabled / (domain + '.conf')
    if target.is_symlink():
        raise ValueError('Existing Nginx configuration symlink preserved')
    original = target.read_bytes() if target.exists() else None
    if original is not None and not original.startswith(MARKER.encode()):
        raise ValueError('Existing project Nginx configuration is not managed by setup; preserved: ' + str(target))
    had_link = link.is_symlink()
    if (link.exists() or had_link) and (not had_link or link.resolve() != target.resolve()):
        raise ValueError('Existing Nginx enabled site preserved: ' + str(link))
    try:
        write(target, contents)
        if not had_link:
            link.symlink_to(target)
        run(['nginx', '-t'], check=True, capture_output=True)
        run(['systemctl', 'reload', 'nginx'], check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        if not had_link:
            link.unlink(missing_ok=True)
        if original is None:
            target.unlink(missing_ok=True)
        else:
            write(target, original)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('domain')
    parser.add_argument('port')
    parser.add_argument('ssl', type=Path)
    args = parser.parse_args()
    try:
        with open('/run/lock/shared-setup-nginx.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            install(args.domain, args.port, args.ssl)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error) + '; for Nginx diagnostics run sudo nginx -t', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
