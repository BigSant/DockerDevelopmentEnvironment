"""PrestaShop configuration file from a template plus env, PS_VERSION and the compatibility table."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from compatibility import check, release_line
from create_project import scaffold
from project import Project
from project_bootstrap import ensure_generated_secrets, set_env_values
from project_policy import validate_configuration
from project_prestashop import existing_keys, generated_keys, required_keys

COMMAND = ROOT / 'docker/runtime/prestashop-command.php'


class CompatibilityTest(unittest.TestCase):
    def test_release_lines_and_php_errors_database_warnings(self):
        self.assertEqual((release_line('1.6.1.24'), release_line('1.7.8.11'), release_line('9.0.2')), ('1.6', '1.7.8', '9.0'))
        self.assertEqual(check('9.0.2', '8.4', 'mysql', '8.4.0'), ([], []))
        errors, warnings = check('1.7.6.5', '8.1', 'mysql', '8.0.45')
        self.assertIn('PHP 8.1 is not supported by PrestaShop 1.7.6', errors[0])
        self.assertIn('mysql 8.0', warnings[0])
        with self.assertRaisesRegex(ValueError, 'compatibility table'):
            release_line('2.0.0')


class KeyFormatTest(unittest.TestCase):
    def test_generated_keys_follow_the_installer_formats(self):
        modern = generated_keys('9.0.2')
        self.assertEqual(sorted(modern), sorted(required_keys('9.0.2')))
        self.assertEqual((len(modern['PS_COOKIE_KEY']), len(modern['PS_COOKIE_IV'])), (64, 32))
        self.assertTrue(modern['PS_NEW_COOKIE_KEY'].startswith('def00000'))
        self.assertEqual(len(modern['PS_NEW_COOKIE_KEY']), 136)
        self.assertIn(b'BEGIN', base64.b64decode(modern['PS_API_PRIVATE_KEY']))
        self.assertIn(b'PUBLIC KEY', base64.b64decode(modern['PS_API_PUBLIC_KEY']))
        self.assertNotIn('PS_API_PRIVATE_KEY', generated_keys('8.2.1'))
        legacy = generated_keys('1.6.1.24')
        self.assertEqual(sorted(legacy), ['PS_COOKIE_IV', 'PS_COOKIE_KEY', 'PS_RIJNDAEL_IV', 'PS_RIJNDAEL_KEY'])
        self.assertEqual((len(legacy['PS_COOKIE_KEY']), len(legacy['PS_COOKIE_IV'])), (56, 8))

    def test_env_values_are_stored_safely(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            path = Path(temporary) / 'local.env'
            set_env_values(path, {'A': 'abc+/=', 'B': 'with $dollar # hash'})
            self.assertEqual(path.read_text(), "A=abc+/=\nB='with $dollar # hash'\n")
            with self.assertRaises(ValueError):
                set_env_values(path, {'C': "quote ' inside"})


class ProjectKeysTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('keys-demo', temporary.name)
        with (self.app / 'env/common.env').open('a') as env:
            env.write('PROFILE=prestashop\nPHP_VERSION=8.4\n')

    def test_ps_version_is_required_and_compatibility_is_checked(self):
        with self.assertRaisesRegex(ValueError, 'Set PS_VERSION'):
            validate_configuration(Project(self.app))
        with (self.app / 'env/common.env').open('a') as env:
            env.write('PS_VERSION=1.7.6.5\n')
        with self.assertRaisesRegex(ValueError, 'PHP 8.4 is not supported by PrestaShop 1.7.6'):
            validate_configuration(Project(self.app))
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SETUP_COMPATIBILITY_CHECK=off\n')
        with self.assertRaisesRegex(ValueError, 'set PS_COOKIE_KEY'):
            validate_configuration(Project(self.app))

    def test_bootstrap_keeps_existing_shop_keys_and_generates_missing_ones(self):
        with (self.app / 'env/common.env').open('a') as env:
            env.write('PS_VERSION=9.0.2\n')
        parameters = self.app / 'public/app/config/parameters.php'
        parameters.parent.mkdir(parents=True)
        parameters.write_text("<?php return array('parameters' => array('cookie_key' => 'live-cookie-key',"
                              " 'secret' => 'per%%cent'));\n")
        ensure_generated_secrets(self.app, 'local')
        env = (self.app / 'env/local.env').read_text()
        self.assertIn('PS_COOKIE_KEY=live-cookie-key\n', env)
        self.assertIn("PS_SECRET='per%cent'\n", env)
        for key in ('PS_COOKIE_IV', 'PS_NEW_COOKIE_KEY', 'PS_API_PRIVATE_KEY', 'PS_API_PUBLIC_KEY', 'DATABASE_ROOT_PASSWORD'):
            self.assertRegex(env, rf'(?m)^{key}=\S')
        before = env
        ensure_generated_secrets(self.app, 'local')
        self.assertEqual((self.app / 'env/local.env').read_text(), before)
        project = Project(self.app)
        php = json.loads(project.capture(['config', '--format', 'json']))['services']['php-fpm']['environment']
        self.assertEqual(php['PS_COOKIE_KEY'], 'live-cookie-key')
        self.assertEqual(php['PS_VERSION'], '9.0.2')
        validate_configuration(project)

    def test_debug_mode_waits_for_the_shop_checkout(self):
        with (self.app / 'env/common.env').open('a') as env:
            env.write('PS_VERSION=9.0.2\nPS_DEBUG_MODE=on\n')
        ensure_generated_secrets(self.app, 'local')
        shutil.rmtree(self.app / 'public')
        (self.app / 'public').mkdir()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            validate_configuration(Project(self.app))
        self.assertIn('No shop code', output.getvalue())
        (self.app / 'public/index.php').write_text('<?php\n')
        with self.assertRaisesRegex(ValueError, 'Restore config/defines.inc.php'):
            validate_configuration(Project(self.app))

    def test_stage_never_generates_keys(self):
        with (self.app / 'env/common.env').open('a') as env:
            env.write('PS_VERSION=9.0.2\n')
        (self.app / 'env/stage.env').write_text('DOMAIN=x\n')
        ensure_generated_secrets(self.app, 'stage')
        self.assertEqual((self.app / 'env/stage.env').read_text(), 'DOMAIN=x\n')

    def test_existing_keys_read_both_layouts(self):
        web = self.app / 'public'
        (web / 'config').mkdir(parents=True)
        (web / 'config/settings.inc.php').write_text("<?php\ndefine('_COOKIE_KEY_', 'k\\'ey');\ndefine('_RIJNDAEL_IV_', 'iv==');\n")
        self.assertEqual(existing_keys(web, '1.6.1.24'), {'PS_COOKIE_KEY': "k'ey", 'PS_RIJNDAEL_IV': 'iv=='})


@unittest.skipUnless(shutil.which('php'), 'PHP CLI is required')
class RuntimeKeysTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'shop'
        self.config = Path(temporary.name) / 'config'
        self.config.mkdir()
        (self.root / 'src/Core').mkdir(parents=True)
        (self.root / 'src/Core/Version.php').write_text("<?php class Version { const VERSION = '9.0.2'; }\n")
        self.env = {'PATH': os.environ['PATH'], 'PROFILE': 'prestashop', 'PS_VERSION': '9.0.2',
                    'DATABASE_HOST': 'database', 'DATABASE_PORT': '3306', 'DATABASE_NAME': 'shop',
                    'DATABASE_USER': 'shop', 'DATABASE_PASSWORD': 'p%ss', **generated_keys('9.0.2')}

    def prepare(self, **env):
        return subprocess.run(['php', str(COMMAND), 'prepare', str(self.root), str(self.config)],
                              env={**self.env, **env}, capture_output=True, text=True)

    def parameters(self):
        out = subprocess.run(['php', '-r', 'echo json_encode(require $argv[1]);', str(self.root / 'app/config/parameters.php')],
                             capture_output=True, text=True, check=True)
        return json.loads(out.stdout)['parameters']

    def test_missing_file_is_created_from_template_and_env(self):
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        parameters = self.parameters()
        self.assertEqual(parameters['cookie_key'], self.env['PS_COOKIE_KEY'])
        self.assertEqual(parameters['database_password'], 'p%%ss')
        self.assertTrue(parameters['api_private_key'].startswith('-----BEGIN'))
        self.assertEqual(parameters['database_prefix'], 'ps_')
        self.assertEqual((self.root / 'app/config/parameters.php').stat().st_mode & 0o777, 0o600)

    def test_env_keys_replace_file_keys_and_missing_env_keys_keep_them(self):
        self.assertEqual(self.prepare().returncode, 0)
        self.assertEqual(self.prepare(PS_COOKIE_KEY='rotated').returncode, 0)
        self.assertEqual(self.parameters()['cookie_key'], 'rotated')
        kept = {k: v for k, v in self.env.items() if k != 'PS_SECRET'}
        result = subprocess.run(['php', str(COMMAND), 'prepare', str(self.root), str(self.config)],
                                env=kept, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.parameters()['secret'], self.env['PS_SECRET'])

    def test_missing_keys_without_a_file_and_version_mismatch_fail(self):
        result = self.prepare(PS_SECRET='')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PS_SECRET', result.stderr)
        result = self.prepare(PS_VERSION='9.0.1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match the shop code', result.stderr)

    def test_legacy_settings_file_is_created_for_1_6(self):
        (self.root / 'src/Core/Version.php').unlink()
        env = {k: v for k, v in self.env.items() if not k.startswith('PS_')}
        env.update(PS_VERSION='1.6.1.24', **generated_keys('1.6.1.24'))
        result = subprocess.run(['php', str(COMMAND), 'prepare', str(self.root), str(self.config)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = subprocess.run(['php', '-r', 'require $argv[1]; echo _COOKIE_KEY_, "|", _PS_VERSION_, "|", _DB_PREFIX_;',
                                 str(self.root / 'config/settings.inc.php')], capture_output=True, text=True, check=True).stdout
        self.assertEqual(values, f"{env['PS_COOKIE_KEY']}|1.6.1.24|ps_")


if __name__ == '__main__':
    unittest.main()
