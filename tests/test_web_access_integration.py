"""Real HTTP/HTTPS access checks against the built shared Nginx image."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('SETUP_NGINX_TEST_IMAGE')


@unittest.skipUnless(IMAGE, 'Set SETUP_NGINX_TEST_IMAGE to a built shared nginx-proxy image')
class WebAccessIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(['docker', *args], capture_output=True, text=True,
                              timeout=30, check=True).stdout.strip()

    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=demo.local', '-keyout', str(self.root / 'domain.key'),
                        '-out', str(self.root / 'domain.crt')], capture_output=True, check=True, timeout=15)
        self.network = 'setup-pma-test-' + uuid.uuid4().hex[:12]
        self.docker('network', 'create', '--internal', self.network)
        self.addCleanup(self.docker, 'network', 'rm', self.network)
        backend = self.network + '-backend'
        (self.root / 'backend.conf').write_text(
            'events {}\nhttp { server { listen 80; location / { return 200 "backend"; } } }\n')
        self.addCleanup(self.docker, 'rm', '-f', backend)
        self.docker('run', '-d', '--name', backend, '--network', self.network,
                    '--network-alias', 'pma', '--network-alias', 'webserver', '--memory', '128m',
                    '-v', f'{self.root}/backend.conf:/test.conf:ro', '--entrypoint', 'nginx',
                    IMAGE, '-c', '/test.conf', '-g', 'daemon off;')

    def start(self, environment, allowed='', trusted='', *extra):
        name = self.network + '-' + uuid.uuid4().hex[:6]
        self.addCleanup(self.docker, 'rm', '-f', name)
        self.docker('run', '-d', '--name', name, '--network', self.network, '--memory', '128m',
                    '-v', f'{self.root}:/etc/ssl/certs:ro',
                    '-e', 'SETUP_ENABLE_PMA=1', '-e', f'SETUP_ENVIRONMENT={environment}',
                    '-e', f'PMA_ALLOWED_IPS={allowed}', '-e', f'TRUSTED_PROXIES={trusted}', *extra,
                    IMAGE, '/usr/local/bin/init.sh', 'demo.local', '60')
        return name

    def status(self, name, scheme='http', host='pma.demo.local', forwarded=None, address='127.0.0.1', user=None):
        port = '443' if scheme == 'https' else '80'
        args = ['exec', name, 'curl', '--noproxy', '*', '-ksS', '--max-time', '2',
                '--resolve', f'{host}:{port}:{address}', '-o', '/dev/null', '-w', '%{http_code}']
        if forwarded:
            args += ['-H', f'X-Forwarded-For: {forwarded}']
        if user:
            args += ['-u', user]
        for _ in range(30):
            try:
                code = self.docker(*args, f'{scheme}://{host}/')
                if code != '502':
                    return code
            except subprocess.CalledProcessError:
                pass
            time.sleep(.1)
        self.fail(self.docker('logs', name))

    def test_remote_default_blocks_both_pma_hosts_but_keeps_application_open(self):
        for environment in ('stage', 'prod'):
            name = self.start(environment)
            for scheme in ('http', 'https'):
                for host in ('pma.demo.local', 'www.pma.demo.local'):
                    self.assertEqual(self.status(name, scheme, host), '403')
                self.assertEqual(self.status(name, scheme, 'demo.local'), '200')

    def test_ipv4_cidr_and_ipv6_allowlist_and_local_access(self):
        for environment, allowed in [('prod', '127.0.0.0/8,::1'), ('local', ''), ('test', '')]:
            name = self.start(environment, allowed)
            for scheme in ('http', 'https'):
                self.assertEqual(self.status(name, scheme), '200')
                self.assertEqual(self.status(name, scheme, address='[::1]'), '200')
        # A list restricts local too.
        name = self.start('local', '203.0.113.10', '192.0.2.1')
        self.assertEqual(self.status(name), '403')

    def test_forwarded_ip_requires_a_trusted_proxy_and_cannot_spoof_the_chain(self):
        untrusted = self.start('stage', '203.0.113.10', '192.0.2.1')
        trusted = self.start('stage', '203.0.113.10', '127.0.0.1')
        for scheme in ('http', 'https'):
            self.assertEqual(self.status(untrusted, scheme, forwarded='203.0.113.10'), '403')
            self.assertEqual(self.status(trusted, scheme, forwarded='203.0.113.10'), '200')
            self.assertEqual(self.status(trusted, scheme, forwarded='198.51.100.1'), '403')
            self.assertEqual(self.status(trusted, scheme, forwarded='203.0.113.10, 198.51.100.1'), '403')
            self.assertEqual(self.status(trusted, scheme), '403')

    def start_tls(self, certificates):
        """The worktree's init.sh with a given data/<env>/ssl directory (empty = nothing provisioned)."""
        name = self.network + '-' + uuid.uuid4().hex[:6]
        self.addCleanup(self.docker, 'rm', '-f', name)
        self.docker('run', '-d', '--name', name, '--network', self.network, '--memory', '64m',
                    '-v', f'{certificates}:/etc/ssl/certs:ro',
                    '-v', f'{ROOT}/docker/docker/nginx-proxy/conf/init.sh:/usr/local/bin/init.sh:ro',
                    '-e', 'SETUP_ENVIRONMENT=prod', IMAGE, 'bash', '/usr/local/bin/init.sh', 'demo.local', '60')
        return name

    def served_certificate(self, name):
        self.assertEqual(self.status(name, 'https', 'demo.local'), '200')
        return self.docker('exec', name, 'sh', '-c', 'openssl s_client -connect 127.0.0.1:443 -servername demo.local '
                           '</dev/null 2>/dev/null | openssl x509 -noout -fingerprint -sha256 -subject -ext subjectAltName')

    def test_without_provisioned_certificates_https_uses_a_generated_pair(self):
        empty = self.root / 'empty'
        empty.mkdir()
        name = self.start_tls(empty)
        served = self.served_certificate(name)
        self.assertIn('demo.local', served)
        self.assertIn('*.demo.local', served)
        self.assertEqual(self.status(name, 'http', 'demo.local'), '200')
        self.assertEqual(list(empty.iterdir()), [], 'the mounted directory must stay untouched')
        self.assertEqual(self.docker('exec', name, 'stat', '-c', '%U %a', '/etc/nginx/ssl-generated/domain.key'), 'root 600')
        # A restart keeps the same pair.
        self.docker('restart', name)
        self.assertEqual(self.served_certificate(name), served)

    def test_a_provisioned_pair_is_served_and_never_replaced(self):
        expected = subprocess.run(['openssl', 'x509', '-in', str(self.root / 'domain.crt'), '-noout', '-fingerprint', '-sha256'],
                                  capture_output=True, text=True, check=True).stdout.split('=', 1)[1].strip()
        name = self.start_tls(self.root)
        self.assertIn(expected, self.served_certificate(name))
        self.assertEqual(self.docker('exec', name, 'sh', '-c', 'ls /etc/nginx/ssl-generated 2>/dev/null | wc -l'), '0')

    def test_half_a_provisioned_pair_stops_nginx(self):
        half = self.root / 'half'
        half.mkdir()
        (half / 'domain.crt').write_bytes((self.root / 'domain.crt').read_bytes())
        name = self.start_tls(half)
        self.assertNotEqual(self.docker('wait', name), '0')
        self.assertIn('domain.key', subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr)

    def test_a_tool_password_is_required_besides_the_allowlist(self):
        # apr1 and bcrypt, the two formats htpasswd writes (-m / -B), both checked by this image's nginx.
        htpasswd = lambda *args: self.docker('run', '--rm', '--entrypoint', 'htpasswd', 'httpd:2.4.59', '-nb', *args)
        entries = [htpasswd('-m', 'ops', 'secret-ops'), htpasswd('-B', 'dev', 'secret-dev')]
        name = self.start('prod', '127.0.0.1', '', '-e', 'PMA_HTPASSWD=' + ' '.join(entries))
        self.assertEqual(self.status(name), '401')
        self.assertEqual(self.status(name, user='ops:wrong'), '401')
        self.assertEqual(self.status(name, user='ops:secret-ops'), '200')
        self.assertEqual(self.status(name, user='dev:secret-dev'), '200')
        # A right password from outside the allowlist is still refused.
        other = self.start('prod', '203.0.113.10', '', '-e', 'PMA_HTPASSWD=' + entries[0])
        self.assertEqual(self.status(other, user='ops:secret-ops'), '403')

    def test_invalid_ip_or_cidr_stops_nginx(self):
        for allowed in ('999.1.2.3', '127.0.0.1/99', '203.0.113.1; allow all;'):
            name = self.start('prod', allowed)
            self.assertNotEqual(self.docker('wait', name), '0')


@unittest.skipUnless(IMAGE, 'Set SETUP_NGINX_TEST_IMAGE to a built shared nginx-proxy image')
class PublishedPortsIntegrationTest(unittest.TestCase):
    """Who may forward a client address, through ports Docker really publishes on the host."""

    def docker(self, *args):
        return subprocess.run(['docker', *args], capture_output=True, text=True,
                              timeout=60, check=True).stdout.strip()

    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                        '-subj', '/CN=demo.local', '-keyout', str(self.root / 'domain.key'),
                        '-out', str(self.root / 'domain.crt')], capture_output=True, check=True, timeout=15)
        self.network = 'setup-publish-test-' + uuid.uuid4().hex[:12]
        self.docker('network', 'create', self.network)
        self.addCleanup(self.docker, 'network', 'rm', self.network)
        # A non-loopback address of this host (the VPN interface on the pilot server).
        self.address = os.environ.get('SETUP_PUBLISH_TEST_ADDRESS') or self.docker(
            'network', 'inspect', 'bridge', '--format', '{{(index .IPAM.Config 0).Gateway}}')
        self.backend = self.network + '-backend'
        # The backend echoes what the upstream (Apache/PHP) would be told.
        (self.root / 'backend.conf').write_text(
            'events {}\nhttp { server { listen 80; location / {\n'
            '    return 200 "for=$http_x_forwarded_for proto=$http_x_forwarded_proto"; } } }\n')
        self.addCleanup(self.docker, 'rm', '-f', self.backend)
        self.docker('run', '-d', '--name', self.backend, '--network', self.network,
                    '--network-alias', 'pma', '--network-alias', 'webserver', '--memory', '64m',
                    '-v', f'{self.root}/backend.conf:/test.conf:ro', '--entrypoint', 'nginx',
                    IMAGE, '-c', '/test.conf', '-g', 'daemon off;')

    def start(self, address, direct, allowed):
        name = self.network + '-' + uuid.uuid4().hex[:6]
        self.addCleanup(self.docker, 'rm', '-f', name)
        self.docker('run', '-d', '--name', name, '--network', self.network, '--memory', '64m',
                    '-p', f'{address}::80', '-v', f'{self.root}:/etc/ssl/certs:ro',
                    '-e', 'SETUP_ENABLE_PMA=1', '-e', 'SETUP_ENVIRONMENT=prod', '-e', f'SETUP_DIRECT_CLIENTS={direct}',
                    '-e', f'PMA_ALLOWED_IPS={allowed}', '-e', f'SITE_ALLOWED_IPS={allowed}',
                    IMAGE, '/usr/local/bin/init.sh', 'demo.local', '60')
        port = self.docker('port', name, '80/tcp').splitlines()[0].rsplit(':', 1)[1]
        return name, address, port

    def request(self, proxy, host='pma.demo.local', forwarded=None, proto=None):
        name, address, port = proxy
        args = ['curl', '--noproxy', '*', '-sS', '--max-time', '3', '--resolve', f'{host}:{port}:{address}',
                '-w', ' %{http_code}']
        if forwarded:
            args += ['-H', f'X-Forwarded-For: {forwarded}']
        if proto:
            args += ['-H', f'X-Forwarded-Proto: {proto}']
        for _ in range(50):
            result = subprocess.run([*args, f'http://{host}:{port}/'], capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and not result.stdout.endswith(' 502'):
                body, code = result.stdout.rsplit(' ', 1)
                return code, body if code == '200' else ''
            time.sleep(.2)
        self.fail(self.docker('logs', name))

    def test_direct_clients_cannot_spoof_an_allowlisted_address(self):
        proxy = self.start(self.address, 1, '203.0.113.10')
        for host in ('pma.demo.local', 'demo.local'):
            for forwarded in ('203.0.113.10', '198.51.100.1, 203.0.113.10', '203.0.113.10, 203.0.113.10'):
                with self.subTest(host=host, forwarded=forwarded):
                    self.assertEqual(self.request(proxy, host, forwarded), ('403', ''))
            self.assertEqual(self.request(proxy, host), ('403', ''))

    def test_direct_clients_are_allowed_by_their_own_address_and_their_headers_are_replaced(self):
        # The peer Docker presents for this host's requests (an empty list leaves the site open).
        peer = self.request(self.start(self.address, 1, ''), 'demo.local')[1].split()[0].removeprefix('for=')
        proxy = self.start(self.address, 1, peer)
        self.assertEqual(self.request(proxy), ('200', f'for={peer} proto=http'))
        self.assertEqual(self.request(proxy, forwarded='203.0.113.10', proto='https'), ('200', f'for={peer} proto=http'))

    def test_the_host_proxy_on_loopback_forwards_the_client_address(self):
        # DirectAdmin / the host Nginx connect to 127.0.0.1 and append the client they saw.
        proxy = self.start('127.0.0.1', 0, '203.0.113.10')
        self.assertEqual(self.request(proxy, forwarded='203.0.113.10', proto='https'),
                         ('200', 'for=203.0.113.10 proto=https'))
        self.assertEqual(self.request(proxy, forwarded='203.0.113.10, 198.51.100.1'), ('403', ''))
        self.assertEqual(self.request(proxy), ('403', ''))

    def test_other_containers_cannot_forward_a_client_address_on_loopback(self):
        name, _, _ = self.start('127.0.0.1', 0, '203.0.113.10')
        self.request((name, '127.0.0.1', self.docker('port', name, '80/tcp').splitlines()[0].rsplit(':', 1)[1]),
                     forwarded='203.0.113.10')
        address = self.docker('inspect', name, '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
        code = self.docker('exec', self.backend, 'curl', '--noproxy', '*', '-sS', '--max-time', '3', '-o', '/dev/null',
                           '-w', '%{http_code}', '--resolve', f'pma.demo.local:80:{address}',
                           '-H', 'X-Forwarded-For: 203.0.113.10', 'http://pma.demo.local/')
        self.assertEqual(code, '403')
