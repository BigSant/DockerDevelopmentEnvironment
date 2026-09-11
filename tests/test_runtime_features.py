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
sys.path[:0] = [str(ROOT), str(ROOT/'docker')]
from prepare_project import prepare
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
                   '-v', f'{config}:/opt/setup/project:ro', '--entrypoint', '/bin/sh',
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
        with contextlib.redirect_stdout(io.StringIO()): prepare(self.root/'shop',layout='app')
        app=self.root/'shop/app'
        (app/'env/local.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=local\nDATABASE_PASSWORD=local-secret\nLOCALHOST_PORT=32001\nLOCALHOST_PORT_SSL=32002\n')
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
        with (app/'env/local.env').open('a') as file: file.write('NODE_VERSION=18\n')
        self.assertNotEqual(Project(app).model()['services']['database']['image'], model['services']['database']['image'])
        override = app/'compose/test.yaml'
        override.write_text('services:\n  database:\n    volumes:\n      - ${PROJECT_DIRECTORY}/data/mysql:/var/lib/mysql\n')
        with self.assertRaisesRegex(ValueError, 'writable mount'): Project(app, 'test')
        override.unlink()
        with test.env_files[-1].open('a') as file: file.write('APP_SOURCE_DIRECTORY=app/public\n')
        with self.assertRaisesRegex(ValueError,'Test data and code'): Project(app,'test')

    def test_start_waits_for_containers_without_application_http_requests(self):
        project = SimpleNamespace(run=Mock(), capture=Mock(side_effect=AssertionError('No application probe')))
        with patch('project_policy.preflight_php') as preflight:
            start_project(project, timeout=12)
        preflight.assert_called_once_with(project)
        project.run.assert_called_once_with(['up', '-d', '--no-build', '--pull', 'never', '--wait', '--wait-timeout', '12'])


if __name__=='__main__': unittest.main()
