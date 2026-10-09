"""D model release: required server settings, the self-contained compose model, the lock and the transfer."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project
import project_release
from project_release import deploy_images, release_model, release_settings, setup_version

REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n'
          'DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test-only\n')


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('release-demo', temporary.name)
        (self.app / 'env/stage.env').write_text(REMOTE)

    def stage(self, extra=''):
        with (self.app / 'env/stage.env').open('a') as env:
            env.write(extra)
        return Project(self.app, 'stage')

    def test_server_settings_are_required_and_described_in_env(self):
        with self.assertRaisesRegex(ValueError, 'needs APP_UID, APP_GID, DEPLOY_PATH, DEPLOY_CONTEXT in env/stage.env'):
            release_settings(self.stage())
        with self.assertRaisesRegex(ValueError, 'ENV=stage or ENV=prod'):
            release_settings(Project(self.app))
        (self.app / 'env/stage.env').write_text(REMOTE + 'DEPLOY_CONTEXT=shop-stage\n')
        with self.assertRaisesRegex(ValueError, 'DEPLOY_CONTEXT runs ENV=stage on a server: set APP_UID'):
            Project(self.app, 'stage')
        (self.app / 'env/stage.env').write_text(REMOTE)
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=srv/shop\nDEPLOY_CONTEXT=shop-stage\n')
        with self.assertRaisesRegex(ValueError, 'absolute project root'):
            release_settings(project)
        project = self.stage('DEPLOY_PATH=/home/shop/app\n')
        self.assertEqual(release_settings(project), ('shop-stage', '/home/shop/app'))
        self.assertEqual((project.process_env['HOST_UID'], project.process_env['HOST_GID']), ('1012', '1013'),
                         'server containers run as the hosting account')
        with self.assertRaisesRegex(ValueError, 'numeric ids'):
            self.stage('APP_UID=shop\n')

    def test_release_model_maps_project_paths_and_needs_no_setup_checkout(self):
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=shop-stage\n')
        model = release_model(project, '/home/shop/site')
        text = json.dumps(model)
        self.assertNotIn(str(ROOT), text, 'no setup path reaches the target')
        self.assertNotIn(str(project.root), text, 'no build-machine path reaches the target')
        self.assertTrue(all('build' not in service for service in model['services'].values()))
        php = model['services']['php-fpm']
        self.assertNotIn('/opt/setup/runtime', [v['target'] for v in php['volumes']], 'the runtime is baked into the image')
        sources = [v['source'] for v in php['volumes'] if v['type'] == 'bind']
        self.assertTrue(sources and all(source.startswith('/home/shop/site/') for source in sources))
        self.assertEqual(php['user'], '1012:1013')
        self.assertEqual(model['name'], project.name)

    def test_a_release_never_comes_from_uncommitted_setup(self):
        dirty = subprocess.CompletedProcess([], 0, 'v2.0.0-3-gabc1234-dirty\n', '')
        with patch('project_release.subprocess.run', return_value=dirty):
            with self.assertRaisesRegex(ValueError, 'uncommitted changes'):
                setup_version()
            self.assertEqual(setup_version(allow_dirty=True), 'v2.0.0-3-gabc1234-dirty')
        ahead = subprocess.CompletedProcess([], 0, 'v2.0.0-3-gabc1234\n', '')
        with patch('project_release.subprocess.run', return_value=ahead):
            self.assertEqual(setup_version(), 'v2.0.0-3-gabc1234', 'stage may build from any committed setup')
            with self.assertRaisesRegex(ValueError, 'ENV=prod releases build from a setup release tag'):
                setup_version(tagged=True)
        with patch('project_release.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'v2.0.0\n', '')):
            self.assertEqual(setup_version(tagged=True), 'v2.0.0')

    def test_deploy_streams_images_to_the_context_and_starts_the_model(self):
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=shop-stage\n')
        with self.assertRaisesRegex(ValueError, 'make release ENV=stage'):
            deploy_images(project)
        directory = project_release.release_directory(project)
        directory.mkdir(parents=True)
        (directory / 'images.lock.json').write_text(json.dumps({'project': 'release-demo-stage', 'images': {'b:1': 'x', 'a:1': 'y'}}))
        calls = []

        class Save:
            stdout = io.BytesIO(b'tar')
            def __init__(self, command, stdout): calls.append(command)
            def wait(self): return 0

        def run(command, **kwargs):
            calls.append(command)
            if 'compose' in command:
                self.assertIn('"services"', kwargs['input'])
            return subprocess.CompletedProcess(command, 0, '', '')

        model = release_model(project, '/home/shop/site')
        with patch('project_release.subprocess.Popen', Save), patch('project_release.subprocess.run', run), \
                patch('project_release.release_model', return_value=model), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy_images(project), ['a:1', 'b:1'])
        self.assertEqual(calls[0], ['docker', 'save', 'a:1', 'b:1'])
        self.assertEqual(calls[1], ['docker', '--context', 'shop-stage', 'load'])
        self.assertEqual(calls[2][:6], ['docker', '--context', 'shop-stage', 'compose', '-p', 'release-demo-stage'])
        self.assertIn('--no-build', calls[2])
        self.assertEqual(calls[2][6:8], ['-f', '-'], 'the model is piped, not written to disk')
        self.assertFalse(any(directory.glob('*compose*')))


if __name__ == '__main__':
    unittest.main()
