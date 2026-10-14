"""Version overrides and package commands use the selected project's sources."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project, main
from project_commands import run_qa
from project_commands import npm, composer


class DevelopmentToolsTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('tools-demo', temporary.name)

    def test_project_env_versions_reach_build_and_change_image_identity(self):
        before = Project(self.app).model()['services']['php-fpm-base']['image']
        versions = {'PHP_VERSION': '8.5', 'XDEBUG_VERSION': '3.5.3', 'COMPOSER_VERSION': '2.10.3',
                    'NODE_VERSION': '22.23.1', 'NPM_VERSION': '10.9.8', 'WEBPACK_VERSION': '5.94.0',
                    'WEBPACK_CLI_VERSION': '5.1.4', 'NVM_VERSION': 'v0.40.4',
                    'SUPERCRONIC_VERSION': '0.2.29'}
        with (self.app / 'env/common.env').open('a') as env:
            env.writelines(f'{key}={value}\n' for key, value in versions.items())
        model = Project(self.app).model()['services']['php-fpm-base']
        for key, value in versions.items():
            self.assertEqual(model['build']['args'][key], value)
        self.assertNotEqual(model['image'], before)
        with (self.app / 'env/local.env').open('a') as env:
            env.write('NPM_VERSION=10.9.7\n')
        overridden = Project(self.app).model()['services']['php-fpm-base']
        self.assertEqual(overridden['build']['args']['NPM_VERSION'], '10.9.7')
        self.assertNotEqual(overridden['image'], model['image'])

    def test_npm_cli_keeps_workdir_and_arguments_separate(self):
        with patch.object(Project, 'run') as execute, patch.object(sys, 'argv', [
                'project.py', '--docker-directory', str(self.app), 'npm',
                '--workdir', 'themes/my theme/_dev', '--command', 'run build -- "value with spaces"']):
            self.assertEqual(main(), 0)
        execute.assert_called_once_with(['exec', '-T', '--workdir', '/var/www/html/themes/my theme/_dev',
                                        'php-fpm', 'npm', 'run', 'build', '--', 'value with spaces'])

    def test_default_and_composer_working_directories_and_invalid_paths(self):
        project = Project(self.app)
        with patch.object(project, 'run') as execute:
            npm(project)
            execute.assert_called_once_with(['exec', '-T', '--workdir', '/var/www/html',
                                             'php-fpm', 'npm', '--version'])
            execute.reset_mock()
            composer(project, 'validate', 'themes/framework')
            execute.assert_called_once_with(['exec', '-T', '--workdir', '/var/www/html/themes/framework',
                                             'php-fpm', 'composer', 'validate'])
            execute.reset_mock()
            for directory in ('/tmp', '../outside', 'themes/../../outside', 'bad\x00path'):
                with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, 'dir must be relative'):
                    npm(project, 'ci', directory)
            execute.assert_not_called()


class QaTest(unittest.TestCase):
    """make phpstan / phpcs / e2e: the tool's container, its default command, e2e only on ENV=test."""

    def project(self, environment='local'):
        calls = []
        project = SimpleNamespace(environment=environment, settings={'PLAYWRIGHT_COMMAND': 'npx playwright test'},
                                  run=lambda args, profiles=None: calls.append((args, profiles)))
        return project, calls

    def test_phpstan_and_phpcs_run_their_make_target_without_dependencies(self):
        project, calls = self.project()
        run_qa(project, 'phpstan')
        run_qa(project, 'phpcs', 'fix -- src')
        self.assertEqual(calls, [(['run', '--rm', '--no-deps', '--entrypoint', 'make', 'php-phpstan', 'report'], 'phpstan'),
                                 (['run', '--rm', '--no-deps', '--entrypoint', 'make', 'php-cs', 'fix', '--', 'src'], 'phpcs')])

    def test_e2e_runs_on_the_test_project_with_its_first_word_as_entrypoint(self):
        project, _ = self.project()
        test, calls = self.project('test')
        with patch('project_testing.test_project', lambda given: test if given is project else None):
            run_qa(project, 'e2e')
            run_qa(project, 'e2e', 'npm run e2e -- --grep cart')
        self.assertEqual(calls, [(['run', '--rm', '--entrypoint', 'npx', 'playwright', 'playwright', 'test'], 'playwright'),
                                 (['run', '--rm', '--entrypoint', 'npm', 'playwright', 'run', 'e2e', '--', '--grep', 'cart'],
                                  'playwright')])

    def test_an_empty_command_is_refused(self):
        project, calls = self.project()
        for command in ('""', "''"):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, 'QA command cannot be empty'):
                run_qa(project, 'phpcs', command)
        self.assertEqual(calls, [])
