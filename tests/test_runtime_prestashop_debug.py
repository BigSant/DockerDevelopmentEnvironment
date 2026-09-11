"""Exercise generated debug policy under real PHP HTTP/CLI and both PS layouts."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
SOURCE = """<?php
// Preserve comment: define('_PS_MODE_DEV_', 'comment');
if (!defined('_PS_MODE_DEV_')) {
    define('_PS_MODE_DEV_', false);
}
define('_PS_DEBUG_SQL_', _PS_MODE_DEV_ === true);
define('_PS_DEBUG_PROFILING_', false);
define('_KEEP_ME_', 'original');
"""


class PrestashopDebugTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / 'config/defines.inc.php'
        self.file.parent.mkdir()
        self.file.write_text(SOURCE)
        self.file.chmod(0o644)
        self.parameters = self.root / 'app/config/parameters.php'
        self.parameters.parent.mkdir(parents=True)
        self.parameters.write_text("<?php return array('parameters'=>array('secret'=>'keep'));")
        self.env = os.environ.copy()
        self.env.update(PROFILE='ps', DATABASE_HOST='database', DATABASE_PORT='3306',
                        DATABASE_NAME='shop', DATABASE_USER='shop', DATABASE_PASSWORD='test-only',
                        PS_DEBUG_MODE='', PS_DEBUG_IPS='')

    def php(self, *args):
        command = ['php']
        if self.env.get('PHP_TEST_IMAGE'):
            command = ['docker', 'run', '--rm', '--network', 'none',
                       '--user', f'{os.getuid()}:{os.getgid()}', '-v', f'{ROOT}:{ROOT}']
            for name in ('PROFILE', 'DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME',
                         'DATABASE_USER', 'DATABASE_PASSWORD', 'PS_DEBUG_MODE', 'PS_DEBUG_IPS'):
                command += ['-e', name]
            command += [self.env['PHP_TEST_IMAGE'], 'php']
        return subprocess.run(command + list(args), env=self.env, capture_output=True, text=True)

    def prepare(self, success=True):
        result = self.php(str(ROOT / 'docker/runtime/prestashop-command.php'), 'prepare',
                          str(self.root), str(self.root / 'project-config'))
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def cli(self):
        result = self.php('-r', "$_SERVER['REMOTE_ADDR']='127.0.0.1'; require $argv[1]; "
                          'echo json_encode(array(_PS_MODE_DEV_, _PS_DEBUG_SQL_, _KEEP_ME_));', str(self.file))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_off_on_repeated_start_and_original_content(self):
        for mode, enabled in [('on', True), ('off', False), ('on', True)]:
            self.env['PS_DEBUG_MODE'] = mode
            self.prepare()
            self.assertEqual(self.cli(), [enabled, enabled, 'original'])
            self.assertEqual(self.file.stat().st_mode & 0o777, 0o644)
            self.assertIn("// Preserve comment: define('_PS_MODE_DEV_', 'comment');", self.file.read_text())
            self.assertIn("define('_PS_DEBUG_PROFILING_', false);", self.file.read_text())
            before = self.file.read_bytes(), self.file.stat().st_mtime_ns
            self.prepare()
            self.assertEqual((self.file.read_bytes(), self.file.stat().st_mtime_ns), before)

    def test_legacy_layout_and_profile_alias(self):
        self.parameters.unlink()
        settings = self.root / 'config/settings.inc.php'
        settings.write_text("<?php define('_PS_VERSION_', '1.6.1.24'); define('_COOKIE_KEY_', 'keep');")
        self.env.update(PROFILE='prestashop', PS_DEBUG_MODE='on')
        self.prepare()
        self.assertEqual(self.cli()[0], True)
        self.assertIn("define('_COOKIE_KEY_', 'keep');", settings.read_text())
        self.assertFalse(self.parameters.exists())

    def test_unmanaged_and_non_ps_do_not_touch_debug(self):
        self.file.unlink()
        self.prepare()  # Empty mode does not require a defines file.
        self.file.write_text(SOURCE.replace('false', 'true'))
        original = self.file.read_bytes()
        self.env.update(PS_DEBUG_IPS='ignored-with-empty-mode')
        self.prepare()
        self.assertEqual(self.file.read_bytes(), original)
        self.env.update(PROFILE='akeneo', PS_DEBUG_MODE='invalid', PS_DEBUG_IPS='invalid')
        before = self.parameters.read_bytes()
        self.prepare()
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(self.parameters.read_bytes(), before)

    def test_invalid_env_leaves_both_files_unchanged(self):
        before = self.file.read_bytes(), self.parameters.read_bytes()
        cases = [('true', ''), ('ON', ''), ('ip', ''), ('ip', '127.0.0.1,'),
                 ('ip', '127.0.0.1:80'), ('ip', '10.0.0.0/8'), ('ip', 'localhost'),
                 ('ip', "127.0.0.1'); die('injection")]  # Env never becomes PHP code.
        for mode, ips in cases:
            with self.subTest(mode=mode, ips=ips):
                self.env.update(PS_DEBUG_MODE=mode, PS_DEBUG_IPS=ips)
                result = self.prepare(success=False)
                self.assertIn('PS_DEBUG_', result.stderr)
                self.assertEqual((self.file.read_bytes(), self.parameters.read_bytes()), before)

    def test_missing_or_duplicate_definition_fails_before_db_update(self):
        self.env['PS_DEBUG_MODE'] = 'off'
        for source in ["<?php // define('_PS_MODE_DEV_', true);",
                       SOURCE + "define('_PS_MODE_DEV_', true);"]:
            self.file.write_text(source)
            before = self.parameters.read_bytes()
            self.assertIn('managed define', self.prepare(success=False).stderr)
            self.assertEqual(self.file.read_text(), source)
            self.assertEqual(self.parameters.read_bytes(), before)
        self.file.unlink()
        self.assertIn('Restore', self.prepare(success=False).stderr)

    def test_symlink_is_rejected_without_changing_target(self):
        target = self.root / 'outside.php'
        self.file.rename(target)
        self.file.symlink_to(target)
        self.env['PS_DEBUG_MODE'] = 'on'
        self.assertIn('symlink', self.prepare(success=False).stderr)
        self.assertEqual(target.read_text(), SOURCE)

    def test_ip_mode_cli_is_off_and_ip_order_does_not_rewrite_file(self):
        self.env.update(PS_DEBUG_MODE='ip', PS_DEBUG_IPS='127.0.0.1, ::1,2001:db8::1')
        self.prepare()
        self.assertEqual(self.cli()[0], False)
        before = self.file.read_bytes(), self.file.stat().st_mtime_ns
        self.env['PS_DEBUG_IPS'] = '2001:0db8:0:0:0:0:0:1,::1,127.0.0.1,127.0.0.1'
        self.prepare()
        self.assertEqual((self.file.read_bytes(), self.file.stat().st_mtime_ns), before)

    def test_complex_original_expression_and_close_tag_remain_valid(self):
        self.file.write_text(SOURCE.replace("define('_PS_MODE_DEV_', false)",
                                           "define('_PS_MODE_DEV_', in_array('a', array('a', 'b'), true))") + '?>')
        self.env['PS_DEBUG_MODE'] = 'off'
        self.prepare()
        self.assertEqual(self.cli()[0], False)
        self.assertTrue(self.file.read_text().endswith('?>'))

    def test_real_http_evaluates_each_address_and_ignores_forwarded_headers(self):
        # This fixture supplies controlled server addresses; production code never
        # obtains REMOTE_ADDR from a query parameter or forwarded HTTP header.
        (self.root / 'index.php').write_text("""<?php
if (isset($_GET['remote'])) $_SERVER['REMOTE_ADDR'] = $_GET['remote'];
else unset($_SERVER['REMOTE_ADDR']);
if (isset($_GET['forwarded'])) {
    $_SERVER['HTTP_X_FORWARDED_FOR'] = $_GET['forwarded'];
    $_SERVER['HTTP_X_REAL_IP'] = $_GET['forwarded'];
}
require __DIR__ . '/config/defines.inc.php';
echo json_encode(array(_PS_MODE_DEV_, _PS_DEBUG_SQL_));
""")
        self.env.update(PS_DEBUG_MODE='ip', PS_DEBUG_IPS='127.0.0.1,::1,2001:db8::1')
        self.prepare()
        if self.env.get('PHP_TEST_IMAGE'):
            container = subprocess.check_output(
                ['docker', 'run', '-d', '--rm', '--user', f'{os.getuid()}:{os.getgid()}',
                 '-v', f'{self.root}:/app:ro', '-p', '127.0.0.1::8080', self.env['PHP_TEST_IMAGE'],
                 'php', '-S', '0.0.0.0:8080', '-t', '/app'], text=True).strip()
            self.addCleanup(lambda: subprocess.run(['docker', 'rm', '-f', container], capture_output=True))
            address = subprocess.check_output(['docker', 'port', container, '8080/tcp'], text=True).strip()
        else:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                address = f'127.0.0.1:{sock.getsockname()[1]}'
            process = subprocess.Popen(['php', '-S', address, '-t', str(self.root)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            def stop():
                process.terminate()
                process.wait(timeout=10)
            self.addCleanup(stop)
        url = 'http://' + address + '/?'
        deadline = time.monotonic() + 15
        while True:
            try:
                with urlopen(url, timeout=1) as response:
                    self.assertEqual(json.load(response), [False, False])
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        for remote, expected in [('127.0.0.1', True), ('::1', True),
                                 ('2001:0db8:0:0:0:0:0:1', True), ('2001:db8::2', False),
                                 ('192.0.2.1', False), ('::ffff:127.0.0.1', False), ('invalid', False), ('', False)]:
            with self.subTest(remote=remote):
                with urlopen(url + urlencode({'remote': remote, 'forwarded': '127.0.0.1'})) as response:
                    self.assertEqual(json.load(response), [expected, expected])
        for mode, enabled in [('off', False), ('on', True)]:
            self.env['PS_DEBUG_MODE'] = mode
            self.prepare()
            with urlopen(url + urlencode({'remote': '192.0.2.1'})) as response:
                self.assertEqual(json.load(response), [enabled, enabled])


if __name__ == '__main__':
    unittest.main()
