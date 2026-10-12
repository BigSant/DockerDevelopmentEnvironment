"""DOMAIN listing several hostnames: resolution, routing places, SQL placeholders; setup writes no shop URL."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
from database_import import render_hook
from host_proxy import configuration
import project_bootstrap
from project_bootstrap import prepare_host, route_exists
from project_domains import HOSTS_VARIABLE, apply_domains, attach_domain_aliases, extra_hosts, resolve_domains
from project_metrics import resolve_metrics

VALUES = {'DOMAIN_LT': 'demo.local', 'DOMAIN_LV': 'lv.demo.local', 'DOMAIN_EE': 'Ee.Demo.Local'}


class ResolveTest(unittest.TestCase):
    def test_a_single_hostname_stays_exactly_as_before(self):
        for raw in ('demo.local', 'Demo.Local', 'localhost'):
            with self.subTest(raw=raw):
                self.assertEqual(resolve_domains(raw, {}), ([raw], {}))
        self.assertEqual(resolve_domains('', {}), ([], {}))

    def test_variables_and_hostnames_in_order_without_repeats(self):
        hosts, references = resolve_domains('DOMAIN_LT DOMAIN_LV extra.local DOMAIN_EE demo.local', VALUES)
        self.assertEqual(hosts, ['demo.local', 'lv.demo.local', 'extra.local', 'ee.demo.local'])
        self.assertEqual(references, {'DOMAIN_LT': 'demo.local', 'DOMAIN_LV': 'lv.demo.local',
                                      'DOMAIN_EE': 'ee.demo.local'})

    def test_a_single_variable_is_resolved(self):
        self.assertEqual(resolve_domains('DOMAIN_LV', VALUES), (['lv.demo.local'], {'DOMAIN_LV': 'lv.demo.local'}))

    def test_unset_variables_and_other_words_are_errors(self):
        for raw, message in [('DOMAIN_LT DOMAIN_XX', 'DOMAIN_XX'), ('demo.local ${DOMAIN_LV}', 'neither'),
                             ('demo.local https://lv.demo.local/', 'neither'), ('demo.local Lv.demo.local', 'neither'),
                             ('demo.local demo.local/latvija/', 'neither')]:
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, message):
                resolve_domains(raw, VALUES)
        for value in ('lv.demo.local:8080', 'lv.demo.local/lv/', 'a b'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'one hostname'):
                resolve_domains('demo.local DOMAIN_LV', {'DOMAIN_LV': value})


class ApplyTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'env').mkdir()
        self.common, self.private = self.root / 'env/common.env', self.root / 'env/local.env'
        self.common.write_text('PROJECT_NAME=demo\n')
        self.private.write_text('DOMAIN_LT=demo.local\nDOMAIN_LV=lv.demo.local\nDOMAIN="DOMAIN_LT DOMAIN_LV"\n')
        self.project = SimpleNamespace(directory=self.root, env_files=[self.common, self.private], process_env={},
                                       command=['docker', 'compose'])

    def test_domain_becomes_the_primary_hostname_for_the_runner_and_compose(self):
        raw = 'DOMAIN_LT DOMAIN_LV'
        settings = {'DOMAIN': raw, 'SQL_DOMAIN': raw}
        apply_domains(self.project, settings)
        self.assertEqual(settings['DOMAIN'], 'demo.local')
        self.assertEqual(settings['SQL_DOMAIN'], 'demo.local', 'SQL_DOMAIN defaults to DOMAIN')
        self.assertEqual(settings[HOSTS_VARIABLE], 'demo.local lv.demo.local')
        self.assertEqual(settings['DOMAIN_REFERENCES'], {'DOMAIN_LT': 'demo.local', 'DOMAIN_LV': 'lv.demo.local'})
        self.assertEqual(self.project.process_env, {'DOMAIN': 'demo.local', HOSTS_VARIABLE: 'demo.local lv.demo.local'})
        self.assertEqual(extra_hosts(settings), ['lv.demo.local'])

    def test_an_own_sql_domain_is_kept(self):
        settings = {'DOMAIN': 'DOMAIN_LT DOMAIN_LV', 'SQL_DOMAIN': 'shop.test.local'}
        apply_domains(self.project, settings)
        self.assertEqual(settings['SQL_DOMAIN'], 'shop.test.local')

    def test_every_hostname_is_a_network_alias_of_nginx_proxy(self):
        self.project.settings = {HOSTS_VARIABLE: 'demo.local lv.demo.local'}
        attach_domain_aliases(self.project)
        overlay = Path(self.project.command[-1])
        self.assertEqual(self.project.command[-2], '-f')
        self.assertTrue(overlay.name.startswith('domain-aliases-'))
        self.assertIn('aliases: ["demo.local", "lv.demo.local"]', overlay.read_text())

    def test_one_hostname_needs_no_alias_overlay(self):
        self.project.settings = {HOSTS_VARIABLE: 'demo.local'}
        attach_domain_aliases(self.project)
        self.assertEqual(self.project.command, ['docker', 'compose'])


class RoutingTest(unittest.TestCase):
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

    def test_host_nginx_serves_every_hostname_and_the_primary_service_names(self):
        config = configuration('demo.local', '32001', self.ssl, ['lv.demo.local', 'demo-ee.local']).decode()
        self.assertIn('server_name demo.local lv.demo.local demo-ee.local pma.demo.local mailpit.demo.local '
                      'cron.demo.local dbmonitor.demo.local backup.demo.local;', config)
        with self.assertRaises(ValueError):
            configuration('demo.local', '32001', self.ssl, ['lv.demo.local;evil'])

    def test_a_route_without_one_of_the_hostnames_is_not_prepared(self):
        nginx = self.root / 'nginx'
        (nginx / 'sites-enabled').mkdir(parents=True)
        (nginx / 'sites-enabled/demo.local.conf').write_bytes(configuration('demo.local', '32001', self.ssl))
        self.assertEqual(route_exists('demo.local', '32001', nginx), 'complete')
        self.assertIsNone(route_exists('demo.local', '32001', nginx, ['lv.demo.local']))
        (nginx / 'sites-enabled/demo.local.conf').write_bytes(
            configuration('demo.local', '32001', self.ssl, ['lv.demo.local']))
        self.assertEqual(route_exists('demo.local', '32001', nginx, ['lv.demo.local']), 'complete')

    def test_host_preparation_adds_dns_certificate_and_route_for_every_hostname(self):
        project = SimpleNamespace(data_directory=self.root, settings={
            'DOMAIN': 'demo.local', HOSTS_VARIABLE: 'demo.local demo-lv.local', 'LOCALHOST_PORT': '32001'})
        local = [(0, 0, 0, '', ('127.0.0.1', 0))]
        lookup = lambda host, port: local if host != 'demo-lv.local' else (_ for _ in ()).throw(
            project_bootstrap.socket.gaierror())
        checked = []
        with patch('project_bootstrap.socket.getaddrinfo', side_effect=lookup), \
                patch('project_bootstrap.resolves_locally', return_value=True), \
                patch('project_bootstrap.certificate_is_valid', side_effect=lambda c, host: checked.append(host) or True), \
                patch('project_bootstrap.subprocess.run', return_value=SimpleNamespace(returncode=0)) as run:
            prepare_host(project)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([Path(c[3]).name for c in commands], ['host_entry.py', 'host_proxy.py'])
        self.assertEqual(commands[0][4:], ['demo-lv.local'], 'the hostname without a local entry gets one')
        self.assertEqual(commands[1][4:], ['demo.local', '32001', str(self.ssl), 'demo-lv.local'])
        self.assertEqual(checked, ['demo.local', 'demo-lv.local'])

    def test_a_new_certificate_names_every_hostname(self):
        project = SimpleNamespace(data_directory=self.root, settings={
            'DOMAIN': 'demo.local', HOSTS_VARIABLE: 'demo.local demo-lv.local', 'LOCALHOST_PORT': '32001'})
        def fake_run(command, **options):
            if command[0] == 'mkcert' and '-cert-file' in command:
                Path(command[command.index('-cert-file') + 1]).write_text('crt')
                Path(command[command.index('-key-file') + 1]).write_text('key')
            return SimpleNamespace(returncode=0)
        with patch('project_bootstrap.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 0))]), \
                patch('project_bootstrap.resolves_locally', return_value=True), \
                patch('project_bootstrap.certificate_is_valid', return_value=False), \
                patch('project_bootstrap.shutil.which', return_value='/usr/bin/mkcert'), \
                patch('project_bootstrap.subprocess.run', side_effect=fake_run) as run:
            prepare_host(project)
        mkcert = next(call.args[0] for call in run.call_args_list if '-cert-file' in call.args[0])
        self.assertEqual(mkcert[-3:], ['demo.local', '*.demo.local', 'demo-lv.local'])

    def test_nginx_proxy_site_answers_to_every_hostname(self):
        script = (ROOT / 'docker/docker/nginx-proxy/conf/init.sh').read_text()
        block = script[script.index('# DOMAIN may list'):script.index('sed -i "s/{DOMAIN}/$domain/g"')]
        sites = (ROOT / 'docker/docker/nginx-proxy/conf/sites.conf').read_text()
        for hosts, expected in [('demo.local lv.demo.local demo-ee.local',
                                 'server_name lv.demo.local www.lv.demo.local demo-ee.local www.demo-ee.local;'),
                                ('', None)]:
            with self.subTest(hosts=hosts):
                conf = self.root / 'sites.conf'
                conf.write_text(sites)
                subprocess.run(['bash', '-euo', 'pipefail', '-c', 'domain=demo.local\n' +
                                block.replace('/etc/nginx/conf.d/sites.conf', str(conf))],
                               env={**os.environ, 'SETUP_DOMAIN_HOSTS': hosts}, check=True)
                text = conf.read_text()
                self.assertNotIn('{DOMAIN_HOSTS}', text)
                if expected:
                    self.assertEqual(text.count(expected), 2, 'the HTTP and the HTTPS server')
                else:
                    self.assertEqual(text, sites.replace(
                        "    # {DOMAIN_HOSTS}: the project's other hostnames (DOMAIN lists several), set by init.sh\n", ''))
        result = subprocess.run(['bash', '-euo', 'pipefail', '-c', 'domain=demo.local\n' +
                                 block.replace('/etc/nginx/conf.d/sites.conf', str(conf))],
                                env={**os.environ, 'SETUP_DOMAIN_HOSTS': 'demo.local bad;host'}, capture_output=True)
        self.assertNotEqual(result.returncode, 0)


class DatabaseTest(unittest.TestCase):
    def test_after_import_sql_may_use_the_listed_variables(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            hook = Path(temporary) / '020-shop-urls.sql'
            hook.write_text("UPDATE `ps_shop_url` SET `domain` = '${DOMAIN_LV}' WHERE `id_shop` = 2;\n"
                            "SELECT '${DOMAIN}';\n")
            project = SimpleNamespace(settings={'PROFILE': '', 'DOMAIN': 'demo.local',
                                                'DOMAIN_REFERENCES': {'DOMAIN_LV': 'lv.demo.local'}})
            rendered = render_hook(project, hook).decode()
        self.assertIn("SET `domain` = 'lv.demo.local'", rendered)
        self.assertIn("SELECT 'demo.local'", rendered)
    def test_metrics_probe_every_hostname_by_default(self):
        values = resolve_metrics({'DOMAIN': 'demo.local', HOSTS_VARIABLE: 'demo.local lv.demo.local'})
        self.assertEqual(values['METRICS_URLS'], 'https://demo.local/ https://lv.demo.local/')
        self.assertEqual(resolve_metrics({'DOMAIN': 'demo.local'})['METRICS_URLS'], 'https://demo.local/')

    def test_setup_writes_no_hostname_of_the_list_into_the_database(self):
        profile = ROOT / 'docker/database-profile'
        for path in profile.rglob('*.sql'):
            with self.subTest(path=path.name):
                self.assertIsNone(re.search(r'\$\{DOMAIN_|SETUP_DOMAIN_HOSTS', path.read_text()))


if __name__ == '__main__':
    unittest.main()
