"""Run against a newly built PHP 8.x local image, without mounting an application.

SETUP_PHP_TEST_IMAGE=<image> python3 -m unittest discover -s tests -p test_php_image_integration.py -v
"""
import json
import os
import subprocess
import unittest


@unittest.skipUnless(os.environ.get('SETUP_PHP_TEST_IMAGE'), 'Set SETUP_PHP_TEST_IMAGE to a local PHP 8.x image')
class PhpImageTest(unittest.TestCase):
    def run_image(self, *command):
        return subprocess.check_output([
            'docker', 'run', '--rm', '--network', 'none', '--user', '1000:1000',
            '--env', 'PHP_MEMORY_LIMIT=512M', '--env', 'PHP_MAX_EXECUTION_TIME=30',
            '--env', 'PHP_UPLOAD_MAX_FILESIZE=20M', '--env', 'PHP_POST_MAX_SIZE=20M',
            '--env', 'NPM_CONFIG_CACHE=/tmp/setup-npm-cache',
            '--env', 'COMPOSER_HOME=/tmp/setup-composer', '--workdir', '/tmp',
            '--entrypoint', command[0], os.environ['SETUP_PHP_TEST_IMAGE'], *command[1:]
        ], text=True, stderr=subprocess.STDOUT, timeout=90)

    def test_required_extensions_and_image_roundtrips(self):
        result = json.loads(self.run_image('php', '-r', '''
            $required = ['curl','dom','fileinfo','gd','iconv','intl','json','mbstring',
                         'openssl','PDO','pdo_mysql','SimpleXML','zip'];
            $missing = array_values(array_filter($required, function ($name) {
                return !extension_loaded($name);
            }));
            $roundtrips = [];
            foreach (['jpeg','webp'] as $format) {
                $write = 'image'.$format;
                $read = 'imagecreatefrom'.$format;
                $path = '/tmp/image.'.$format;
                $image = imagecreatetruecolor(8, 8);
                $roundtrips[$format] = function_exists($write) && $write($image, $path)
                    && function_exists($read) && imagesx($read($path)) === 8;
            }
            echo json_encode(['missing'=>$missing,'roundtrips'=>$roundtrips,
                              'freetype'=>gd_info()['FreeType Support']]);
        '''))
        self.assertEqual(result['missing'], [])
        self.assertEqual(result['roundtrips'], {'jpeg': True, 'webp': True})
        self.assertTrue(result['freetype'])

    def test_ini_and_fpm_configuration(self):
        result = json.loads(self.run_image('php', '-r', '''
            echo json_encode(['memory'=>ini_get('memory_limit'),
                              'fopen'=>(bool)ini_get('allow_url_fopen'),
                              'include'=>(bool)ini_get('allow_url_include')]);
        '''))
        self.assertEqual(result, {'memory': '512M', 'fopen': True, 'include': False})
        fpm = self.run_image('php-fpm', '-tt')
        self.assertIn('test is successful', fpm)
        self.assertIn('php_admin_value[memory_limit] = 512M', fpm)

    def test_development_tools_are_available_to_host_uid(self):
        for command in (('composer','--version'), ('node','--version'), ('npm','--version'),
                        ('npx','--version'), ('webpack','--version'), ('phpstan','--version')):
            with self.subTest(command=command):
                self.assertTrue(self.run_image(*command).strip())
        self.assertEqual(self.run_image('php', '-r', 'echo extension_loaded("xdebug") ? "yes" : "no";'), 'yes')
