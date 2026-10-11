"""install-host planning and the make setup orchestrator, without sudo or Docker changes."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
import install_host
import project_bootstrap
import project_setup


class InstallHostTest(unittest.TestCase):
    def test_plan_installs_missing_packages_and_root_owned_helpers(self):
        which = lambda command: None if command in ('mkcert', 'rsync') else f'/usr/bin/{command}'
        compose = type('R', (), {'returncode': 0, 'stdout': '2.30.0\n'})()
        steps, problems = install_host.plan('dev', which=which, run=lambda *a, **k: compose)
        self.assertEqual(problems, [])
        self.assertEqual(steps[0], ['apt-get', 'install', '-y', 'mkcert', 'rsync'])
        installed = [step[-1] for step in steps if step[0] == 'install' and step[-1].endswith('.py')]
        self.assertEqual(installed, ['/usr/local/lib/setup/host_entry.py', '/usr/local/lib/setup/host_proxy.py'])

    def test_missing_docker_and_old_compose_need_a_person(self):
        _, problems = install_host.plan('dev', which=lambda c: None if c == 'docker' else '/bin/x')
        self.assertIn('Docker Engine', problems[0])
        old = type('R', (), {'returncode': 0, 'stdout': '2.20.1\n'})()
        _, problems = install_host.plan('dev', which=lambda c: '/bin/x', run=lambda *a, **k: old)
        self.assertIn('2.24.4', problems[0])

    def test_sudoers_rule_allows_only_the_installed_helpers(self):
        rule = install_host.sudoers_rule('dev')  # the system interpreter, never the caller's venv
        self.assertIn('dev ALL=(root) NOPASSWD: /usr/bin/python3 /usr/local/lib/setup/host_entry.py *, '
                      '/usr/bin/python3 /usr/local/lib/setup/host_proxy.py *', rule)
        self.assertNotIn(str(ROOT), rule)

    def test_installed_copies_must_match_the_checkout(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            installed = Path(temporary)
            for name in install_host.HELPERS:
                (installed / name).write_bytes((ROOT / 'docker' / name).read_bytes())
            self.assertTrue(install_host.installed_helpers_current(installed))
            (installed / 'host_proxy.py').write_text('# changed\n')
            self.assertFalse(install_host.installed_helpers_current(installed))


class BootstrapHelperTest(unittest.TestCase):
    def test_prefers_the_installed_copy_and_rejects_an_outdated_one(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            installed = Path(temporary)
            with patch.object(project_bootstrap, 'INSTALLED_HELPERS', installed):
                self.assertEqual(project_bootstrap.host_helper('host_entry.py'), ROOT / 'docker/host_entry.py')
                (installed / 'host_entry.py').write_bytes((ROOT / 'docker/host_entry.py').read_bytes())
                self.assertEqual(project_bootstrap.host_helper('host_entry.py'), installed / 'host_entry.py')
                (installed / 'host_entry.py').write_text('# old\n')
                with self.assertRaisesRegex(ValueError, 'rerun setup/install-host'):
                    project_bootstrap.host_helper('host_entry.py')

    def test_an_existing_route_to_the_same_port_counts_as_prepared(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            nginx = Path(temporary)
            (nginx / 'sites-enabled').mkdir()
            (nginx / 'sites-enabled/shop.local.conf').write_text(
                'server {\n  server_name shop.local pma.shop.local;\n  location / { proxy_pass http://127.0.0.1:3039; }\n}\n')
            self.assertEqual(project_bootstrap.route_exists('shop.local', '3039', nginx), 'partial',
                             'a route without the newer service host names is refreshed')
            self.assertIsNone(project_bootstrap.route_exists('shop.local', '3041', nginx))
            self.assertIsNone(project_bootstrap.route_exists('other.local', '3039', nginx))
            (nginx / 'sites-enabled/shop.local.conf').write_text(
                'server {\n  server_name shop.local pma.shop.local mailpit.shop.local cron.shop.local dbmonitor.shop.local tables.shop.local;\n'
                '  location / { proxy_pass http://127.0.0.1:3039; }\n}\n')
            self.assertEqual(project_bootstrap.route_exists('shop.local', '3039', nginx), 'complete')


class OrchestratorTest(unittest.TestCase):
    def test_steps_run_in_order_and_stop_at_the_first_failure(self):
        calls = []
        project = type('P', (), {'environment': 'local', 'settings': {'DATABASE_NAME': 'shop'},
                                 'run': lambda self, args, profiles=None: calls.append(('build', args[1:2]))})()
        patches = [patch('project_environment.initialize_directories', lambda p: calls.append('init')),
                   patch('project_bootstrap.bootstrap', lambda p: (calls.append('bootstrap'), p)[1]),
                   patch.object(project_setup, 'schema_hook', lambda p: calls.append('schema-hook')),
                   patch('project_application.prepare_application', lambda p: calls.append('app-prepare')),
                   patch('project_health.start_project', lambda p, t: calls.append('up')),
                   patch.object(project_setup, 'database_tables', lambda p: 0)]
        with contextlib.ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                project_setup.run_setup(project)
        self.assertEqual(calls, ['init', 'bootstrap', 'schema-hook', ('build', ['php-fpm-base']), ('build', ['php-fpm-core']),
                                 ('build', []), 'app-prepare', 'up'])
        self.assertIn('[5/5] up', output.getvalue())
        self.assertIn('make db-load set=dev', output.getvalue())
        calls.clear()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('project_environment.initialize_directories', lambda p: calls.append('init')))
            stack.enter_context(patch('project_bootstrap.bootstrap', side_effect=ValueError('no TLS')))
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'no TLS'):
                project_setup.run_setup(project)
        self.assertEqual(calls, ['init'])


    def test_setup_installs_the_schema_hook_but_leaves_a_hook_manager_alone(self):
        from types import SimpleNamespace
        local = SimpleNamespace(environment='local', settings={'SCHEMA_DIRECTORY': 'app/database/schema'})
        with patch('database_schema.install_schema_hook') as install:
            project_setup.schema_hook(local)
            project_setup.schema_hook(SimpleNamespace(environment='test', settings=local.settings))
            project_setup.schema_hook(SimpleNamespace(environment='local', settings={}))
        install.assert_called_once_with(local)
        with patch('database_schema.install_schema_hook', side_effect=ValueError('core.hooksPath is configured')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            project_setup.schema_hook(local)
        self.assertIn('Schema hook not installed: core.hooksPath', output.getvalue())


if __name__ == '__main__':
    unittest.main()
