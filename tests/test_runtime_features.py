"""Profile/version boundaries, isolated test files and container startup."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'docker'), str(ROOT/'tests')]
from project_scaffold import scaffold
from project import Project
from project_bootstrap import initialize_test
from project_health import start_project


class RuntimeFeaturesTest(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'.test-work'; scratch.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        self.env=os.environ.copy()
        self.env.update(PROFILE='ps',DATABASE_HOST='database',DATABASE_PORT='3307',DATABASE_NAME='isolated',DATABASE_USER='test',DATABASE_PASSWORD="quote' $money \\n %%")

    def php(self, *args):
        image=self.env.get('PHP_TEST_IMAGE')
        if image:
            command=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{ROOT}:{ROOT}']
            for name in ('PROFILE','DATABASE_HOST','DATABASE_PORT','DATABASE_NAME','DATABASE_USER','DATABASE_PASSWORD'):
                command+=['-e',name]
            command += [image,'php']
        else: command=['php']
        return subprocess.run(command+list(args),env=self.env,capture_output=True,text=True)

    def invoke(self):
        return self.php(str(ROOT/'docker/runtime/prestashop-command.php'),'prepare',str(self.root),str(self.root/'project-config'))

    def test_ps16_preserves_defines_and_keys_and_supports_project_constants(self):
        file=self.root/'config/settings.inc.php';file.parent.mkdir()
        original="""<?php
// Keep this comment: define('_DB_NAME_', 'do not replace');
define('_PS_VERSION_', '1.6.1.24');
define('_DB_SERVER_', 'old');
define('_DB_NAME_', 'old');
define('_DB_USER_', 'old');
define('_DB_PASSWD_', 'old');
define('_COOKIE_KEY_', 'preserve');
define('_DB_PREFIX_', 'custom_');
"""
        file.write_text(original)
        override=self.root/'project-config/prestashop/settings.override.php';override.parent.mkdir(parents=True)
        override.write_text("<?php return array('_CUSTOM_FLAG_' => true);")
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        value=self.php('-r', 'require $argv[1]; echo json_encode(array(_DB_SERVER_,_DB_NAME_,_DB_PASSWD_,_COOKIE_KEY_,_CUSTOM_FLAG_));',str(file))
        self.assertEqual(json.loads(value.stdout),['database:3307','isolated',self.env['DATABASE_PASSWORD'],'preserve',True])
        self.assertIn("// Keep this comment: define('_DB_NAME_', 'do not replace');",file.read_text())
        before=file.read_bytes(),file.stat().st_mtime_ns
        self.assertEqual(self.invoke().returncode,0)
        self.assertEqual((file.read_bytes(),file.stat().st_mtime_ns),before)
        self.assertFalse((self.root/'app/config/parameters.php').exists())

    def test_modern_extra_parameters_and_non_ps_noop(self):
        file=self.root/'app/config/parameters.php';file.parent.mkdir(parents=True)
        file.write_text("<?php return array('parameters'=>array('secret'=>'keep','cookie_key'=>'keep-cookie'));")
        override=self.root/'project-config/prestashop/parameters.override.php';override.parent.mkdir(parents=True)
        override.write_text("<?php return array('feature_enabled'=>true, 'custom_url'=>'https://example.invalid/%value');")
        self.env['PROFILE']='akeneo';before=file.read_bytes()
        self.assertEqual(self.invoke().returncode,0)
        self.assertEqual(file.read_bytes(),before)
        self.env['PROFILE']='ps'
        result=self.invoke();self.assertEqual(result.returncode,0,result.stderr)
        config=json.loads(self.php('-r','echo json_encode(require $argv[1]);',str(file)).stdout)['parameters']
        self.assertTrue(config['feature_enabled']);self.assertEqual(config['custom_url'],'https://example.invalid/%%value')
        self.assertEqual(config['secret'],'keep')
        self.assertFalse((self.root/'config/settings.inc.php').exists())

    def test_yaml_installation_is_not_mistaken_for_legacy_settings(self):
        legacy = self.root/'config/settings.inc.php'
        legacy.parent.mkdir()
        legacy.write_text('<?php // Deprecated modern compatibility stub\n')
        yaml = self.root/'app/config/parameters.yml'
        yaml.parent.mkdir(parents=True)
        yaml.write_text('parameters: {}\n')
        original = legacy.read_bytes()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('parameters.yml', result.stderr)
        self.assertEqual(legacy.read_bytes(), original)

    @unittest.skipUnless(os.environ.get('PHP_TEST_IMAGE'), 'set PHP_TEST_IMAGE to verify the actual container entrypoint')
    def test_non_ps_startup_hooks_run_in_order_and_failure_blocks_application(self):
        config = self.root/'project-config'
        hooks = config/'startup'
        hooks.mkdir(parents=True)
        (hooks/'010-first.sh').write_text('echo first-hook\n')
        second = hooks/'020-second.sh'
        second.write_text('echo second-hook\n')
        command = ['docker', 'run', '--rm', '--network', 'none',
                   '--user', f'{os.getuid()}:{os.getgid()}', '-e', 'PROFILE=akeneo',
                   '-v', f'{ROOT}/docker/runtime:/opt/setup/runtime:ro',
                   '-v', f'{config}:/opt/setup/project:ro',
                   # The effective drop-in directories setup's Dockerfile creates (0777); a stock php image has none.
                   '--tmpfs', '/usr/local/etc/php/project.r:mode=1777',
                   '--tmpfs', '/usr/local/etc/php/project.r.env:mode=1777', '--entrypoint', '/bin/sh',
                   os.environ['PHP_TEST_IMAGE'], '/opt/setup/runtime/start.sh',
                   'php', '-r', 'echo "application-started\\n";']
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['first-hook', 'second-hook', 'application-started'])
        second.write_text('echo second-hook\nexit 23\n')
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 23)
        self.assertNotIn('application-started', result.stdout)

    def test_test_environment_has_distinct_code_data_ports_and_config(self):
        scaffold(self.root/'shop')
        app=self.root/'shop/app'
        (app/'env/local.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=local\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=local-secret\nLOCALHOST_PORT=32001\nLOCALHOST_PORT_SSL=32002\n')
        (app/'public').mkdir();(app/'public/index.php').write_text('<?php echo "original";')
        project=Project(app)
        with patch('project_bootstrap.prepare_host'),contextlib.redirect_stdout(io.StringIO()):
            test=initialize_test(project)
            again=initialize_test(project)
        self.assertEqual(test.name,'shop-test')
        self.assertNotEqual(project.web_directory,test.web_directory)
        self.assertNotEqual(project.data_directory,test.data_directory)
        self.assertNotEqual(project.settings['LOCALHOST_PORT'],test.settings['LOCALHOST_PORT'])
        (test.web_directory/'index.php').write_text('test only')
        self.assertEqual((app/'public/index.php').read_text(),'<?php echo "original";')
        self.assertEqual(test.env_files[-1].stat().st_mode&0o777,0o600)
        self.assertNotIn('local-secret',test.env_files[-1].read_text())
        model=test.model()
        self.assertEqual(model['services']['database']['image'],project.model()['services']['database']['image'])
        self.assertNotEqual(model['services']['database']['container_name'],project.model()['services']['database']['container_name'])
        before = project.model()['services']
        with (app/'env/local.env').open('a') as file: file.write('NODE_VERSION=18\n')
        after = Project(app).model()['services']
        # Per-image fingerprints: a PHP build argument renames only the PHP images.
        self.assertNotEqual(after['php-fpm']['image'], before['php-fpm']['image'])
        self.assertEqual(after['database']['image'], before['database']['image'])
        override = app/'compose/test.yaml'
        override.parent.mkdir()
        override.write_text('services:\n  database:\n    volumes:\n      - ${PROJECT_DIRECTORY}/data/mysql:/var/lib/mysql\n')
        with self.assertRaisesRegex(ValueError, 'writable mount'): Project(app, 'test')
        override.unlink()
        # e2e runs only in ENV=test: the shared Playwright config and specs are mounted read-only there.
        common = (app/'env/common.env').read_text()
        (app/'env/common.env').write_text(common + 'SERVICES=playwright\n')
        e2e = Project(app, 'test')
        e2e.validate_test_isolation('playwright')
        self.assertEqual(e2e.model()['services']['playwright']['environment']['PLAYWRIGHT_DATA_DIR'], '/e2e/data')
        (app/'env/common.env').write_text(common)
        with test.env_files[-1].open('a') as file: file.write('APP_SOURCE_DIRECTORY=app/public\n')
        with self.assertRaisesRegex(ValueError,'Test data and code'): Project(app,'test')

    def test_a_test_session_publishes_nothing_and_bounds_playwright(self):
        scaffold(self.root/'shop')
        app=self.root/'shop/app'
        (app/'env/local.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=local\nDATABASE_ROOT_PASSWORD=r\nDATABASE_PASSWORD=p\nLOCALHOST_PORT=32001\nLOCALHOST_PORT_SSL=32002\n')
        (app/'public').mkdir();(app/'public/index.php').write_text('<?php echo "x";')
        (app/'env/common.env').write_text((app/'env/common.env').read_text() + 'SERVICES=playwright\n')
        (app/'env/test.env').write_text('TEST_TLS=self-signed\nTEST_HOST_ROUTE=off\nPUBLISH_ADDRESS=10.0.0.5\nPLAYWRIGHT_MEMORY=1024m\nPLAYWRIGHT_CPUS=1.5\n')
        project=Project(app)
        with patch('project_bootstrap.prepare_host'),contextlib.redirect_stdout(io.StringIO()):
            initialize_test(project)
        session=Project(app,'test').model()
        self.assertFalse(session['services']['nginx-proxy'].get('ports'), 'TEST_HOST_ROUTE=off publishes no port, whatever PUBLISH_ADDRESS says')
        self.assertTrue(next(iter(session['networks'].values())).get('internal'), 'the session reaches nothing but itself')
        playwright=session['services']['playwright']
        self.assertEqual((playwright['mem_limit'], playwright['memswap_limit'], playwright['cpus'], playwright['pids_limit']),
                         ('1073741824', '1073741824', 1.5, 1024))
        self.assertEqual((playwright['cap_drop'], playwright['security_opt']), (['ALL'], ['no-new-privileges:true']))
        self.assertEqual(playwright['user'], f'{os.getuid()}:{os.getgid()}')
        self.assertIn('/e2e/tests/node_modules', [volume['target'] for volume in playwright['volumes'] if volume.get('read_only')])
        (app/'env/test.env').write_text((app/'env/test.env').read_text().replace('TEST_HOST_ROUTE=off', 'TEST_HOST_ROUTE=on'))
        local_test=Project(app,'test').model()
        self.assertTrue(local_test['services']['nginx-proxy'].get('ports'), 'a developer\'s ENV=test keeps its ports')
        self.assertFalse(next(iter(local_test['networks'].values())).get('internal'))

    def session_project(self, services='playwright metrics cron'):
        """A project with metrics and cron as setup-server's test session renders it (TEST_SESSION=on)."""
        scaffold(self.root/'shop')
        app=self.root/'shop/app'
        content=self.root/'content'
        for name in ('tests','config','public'):
            (content/name).mkdir(parents=True)
        (content/'public/index.php').write_text('<?php echo "client";')
        session=(f'TEST_SESSION=on\nE2E_TESTS_DIRECTORY={content}/tests\nE2E_CONFIG_DIRECTORY={content}/config\n'
                 f'TEST_APP_SOURCE={content}/public\nTEST_SESSION_ID=ts-0123456789ab\nTEST_CGROUP_PARENT=setup-server-test.slice\n')
        (app/'env/local.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=local\nDATABASE_ROOT_PASSWORD=r\n'
                                         'DATABASE_PASSWORD=p\nLOCALHOST_PORT=32001\nLOCALHOST_PORT_SSL=32002\n' + session)
        (app/'public').mkdir();(app/'public/index.php').write_text('<?php echo "trusted";')
        (app/'env/common.env').write_text((app/'env/common.env').read_text() + f'SERVICES={services}\n')
        (app/'env/test.env').write_text('TEST_TLS=self-signed\nTEST_HOST_ROUTE=off\nPUBLISH_ADDRESS=10.0.0.5\nPLAYWRIGHT_MEMORY=1024m\n'
                                        'PLAYWRIGHT_CPUS=1.5\nCRON_SCHEDULE=off\n' + session)
        with patch('project_bootstrap.prepare_host'),contextlib.redirect_stdout(io.StringIO()):
            initialize_test(Project(app))
        return app, content

    def test_a_test_session_is_confined_on_every_service_and_network(self):
        app, content = self.session_project()
        test=Project(app,'test')
        model=test.model()
        self.assertEqual((app/'.generated/test/app/index.php').read_text(), '<?php echo "client";', 'the session copies its own shop code')
        self.assertNotIn('metrics', model['services'], 'metrics reads the Docker socket: never in a session')
        self.assertIn('cron', model['services'])
        self.assertTrue(model['networks'])
        for name, network in model['networks'].items():
            self.assertTrue(network.get('internal'), f'network {name}')
            self.assertEqual(network['driver_opts'], {'com.docker.network.bridge.gateway_mode_ipv4': 'isolated',
                                                      'com.docker.network.bridge.gateway_mode_ipv6': 'isolated'}, name)
            self.assertEqual(network['labels']['setup.session'], 'ts-0123456789ab')
        allowed={'CHOWN','DAC_OVERRIDE','FOWNER','FSETID','KILL','NET_BIND_SERVICE','SETGID','SETUID'}
        for name, service in model['services'].items():
            with self.subTest(service=name):
                self.assertFalse(service.get('ports'))
                self.assertNotIn('host-gateway', json.dumps(service.get('extra_hosts') or []))
                self.assertFalse(service.get('privileged'))
                self.assertEqual(service['cap_drop'], ['ALL'])
                self.assertLessEqual(set(service.get('cap_add') or []), allowed)
                if str(service.get('user') or '0').split(':')[0] not in ('0', 'root'):
                    self.assertFalse(service.get('cap_add'), 'a service that starts as its user gets no capability back')
                self.assertTrue(service['cpus'])
                self.assertEqual(service['labels']['setup.session'], 'ts-0123456789ab')
                self.assertEqual(service['cgroup_parent'], 'setup-server-test.slice')
                self.assertIn('no-new-privileges:true', service['security_opt'])
                self.assertTrue(service['pids_limit'])
                self.assertEqual(str(service['memswap_limit']), str(service['mem_limit']))
                for volume in service.get('volumes', []):
                    self.assertNotIn('docker.sock', volume.get('source', ''))
        budget=json.loads((app/'.generated/test/session-budget.json').read_text())
        self.assertNotIn('php-fpm-base', budget['services'], 'an image build stage never runs: not in the budget')
        self.assertIn('playwright', budget['services'], 'a one-off runner is')
        from project_isolation import session_budget
        self.assertEqual((budget['memory_mb'], budget['cpus']), session_budget(json.loads(json.dumps(model))))
        playwright=model['services']['playwright']
        self.assertTrue(playwright['read_only'])
        self.assertEqual(playwright['tmpfs'], ['/tmp:size=256m,mode=1777'])
        mounts={volume['target']: volume for volume in playwright['volumes']}
        for target, source in (('/e2e/tests', content/'tests'), ('/e2e/config', content/'config')):
            self.assertEqual((Path(mounts[target]['source']), mounts[target].get('read_only')), (source, True), target)

    def test_the_session_validator_refuses_what_the_overlay_would_not_allow(self):
        app, content = self.session_project('playwright')
        test=Project(app,'test')
        from project_isolation import validate_session_model
        good=test.model()
        validate_session_model(test, good)
        def bad(change):
            model=json.loads(json.dumps(good))
            change(model)
            return model
        network=next(iter(good['networks']))
        cases={'a published port': lambda m: m['services']['php-fpm'].update(ports=[{'target': 80, 'published': '8080'}]),
               'a routed network': lambda m: m['networks'][network].update(internal=False),
               'privileged': lambda m: m['services']['php-fpm'].update(privileged=True),
               'pid host': lambda m: m['services']['database'].update(pid='host'),
               'SYS_ADMIN': lambda m: m['services']['php-fpm'].update(cap_add=['SYS_ADMIN']),
               'no cap_drop': lambda m: m['services']['php-fpm'].update(cap_drop=[]),
               'privileges': lambda m: m['services']['php-fpm'].update(security_opt=[]),
               'swap': lambda m: m['services']['php-fpm'].update(memswap_limit='-1'),
               'host-gateway': lambda m: m['services']['php-fpm'].update(extra_hosts=['host.docker.internal=host-gateway']),
               'docker socket': lambda m: m['services']['php-fpm']['volumes'].append(
                   {'type': 'bind', 'source': '/var/run/docker.sock', 'target': '/var/run/docker.sock'}),
               'a host directory': lambda m: m['services']['php-fpm']['volumes'].append({'type': 'bind', 'source': '/etc', 'target': '/x', 'read_only': True}),
               'specs writable': lambda m: m['services']['playwright']['volumes'].append({'type': 'bind', 'source': str(content/'tests'), 'target': '/y'}),
               'a writable root': lambda m: m['services']['playwright'].update(read_only=False),
               'another container\'s pid': lambda m: m['services']['php-fpm'].update(pid='container:other'),
               'no cpus': lambda m: m['services']['php-fpm'].pop('cpus'),
               'a routed gateway': lambda m: m['networks'][network]['driver_opts'].pop('com.docker.network.bridge.gateway_mode_ipv4'),
               'no session label': lambda m: m['services']['php-fpm']['labels'].pop('setup.session'),
               'capabilities for a non-root service': lambda m: m['services']['php-fpm'].update(cap_add=['CHOWN']),
               'outside its slice': lambda m: m['services']['php-fpm'].update(cgroup_parent='system.slice')}
        from project_isolation import check_inputs
        for label, change in {'a build from the shop': {'build': {'context': str(app/'.generated/test/app')}},
                              'an env_file from the specs': {'env_file': [{'path': str(content/'tests/x.env')}]},
                              'an additional context from the shop': {'build': {'context': str(app), 'additional_contexts': {'x': str(content/'public')}}}}.items():
            with self.subTest(case=label), self.assertRaisesRegex(ValueError, 'client-writable'):
                check_inputs(test, {'services': {'php-fpm': change}})
        for label, change in cases.items():
            with self.subTest(case=label), self.assertRaisesRegex(ValueError, 'Test session'):
                validate_session_model(test, bad(change))

    def test_real_projects_get_a_session_cron_backup_doctrine_and_prestashop(self):
        app, content = self.session_project('playwright cron backup php-doctrine-migrations')
        for path in ('config/cron', 'config/doctrine-migrations', 'database/schema'):
            (app/path).mkdir(parents=True, exist_ok=True)
        model = Project(app,'test').model()  # the validator would refuse a writable mount outside .generated/test
        self.assertEqual(model['services']['php-doctrine-migrations']['profiles'], ['doctrine'])
        for name in ('cron', 'backup', 'php-doctrine-migrations'):
            writable = [volume['target'] for volume in model['services'][name].get('volumes', [])
                        if volume.get('type') == 'bind' and not volume.get('read_only')
                        and not Path(volume['source']).resolve().is_relative_to((app/'.generated/test').resolve())]
            self.assertEqual(writable, [], name)

    def test_a_prestashop_session_gets_its_generated_secrets(self):
        scaffold(self.root/'ps')
        app=self.root/'ps/app'
        (app/'env/common.env').write_text((app/'env/common.env').read_text() + 'PROFILE=prestashop\nPS_VERSION=8.1.7\n')
        (app/'env/local.env').write_text('DOMAIN=ps.localhost\nDATABASE_NAME=ps\nDATABASE_USER=local\nDATABASE_ROOT_PASSWORD=r\n'
                                         'DATABASE_PASSWORD=p\nLOCALHOST_PORT=32011\nLOCALHOST_PORT_SSL=32012\nPS_COOKIE_KEY=local\n')
        (app/'public').mkdir(); (app/'public/index.php').write_text('<?php')
        with patch('project_bootstrap.prepare_host'),contextlib.redirect_stdout(io.StringIO()):
            initialize_test(Project(app))
        text=(app/'env/test.env').read_text()
        self.assertRegex(text, r'(?m)^PS_COOKIE_KEY=\S+', 'make test-prepare needs the keys ENV=test would otherwise lack')

    def test_a_session_takes_no_compose_file_from_client_content(self):
        app, content = self.session_project('playwright')
        (app/'.generated/test/app/evil.yml').write_text('services:\n  php-fpm:\n    privileged: true\n')
        (app/'env/common.env').write_text((app/'env/common.env').read_text() + 'PROJECT_COMPOSE_FILES=.generated/test/app/evil.yml\n')
        with self.assertRaisesRegex(ValueError, 'client-writable'):
            Project(app,'test')
        (app/'env/common.env').write_text((app/'env/common.env').read_text().replace('PROJECT_COMPOSE_FILES=.generated/test/app/evil.yml\n', ''))
        (app/'env/test.env').write_text((app/'env/test.env').read_text().replace(f'E2E_TESTS_DIRECTORY={content}/tests', f'E2E_TESTS_DIRECTORY={app}/tests'))
        (app/'tests').mkdir(exist_ok=True)
        with self.assertRaisesRegex(ValueError, 'outside'):
            Project(app,'test')

    def test_start_waits_for_containers_without_application_http_requests(self):
        project = SimpleNamespace(run=Mock(), capture=Mock(side_effect=AssertionError('No application probe')))
        with patch('project_policy.preflight_php') as preflight, \
                patch('project_environment.runtime_directories', return_value=([], set())):
            start_project(project, timeout=12)
        preflight.assert_called_once_with(project)
        project.run.assert_called_once_with(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '12'])


if __name__=='__main__': unittest.main()
