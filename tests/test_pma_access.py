"""PMA access rules and environment-specific Compose service selection."""
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
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project


class PmaAccessTest(unittest.TestCase):
    def rules(self, environment, allowed='', trusted=''):
        return subprocess.run(['bash', str(ROOT / 'docker/docker/nginx-proxy/conf/pma-access.sh')],
                              env={**os.environ, 'SETUP_ENVIRONMENT': environment,
                                   'PMA_ALLOWED_IPS': allowed, 'PMA_TRUSTED_PROXIES': trusted},
                              capture_output=True, text=True, timeout=5)

    def test_local_and_test_stay_open_with_a_shared_allowlist(self):
        for environment in ('local', 'test'):
            result = self.rules(environment, '203.0.113.10')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')

    def test_remote_environments_deny_by_default(self):
        for environment in ('stage', 'prod', 'live', ''):
            result = self.rules(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'deny all;\n')

    def test_literal_addresses_cidrs_and_explicit_trusted_proxies(self):
        result = self.rules('stage', '203.0.113.10,198.51.100.0/24\n2001:db8::10', '10.0.0.2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'set_real_ip_from 10.0.0.2;\nreal_ip_header X-Forwarded-For;\n'
                         'real_ip_recursive on;\nallow 203.0.113.10;\nallow 198.51.100.0/24;\n'
                         'allow 2001:db8::10;\ndeny all;\n')

    def test_directive_injection_and_hostnames_are_rejected(self):
        for value in ('all', 'example.com', '203.0.113.1; allow all;', '127.0.0.1\ninclude /tmp/rules;', '${IP}'):
            for key in ('allowed', 'trusted'):
                with self.subTest(value=value, key=key):
                    result = self.rules('prod', **{key: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Invalid IP/CIDR', result.stderr)

    def test_yaml_enables_pma_everywhere_and_mailpit_only_locally(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold('pma-demo', temporary)
            (app / 'compose/common.yaml').write_text('''include:
  - ${ROOT_DIRECTORY}/docker/pma/docker-compose.yml
services:
  nginx-proxy:
    environment:
      PMA_ALLOWED_IPS: ${PMA_ALLOWED_IPS:-}
      PMA_TRUSTED_PROXIES: ${PMA_TRUSTED_PROXIES:-}
''')
            (app / 'compose/local.yaml').write_text('''include:
  - ${ROOT_DIRECTORY}/docker/mailpit/docker-compose.yml
''')
            for environment in ('local', 'stage', 'prod'):
                if environment != 'local':
                    (app / f'env/{environment}.env').write_text(
                        'DOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_PASSWORD=test\n')
                project = Project(app, environment)
                services = json.loads(project.capture(['config', '--format', 'json']))['services']
                self.assertIn('pma', services)
                self.assertEqual('mailpit' in services, environment == 'local')
                for name in ('pma', 'mailpit'):
                    if name in services:
                        self.assertNotIn('profiles', services[name])
                self.assertNotIn('ports', services['pma'])
                proxy_env = services['nginx-proxy']['environment']
                self.assertEqual(proxy_env['SETUP_ENABLE_PMA'], '1')
                self.assertEqual(proxy_env['SETUP_ENVIRONMENT'], environment)
                self.assertEqual(proxy_env['PMA_ALLOWED_IPS'], '')
                self.assertEqual(proxy_env['PMA_TRUSTED_PROXIES'], '')

            with (app / 'env/common.env').open('a') as env:
                env.write('PMA_ALLOWED_IPS="203.0.113.10 2001:db8::10"\nPMA_TRUSTED_PROXIES=10.20.0.5\n')
            with (app / 'env/stage.env').open('a') as env:
                env.write('PMA_ALLOWED_IPS=198.51.100.0/24\nPMA_TRUSTED_PROXIES=10.20.0.6\n')
            with (app / 'env/prod.env').open('a') as env:
                env.write('PMA_ALLOWED_IPS=\nPMA_TRUSTED_PROXIES=\n')
            for environment, allowed, trusted in (
                    ('local', '203.0.113.10 2001:db8::10', '10.20.0.5'),
                    ('stage', '198.51.100.0/24', '10.20.0.6'), ('prod', '', '')):
                project = Project(app, environment)
                services = json.loads(project.capture(['config', '--format', 'json']))['services']
                proxy_env = services['nginx-proxy']['environment']
                self.assertEqual(proxy_env['PMA_ALLOWED_IPS'], allowed)
                self.assertEqual(proxy_env['PMA_TRUSTED_PROXIES'], trusted)
