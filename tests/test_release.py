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
from project_release import build_release, deploy_images, release_model, release_settings, setup_version

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

    def test_the_backup_service_reads_the_profiles_from_the_image(self):
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SERVICES=backup\n')
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=shop-stage\n'
                             'BACKUP_ALLOWED_IPS=10.8.0.0/24\n')
        model = release_model(project, '/home/shop/site')
        self.assertNotIn('profiles', model['services']['backup'],
                         'the model holds the selected services only; compose up would skip a profiled one')
        targets = [volume['target'] for volume in model['services']['backup']['volumes']]
        self.assertNotIn('/opt/setup/database-profile', targets, 'baked into the image, not mounted')
        self.assertNotIn(str(ROOT), json.dumps(model))
        dockerfile = (ROOT / 'docker/Dockerfile').read_text()
        self.assertIn('COPY /database-profile/ /opt/setup/database-profile/', dockerfile)

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

    def test_deploy_protected_paths_come_from_the_project_env(self):
        from project_deploy import protected_paths
        project = self.stage('DEPLOY_PROTECTED_PATHS="var/data/ var/log/"\n')
        self.assertEqual(protected_paths(project)[-2:], ['/var/data/', '/var/log/'])
        self.assertNotIn('/.htaccess', protected_paths(project), 'this scaffold has an empty (generic) PROFILE')

    def external_project(self):
        (self.app / 'compose').mkdir(exist_ok=True)
        # Meilisearch comes from a registry; the worker reuses the built PHP image like cron does.
        (self.app / 'compose/common.yaml').write_text(
            'services:\n'
            '  search:\n    image: getmeili/meilisearch:v1.10\n'
            '  worker:\n    image: ${PROJECT_NAME}-php-fpm-${IMAGE_ENV:-${ENV}}${SETUP_IMAGE_SUFFIX_PHP_FPM:-}\n'
            '    command: [\"php\", \"-v\"]\n')
        return self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=shop-stage\n')

    def release(self, project, pull_fails=False, missing=()):
        built = {service['image'] for service in project.model()['services'].values() if service.get('build')}
        present = built - set(missing)
        pulls = []
        real = subprocess.run

        def run(command, **kwargs):
            if command[:2] == ['git', '-C']:
                return subprocess.CompletedProcess(command, 0, 'v2.0.0\n', '')
            if command[:2] == ['docker', 'pull']:
                pulls.append(command)
                if not pull_fails:
                    present.add(command[2])
                return subprocess.CompletedProcess(command, 1 if pull_fails else 0, '', 'denied' if pull_fails else '')
            if command[:3] == ['docker', 'image', 'inspect']:
                name = command[-1]
                if name not in present:
                    return subprocess.CompletedProcess(command, 1, '', 'No such image')
                if 'RepoDigests' in command[4]:
                    digest = [name.split(':')[0] + '@sha256:abc'] if '/' in name else []
                    return subprocess.CompletedProcess(command, 0, json.dumps(digest), '')
                return subprocess.CompletedProcess(command, 0, f'sha256:{name}\n', '')
            self.assertEqual(command[:2], ['docker', 'compose'], 'nothing else runs during the release')
            return real(command, **kwargs)

        with patch('project_release.subprocess.run', run), patch.object(project, 'run', lambda *a, **k: None), \
                patch('project_release.trivy_report'), contextlib.redirect_stdout(io.StringIO()):
            return build_release(project), pulls

    def test_release_pulls_locks_and_ships_external_project_images(self):
        project = self.external_project()
        lock, pulls = self.release(project)
        self.assertEqual(pulls, [['docker', 'pull', 'getmeili/meilisearch:v1.10']], 'only images no service builds are pulled')
        self.assertEqual(lock['images']['getmeili/meilisearch:v1.10'], 'sha256:getmeili/meilisearch:v1.10')
        self.assertTrue(all(lock['images'].values()), 'every image in the lock exists on this machine')
        self.assertEqual(lock['external'], {'getmeili/meilisearch:v1.10': 'getmeili/meilisearch@sha256:abc'})
        written = json.loads((project_release.release_directory(project) / 'images.lock.json').read_text())
        self.assertEqual(written['images'], lock['images'])
        saved = []

        class Save:
            stdout = io.BytesIO(b'tar')
            def __init__(self, command, stdout): saved.append(command)
            def wait(self): return 0

        model = release_model(project, '/home/shop/site')
        with patch('project_release.subprocess.Popen', Save), patch('project_release.release_model', return_value=model), \
                patch('project_release.subprocess.run', lambda command, **k: subprocess.CompletedProcess(command, 0, '', '')), \
                patch('project_release.prepare_runtime_directories'), contextlib.redirect_stdout(io.StringIO()):
            deploy_images(project)
        self.assertIn('getmeili/meilisearch:v1.10', saved[0], 'the external image travels with docker save')

    def test_release_stops_when_an_image_cannot_be_had(self):
        project = self.external_project()
        lock_file = project_release.release_directory(project) / 'images.lock.json'
        with self.assertRaisesRegex(ValueError, 'Cannot pull getmeili/meilisearch:v1.10'):
            self.release(project, pull_fails=True)
        self.assertFalse(lock_file.exists(), 'no lock that docker save would fail on')
        php = project.model()['services']['php-fpm']['image']
        with self.assertRaisesRegex(ValueError, f'not on this machine: {php}'):
            self.release(project, missing={php})
        self.assertFalse(lock_file.exists())

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
        prepare = lambda project, model, path: calls.append(['prepare', path])
        with patch('project_release.subprocess.Popen', Save), patch('project_release.subprocess.run', run), \
                patch('project_release.release_model', return_value=model), \
                patch('project_release.prepare_runtime_directories', prepare), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy_images(project), ['a:1', 'b:1'])
        self.assertEqual(calls[0], ['docker', 'save', 'a:1', 'b:1'])
        self.assertEqual(calls[1], ['docker', '--context', 'shop-stage', 'load'])
        self.assertEqual(calls[2], ['prepare', '/home/shop/site'], 'the directories exist before the containers start')
        self.assertEqual(calls[3][:6], ['docker', '--context', 'shop-stage', 'compose', '-p', 'release-demo-stage'])
        self.assertIn('--no-build', calls[3])
        self.assertEqual(calls[3][6:8], ['-f', '-'], 'the model is piped, not written to disk')
        self.assertFalse(any(directory.glob('*compose*')))

    def test_deploy_creates_the_code_and_data_directories_as_the_deploying_account(self):
        # Directories Docker creates for a missing bind source belong to root: php-fpm and MySQL, which run as
        # APP_UID, could not write the code directory or their data on a first deploy.
        target = self.app.parent / 'target'
        project = self.stage(f'APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH={target}\nDEPLOY_CONTEXT=default\n')
        model = release_model(project, str(target))
        runtime = project_release.runtime_directories(project, model, str(target))
        self.assertIn(str(target / 'app/stage/public'), runtime, 'the code directory, filled later by make deploy')
        self.assertIn(str(target / 'data/stage/mysql'), runtime)
        self.assertFalse([path for path in runtime if '/config/' in path], 'configuration comes from the checkout')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(project_release.prepare_runtime_directories(project, model, str(target)), runtime)
        self.assertTrue(all(Path(path).is_dir() for path in runtime))
        locked = target / 'data/stage/mysql'
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        with patch('project_release.os.access', lambda path, mode: Path(path) != locked), \
                self.assertRaisesRegex(ValueError, f'not writable.*{locked}.*chown'):
            project_release.prepare_runtime_directories(project, model, str(target))
        project.settings['DEPLOY_SSH'] = 'shop@host.example'
        calls = []
        with patch('project_release.subprocess.run', lambda command, **kwargs: calls.append(command)), \
                contextlib.redirect_stdout(io.StringIO()):
            project_release.prepare_runtime_directories(project, model, str(target))
        self.assertEqual(calls[0][:5], ['ssh', 'shop@host.example', 'mkdir', '-p', '--'])
        self.assertEqual(calls[0][5:], runtime)


if __name__ == '__main__':
    unittest.main()
