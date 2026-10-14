"""Cache extensions are present but disabled in the PHP image and work once enabled."""
import os
import subprocess
import time
import unittest
import uuid

PHP = os.environ.get('SETUP_PHP_TEST_IMAGE')
MEMCACHED = os.environ.get('SETUP_MEMCACHED_TEST_IMAGE')
REDIS = os.environ.get('SETUP_REDIS_TEST_IMAGE')


@unittest.skipUnless(PHP and MEMCACHED and REDIS,
                     'Set SETUP_PHP_TEST_IMAGE, SETUP_MEMCACHED_TEST_IMAGE and SETUP_REDIS_TEST_IMAGE')
class CacheExtensionsIntegrationTest(unittest.TestCase):
    def docker(self, *args):
        return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=60, check=True).stdout.strip()

    def test_extensions_are_off_by_default_and_reach_their_servers_when_enabled(self):
        network = 'setup-cache-' + uuid.uuid4().hex[:10]
        self.docker('network', 'create', '--internal', network)
        self.addCleanup(subprocess.run, ['docker', 'network', 'rm', network], capture_output=True)
        for alias, image, extra in (('memcached', MEMCACHED, []), ('redis', REDIS, ['-e', 'REDIS_MAXMEMORY_MB=64'])):
            name = f'{network}-{alias}'
            self.docker('run', '-d', '--name', name, '--network', network, '--network-alias', alias, *extra, image)
            self.addCleanup(subprocess.run, ['docker', 'rm', '-f', name], capture_output=True)
        php = ['run', '--rm', '--network', network, '--entrypoint', 'php', PHP]
        loaded = self.docker(*php, '-r', 'echo json_encode([extension_loaded("memcached"), extension_loaded("redis")]);')
        self.assertEqual(loaded, '[false,false]')
        script = ('$m = new Memcached(); $m->addServer("memcached", 11211);'
                  '$r = new Redis(); for ($i = 0; $i < 50; $i++) { try { $r->connect("redis", 6379); break; } catch (Exception $e) { usleep(100000); } }'
                  'for ($i = 0; $i < 50 && !$m->set("k", "mc"); $i++) usleep(100000);'
                  '$r->set("k", "rd"); echo $m->get("k"), " ", $r->get("k");')
        time.sleep(1)
        result = self.docker(*php, '-d', 'extension=memcached.so', '-d', 'extension=redis.so', '-r', script)
        self.assertEqual(result, 'mc rd')
