"""Profile rule: a generic project (empty or `generic` PROFILE) never reads or touches PrestaShop things.

The cycle test drives the runner's own entry point (what `make <action>` calls) against a scaffolded generic
project, with stand-ins for docker, sudo and mkcert on PATH: Compose renders the real model, every other
Docker call is recorded (argv and the SQL sent to the database) and answered without containers.
"""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
import project as runner
from create_project import complete_options, scaffold
from project import Project

FAKE_DOCKER = '''#!/usr/bin/env python3
import json, os, sys
REAL, LOG = {real!r}, {log!r}
args = sys.argv[1:]
VALUED = ('--project-directory', '--env-file', '--project-name', '-f', '-p', '--profile')
def subcommand(args):
    index = 1
    while index < len(args):
        if args[index] in VALUED: index += 2
        elif args[index].startswith('-'): index += 1
        else: return args[index]
    return ''
command = subcommand(args) if args[:1] == ['compose'] else (args[0] if args else '')
if command in ('config', 'version'):
    os.execv(REAL, [REAL, *args])
stdin = sys.stdin.read() if command == 'exec' and not sys.stdin.isatty() else ''
with open(LOG, 'a') as log:
    log.write(json.dumps({{'argv': args, 'stdin': stdin}}) + '\\n')
if command == 'exec' and 'database' in args:
    print('0' if 'COUNT(*)' in stdin else '', end='')
elif command == 'ps' and '--services' in args:
    print('database\\nphp-fpm\\nwebserver\\nnginx-proxy')  # PHP runs: a PrestaShop step would fire now
elif args[:1] == ['info']:
    print('27.0.0' if 'ServerVersion' in ' '.join(args) else '16000000000 8')
elif args[:2] == ['context', 'inspect']:
    print('unix:///var/run/docker.sock')
'''
FAKE_SUDO = '#!/bin/sh\necho "$@" >> {log}\n'
FAKE_MKCERT = '''#!/bin/sh
while [ $# -gt 0 ]; do
    case "$1" in -cert-file) echo cert > "$2"; shift 2 ;; -key-file) echo key > "$2"; shift 2 ;; *) shift ;; esac
done
'''
SCHEMA = ('CREATE TABLE `app_note` (\n  `id` int NOT NULL AUTO_INCREMENT,\n  `body` varchar(255) NOT NULL,\n'
          '  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n')
# Files a PrestaShop step would write, read or copy; a generic project keeps whatever it has there.
PRESTASHOP_BAIT = ('app/config/parameters.php', 'config/settings.inc.php', 'config/defines.inc.php')


def scratch(test):
    directory = ROOT / '.test-work'
    directory.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='generic-', dir=directory)
    test.addCleanup(temporary.cleanup)
    return Path(temporary.name)


def generic_project(parent, profile='generic'):
    """A scaffolded generic project with a schema snapshot, a data set, a project hook and PHPUnit suites."""
    with contextlib.redirect_stdout(io.StringIO()):
        app = scaffold('generic-demo', parent, complete_options({'profile': 'generic', 'php_version': '8.4',
                                                                 'database': 'mysql', 'database_version': '8.4',
                                                                 'services': ''}))
    common = app / 'env/common.env'
    common.write_text(common.read_text().replace('PROFILE=generic', f'PROFILE={profile}')
                      + 'SCHEMA_DIRECTORY=app/database/schema\n')
    schema = app / 'database/schema'
    schema.mkdir(parents=True)
    (schema / 'table-app_note.sql').write_text(SCHEMA)
    (schema / 'schema.manifest.json').write_text(json.dumps(
        {'format': 1, 'dialect': 'mysql', 'tables': [{'name': 'app_note', 'file': 'table-app_note.sql'}]}) + '\n')
    (app / 'database/data/dev').mkdir(parents=True)
    (app / 'database/data/dev/content.sql').write_text("INSERT INTO `app_note` (`id`, `body`) VALUES\n(1,'hello');\n")
    (app / 'database/after-import/common').mkdir(parents=True)
    (app / 'database/after-import/common/010-host.sql').write_text(
        "UPDATE `app_note` SET `body` = 'https://${DOMAIN}/' WHERE `id` = 1;\n")
    (app / 'tests').mkdir()
    (app / 'tests/phpunit.xml.dist').write_text('<phpunit/>\n')
    public = app / 'public'
    (public / 'phpunit.xml.dist').write_text('<phpunit/>\n')
    (public / 'modules/blog').mkdir(parents=True)
    (public / 'modules/blog/phpunit.xml').write_text('<phpunit/>\n')  # an ordinary folder, not a PrestaShop module
    for relative in PRESTASHOP_BAIT:
        (public / relative).parent.mkdir(parents=True, exist_ok=True)
        (public / relative).write_text('<?php // the application owns this file\n')
    # Its own repository: the schema hook that make setup installs belongs to this project, never to setup's.
    subprocess.run(['git', 'init', '-q', str(app.parent)], check=True)
    (app.parent / 'dump.sql').write_text(SCHEMA + "INSERT INTO `app_note` VALUES (1,'live');\n")
    return app


class GenericCycleTest(unittest.TestCase):
    """make setup, db-import, test-prepare and ide-init of a generic project: no PrestaShop step, file or query."""

    profile = 'generic'

    def setUp(self):
        self.parent = scratch(self)
        self.app = generic_project(self.parent, self.profile)
        bin_directory = self.parent / 'bin'
        bin_directory.mkdir()
        self.log = self.parent / 'docker.log'
        stand_ins = {'docker': FAKE_DOCKER.format(real=shutil.which('docker'), log=str(self.log)),
                     'sudo': FAKE_SUDO.format(log=self.parent / 'sudo.log'), 'mkcert': FAKE_MKCERT}
        for name, text in stand_ins.items():
            (bin_directory / name).write_text(text)
            (bin_directory / name).chmod(0o755)
        environment = patch.dict(os.environ, {'PATH': f'{bin_directory}:{os.environ["PATH"]}',
                                              'XDG_CONFIG_HOME': str(self.parent / 'xdg')})
        environment.start()
        self.addCleanup(environment.stop)
        # *.local names would wait for multicast DNS; unresolved, the host entry goes through (fake) sudo.
        unresolved = patch('project_bootstrap.socket.getaddrinfo', side_effect=socket.gaierror('not found'))
        unresolved.start()
        self.addCleanup(unresolved.stop)

    def make(self, action, *options):
        argv = ['project.py', '--docker-directory', str(self.app), '--project-directory', str(self.app.parent),
                '--env', 'local', action, *options]
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            code = runner.main()
        self.assertEqual(code, 0, f'make {action}: {errors.getvalue()}{output.getvalue()[-2000:]}')
        return output.getvalue()

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def assert_no_prestashop(self, text, where):
        # PrestaShop names: the adapter and release paths, the table prefix, configuration keys and the shop URL.
        self.assertNotRegex(text, r'(?i)prestashop|(?<![A-Za-z0-9])ps_|shop_url', f'{where} mentions PrestaShop')

    def test_the_whole_cycle_runs_without_prestashop(self):
        public = self.app / 'public'
        bait = {relative: (public / relative).read_bytes() for relative in PRESTASHOP_BAIT}
        output = self.make('setup')
        self.assertIn('No application preparation for this profile.', output)
        self.assertIn('Setup complete.', output)
        self.assertIn(f'--docker-directory {self.app} ', (self.app.parent / '.git/hooks/pre-commit').read_text())
        output += self.make('db-import', '--dump', str(self.app.parent / 'dump.sql'))
        output += self.make('test-prepare')
        output += self.make('ide-init')
        output += self.make('doctor')
        output += self.make('runtime-info')
        self.assert_no_prestashop(output, 'the command output')
        self.assertNotRegex(output, r'(?m)^PS_', 'runtime-info lists no PrestaShop switches')

        calls = self.calls()
        actions = [call['argv'] for call in calls]
        self.assertTrue(any('build' in argv for argv in actions) and any('up' in argv for argv in actions))
        for call in calls:
            self.assert_no_prestashop(' '.join(call['argv']), 'a Docker command')
            self.assert_no_prestashop(call['stdin'], 'SQL sent to the database')
        sql = ''.join(call['stdin'] for call in calls)
        self.assertIn("'live'", sql, 'the dump reached the local database')
        self.assertIn("'hello'", sql, 'the data set reached the test database')
        self.assertIn('https://generic-demo.local/', sql, 'project after-import hooks still run')
        self.assertIn('https://generic-demo.test.local/', sql)

        for relative, contents in bait.items():
            self.assertEqual((public / relative).read_bytes(), contents, f'{relative} is the application\'s own')
        private = (self.app / 'env/local.env').read_text() + (self.app / 'env/test.env').read_text()
        self.assertNotIn('PS_', private, 'no PrestaShop keys are generated')
        copy = self.app / '.generated/test/app'
        self.assertTrue((copy / 'index.php').is_file())
        for relative in ('var/cache', 'var/logs', 'cache'):
            self.assertFalse((copy / relative).exists(), f'{relative} is a PrestaShop folder')

        run_configurations = {path.name for path in (self.app / '.idea/runConfigurations').glob('setup_phpunit_*.xml')}
        self.assertEqual(run_configurations, {'setup_phpunit_app.xml', 'setup_phpunit_project.xml'},
                         'modules/ is an ordinary folder of a generic application')
        module = (self.app / '.idea/generic-demo.iml').read_text()
        self.assertNotIn('public/var/cache', module)
        model = Project(self.app).model()['services']['php-fpm-core']['build']['args']
        self.assertEqual(model['PS_RELEASE'], 'off')
        self.assertNotIn('prestashop', model['PS_RELEASE_IMAGE'])


class EmptyProfileCycleTest(GenericCycleTest):
    """An empty PROFILE is the same generic application."""

    profile = ''


class GenericSettingsTest(unittest.TestCase):
    """PrestaShop settings and placeholders are refused in a generic project instead of being acted on."""

    def setUp(self):
        self.app = generic_project(scratch(self))

    def common(self, text):
        with (self.app / 'env/common.env').open('a') as env:
            env.write(text)

    def test_the_prestashop_release_is_not_built_into_a_generic_image(self):
        for setting in ('PS_VERSION=9.0.2\nPS_RELEASE=on\n', 'PS_RELEASE_PATHS=vendor\n', 'PS_ADMIN_DIR=admin123\n'):
            with self.subTest(setting=setting):
                common = (self.app / 'env/common.env').read_text()
                self.common(setting)
                with self.assertRaisesRegex(ValueError, 'PROFILE=prestashop'):
                    Project(self.app)
                (self.app / 'env/common.env').write_text(common)

    def test_prestashop_hook_values_are_refused_in_generic_sql(self):
        from database_import import plan_import
        hook = self.app / 'database/after-import/common/020-prefix.sql'
        for token in ('${DB_PREFIX}', '${PS_SSL}'):
            with self.subTest(token=token):
                hook.write_text(f"UPDATE `{token}configuration` SET value = '{token}';\n")
                with self.assertRaisesRegex(ValueError, 'PROFILE=prestashop'):
                    plan_import(Project(self.app), str(self.app.parent / 'dump.sql'))

    def test_the_prestashop_table_prefix_is_refused_in_a_generic_registry(self):
        from database_registry import load
        project = Project(self.app)
        (self.app / 'database/table-registry.txt').write_text('app_note content full import\n')
        self.assertEqual(load(project), [('app_note', 'content', 'full', 'import')])
        (self.app / 'database/table-registry.txt').write_text('{p}note content full import\n')
        with self.assertRaisesRegex(ValueError, 'PROFILE=prestashop'):
            load(project)


class GenericUnitsTest(unittest.TestCase):
    """Each guarded place, called directly with a generic and a PrestaShop project."""

    def setUp(self):
        self.root = scratch(self)

    def test_suites_of_a_generic_application_ignore_modules(self):
        from project_testing import find_suites
        web = self.root / 'web'
        for relative in ('phpunit.xml.dist', 'modules/blog/phpunit.xml', 'modules/blog/tests/phpunit.xml.dist'):
            (web / relative).parent.mkdir(parents=True, exist_ok=True)
            (web / relative).write_text('<phpunit/>\n')
        self.assertEqual(sorted(find_suites(web, prestashop=False)), ['app'])
        self.assertEqual(sorted(find_suites(web, prestashop=True)), ['blog', 'shop'])

    def test_a_generic_data_set_needs_no_product_sample_or_prestashop_key(self):
        from database_dataset import legacy_password_key, product_sample_needed
        directory = self.root / 'data'
        directory.mkdir()
        (directory / 'dataset.env').write_text('PS_COOKIE_KEY=' + 'a' * 56 + '\n')
        generic = SimpleNamespace(settings={'PROFILE': '', 'PS_VERSION': '1.6.1.24'})
        prestashop = SimpleNamespace(settings={'PROFILE': 'prestashop', 'PS_VERSION': '1.6.1.24'})
        self.assertIsNone(legacy_password_key(generic, directory), 'a generic set never uses a PrestaShop cookie key')
        self.assertEqual(legacy_password_key(prestashop, directory), 'a' * 56)
        entries = [('app_note', 'content', 'full', 'import'), ('app_user', 'users', 'faker', 'import')]
        recipe = lambda table: None
        self.assertFalse(product_sample_needed(entries, ['app_note', 'app_user'], recipe))
        self.assertTrue(product_sample_needed([('app_item', 'catalog', 'slice', 'import')], ['app_item'], recipe))
        self.assertTrue(product_sample_needed(entries, ['app_note'], lambda table: '`id` IN {products}'))


if __name__ == '__main__':
    unittest.main()
