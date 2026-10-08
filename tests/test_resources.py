"""Per-service container limits, derived internal sizes and the stage/prod requirement."""
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
from project import Project
from project_policy import MissingLimits, resolve_policy, suggested_limits

LIMITS = {'PHP_MEMORY': '4g', 'PHP_CPUS': '3', 'DATABASE_MEMORY': '8g', 'DATABASE_CPUS': '4',
          'WEBSERVER_MEMORY': '512m', 'WEBSERVER_CPUS': '1', 'PROXY_MEMORY': '256m', 'PROXY_CPUS': '0.5'}


class ResourcePolicyTest(unittest.TestCase):
    def test_local_defaults_per_service(self):
        values = resolve_policy({}, 'local')
        self.assertEqual((values['PHP_MEMORY'], values['DATABASE_MEMORY'], values['WEBSERVER_MEMORY'],
                          values['PROXY_MEMORY'], values['CRON_MEMORY']), ('1g', '2g', '256m', '128m', '512m'))
        self.assertEqual((values['PHP_CPUS'], values['PROXY_CPUS']), ('2', '0.5'))

    def test_internal_sizes_follow_the_limits_and_explicit_values_win(self):
        values = resolve_policy(dict(LIMITS), 'prod')
        self.assertEqual(values['DATABASE_BUFFER_POOL'], '4915M')
        self.assertEqual(values['PHP_FPM_MAX_CHILDREN'], '31')
        self.assertEqual((values['PHP_FPM_START_SERVERS'], values['PHP_FPM_MAX_SPARE_SERVERS']), ('7', '15'))
        self.assertEqual(values['MEMCACHED_CACHE_MB'], '230')
        explicit = resolve_policy({**LIMITS, 'DATABASE_BUFFER_POOL': '6G', 'PHP_FPM_MAX_CHILDREN': '12',
                                   'MEMCACHED_MEMORY': '1g', 'MEMCACHED_CACHE_MB': '700'}, 'prod')
        self.assertEqual((explicit['DATABASE_BUFFER_POOL'], explicit['PHP_FPM_MAX_CHILDREN'],
                          explicit['MEMCACHED_CACHE_MB']), ('6G', '12', '700'))

    def test_previous_shared_names_remain_accepted(self):
        values = resolve_policy({'PHP_CONTAINER_MEMORY': '2g', 'DB_CONTAINER_MEMORY': '3g',
                                 'WEB_CONTAINER_MEMORY': '384m', 'CONTAINER_CPUS': '1.5',
                                 'MEMCACHED_MEMORY': '64'}, 'local')
        self.assertEqual((values['PHP_MEMORY'], values['DATABASE_MEMORY'], values['WEBSERVER_MEMORY'],
                          values['PROXY_MEMORY']), ('2g', '3g', '384m', '384m'))
        self.assertEqual({values[f'{n}_CPUS'] for n in ('PHP', 'DATABASE', 'CRON', 'PMA')}, {'1.5'})
        self.assertEqual(values['MEMCACHED_CACHE_MB'], '64')

    def test_stage_and_prod_require_the_core_limits(self):
        for environment in ('stage', 'prod'):
            with self.subTest(environment=environment), self.assertRaises(MissingLimits) as raised:
                resolve_policy({'PHP_MEMORY': '1g'}, environment)
            self.assertNotIn('PHP_MEMORY', raised.exception.missing)
            self.assertIn('DATABASE_MEMORY', raised.exception.missing)
        self.assertEqual(resolve_policy({}, 'test')['PHP_MEMORY'], '1g')

    def test_invalid_sizes_and_cpus_fail(self):
        for settings in ({'PHP_MEMORY': '1gb'}, {'DATABASE_CPUS': '0'}, {'PROXY_CPUS': 'two'},
                         {'DATABASE_BUFFER_POOL': '50%'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                resolve_policy(settings, 'local')

    def test_suggestions_scale_with_the_host(self):
        suggestion = suggested_limits(32 * 1024 ** 3, 8)
        self.assertEqual((suggestion['PHP_MEMORY'], suggestion['DATABASE_MEMORY'], suggestion['PHP_CPUS']),
                         ('9792m', '14720m', '4'))
        small = suggested_limits(2 * 1024 ** 3, 1)
        self.assertEqual((small['DATABASE_MEMORY'], small['PROXY_MEMORY'], small['PROXY_CPUS']), ('1024m', '128m', '0.5'))


class ResourceModelTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('limits-demo', temporary.name)

    def test_limits_reach_each_service_and_derived_values_reach_their_service(self):
        with (self.app / 'env/local.env').open('a') as env:
            env.write('DATABASE_MEMORY=3g\nPROXY_CPUS=0.25\n')
        services = Project(self.app).model()['services']
        self.assertEqual(int(services['database']['mem_limit']), 3 * 1024 ** 3)
        self.assertEqual(float(services['nginx-proxy']['cpus']), 0.25)
        self.assertEqual(int(services['webserver']['mem_limit']), 256 * 1024 ** 2)
        self.assertEqual(services['database']['environment']['DATABASE_BUFFER_POOL'], '1843M')
        self.assertEqual(services['php-fpm']['environment']['PHP_FPM_MAX_CHILDREN'], '7')

    def test_missing_remote_limits_name_the_keys_and_suggest_values(self):
        (self.app / 'env/prod.env').write_text('DOMAIN=limits.example\nDATABASE_USER=x\nDATABASE_NAME=x\nDATABASE_PASSWORD=x\n')
        import subprocess
        real = subprocess.run
        info = subprocess.CompletedProcess([], 0, stdout=f'{16 * 1024 ** 3} 4\n')
        fake = lambda command, *a, **k: info if command[:2] == ['docker', 'info'] else real(command, *a, **k)
        with patch('project.subprocess.run', side_effect=fake), self.assertRaises(ValueError) as raised:
            Project(self.app, 'prod')
        message = str(raised.exception)
        self.assertIn('prod requires explicit container limits', message)
        self.assertIn('16.0 GiB RAM, 4 CPUs', message)
        self.assertIn('DATABASE_MEMORY=7360m', message)


if __name__ == '__main__':
    unittest.main()
