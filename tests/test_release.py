"""D model release: required server settings, the self-contained compose model, the lock and the transfer."""

import contextlib
import io
import json
from pathlib import Path
import shutil
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
from project_target import LocalTarget, SshTarget

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
        with self.assertRaisesRegex(ValueError, 'ENV=stage, prod or one STAGE_ENVIRONMENTS lists'):
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

        built = []
        with patch('project_release.subprocess.run', run), patch.object(project, 'run', lambda *a, **k: None), \
                patch('project_images.build_images', built.append), \
                patch('project_release.trivy_report'), contextlib.redirect_stdout(io.StringIO()):
            lock = build_release(project)
        self.assertEqual(built, [project], 'the release builds through build_images, parents first')
        return lock, pulls

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
            def __init__(self, command, **kwargs): saved.append(command)
            def wait(self): return 0

        model = release_model(project, '/home/shop/site')
        with patch('project_release.subprocess.Popen', Save), patch('project_release.release_model', return_value=model), \
                patch('project_release.subprocess.run', lambda command, **k: subprocess.CompletedProcess(command, 0, '', '')), \
                patch('project_release.prepare_runtime_directories'), patch('project_release.publish_release_info'), \
                contextlib.redirect_stdout(io.StringIO()):
            deploy_images(project)
        self.assertIn('getmeili/meilisearch:v1.10', saved[0], 'the external image travels with docker save')

    def test_only_the_images_the_target_lacks_or_holds_in_another_build_travel(self):
        layers = lambda *ids: {'RootFS': {'Layers': list(ids)}, 'Config': {'Cmd': ['run'], 'Env': None}, 'Os': 'linux',
                               'Architecture': 'amd64'}
        here = {'a:1': layers('l1', 'l2'), 'b:1': layers('l1', 'l3'), 'c:1': layers('l4')}
        # The target's store words it otherwise (no Env, more fields): the same image all the same.
        there = {'a:1': {**layers('l1', 'l2'), 'Config': {'Cmd': ['run'], 'Hostname': ''}, 'Id': 'sha256:manifest'},
                 'b:1': layers('l1', 'old')}
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[-2:] == ['{{json .DriverStatus}}']:
                return subprocess.CompletedProcess(command, 0, '[["Backing Filesystem","extfs"]]', '')
            if 'inspect' in command:
                images = there if '--context' in command else here
                found = images.get(command[-1])
                return subprocess.CompletedProcess(command, 0 if found else 1, json.dumps(found) if found else '', '')
            return subprocess.CompletedProcess(command, 0, '', '')

        class Stage:
            stdout = io.BytesIO(b'tar')
            def __init__(self, command, **kwargs): calls.append(command)
            def wait(self): return 0

        with patch('project_release.subprocess.run', run), patch('project_release.subprocess.Popen', Stage), \
                patch('project_release.shutil.which', return_value='/usr/bin/zstd'), contextlib.redirect_stdout(io.StringIO()) as out:
            project_release.send_images('shop-stage', ['a:1', 'b:1', 'c:1'])
        self.assertIn(['docker', 'save', 'b:1', 'c:1'], calls, 'a:1 is there already')
        self.assertIn(['zstd', '-q', '-T0', '-3', '-c'], calls, 'a classic store saves the layers uncompressed')
        self.assertIn('sending 2 images to Docker context shop-stage (1 there already), zstd', out.getvalue())
        there.update({'b:1': here['b:1'], 'c:1': here['c:1']})
        calls.clear()
        with patch('project_release.subprocess.run', run), patch('project_release.subprocess.Popen', Stage), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            project_release.send_images('shop-stage', ['a:1', 'b:1', 'c:1'])
        self.assertFalse([call for call in calls if 'save' in call or 'load' in call])
        self.assertIn('has the 3 images already: nothing to send', out.getvalue())

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
            def __init__(self, command, **kwargs): calls.append(command)
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
                patch('project_release.prepare_runtime_directories', prepare), \
                patch('project_release.publish_release_info') as published, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deploy_images(project), ['a:1', 'b:1'])
        # The dashboard's release card: the images have just started on DEPLOY_PATH.
        published.assert_called_once_with(project, '/home/shop/site', images=True)
        # Neither Docker knows the images (the inspections find nothing): both travel.
        calls = [call for call in calls if 'inspect' not in call and 'info' not in call and call[0] != 'zstd']
        self.assertEqual(calls[0], ['docker', 'save', 'a:1', 'b:1'])
        self.assertEqual(calls[1], ['docker', '--context', 'shop-stage', 'load'])
        self.assertEqual(calls[2], ['prepare', '/home/shop/site'], 'the directories exist before the containers start')
        self.assertEqual(calls[3][3:5], ['ps', '-aq'], 'containers left off their networks are found first')
        compose = calls[-1]
        self.assertEqual(compose[:6], ['docker', '--context', 'shop-stage', 'compose', '-p', 'release-demo-stage'])
        self.assertIn('--no-build', compose)
        self.assertEqual(compose[6:8], ['-f', '-'], 'the model is piped, not written to disk')
        self.assertFalse(any(directory.glob('*compose*')))

    def test_a_container_missing_one_of_its_networks_is_recreated(self):
        # A start that failed on a taken port can leave a container with only some of its networks; compose sees
        # an unchanged config and only restarts it ("host not found in upstream" for good).
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=shop-stage\n')
        model = {'networks': {'network_app': {'name': 'shop-stage-network'}, 'network_metrics': {'name': 'shop-stage-metrics'}},
                 'services': {'nginx-proxy': {'networks': {'network_app': {'aliases': ['x']}, 'network_metrics': {}}},
                              'webserver': {'networks': {'network_app': None}}, 'cron': {}}}
        inspected = [{'Name': '/shop-stage-nginx-proxy', 'Config': {'Labels': {'com.docker.compose.service': 'nginx-proxy'}},
                      'NetworkSettings': {'Networks': {'shop-stage-metrics': {}}}},
                     {'Name': '/shop-stage-webserver', 'Config': {'Labels': {'com.docker.compose.service': 'webserver'}},
                      'NetworkSettings': {'Networks': {'shop-stage-network': {}}}},
                     {'Name': '/shop-stage-cron', 'Config': {'Labels': {'com.docker.compose.service': 'cron'}},
                      'NetworkSettings': {'Networks': {'shop-stage-default': {}}}}]
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if command[3:5] == ['ps', '-aq']:
                return subprocess.CompletedProcess(command, 0, 'a\nb\nc\n', '')
            if command[3] == 'inspect':
                return subprocess.CompletedProcess(command, 0, json.dumps(inspected), '')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch('project_release.subprocess.run', run), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(project_release.recreate_detached(project, 'shop-stage', 'release-demo-stage', model),
                             ['shop-stage-nginx-proxy'])
        self.assertEqual(calls[0][:6], ['docker', '--context', 'shop-stage', 'ps', '-aq', '--filter'])
        self.assertIn('label=com.docker.compose.project=release-demo-stage', calls[0])
        self.assertEqual(calls[-1], ['docker', '--context', 'shop-stage', 'rm', '-f', 'shop-stage-nginx-proxy'])
        self.assertIn('shop-stage-network', out.getvalue())

    def test_a_published_port_held_by_something_else_stops_the_deploy_first(self):
        project = self.stage('APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH=/home/shop/site\nDEPLOY_CONTEXT=default\n')
        model = {'services': {'nginx-proxy': {'ports': [
            {'host_ip': '10.8.0.14', 'published': '80', 'target': 80, 'protocol': 'tcp'},
            {'host_ip': '10.8.0.14', 'published': '443', 'target': 443, 'protocol': 'tcp'}]}}}
        listening = 'LISTEN 0 511 0.0.0.0:80 0.0.0.0:*\nLISTEN 0 4096 10.8.0.14:443 0.0.0.0:*\nLISTEN 0 511 [::]:80 [::]:*\n' \
                    'LISTEN 0 4096 127.0.0.1:9102 0.0.0.0:*\n'

        def target(own):
            def run(command, **kwargs):
                if command[0] == 'ss':
                    return subprocess.CompletedProcess(command, 0, listening, '')
                return subprocess.CompletedProcess(command, 0, own, '')
            return run

        def on(own):
            """ss on this machine and its Docker answer from the fake."""
            run, stack = target(own), contextlib.ExitStack()
            stack.enter_context(patch('project_release.subprocess.run', run))
            stack.enter_context(patch('project_release.deploy_target', lambda project: LocalTarget(run=run)))
            return stack

        with on('10.8.0.14:443->443/tcp\n'):
            with self.assertRaisesRegex(ValueError, r'10\.8\.0\.14:80 .*in use on the target.*nothing was started'):
                project_release.check_ports(project, 'default', 'release-demo-stage', model)
        # This project's own proxy holds both: nothing to free.
        with on('10.8.0.14:80->80/tcp, 10.8.0.14:443->443/tcp\n'):
            project_release.check_ports(project, 'default', 'release-demo-stage', model)
        # A remote Docker context without DEPLOY_SSH: no way to look, compose reports it as before.
        with patch('project_release.subprocess.run', side_effect=AssertionError('no lookup')):
            project_release.check_ports(project, 'shop-stage', 'release-demo-stage', model)

    @unittest.skipUnless(shutil.which('rsync'), 'rsync is required')
    def test_the_release_info_says_what_runs_on_the_target(self):
        from project_deploy import record
        target = self.app.parent / 'target'
        project = self.stage(f'APP_UID=1012\nAPP_GID=1013\nDEPLOY_PATH={target}\nDEPLOY_CONTEXT=default\n')
        directory = project_release.release_directory(project)
        directory.mkdir(parents=True)
        (directory / 'images.lock.json').write_text(json.dumps({'project': 'release-demo-stage', 'setup': 'v2.13.0', 'images': {}}))
        current = target / 'data/stage/release/current.json'
        with contextlib.redirect_stdout(io.StringIO()):
            project_release.publish_release_info(project, str(target), images=True)
        first = json.loads(current.read_text())
        self.assertEqual((first['environment'], first['setup']), ('stage', 'v2.13.0'))
        self.assertIn('images_at', first)
        self.assertNotIn('commit', first, 'no code deployed yet')
        # The last completed deploy, not a failed one after it; the images' time stays.
        record(project, {'ref': 'v1.4.0', 'commit': 'a' * 40, 'at': '2026-10-16T06:05:00+00:00'})
        record(project, {'ref': 'v1.5.0', 'commit': 'b' * 40, 'at': '2026-10-16T07:00:00+00:00', 'failed': 'after-rsync'})
        with contextlib.redirect_stdout(io.StringIO()):
            project_release.publish_release_info(project, str(target))
        second = json.loads(current.read_text())
        self.assertEqual((second['ref'], second['commit'], second['deployed_at'], second['images_at']),
                         ('v1.4.0', 'a' * 40, '2026-10-16T06:05:00+00:00', first['images_at']))
        self.assertEqual(current.stat().st_mode & 0o777, 0o644, 'the metrics service reads it as the project account')
        # make release alone ships nothing: a code-only deploy keeps the setup version of the images that run.
        (directory / 'images.lock.json').write_text(json.dumps({'project': 'release-demo-stage', 'setup': 'v2.14.0', 'images': {}}))
        with contextlib.redirect_stdout(io.StringIO()):
            project_release.publish_release_info(project, str(target))
        self.assertEqual(json.loads(current.read_text())['setup'], 'v2.13.0')
        # The deploy is done when it runs: a target that refuses the file is a warning, not a failed deploy.
        class Refusing(LocalTarget):
            def push(self, source, path, *options):
                raise subprocess.CalledProcessError(255, ['rsync'])
        with patch('project_release.deploy_target', lambda project: Refusing()), contextlib.redirect_stdout(io.StringIO()) as out:
            project_release.publish_release_info(project, str(target), images=True)
        self.assertIn('Warning: the release information for the dashboard was not written (CalledProcessError)', out.getvalue())
        self.assertEqual(json.loads(current.read_text())['setup'], 'v2.13.0', 'the target keeps its previous file')

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
        calls = []
        server = SshTarget('shop@host.example', run=lambda command, **kwargs: calls.append(command))
        with patch('project_release.deploy_target', lambda project: server), contextlib.redirect_stdout(io.StringIO()):
            project_release.prepare_runtime_directories(project, model, str(target))
        self.assertEqual(calls, [server.ssh('mkdir', '-p', '--', *runtime)], 'a server checks no local permissions')


if __name__ == '__main__':
    unittest.main()
