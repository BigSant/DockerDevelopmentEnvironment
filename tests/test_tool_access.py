"""setup-server's tool access: users and addresses beside target.env reach nginx-proxy on the target."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
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
import project_tool_access
from project_tool_access import apply_tool_access, check_source, push_tool_access, server_addresses

ACCESS = ROOT / 'docker/docker/nginx-proxy/conf/access.sh'
REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n'
          'DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test-only\n')
USERS = 'tomas:$6$salt$sha512.crypt.hash\nana:$6$other$hash.value\n'


def scratch(test):
    directory = ROOT / '.test-work'
    directory.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=directory)
    test.addCleanup(temporary.cleanup)
    return Path(temporary.name)


class ProxyRulesTest(unittest.TestCase):
    """access.sh with the files setup-server keeps in data/<env>/tool-access."""

    def setUp(self):
        self.output = scratch(self)
        self.server = self.output / 'tool-access'
        self.server.mkdir()

    def rules(self, environment='prod', **settings):
        env = {'PATH': os.environ['PATH'], 'SETUP_ENVIRONMENT': environment,
               'SETUP_ACCESS_DIR': str(self.output / 'access'), 'SETUP_REAL_IP_FILE': str(self.output / 'real-ip.conf'),
               'SETUP_ROUTE_FILE': str(self.output / 'none'), 'SETUP_TOOL_ACCESS_DIR': str(self.server), **settings}
        result = subprocess.run(['bash', str(ACCESS)], env=env, capture_output=True, text=True, timeout=5)
        read = lambda name: (self.output / 'access' / f'{name}.conf').read_text()
        return result, read

    def test_without_the_files_nothing_changes(self):
        result, read = self.rules(PMA_ALLOWED_IPS='203.0.113.10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read('pma'), 'allow 203.0.113.10;\ndeny all;\n')
        self.assertEqual(read('cron'), 'deny all;\n')

    def test_server_addresses_join_every_tool_list_but_never_the_site(self):
        (self.server / 'allowed-ips').write_text('# the VPN server\n198.51.100.7\n203.0.113.10 # office\n')
        result, read = self.rules(ALLOWED_IPS='203.0.113.10', METRICS_ALLOWED_IPS='192.0.2.5')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read('site'), 'allow 203.0.113.10;\ndeny all;\n', 'the shop stays as the project set it')
        self.assertEqual(read('pma'), 'allow 203.0.113.10;\nallow 198.51.100.7;\ndeny all;\n', 'once each')
        self.assertEqual(read('metrics'), 'allow 192.0.2.5;\nallow 198.51.100.7;\nallow 203.0.113.10;\ndeny all;\n')
        self.assertEqual(read('dbmonitor'), read('pma'))

    def test_server_users_guard_the_password_tools_beside_their_own(self):
        (self.server / 'users.htpasswd').write_text(USERS)
        result, read = self.rules(ALLOWED_IPS='198.51.100.7', CRON_HTPASSWD='ops:$apr1$x$y ana:$apr1$own$pass',
                                  PROXY_TOOLS='search')
        self.assertEqual(result.returncode, 0, result.stderr)
        users = self.server / 'users.htpasswd'
        for name in ('pma', 'mailpit', 'backup', 'metrics', 'search'):
            self.assertEqual(read(name), f'allow 198.51.100.7;\ndeny all;\nauth_basic "{name}";\nauth_basic_user_file {users};\n', name)
        joined = self.output / 'access/cron.htpasswd'
        self.assertIn(f'auth_basic_user_file {joined};', read('cron'))
        self.assertEqual(joined.read_text(), 'ops:$apr1$x$y\nana:$apr1$own$pass\ntomas:$6$salt$sha512.crypt.hash\n',
                         "either login opens it; a name in both keeps the tool's own password")
        self.assertEqual(stat.S_IMODE(joined.stat().st_mode), 0o644)
        # A revocation reaches the joined file with the next run (make tool-access), the tool's own users stay.
        (self.server / 'users.htpasswd').write_text('')
        self.assertEqual(self.rules(ALLOWED_IPS='198.51.100.7', CRON_HTPASSWD='ops:$apr1$x$y')[0].returncode, 0)
        self.assertEqual(joined.read_text(), 'ops:$apr1$x$y\n')
        self.assertNotIn('auth_basic', read('dbmonitor'), 'its bearer token takes the Authorization header')
        self.assertNotIn('auth_basic', read('site'))

    def test_an_empty_user_file_lets_nobody_in(self):
        (self.server / 'users.htpasswd').write_text('')
        result, read = self.rules(ALLOWED_IPS='198.51.100.7')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('auth_basic_user_file', read('pma'), 'nobody granted is nobody let in, not the IP rule alone')

    def test_a_bad_server_address_stops_before_any_rule_is_written(self):
        (self.server / 'allowed-ips').write_text('198.51.100.7\nall\n')
        result, _ = self.rules(ALLOWED_IPS='198.51.100.7')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid IP/CIDR in TOOL_ACCESS_ALLOWED_IPS: all', result.stderr)
        self.assertFalse((self.output / 'access/pma.conf').exists(), 'a reload keeps the rules nginx runs with')


class ToolAccessTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('tools-demo', scratch(self))
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SERVICES=pma\n')
        self.target = self.app.parent / 'target'
        (self.app / 'env/stage.env').write_text(REMOTE + f'APP_UID={os.getuid()}\nAPP_GID={os.getgid()}\n'
                                                         f'DEPLOY_PATH={self.target}\n')
        self.source = self.app.parent / 'tool-access/stage'

    def keep(self, users=USERS, addresses='198.51.100.7\n'):
        self.source.mkdir(parents=True, exist_ok=True)
        (self.source / 'users.htpasswd').write_text(users)
        (self.source / 'allowed-ips').write_text(addresses)
        for path in (self.source / 'users.htpasswd', self.source / 'allowed-ips'):
            path.chmod(0o600)

    def test_server_addresses_satisfy_the_stage_allowlist_guard(self):
        with self.assertRaisesRegex(ValueError, 'pma on stage requires PMA_ALLOWED_IPS or ALLOWED_IPS'):
            Project(self.app, 'stage')
        self.keep()
        project = Project(self.app, 'stage')
        self.assertEqual(server_addresses(project), ['198.51.100.7'])
        proxy = json.loads(project.capture(['config', '--format', 'json']))['services']['nginx-proxy']
        mounts = {volume['target']: volume for volume in proxy['volumes']}
        self.assertEqual(mounts['/etc/nginx/tool-access']['source'].rstrip('/'), str(project.data_directory / 'tool-access'))
        self.assertTrue(mounts['/etc/nginx/tool-access']['read_only'])

    def test_apply_copies_the_files_readable_to_nginx_and_reloads_it(self):
        self.keep()
        project = Project(self.app, 'stage')
        with patch.object(Project, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(apply_tool_access(project))
        copied = self.target / 'data/stage/tool-access'
        self.assertEqual((copied / 'users.htpasswd').read_text(), USERS)
        self.assertEqual(stat.S_IMODE((copied / 'users.htpasswd').stat().st_mode), 0o644, "nginx's workers read it")
        run.assert_called_once_with(['exec', '-T', 'nginx-proxy', 'sh', '-c', project_tool_access.RELOAD])
        self.assertIn('2 users, 1 addresses', out.getvalue())
        # A revocation: the line goes on the target too, and a file setup-server dropped is gone.
        self.keep(users='tomas:$6$salt$sha512.crypt.hash\n')
        (self.source / 'allowed-ips').unlink()
        with patch.object(Project, 'run'), contextlib.redirect_stdout(io.StringIO()):
            apply_tool_access(project)
        self.assertEqual((copied / 'users.htpasswd').read_text(), 'tomas:$6$salt$sha512.crypt.hash\n')
        self.assertFalse((copied / 'allowed-ips').exists())

    def test_without_the_directory_the_target_is_left_alone(self):
        with (self.app / 'env/stage.env').open('a') as env:
            env.write('ALLOWED_IPS=203.0.113.10\n')
        project = Project(self.app, 'stage')
        with patch.object(Project, 'run') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(apply_tool_access(project))
        run.assert_not_called()
        self.assertFalse((self.target / 'data/stage/tool-access').exists())
        self.assertIn('nothing changed', out.getvalue())

    def test_a_stopped_proxy_is_a_clear_error(self):
        self.keep()
        project = Project(self.app, 'stage')
        failed = subprocess.CalledProcessError(1, ['docker'])
        with patch.object(Project, 'run', side_effect=failed), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(ValueError, 'nginx-proxy did not take the tool access'):
                apply_tool_access(project)

    def test_malformed_files_never_reach_the_target_nor_show_a_hash(self):
        for users, addresses, message in (('tomas:$6$secret$hash\nno-hash-here\n', '', 'line 2 is not name:hash'),
                                          ('', 'deny all\n', "'deny all' is not an IP")):
            self.keep(users, addresses)
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message) as raised:
                check_source(self.source)
            self.assertNotIn('secret', str(raised.exception))
        self.keep('', 'all\n')
        with self.assertRaisesRegex(ValueError, "'all' is not an IP"):
            push_tool_access(Project(self.app, 'stage'), str(self.target))
        self.assertFalse((self.target / 'data/stage/tool-access').exists())

    def test_deploy_images_brings_the_access_before_the_start_and_reloads_after_it(self):
        self.keep()
        with (self.app / 'env/stage.env').open('a') as env:
            env.write('DEPLOY_CONTEXT=shop-stage\n')
        project = Project(self.app, 'stage')
        directory = project_release.release_directory(project)
        directory.mkdir(parents=True)
        (directory / 'images.lock.json').write_text(json.dumps({'project': 'tools-demo-stage', 'images': {}}))
        calls = []
        run = lambda command, **kwargs: calls.append(command[3] if command[2] == 'shop-stage' else command[0]) or \
            subprocess.CompletedProcess(command, 0, '', '')
        with patch('project_release.subprocess.run', run), patch('project_release.prepare_runtime_directories'), \
                patch('project_release.release_model', return_value={'services': {}}), \
                patch('project_release.sync_environment'), \
                patch('project_release.check_ports'), patch('project_release.publish_release_info'), \
                patch('project_release.push_tool_access', lambda project, path: calls.append('push') or True), \
                patch('project_release.reload_proxy', lambda project: calls.append('reload')), \
                contextlib.redirect_stdout(io.StringIO()):
            project_release.deploy_images(project, transfer=False)
        self.assertEqual(calls[calls.index('push'):], ['push', 'ps', 'compose', 'reload'])


if __name__ == '__main__':
    unittest.main()
