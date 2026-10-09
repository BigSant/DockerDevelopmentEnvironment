"""The CORE Redis image: a cache by default, persistence only through a project drop-in."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

IMAGE = os.environ.get('SETUP_REDIS_TEST_IMAGE')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(IMAGE, 'Set SETUP_REDIS_TEST_IMAGE to a built setup Redis image')
class RedisIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=45, check=True).stdout.strip()

    def start(self, config=None):
        name = 'setup-redis-test-' + uuid.uuid4().hex[:12]
        mounts = ['-v', f'{config}:/usr/local/etc/redis/project.d:ro'] if config else []
        self.docker('run', '-d', '--name', name, '--network', 'none', '--tmpfs', '/data:mode=1777',
                    '-e', 'REDIS_MAXMEMORY_MB=200', '-e', 'TZ=UTC', '--user', f'{os.getuid()}:{os.getgid()}',
                    *mounts, IMAGE)
        self.addCleanup(subprocess.run, ['docker', 'rm', '-f', name], capture_output=True)
        for _ in range(40):
            try:
                if self.docker('exec', name, 'redis-cli', 'ping') == 'PONG':
                    return name
            except subprocess.CalledProcessError:
                pass
            time.sleep(.25)
        self.fail(self.docker('logs', name))

    def config(self, name, key):
        # redis-cli prints the key, then its value (an empty value is an empty line).
        lines = subprocess.run(['docker', 'exec', name, 'redis-cli', 'config', 'get', key],
                               capture_output=True, text=True, check=True).stdout.split('\n')
        return lines[1] if len(lines) > 1 else ''

    def test_default_is_a_capped_cache_without_persistence(self):
        name = self.start()
        self.assertEqual(self.config(name, 'save'), '')
        self.assertEqual(self.config(name, 'appendonly'), 'no')
        self.assertEqual(self.config(name, 'maxmemory'), str(200 * 1024 * 1024))
        self.assertEqual(self.config(name, 'maxmemory-policy'), 'allkeys-lru')

    def test_project_drop_in_enables_persistence(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            config = Path(temporary)
            (config / 'persistence.conf').write_text('appendonly yes\nsave 900 1\n')
            os.chmod(config, 0o755)
            os.chmod(config / 'persistence.conf', 0o644)
            name = self.start(config)
            self.assertEqual(self.config(name, 'appendonly'), 'yes')
            self.assertEqual(self.config(name, 'save'), '900 1')
