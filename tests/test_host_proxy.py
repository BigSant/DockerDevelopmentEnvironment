"""Local-domain route generation and rollback without changing system files."""
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
from host_proxy import configuration, install, MARKER
from project_bootstrap import certificate_is_valid, prepare_host


class HostProxyTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.ssl = self.root / 'ssl'
        self.ssl.mkdir()
        for name in ('domain.crt', 'domain.key'):
            (self.ssl / name).write_text('test certificate placeholder')
        (self.root / 'sites-available').mkdir()
        (self.root / 'sites-enabled').mkdir()
        self.target = self.root / 'sites-available/demo.local.conf'
        self.link = self.root / 'sites-enabled/demo.local.conf'

    def test_http_and_https_preserve_original_host_and_scheme(self):
        config = configuration('demo.local', '32001', self.ssl).decode()
        self.assertIn('server_name demo.local pma.demo.local mailpit.demo.local;', config)
        self.assertEqual(config.count('proxy_pass http://127.0.0.1:32001;'), 2)
        self.assertIn('listen 80;', config)
        self.assertIn('listen 443 ssl http2;', config)
        self.assertIn('proxy_set_header Host $http_host;', config)
        self.assertIn('proxy_set_header X-Forwarded-Proto $scheme;', config)

    def test_host_preparation_installs_route_without_env_toggle(self):
        project = SimpleNamespace(data_directory=self.root, settings={
            'DOMAIN': 'demo.local', 'LOCALHOST_PORT': '32001'})
        with patch('project_bootstrap.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 0))]), \
                patch('project_bootstrap.certificate_is_valid', return_value=True), \
                patch('project_bootstrap.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            prepare_host(project)
        route = run.call_args.args[0]
        self.assertEqual(route[:2], ['sudo', '-n'])
        self.assertEqual(Path(route[3]).name, 'host_proxy.py')
        self.assertEqual(route[4:], ['demo.local', '32001', str(self.ssl)])

    def test_install_validates_before_reload_and_preserves_other_sites(self):
        other = self.root / 'sites-available/existing.local.conf'
        other.write_text('unrelated')
        run = Mock()
        install('demo.local', '32001', self.ssl, self.root, run)
        self.assertEqual(self.link.resolve(), self.target)
        self.assertTrue(self.target.read_text().startswith(MARKER))
        self.assertEqual(other.read_text(), 'unrelated')
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['nginx', '-t'], ['systemctl', 'reload', 'nginx']])

    def test_unmanaged_site_is_never_overwritten(self):
        self.target.write_text('user configuration')
        with self.assertRaisesRegex(ValueError, 'preserved'):
            install('demo.local', '32001', self.ssl, self.root, Mock())
        self.assertEqual(self.target.read_text(), 'user configuration')
        self.assertFalse(self.link.exists())

    def test_failed_nginx_validation_removes_new_route(self):
        run = Mock(side_effect=subprocess.CalledProcessError(1, ['nginx', '-t']))
        with self.assertRaises(subprocess.CalledProcessError):
            install('demo.local', '32001', self.ssl, self.root, run)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.link.is_symlink())
        self.assertEqual(run.call_count, 1)

    def test_failed_reload_restores_previous_managed_config(self):
        original = configuration('demo.local', '32001', self.ssl)
        self.target.write_bytes(original)
        self.link.symlink_to(self.target)
        run = Mock(side_effect=[None, subprocess.CalledProcessError(1, ['systemctl', 'reload', 'nginx'])])
        with self.assertRaises(subprocess.CalledProcessError):
            install('demo.local', '32002', self.ssl, self.root, run)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(self.link.resolve(), self.target)

    def test_rejects_config_injection_and_invalid_ports(self):
        for domain, port in [('demo.local;evil', '32001'), ('../outside', '32001'), ('demo.local', '0'),
                             ('demo.local', '80'), ('demo.local', '65536'), ('demo.local', '32001;')]:
            with self.subTest(domain=domain, port=port), self.assertRaises(ValueError):
                configuration(domain, port, self.ssl)

    def test_real_openssl_rejects_certificate_for_previous_domain(self):
        certificate = self.ssl / 'domain.crt'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '60',
                        '-subj', '/CN=old.localhost', '-addext', 'subjectAltName=DNS:old.localhost',
                        '-keyout', str(self.ssl / 'domain.key'), '-out', str(certificate)],
                       capture_output=True, check=True, timeout=15)
        self.assertTrue(certificate_is_valid(certificate, 'old.localhost'))
        self.assertFalse(certificate_is_valid(certificate, 'new.local'))
