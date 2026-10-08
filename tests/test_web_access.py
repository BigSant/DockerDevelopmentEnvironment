"""Client IP rules for every web entry point and the runner's stage/prod guard."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REMOTE_LIMITS = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\n'
                 'WEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\n')
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project

ACCESS = ROOT / 'docker/docker/nginx-proxy/conf/access.sh'


class AccessRulesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)

    def rules(self, environment, **settings):
        env = {'PATH': os.environ['PATH'], 'SETUP_ENVIRONMENT': environment,
               'SETUP_ACCESS_DIR': str(self.output / 'access'), 'SETUP_REAL_IP_FILE': str(self.output / 'real-ip.conf'),
               **settings}
        result = subprocess.run(['bash', str(ACCESS)], env=env, capture_output=True, text=True, timeout=5)
        read = lambda name: (self.output / 'access' / f'{name}.conf').read_text() if result.returncode == 0 else None
        return result, read

    def test_empty_lists_keep_local_and_test_open(self):
        for environment in ('local', 'test'):
            result, read = self.rules(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('site', 'pma', 'mailpit', 'cron', 'dbmonitor'):
                self.assertEqual(read(name), '', name)

    def test_empty_lists_deny_tools_but_keep_the_site_open_on_stage_and_prod(self):
        for environment in ('stage', 'prod'):
            result, read = self.rules(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read('site'), '')
            for name in ('pma', 'mailpit', 'cron', 'dbmonitor'):
                self.assertEqual(read(name), 'deny all;\n', name)

    def test_common_list_applies_everywhere_and_service_list_replaces_it(self):
        result, read = self.rules('local', ALLOWED_IPS='203.0.113.10,198.51.100.0/24',
                                  PMA_ALLOWED_IPS='2001:db8::10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read('site'), 'allow 203.0.113.10;\nallow 198.51.100.0/24;\ndeny all;\n')
        self.assertEqual(read('mailpit'), read('site'))
        self.assertEqual(read('pma'), 'allow 2001:db8::10;\ndeny all;\n')

    def test_trusted_proxies_default_and_override(self):
        result, _ = self.rules('local')
        self.assertEqual(result.returncode, 0, result.stderr)
        default = (self.output / 'real-ip.conf').read_text()
        for address in ('127.0.0.1', '::1', '172.16.0.0/12'):
            self.assertIn(f'set_real_ip_from {address};', default)
        self.assertIn('real_ip_header X-Forwarded-For;', default)
        self.rules('local', TRUSTED_PROXIES='10.20.0.5')
        self.assertEqual((self.output / 'real-ip.conf').read_text(),
                         'set_real_ip_from 10.20.0.5;\nreal_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n')

    def test_directive_injection_and_hostnames_are_rejected(self):
        for value in ('all', 'example.com', '203.0.113.1; allow all;', '${IP}'):
            for key in ('ALLOWED_IPS', 'SITE_ALLOWED_IPS', 'TRUSTED_PROXIES'):
                with self.subTest(value=value, key=key):
                    result, _ = self.rules('prod', **{key: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Invalid IP/CIDR', result.stderr)


class RunnerAccessTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('access-demo', temporary.name)
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SERVICES=pma mailpit\n')
        (self.app / 'env/stage.env').write_text(
            REMOTE_LIMITS + 'DOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_PASSWORD=test\n')

    def test_stage_tools_require_an_allowlist(self):
        with self.assertRaisesRegex(ValueError, 'pma on stage requires PMA_ALLOWED_IPS or ALLOWED_IPS'):
            Project(self.app, 'stage')
        with (self.app / 'env/stage.env').open('a') as env:
            env.write('ALLOWED_IPS=203.0.113.10\nSITE_ALLOWED_IPS=198.51.100.0/24\n')
        services = json.loads(Project(self.app, 'stage').capture(['config', '--format', 'json']))['services']
        proxy = services['nginx-proxy']['environment']
        self.assertEqual(proxy['ALLOWED_IPS'], '203.0.113.10')
        self.assertEqual(proxy['SITE_ALLOWED_IPS'], '198.51.100.0/24')

    def test_local_tools_start_without_a_list_and_ports_bind_to_localhost(self):
        services = json.loads(Project(self.app).capture(['config', '--format', 'json']))['services']
        self.assertEqual(services['nginx-proxy']['environment']['SETUP_ENABLE_MAILPIT'], '1')
        for port in services['nginx-proxy']['ports']:
            self.assertEqual(port['host_ip'], '127.0.0.1')


if __name__ == '__main__':
    unittest.main()
