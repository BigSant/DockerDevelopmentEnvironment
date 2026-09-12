"""Version overrides and package commands use the selected project's sources."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project, main
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
