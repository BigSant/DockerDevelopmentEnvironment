"""Check the shipped Redis configuration from another application container."""
import os
from pathlib import Path
import subprocess
import time
import unittest
import uuid


IMAGE = os.environ.get('SETUP_REDIS_TEST_IMAGE')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(IMAGE, 'Set SETUP_REDIS_TEST_IMAGE to run real Redis integration')
class RedisIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=45, check=True).stdout.strip()

    def test_application_network_can_reach_template_redis(self):
        name = 'setup-redis-test-' + uuid.uuid4().hex[:12]
        config = ROOT / 'templates/grouped/config/redis'
        self.docker('network', 'create', name)
        try:
            self.docker('run', '-d', '--name', name, '--network', name, '--network-alias', 'redis',
                        '--memory', '256m', '--tmpfs', '/data',
                        '-v', f'{config}/redis.conf:/usr/local/etc/redis/redis.conf:ro',
                        '-v', f'{config}/local:/usr/local/etc/redis/environment:ro',
                        IMAGE, 'redis-server', '/usr/local/etc/redis/redis.conf')
            for _ in range(20):
                try:
                    if self.docker('exec', name, 'redis-cli', 'ping') == 'PONG':
                        break
                except subprocess.CalledProcessError:
                    pass
                time.sleep(.25)
            else:
                self.fail('Redis failed to become ready')
            # Loopback PING alone misses protected-mode rejection of application clients.
            result = self.docker('run', '--rm', '--network', name, IMAGE,
                                 'redis-cli', '-h', 'redis', 'ping')
            self.assertEqual(result, 'PONG')
        finally:
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
            subprocess.run(['docker', 'network', 'rm', name], capture_output=True, timeout=30)
