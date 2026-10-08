"""Optional Memcached service: absent by default, selected only by an explicit YAML include."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
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

            (app / 'compose/local.yaml').write_text('''include:
  - ${ROOT_DIRECTORY}/docker/memcached/docker-compose.yml
''')
            (app / 'env/prod.env').write_text(
                'DOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_PASSWORD=test\n')
            for environment in ('local', 'prod'):
                services = json.loads(Project(app, environment).capture(['config', '--format', 'json']))['services']
                self.assertEqual('memcached' in services, environment == 'local')
            memcached = json.loads(Project(app, 'local').capture(['config', '--format', 'json']))['services']['memcached']
            self.assertNotIn('ports', memcached)
            self.assertNotIn('profiles', memcached)
            self.assertEqual(memcached['command'], ['memcached', '-m', '128'])
            self.assertEqual(memcached['container_name'], 'cache-demo-local-memcached-1.6.45')
            self.assertIn('network_app', memcached['networks'])

            with (app / 'env/common.env').open('a') as env:
                env.write('MEMCACHED_MEMORY=64\n')
            memcached = json.loads(Project(app, 'local').capture(['config', '--format', 'json']))['services']['memcached']
            self.assertEqual(memcached['command'], ['memcached', '-m', '64'])


if __name__ == '__main__':
    unittest.main()
