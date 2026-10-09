"""Hidden paths, dumps and Composer manifests are not served by the shared Apache image."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
APACHE = os.environ.get('SETUP_APACHE_TEST_IMAGE')
CLIENT = os.environ.get('SETUP_NGINX_TEST_IMAGE')  # any image with curl


@unittest.skipUnless(APACHE and CLIENT, 'Set SETUP_APACHE_TEST_IMAGE and SETUP_NGINX_TEST_IMAGE (curl client)')
class WebserverSecurityIntegrationTest(unittest.TestCase):
    def test_hidden_paths_dumps_and_manifests_answer_404(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            base = Path(temporary)
            docroot, certs = base / 'html', base / 'certs'
            for relative in ('.git/config', '.env', 'dump.sql', 'backup.SQL.GZ', 'old.bak', 'composer.json',
                             'composer.lock', 'index.html', '.well-known/acme-challenge/token', 'img/logo.txt'):
                (docroot / relative).parent.mkdir(parents=True, exist_ok=True)
                (docroot / relative).write_text('content\n')
            certs.mkdir()
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=demo.local',
                            '-keyout', str(certs / 'domain.key'), '-out', str(certs / 'domain.crt')], check=True, capture_output=True)
            for path in base.rglob('*'):
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            os.chmod(base, 0o755)
            name = 'setup-apache-' + uuid.uuid4().hex[:8]
            subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '-v', f'{docroot}:/var/www/html:ro',
                            '-v', f'{certs}:/etc/ssl/certs:ro', '-e', 'TZ=UTC', '-e', 'SETUP_ENV=local',
                            '-e', 'PROJECT_NAME=demo', '-e', 'DOMAIN=demo.local', APACHE,
                            '/usr/local/bin/init.sh', 'demo.local', '/', 'local'], check=True, capture_output=True)
            self.addCleanup(subprocess.run, ['docker', 'rm', '-f', name], capture_output=True)

            def status(path):
                for _ in range(30):
                    result = subprocess.run(['docker', 'run', '--rm', '--network', f'container:{name}', '--entrypoint', 'curl',
                                             CLIENT, '-s', '-o', '/dev/null', '-w', '%{http_code}', f'http://127.0.0.1{path}'],
                                            capture_output=True, text=True)
                    if result.stdout not in ('', '000'):
                        return result.stdout
                    time.sleep(.5)
                self.fail(subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr)
            for path in ('/.git/config', '/.env', '/dump.sql', '/backup.SQL.GZ', '/old.bak', '/composer.json', '/composer.lock'):
                self.assertEqual(status(path), '404', path)
            for path in ('/index.html', '/.well-known/acme-challenge/token', '/img/logo.txt'):
                self.assertEqual(status(path), '200', path)

    def test_apache_starts_without_certificates(self):
        # Apache serves plain HTTP behind nginx-proxy; a server's data/<env>/ssl may be empty.
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            docroot = Path(temporary) / 'html'
            docroot.mkdir()
            (docroot / 'index.html').write_text('content\n')
            os.chmod(temporary, 0o755); os.chmod(docroot, 0o755); os.chmod(docroot / 'index.html', 0o644)
            name = 'setup-apache-' + uuid.uuid4().hex[:8]
            subprocess.run(['docker', 'run', '-d', '--rm', '--name', name, '--memory', '128m', '-v', f'{docroot}:/var/www/html:ro',
                            '-v', f'{ROOT}/docker/docker/apache/conf/init.sh:/usr/local/bin/init.sh:ro',
                            '-e', 'TZ=UTC', '-e', 'SETUP_ENV=prod', '-e', 'PROJECT_NAME=demo', '-e', 'DOMAIN=demo.local', APACHE,
                            'bash', '/usr/local/bin/init.sh', 'demo.local', '/', 'prod'], check=True, capture_output=True)
            self.addCleanup(subprocess.run, ['docker', 'rm', '-f', name], capture_output=True)
            for _ in range(30):
                result = subprocess.run(['docker', 'run', '--rm', '--network', f'container:{name}', '--entrypoint', 'curl',
                                         CLIENT, '-s', '-o', '/dev/null', '-w', '%{http_code}', 'http://127.0.0.1/index.html'],
                                        capture_output=True, text=True)
                if result.stdout == '200':
                    return
                time.sleep(.5)
            self.fail(subprocess.run(['docker', 'logs', name], capture_output=True, text=True).stderr)
