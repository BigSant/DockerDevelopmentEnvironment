"""Runtime readiness and opt-in application probes, without framework assumptions."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

PS_PROFILES = ('ps', 'prestashop')


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(request.full_url), urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise ValueError('Smoke URL redirects to a different origin; check the application domain/configuration')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def smoke(project, timeout=90):
    active = json.loads(project.capture(['config', '--format', 'json']))['services']
    running = set(project.capture(['ps', '--status', 'running', '--services']).split())
    missing = set(active) - running
    if missing:
        raise ValueError('Services are not running: ' + ', '.join(sorted(missing)))
    profile = project.settings.get('PROFILE', '')
    if profile in PS_PROFILES:
        if 'php-fpm' not in active:
            raise ValueError('PrestaShop smoke requires php-fpm')
        project.capture(['exec', '-T', 'php-fpm', 'php', '/opt/setup/runtime/prestashop-command.php', 'check'])
        print('PrestaShop configuration and database connection: OK')
    url = project.settings.get('SMOKE_URL', '')
    if not url and profile in PS_PROFILES:
        domain = project.settings.get('DOMAIN', '')
        port = project.settings.get('LOCALHOST_PORT', '')
        if domain and port and port != '0': url = f'http://{domain}:{port}/'
    if not url:
        print('Services running; no HTTP probe configured (set SMOKE_URL for this project).')
        return
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('SMOKE_URL must be an HTTP(S) URL without credentials')
    expected = project.settings.get('SMOKE_EXPECT', '')
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(SameOriginRedirect())
    while True:
        try:
            with opener.open(url, timeout=min(60, max(1, deadline-time.monotonic()))) as response:
                body = response.read(2 * 1024 * 1024)
                if not 200 <= response.status < 300:
                    raise ValueError('Unexpected HTTP status')
                if expected and expected.encode() not in body:
                    raise ValueError('HTTP response does not contain SMOKE_EXPECT')
                print(f'HTTP smoke: {response.status}; {parsed.scheme}://{parsed.netloc}{parsed.path or "/"}')
                return
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            if time.monotonic() >= deadline:
                raise ValueError('HTTP smoke failed; check URL, redirect origin, TLS and SMOKE_EXPECT') from error
            time.sleep(min(1, max(0, deadline-time.monotonic())))


def start_project(project, timeout=90):
    project.run(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', str(timeout)])
    smoke(project, timeout)
