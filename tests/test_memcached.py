"""Optional Memcached service: absent by default, selected only by an explicit YAML include."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REMOTE_LIMITS = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\n'
                 'WEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\n')
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project


class MemcachedServiceTest(unittest.TestCase):
    def test_new_project_has_no_memcached_until_an_environment_includes_it(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold('cache-demo', temporary)
            services = json.loads(Project(app, 'local').capture(['config', '--format', 'json']))['services']
            self.assertNotIn('memcached', services)

            (app / 'compose').mkdir(exist_ok=True)
            (app / 'compose/local.yaml').write_text('''include:
  - ${ROOT_DIRECTORY}/docker/memcached/docker-compose.yml
''')
            (app / 'env/prod.env').write_text(
                REMOTE_LIMITS + 'DOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test\n')
            for environment in ('local', 'prod'):
                services = json.loads(Project(app, environment).capture(['config', '--format', 'json']))['services']
                self.assertEqual('memcached' in services, environment == 'local')
            memcached = json.loads(Project(app, 'local').capture(['config', '--format', 'json']))['services']['memcached']
            self.assertNotIn('ports', memcached)
            self.assertNotIn('profiles', memcached)
            # ~90% of the 256m default container limit, unless MEMCACHED_CACHE_MB sets the cache size.
            self.assertEqual(memcached['command'], ['memcached', '-m', '230'])
            self.assertEqual(memcached['container_name'], 'cache-demo-local-memcached-1.6.45')
            self.assertIn('network_app', memcached['networks'])

            with (app / 'env/common.env').open('a') as env:
                env.write('MEMCACHED_CACHE_MB=64\n')
            memcached = json.loads(Project(app, 'local').capture(['config', '--format', 'json']))['services']['memcached']
            self.assertEqual(memcached['command'], ['memcached', '-m', '64'])


if __name__ == '__main__':
    unittest.main()


class RedisServiceTest(unittest.TestCase):
    def test_redis_is_a_catalogue_service_with_a_derived_memory_cap(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold('redis-demo', temporary)
            with (app / 'env/common.env').open('a') as env:
                env.write('SERVICES=redis\nREDIS_MEMORY=512m\n')
            services = json.loads(Project(app).capture(['config', '--format', 'json']))['services']
            redis = services['redis']
            self.assertNotIn('ports', redis)
            self.assertNotIn('profiles', redis)
            self.assertEqual(redis['environment']['REDIS_MAXMEMORY_MB'], '460')
            self.assertEqual(int(redis['mem_limit']), 512 * 1024 ** 2)
            targets = [v['target'] for v in redis['volumes']]
            self.assertEqual(targets, ['/data'])  # missing config/redis drop-in directories are not mounted
