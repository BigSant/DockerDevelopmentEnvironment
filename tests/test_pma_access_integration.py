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
class PmaAccessIntegrationTest(unittest.TestCase):
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

    def start(self, environment, allowed='', trusted=''):
        name = self.network + '-' + uuid.uuid4().hex[:6]
        self.addCleanup(self.docker, 'rm', '-f', name)
        self.docker('run', '-d', '--name', name, '--network', self.network, '--memory', '128m',
                    '-v', f'{self.root}:/etc/ssl/certs:ro',
                    '-e', 'SETUP_ENABLE_PMA=1', '-e', f'SETUP_ENVIRONMENT={environment}',
                    '-e', f'PMA_ALLOWED_IPS={allowed}', '-e', f'PMA_TRUSTED_PROXIES={trusted}',
                    IMAGE, '/usr/local/bin/init.sh', 'demo.local', 'allow all;', '60')
        return name

    def status(self, name, scheme='http', host='pma.demo.local', forwarded=None, address='127.0.0.1'):
        port = '443' if scheme == 'https' else '80'
        args = ['exec', name, 'curl', '--noproxy', '*', '-ksS', '--max-time', '2',
                '--resolve', f'{host}:{port}:{address}', '-o', '/dev/null', '-w', '%{http_code}']
        if forwarded:
            args += ['-H', f'X-Forwarded-For: {forwarded}']
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
        for environment, allowed in [('prod', '127.0.0.0/8,::1'), ('local', '203.0.113.10'),
                                     ('test', '203.0.113.10')]:
            name = self.start(environment, allowed)
            for scheme in ('http', 'https'):
                self.assertEqual(self.status(name, scheme), '200')
                self.assertEqual(self.status(name, scheme, address='[::1]'), '200')

    def test_forwarded_ip_requires_a_trusted_proxy_and_cannot_spoof_the_chain(self):
        untrusted = self.start('stage', '203.0.113.10')
        trusted = self.start('stage', '203.0.113.10', '127.0.0.1')
        for scheme in ('http', 'https'):
            self.assertEqual(self.status(untrusted, scheme, forwarded='203.0.113.10'), '403')
            self.assertEqual(self.status(trusted, scheme, forwarded='203.0.113.10'), '200')
            self.assertEqual(self.status(trusted, scheme, forwarded='198.51.100.1'), '403')
            self.assertEqual(self.status(trusted, scheme, forwarded='203.0.113.10, 198.51.100.1'), '403')
            self.assertEqual(self.status(trusted, scheme), '403')

    def test_invalid_ip_or_cidr_stops_nginx(self):
        for allowed in ('999.1.2.3', '127.0.0.1/99', '203.0.113.1; allow all;'):
            name = self.start('prod', allowed)
            self.assertNotEqual(self.docker('wait', name), '0')
