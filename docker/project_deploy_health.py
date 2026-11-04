"""make deploy-health ENV=stage|prod|<extra> [attempt=ID] [rollback=1]: the HTTP check of a deployed release.

The pages: the project's fixed smoke list DEPLOY_HEALTH_PATHS (env/common.env, an environment's example may override
it) plus DEPLOY_HEALTH_EXTRA from the caller's process environment (AI OS passes the changed pages), at most 30,
duplicates dropped. One GET each, no redirect followed, 10 s for the whole request; 200-399 passes, a timeout, a connection error
or a 5xx is tried once more after 5 s. Only the status, the time and the tries are kept, never a body.

Where: on a target on this host (no DEPLOY_SSH) http://<PUBLISH_ADDRESS, else 127.0.0.1>:<LOCALHOST_PORT><path> with
Host: <first DOMAIN> and X-Forwarded-Proto: https, which nginx-proxy trusts from loopback. On another server
https://<first DOMAIN><path>, connected to DEPLOY_HEALTH_ADDRESS when set (as METRICS_PROBE_ADDRESS: the name stays
in SNI, the Host header and the certificate check).

The release: DEPLOY_HEALTH_RELEASE_PATH (e.g. /_health) must answer JSON whose `release` is the deployed commit, which
proves php-fpm serves the new code; it is asked for up to 30 s before the other pages.

The result goes into the environment's last history entry: `health: ok|failed`, and on failure `failed: "health"`,
so the release is never a rollback target and setup-server's metrics count a failed deploy. With attempt=ID the last
entry must be that attempt's (rollback=1: its rollback), and the attempt file gets the `health` (or
`health_after_rollback`) event and its outcome.
"""

from http.client import HTTPConnection, HTTPException, HTTPSConnection
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import threading
import time

from project_attempts import Attempt, check_attempt, read_history, write_history
from project_env_files import file_settings

PATH = re.compile(r'/[A-Za-z0-9._~/%-]{0,200}')
MAX_PATHS = 30
TIMEOUT = 10
RETRY_DELAY = 5
RELEASE_WAIT = 30
RELEASE_BODY_LIMIT = 64 * 1024
USAGE, FAILED = 64, 1
EXTRA_VARIABLE = 'DEPLOY_HEALTH_EXTRA'


class Refusal(ValueError):
    """Nothing to check, or arguments that do not match the history; nothing was changed."""


def health_paths(values, environ):
    """(the pages, the release page): validated, de-duplicated, in listed order."""
    listed = str(values.get('DEPLOY_HEALTH_PATHS') or '').split() + str(environ.get(EXTRA_VARIABLE) or '').split()
    release = str(values.get('DEPLOY_HEALTH_RELEASE_PATH') or '')
    for path in listed + ([release] if release else []):
        if not PATH.fullmatch(path):
            raise Refusal(f'Health check path refused: {path!r} (a path such as /admin/login: letters, digits, . _ ~ / % -)')
    paths = list(dict.fromkeys(listed + ([release] if release else [])))
    if len(paths) > MAX_PATHS:
        raise Refusal(f'At most {MAX_PATHS} health check paths (DEPLOY_HEALTH_PATHS, {EXTRA_VARIABLE} and '
                      f'DEPLOY_HEALTH_RELEASE_PATH); {len(paths)} were given')
    return paths, release


def configured(values, environ):
    """Whether the project opted in: it names smoke pages or a release page, or the caller passes pages."""
    return any(str(source.get(key) or '').strip() for source, key in (
        (values, 'DEPLOY_HEALTH_PATHS'), (values, 'DEPLOY_HEALTH_RELEASE_PATH'), (environ, EXTRA_VARIABLE)))


class Endpoint:
    """Where the pages are asked: this host's published port, or the domain over HTTPS (optionally at an address)."""

    def __init__(self, values):
        domains = str(values.get('DOMAIN') or '').split()
        if not domains:
            raise Refusal('The health check needs DOMAIN (env/<env>.env.example): its first name is the Host asked')
        self.domain = domains[0]
        self.local = not values.get('DEPLOY_SSH')
        if self.local:
            port = str(values.get('LOCALHOST_PORT') or '')
            if not port.isdigit() or not 0 < int(port) < 65536:
                raise Refusal('The health check of a target on this host needs LOCALHOST_PORT (the published HTTP port)')
            self.address, self.port = str(values.get('PUBLISH_ADDRESS') or '127.0.0.1'), int(port)
        else:
            self.address, self.port = str(values.get('DEPLOY_HEALTH_ADDRESS') or ''), 443
            if self.address:
                try:
                    ipaddress.ip_address(self.address)
                except ValueError:
                    raise Refusal('DEPLOY_HEALTH_ADDRESS must be an IPv4 or IPv6 address (where the site is served)') from None

    def connection(self, timeout=TIMEOUT):
        if self.local:
            return HTTPConnection(self.address, self.port, timeout=timeout)
        if not self.address:
            return HTTPSConnection(self.domain, self.port, timeout=timeout, context=ssl.create_default_context())
        address = self.address

        class Pinned(HTTPSConnection):
            def connect(self):
                # Assigned before the handshake, so the request's watchdog can cut a slow one too.
                self.sock = socket.create_connection((address, self.port), self.timeout)
                self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)
        return Pinned(self.domain, self.port, timeout=timeout, context=ssl.create_default_context())

    def get(self, path, body=False, deadline=None):
        """(status or None, the error word, the body when asked): one GET, no redirect followed. One monotonic deadline
        (TIMEOUT, or the caller's earlier one) bounds the connect, the headers and the body together: a socket timeout
        alone restarts with every byte, so at the deadline a watchdog shuts the socket down."""
        deadline = min(deadline or float('inf'), time.monotonic() + TIMEOUT)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None, 'timeout', b''
        headers = {'Host': self.domain, 'User-Agent': 'setup-deploy-health', 'Cache-Control': 'no-cache'}
        if self.local:
            headers['X-Forwarded-Proto'] = 'https'
        connection = self.connection(timeout=remaining)
        expired, sockets = threading.Event(), []

        def expire():
            # The connection drops its socket once an answer will close it (HTTP/1.0, Connection: close) while the
            # response still reads the body from it: the socket seen at the request is shut down too.
            expired.set()
            for sock in (connection.sock, *sockets):
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        watchdog = threading.Timer(remaining, expire)
        watchdog.daemon = True
        watchdog.start()
        try:
            connection.request('GET', path, headers=headers)
            sockets.append(connection.sock)
            if expired.is_set():  # expired while connecting: the socket the watchdog could not see yet
                return None, 'timeout', b''
            response = connection.getresponse()
            data = response.read(RELEASE_BODY_LIMIT) if body else b''
            if expired.is_set():
                return None, 'timeout', b''
            return response.status, None, data
        except (socket.timeout, TimeoutError):
            return None, 'timeout', b''
        except (OSError, HTTPException, ssl.SSLError, ValueError) as error:
            return None, 'timeout' if expired.is_set() else type(error).__name__, b''
        finally:
            watchdog.cancel()
            connection.close()


def check_page(endpoint, path, sleep, retry_delay=RETRY_DELAY):
    """{path, status, ms, tries[, error]}: a timeout, connection error or 5xx is tried once more."""
    for tries in (1, 2):
        started = time.monotonic()
        status, error, _ = endpoint.get(path)
        result = {'path': path, 'status': status, 'ms': round((time.monotonic() - started) * 1000), 'tries': tries}
        if error:
            result['error'] = error
        if (status is None or status >= 500) and tries == 1:
            sleep(retry_delay)
            continue
        return result


def passed(result):
    return result['status'] is not None and 200 <= result['status'] < 400


def release_seen(endpoint, path, expected, sleep, wait=RELEASE_WAIT):
    """The commit the release page reports, asked until it is the expected one or the wait is over."""
    deadline, seen = time.monotonic() + wait, None
    while True:
        status, _, body = endpoint.get(path, body=True, deadline=deadline)
        if status == 200:
            try:
                value = json.loads(body.decode()).get('release')
            except (ValueError, AttributeError, UnicodeDecodeError):
                value = None
            # Only a commit or a short word is repeated: the page's text never reaches a log or the status.
            seen = value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[A-Za-z0-9._-]{1,40}', value) else 'unreadable'
            if seen == expected:
                return seen
        if time.monotonic() >= deadline:
            return seen
        sleep(min(2, max(0, deadline - time.monotonic())))


def target_entry(entries, attempt, rollback):
    """The history entry this check is about: the environment's last, which must be the attempt's when one is named."""
    if not entries:
        raise Refusal('Nothing was deployed to this environment yet (no deploy history)')
    last = entries[-1]
    if attempt and last.get('attempt') != attempt:
        raise Refusal(f'The last deploy of this environment is not attempt {attempt}; nothing was checked')
    if rollback and not last.get('rollback'):
        raise Refusal('rollback=1 checks a rollback, but the last deploy of this environment is no rollback')
    if last.get('failed') and last['failed'] != 'health':
        raise Refusal(f'The last deploy failed ({last["failed"]}); its release is not served as deployed')
    return last


def deploy_health(directory, environment, attempt=None, rollback=False, environ=None, out=None, err=None,
                  sleep=time.sleep, retry_delay=RETRY_DELAY, release_wait=RELEASE_WAIT):
    """make deploy-health; returns the exit status: 0 healthy, 1 failed, 64 refused (nothing checked or changed)."""
    from project_attempts import environment_lock
    environ = dict(os.environ if environ is None else environ)
    out, err = out or sys.stdout, err or sys.stderr
    directory = Path(directory)
    try:
        if attempt:
            check_attempt(attempt, 'attempt')
        values, _ = file_settings(directory, environment)
        paths, release = health_paths(values, environ)
        if not paths:
            raise Refusal('No health check pages: set DEPLOY_HEALTH_PATHS or DEPLOY_HEALTH_RELEASE_PATH '
                          f'(env/common.env) or pass {EXTRA_VARIABLE}')
        endpoint = Endpoint(values)
        target_entry(read_history(directory, environment), attempt, rollback)
    except (Refusal, ValueError) as error:
        print(error, file=err)
        print('Nothing was checked.', file=err)
        refused_rollback_check(directory, environment, attempt, rollback, environ, str(error))
        return USAGE
    with environment_lock(directory, environment, environ):
        entries = read_history(directory, environment)
        expected = target_entry(entries, attempt, rollback)['commit']
        where = f'{endpoint.address}:{endpoint.port} as {endpoint.domain}' if endpoint.local else \
            f'https://{endpoint.domain}' + (f' at {endpoint.address}' if endpoint.address else '')
        print(f'==> health check of {expected[:12]} on {where}: {" ".join(paths)}', file=out, flush=True)
        seen = release_seen(endpoint, release, expected, sleep, release_wait) if release else None
        results = [check_page(endpoint, path, sleep, retry_delay) for path in paths]
        ok = all(passed(result) for result in results) and (not release or seen == expected)
        for result in results:
            mark = 'ok' if passed(result) else 'FAILED'
            status = result['status'] if result['status'] is not None else result.get('error')
            print(f"    {mark:6} {status} {result['path']} ({result['ms']} ms, {result['tries']} tries)", file=out)
        if release:
            print(f'    release {release}: {seen or "no answer"}' + ('' if seen == expected else f' (expected {expected})'),
                  file=out)
        # The entry is the last one still: make deploy and this check run under the same lock.
        entries[-1]['health'] = 'ok' if ok else 'failed'
        if not ok:
            entries[-1]['failed'] = 'health'
        elif entries[-1].get('failed') == 'health':
            del entries[-1]['failed']  # a passing check again: the release serves, so it is live and a rollback target
        write_history(directory, environment, entries)
        record_attempt(directory, environment, attempt, rollback, ok, seen, results)
    print(f"Health check {'passed' if ok else 'FAILED'}: {expected[:12]} on ENV={environment}.", file=out if ok else err)
    return 0 if ok else FAILED


def refused_rollback_check(directory, environment, attempt, rollback, environ, message):
    """rollback=1 for an attempt whose rollback is the last deploy, but nothing could be checked: the rollback is
    not confirmed, so the attempt is rollback_failed (rolled_back needs a check that passed)."""
    from project_attempts import ATTEMPT, environment_lock
    if not (rollback and attempt and ATTEMPT.fullmatch(attempt)):
        return
    with environment_lock(directory, environment, environ):
        entries = read_history(directory, environment)
        record = Attempt(directory, environment, attempt)
        if not entries or entries[-1].get('attempt') != attempt or not entries[-1].get('rollback') or record.read() is None:
            return
        record.event('health_after_rollback', 'refused', message=message.splitlines()[0])
        record.update(outcome='rollback_failed')


def record_attempt(directory, environment, attempt, rollback, ok, seen, results):
    record = Attempt(directory, environment, attempt)
    if not record:
        return
    fields = {'release_seen': seen, 'results': results} if seen is not None else {'results': results}
    record.event('health_after_rollback' if rollback else 'health', 'ok' if ok else 'failed', **fields)
    if rollback:
        record.update(outcome='rolled_back' if ok else 'rollback_failed')
    elif ok:
        record.update(outcome='live', reason=None)
    else:
        # The code is on the target: a prod rollback (the pipeline's, or the workflow's) moves it on from here.
        record.update(outcome='failed_after_code', reason='health')
