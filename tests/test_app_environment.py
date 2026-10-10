"""APP_ENVIRONMENT: application settings reach php-fpm and cron by name; cron gets php-fpm's DB connection."""

import contextlib
import io
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project
from project_app_environment import application_names, reserved

REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n'
          'DATABASE_ROOT_PASSWORD=root-test-only\nALLOWED_IPS=203.0.113.10\n')


def environment_keys(compose_file, service):
    """Keys of one service's environment block in a setup compose file (no YAML parser needed)."""
    text = compose_file.read_text()
    block = re.search(rf'(?ms)^  {re.escape(service)}:\n.*?^    environment:\n(.*?)(?=^    \S|^  \S|\Z)', text)
    return re.findall(r'(?m)^      ([A-Z][A-Z0-9_]*):', block[1])


class ApplicationEnvironmentTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('appenv-demo', temporary.name)

    def append(self, name, text):
        with (self.app / name).open('a') as handle:
            handle.write(text)

    def overlays(self, project):
        return [part for part in project.command if 'app-environment-' in part]

    def test_cron_gets_the_database_connection_and_identity_of_php_fpm(self):
        self.append('env/common.env', 'SERVICES=cron\n')
        services = Project(self.app).model()['services']
        php, cron = services['php-fpm']['environment'], services['cron']['environment']
        for key in ('DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD',
                    'DOMAIN', 'PROJECT_NAME', 'SETUP_ENV'):
            self.assertEqual(cron[key], php[key], key)
        self.assertEqual((cron['DATABASE_HOST'], cron['DATABASE_PORT']), ('database', '3306'))

    def test_without_the_setting_nothing_is_generated(self):
        self.append('env/common.env', 'SERVICES=cron\n')
        project = Project(self.app)
        self.assertEqual(self.overlays(project), [])
        self.assertEqual(sorted(p.name for p in (self.app / '.generated').glob('app-environment-*')), [])

    def test_listed_names_reach_php_fpm_and_cron_with_references_only_on_disk(self):
        self.append('env/common.env', 'SERVICES=cron\nAPP_ENVIRONMENT="SUPPLIER_URL\n  SUPPLIER_TOKEN SUPPLIER_URL"\n'
                                      'SUPPLIER_URL=https://supplier.example.com\n')
        self.append('env/local.env', "SUPPLIER_TOKEN='token-local'\n")
        project = Project(self.app)
        services = project.model()['services']
        for service in ('php-fpm', 'cron'):
            environment = services[service]['environment']
            self.assertEqual(environment['SUPPLIER_URL'], 'https://supplier.example.com', service)
            self.assertEqual(environment['SUPPLIER_TOKEN'], 'token-local', service)
        [overlay] = [Path(part) for part in self.overlays(project)]
        text = overlay.read_text()
        self.assertNotIn('supplier.example.com', text)
        self.assertNotIn('token-local', text)
        self.assertEqual(re.findall(r'(?m)^      (\w+): (.*)$', text),
                         [('SUPPLIER_URL', '${SUPPLIER_URL-}'), ('SUPPLIER_TOKEN', '${SUPPLIER_TOKEN-}')] * 2)
        self.assertEqual(overlay.stat().st_mode & 0o777, 0o600)

    def test_cron_is_left_out_when_it_does_not_run_and_unset_names_are_empty(self):
        self.append('env/common.env', 'APP_ENVIRONMENT="SUPPLIER_URL"\n')
        project = Project(self.app)
        services = project.model()['services']
        self.assertNotIn('cron', services)
        self.assertEqual(services['php-fpm']['environment']['SUPPLIER_URL'], '')
        self.assertNotIn('cron:', Path(self.overlays(project)[0]).read_text())

    def test_server_values_come_from_the_process_environment_and_env_files_win(self):
        self.append('env/common.env', 'SERVICES=cron\nAPP_ENVIRONMENT="SUPPLIER_TOKEN SUPPLIER_MODE"\n')
        (self.app / 'env/prod.env').write_text(REMOTE + 'CRON_ALLOWED_IPS=203.0.113.10\nSUPPLIER_MODE=live\n')
        secret = 'deployment-variable-value'
        with patch.dict('os.environ', {'SUPPLIER_TOKEN': secret, 'SUPPLIER_MODE': 'sandbox',
                                       'DATABASE_PASSWORD': 'pipeline-password'}):
            project = Project(self.app, 'prod')
        services = project.model()['services']
        for service in ('php-fpm', 'cron'):
            environment = services[service]['environment']
            self.assertEqual(environment['SUPPLIER_TOKEN'], secret, service)
            self.assertEqual(environment['SUPPLIER_MODE'], 'live', 'a name set in an env file ignores the process value')
            self.assertEqual(environment['DATABASE_PASSWORD'], 'pipeline-password', service)
        for path in (self.app / '.generated').rglob('*'):
            if path.is_file():
                self.assertNotIn(secret, path.read_text(errors='replace'), path)

    def test_a_name_the_project_still_lists_keeps_its_own_definition(self):
        self.append('env/common.env', 'SERVICES=cron\nAPP_ENVIRONMENT="SUPPLIER_URL SUPPLIER_MODE"\nSUPPLIER_MODE=live\n')
        (self.app / 'compose').mkdir()
        (self.app / 'compose/base.yaml').write_text(
            'services:\n  php-fpm:\n    environment:\n      SUPPLIER_URL: ${SUPPLIER_URL:-https://fallback.example.com}\n'
            '  cron:\n    profiles: [cron]\n    environment:\n      SUPPLIER_MODE: ${SUPPLIER_MODE}\n      DATABASE_NAME: ${DATABASE_NAME-}\n')
        services = Project(self.app).model()['services']
        self.assertEqual(services['php-fpm']['environment']['SUPPLIER_URL'], 'https://fallback.example.com')
        self.assertEqual(services['cron']['environment']['SUPPLIER_URL'], '')
        self.assertEqual(services['cron']['environment']['SUPPLIER_MODE'], 'live')
        self.assertEqual(services['cron']['environment']['DATABASE_NAME'], 'appenv_demo')

    def test_image_fingerprints_ignore_the_runtime_environment(self):
        self.append('env/common.env', 'SERVICES=cron\n')
        before = Project(self.app).image_suffixes
        self.append('env/common.env', 'APP_ENVIRONMENT="SUPPLIER_URL SUPPLIER_TOKEN"\nSUPPLIER_URL=https://x.example.com\n')
        self.assertEqual(Project(self.app).image_suffixes, before)

    def test_invalid_and_setup_owned_names_are_refused_by_name(self):
        cases = {
            'supplier_url': 'variable names only.*supplier_url',
            '1ST_KEY': 'variable names only.*1ST_KEY',
            'SUPPLIER-URL': 'variable names only.*SUPPLIER-URL',
            '${SUPPLIER}': r'variable names only.*\$\{SUPPLIER\}',
            'OK_NAME DATABASE_HOST PROFILE': 'cannot pass DATABASE_HOST, PROFILE: setup sets or computes',
            'MYSQL_PWD': 'cannot pass MYSQL_PWD',
            'ENV': 'cannot pass ENV',
            'SERVICES': 'cannot pass SERVICES',
            'APP_ENVIRONMENT': 'cannot pass APP_ENVIRONMENT',
            'PS_COOKIE_KEY': 'cannot pass PS_COOKIE_KEY',
            'CRON_HISTORY_KEEP': 'cannot pass CRON_HISTORY_KEEP',
        }
        for value, message in cases.items():
            with self.subTest(value=value):
                (self.app / 'env/common.env').write_text(f"PROJECT_NAME=appenv-demo\nAPP_ENVIRONMENT='{value}'\n")
                with self.assertRaisesRegex(ValueError, message):
                    Project(self.app)

    def test_every_name_setup_sets_in_the_application_services_is_reserved(self):
        settings = Project(self.app).settings
        owned = (environment_keys(ROOT / 'docker/docker/php-fpm/docker-compose.yml', 'php-fpm')
                 + environment_keys(ROOT / 'docker/docker/cron/docker-compose.yml', 'cron'))
        self.assertIn('PHP_INI_SCAN_DIR', owned)
        self.assertIn('DATABASE_PASSWORD', owned)
        self.assertEqual([name for name in owned if not reserved(name, settings)], [])
        # The variables the runner computes for Compose interpolation.
        for name in ('ROOT_DIRECTORY', 'PROJECT_WEB_DIRECTORY', 'PROJECT_DATA_DIRECTORY', 'DOCKERFILE_DIRECTORY',
                     'BUILD_ENV', 'IMAGE_ENV', 'ENV', 'HOST_UID', 'HOST_GID', 'SETUP_IMAGE_SUFFIX_PHP_FPM',
                     'SETUP_CRON_CONFIG_ACCESS', 'PS_RELEASE_IMAGE', 'DOCKER_CONTEXT', 'COMPOSE_PROFILES'):
            self.assertTrue(reserved(name, settings), name)
        self.assertFalse(reserved('ASANA_TOKEN', settings))
        self.assertEqual(application_names({'APP_ENVIRONMENT': ' A  B A\nC '}), ['A', 'B', 'C'])
        self.assertEqual(application_names({'APP_ENVIRONMENT': ''}), [])


if __name__ == '__main__':
    unittest.main()
