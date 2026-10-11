"""The LIVE environment probe (tools/live-probe/setup-probe.php): reports, secrets, the gate, setup tables."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / 'tools/live-probe/setup-probe.php'
PHP = shutil.which('php')
TOKEN = 'Tk7_pQ2xV9mZr4LwS8nB3yHc6JdF1gUe'

SECRETS = [
    'FixtureDbPassw0rd!', 'FixtureMailerSecret', 'FixtureSecretValue123', 'FixtureCookieKey0123456789abcdef',
    'FixtureCookieIv9876', 'def00000FixtureNewCookieKey', 'RedisAuthSecret99',
    "Legacy$Pass'word1", 'LegacyCookieKey56chars', 'LegacyIv88', 'LegacyRijndaelKey32', 'LegacyRijndaelIv16==',
    'GenericDbPassw0rd', 'base64:GenericAppKeySecret=', 'sk_live_GenericStripe123', 'cronSecretToken777',
    # Application database users count as credentials too.
    'fixture_shopuser', 'fixture_legacyuser', 'fixture_appuser',
]

PS8_PARAMETERS = """<?php return array (
  'parameters' => array (
    'database_host' => '127.0.0.1:1', 'database_port' => '', 'database_name' => 'shop_live',
    'database_user' => 'fixture_shopuser', 'database_password' => 'FixtureDbPassw0rd!',
    'database_prefix' => 'pss_', 'database_engine' => 'InnoDB',
    'mailer_transport' => 'smtp', 'mailer_host' => 'smtp.example.com', 'mailer_password' => 'FixtureMailerSecret',
    'secret' => 'FixtureSecretValue123', 'cookie_key' => 'FixtureCookieKey0123456789abcdef',
    'cookie_iv' => 'FixtureCookieIv9876', 'new_cookie_key' => 'def00000FixtureNewCookieKey',
    'ps_caching' => 'CacheMemcached', 'ps_cache_enable' => true, 'locale' => 'lt-LT',
  ),
);
"""

PS16_SETTINGS = """<?php
define('_DB_SERVER_', '127.0.0.1:1');
define('_DB_NAME_', 'legacy_shop');
define('_DB_USER_', 'fixture_legacyuser');
define('_DB_PASSWD_', 'Legacy$Pass\\'word1');
define('_DB_PREFIX_', 'ps_');
define('_MYSQL_ENGINE_', 'InnoDB');
define('_PS_CACHING_SYSTEM_', 'CacheFs');
define('_PS_CACHE_ENABLED_', '0');
define('_COOKIE_KEY_', 'LegacyCookieKey56chars');
define('_COOKIE_IV_', 'LegacyIv88');
define('_RIJNDAEL_KEY_', 'LegacyRijndaelKey32');
define('_RIJNDAEL_IV_', 'LegacyRijndaelIv16==');
define('_PS_VERSION_', '1.6.1.24');
define('_PS_CREATION_DATE_', '2016-01-01');
"""

GENERIC_ENV = """APP_ENV=production
APP_KEY=base64:GenericAppKeySecret=
DB_CONNECTION=mysql
DB_HOST=127.0.0.1
DB_PORT=1
DB_DATABASE=app_live
DB_USERNAME=fixture_appuser
DB_PASSWORD="GenericDbPassw0rd"
STRIPE_SECRET=sk_live_GenericStripe123
MAIL_HOST=smtp.example.com
CRON_URL=https://app.example.com/cron?token=cronSecretToken777
"""


def write(root, relative, text):
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def make_ps8(root):
    write(root, 'app/AppKernel.php', "<?php\nclass AppKernel { const VERSION = '8.1.5'; }\n")
    write(root, 'app/config/parameters.php', PS8_PARAMETERS)
    write(root, 'config/defines.inc.php', "<?php\ndefine('_PS_MODE_DEV_', false);\n")
    write(root, 'classes/index.php', '<?php\n')
    write(root, 'index.php', '<?php\n')
    write(root, 'override/classes/Cart.php', '<?php class Cart extends CartCore {}\n')
    write(root, 'override/index.php', '<?php\n')
    write(root, 'img/p/1.jpg', 'x')
    write(root, 'admin-fixture/init.php', '<?php\n')
    write(root, 'admin-fixture/ajax-tab.php', '<?php\n')
    write(root, '.htaccess', 'php_value max_input_vars 5000\n<IfModule mod_php7.c>\nphp_flag display_errors off\n</IfModule>\n'
                             '<IfModule mod_rewrite.c>\nRewriteEngine on\n</IfModule>\nAddOutputFilterByType BROTLI_COMPRESS text/html\n')
    write(root, '.user.ini', 'memory_limit = 768M\nsession.save_path = "tcp://127.0.0.1:6379?auth=RedisAuthSecret99"\n')


def make_ps16(root):
    write(root, 'config/settings.inc.php', PS16_SETTINGS)
    write(root, 'config/defines.inc.php', "<?php\nif (!defined('_PS_MODE_DEV_')) {\n    define('_PS_MODE_DEV_', true);\n}\n")
    write(root, 'classes/index.php', '<?php\n')
    write(root, 'index.php', '<?php\n')


def make_generic(root):
    write(root, 'composer.json', json.dumps({'name': 'fixture/app', 'require': {
        'php': '^8.2', 'ext-redis': '*', 'ext-xsl': '*', 'laravel/framework': '^11.0'}}))
    write(root, 'artisan', '#!/usr/bin/env php\n')
    write(root, '.env', GENERIC_ENV)
    write(root, 'public/index.php', '<?php\n')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def configured_probe(directory, token=TOKEN, ips='127.0.0.1', expires=None):
    """A copy of the probe with the three constants filled in."""
    if expires is None:
        expires = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d %H:%M')
    text = PROBE.read_text()
    for name, value in (('PROBE_TOKEN', token), ('PROBE_ALLOWED_IPS', ips), ('PROBE_EXPIRES', expires)):
        text = text.replace(f"define('{name}', '');", f"define('{name}', '{value}');", 1)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'setup-probe.php').write_text(text)
    shutil.copy(PROBE.parent / '.htaccess', directory / '.htaccess')


@unittest.skipUnless(PHP, 'php is not installed')
class ProbeCliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / '.test-work').mkdir(exist_ok=True)
        cls.work = tempfile.TemporaryDirectory(dir=ROOT / '.test-work')
        base = Path(cls.work.name)
        cls.roots = {'ps8': base / 'ps8', 'ps16': base / 'ps16', 'generic': base / 'generic'}
        make_ps8(cls.roots['ps8'])
        make_ps16(cls.roots['ps16'])
        make_generic(cls.roots['generic'])
        cls.outputs = {}
        for name, root in cls.roots.items():
            for fmt in ('md', 'env', 'json'):
                cls.outputs[name, fmt] = cls.run_probe(root, fmt)

    @classmethod
    def tearDownClass(cls):
        cls.work.cleanup()

    @staticmethod
    def run_probe(root, fmt, *extra):
        result = subprocess.run([PHP, str(PROBE), f'--format={fmt}', f'--root={root}', *extra],
                                capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise AssertionError(f'probe failed ({result.returncode}): {result.stdout[-2000:]} {result.stderr[-2000:]}')
        return result.stdout

    def report(self, name):
        return json.loads(self.outputs[name, 'json'])

    def plan(self, name, section='common_env'):
        return {entry['key']: entry for entry in self.report(name)['setup'][section]}

    def test_prestashop_8_parameters(self):
        report = self.report('ps8')
        self.assertEqual(report['_meta']['mode'], 'cli')
        self.assertTrue(report['_meta']['guarantees']['read_only'])
        self.assertIn('REMINDER', report['_meta']['reminder'])
        self.assertEqual((report['application']['type'], report['application']['version']), ('prestashop', '8.1.5'))
        presence = report['prestashop']['key_presence']
        self.assertEqual(sorted(report['prestashop']['required_keys']),
                         ['PS_COOKIE_IV', 'PS_COOKIE_KEY', 'PS_NEW_COOKIE_KEY', 'PS_SECRET'])
        self.assertTrue(all(presence[key]['present'] for key in report['prestashop']['required_keys']))
        self.assertEqual(report['prestashop']['admin_directory'], 'admin-fixture')
        self.assertEqual(report['prestashop']['overrides']['files'], ['override/classes/Cart.php'])
        common = self.plan('ps8')
        self.assertEqual(common['PROFILE']['value'], 'prestashop')
        self.assertEqual(common['PS_VERSION']['value'], '8.1.5')
        self.assertEqual(common['PS_DATABASE_PREFIX']['value'], 'pss_')
        self.assertEqual(common['PS_LOCALE']['value'], 'lt-LT')
        # The CLI cannot see the site's limits; the .user.ini of the root stands in for it.
        self.assertEqual(common['PHP_MEMORY_LIMIT']['value'], '768M')
        self.assertIn('memcached', common['SERVICES']['value'].split())
        self.assertEqual(self.plan('ps8', 'prod_env')['DATABASE_NAME']['value'], 'shop_live')
        ini = self.plan('ps8', 'php_ini')
        self.assertEqual(ini['max_input_vars']['value'], '5000')
        self.assertEqual(ini['extension']['value'], 'memcached.so')

    def test_htaccess_findings(self):
        risks = {risk['id']: risk for risk in self.report('ps8')['risks']}
        self.assertEqual(risks['htaccess-php-value']['level'], 'high')
        self.assertEqual(risks['htaccess-brotli']['level'], 'high')
        self.assertNotIn('htaccess-rewrite', risks)
        php_values = self.report('ps8')['webserver']['htaccess']['php_values']
        self.assertEqual([(value['name'], value['guarded']) for value in php_values],
                         [('max_input_vars', False), ('display_errors', True)])

    def test_unreachable_database_degrades(self):
        for name in self.roots:
            report = self.report(name)
            self.assertTrue(report['database']['configured'], name)
            self.assertFalse(report['database']['available'], name)
            self.assertTrue(report['database']['error'], name)
            self.assertIn('db-unreachable', [risk['id'] for risk in report['risks']], name)
            self.assertIn('# DATABASE_ENGINE=mysql', self.outputs[name, 'env'])
            self.assertIn('Not reachable:', self.outputs[name, 'md'])

    def test_prestashop_16_settings(self):
        report = self.report('ps16')
        self.assertEqual(report['application']['version'], '1.6.1.24')
        self.assertEqual(report['prestashop']['config_file'], 'config/settings.inc.php')
        self.assertEqual(report['database']['name'], 'legacy_shop')
        self.assertEqual(sorted(report['prestashop']['required_keys']),
                         ['PS_COOKIE_IV', 'PS_COOKIE_KEY', 'PS_RIJNDAEL_IV', 'PS_RIJNDAEL_KEY'])
        self.assertTrue(all(item['present'] for item in report['prestashop']['key_presence'].values()))
        self.assertTrue(report['prestashop']['debug']['mode_dev'])
        self.assertIn('ps-debug-live', [risk['id'] for risk in report['risks']])
        self.assertIn('PS_VERSION=1.6.1.24', self.outputs['ps16', 'env'])
        self.assertIn('# PS_RIJNDAEL_KEY="<_RIJNDAEL_KEY_ from LIVE>"', self.outputs['ps16', 'env'])

    def test_generic_composer_application(self):
        report = self.report('generic')
        self.assertEqual(report['application']['type'], 'laravel')
        self.assertIsNone(report['prestashop'])
        self.assertEqual(report['composer']['php'], '^8.2')
        self.assertEqual(sorted(report['composer']['extensions']), ['redis', 'xsl'])
        self.assertEqual(report['database']['name'], 'app_live')
        common = self.plan('generic')
        self.assertEqual(common['PROFILE']['value'], '')
        self.assertEqual(common['DOCUMENT_ROOT']['value'], 'public')
        self.assertIn('redis', common['SERVICES']['value'].split())
        names = common['APP_ENVIRONMENT']['value'].split()
        self.assertIn('STRIPE_SECRET', names)
        self.assertIn('MAIL_HOST', names)
        self.assertNotIn('PS_VERSION', self.outputs['generic', 'env'])
        self.assertIn('PROFILE=', self.outputs['generic', 'env'])
        secret_names = [item['name'] for item in report['setup']['secrets']]
        self.assertIn('STRIPE_SECRET', secret_names)
        self.assertIn('xsl', [item['extension'] for item in report['setup']['dockerfile']]
                      + report['extensions']['core'])

    def test_outputs_never_contain_secret_values(self):
        for (name, fmt), output in self.outputs.items():
            for secret in SECRETS:
                self.assertNotIn(secret, output, f'{name} {fmt}')
                self.assertNotIn(json.dumps(secret)[1:-1], output, f'{name} {fmt}')

    def test_env_output_uses_setup_setting_names(self):
        known = set(re.findall(r'^\s+([A-Z][A-Z0-9_]*):', (ROOT / 'docker/project-settings.yaml').read_text(), re.M))
        known |= set(re.findall(r'^([A-Z][A-Z0-9_]*)=', (ROOT / 'docker/.env').read_text(), re.M))
        for compose in (ROOT / 'docker/docker').glob('*/docker-compose.yml'):
            known |= set(re.findall(r'\$\{([A-Z][A-Z0-9_]*)', compose.read_text()))
        seen = set()
        for name in self.roots:
            for line in self.outputs[name, 'env'].splitlines():
                match = re.match(r'^(?:# )?([A-Z][A-Z0-9_]*)=', line)
                if match:
                    seen.add(match.group(1))
        self.assertTrue({'PROFILE', 'PS_VERSION', 'PHP_VERSION', 'DATABASE_ENGINE', 'TZ', 'SERVICES', 'PHP_MEMORY_LIMIT',
                         'PHP_MAX_EXECUTION_TIME', 'PHP_UPLOAD_MAX_FILESIZE', 'PHP_POST_MAX_SIZE', 'DOMAIN', 'DATABASE_NAME',
                         'PHP_MEMORY', 'DATABASE_MEMORY', 'PS_COOKIE_KEY', 'PS_DATABASE_PREFIX', 'DOCUMENT_ROOT',
                         'APP_ENVIRONMENT'} <= seen, seen)
        self.assertEqual(sorted(seen - known), [])

    def test_markdown_report_sections(self):
        report = self.outputs['ps8', 'md']
        for heading in ('## 1. Summary', '## 2. Setup settings', '## 3. Details', '## 4. Mismatches and risks',
                        '## 5. Instructions for an LLM', '## 6. Removal'):
            self.assertIn(heading, report)
        self.assertEqual(report.count('REMINDER: remove the probe'), 4)
        self.assertIn('FROM ${SETUP_PHP_IMAGE}', report)

    def test_cli_delete_keeps_a_shared_directory(self):
        directory = Path(self.work.name) / 'shared'
        directory.mkdir()
        shutil.copy(PROBE, directory / 'setup-probe.php')
        write(directory, 'keep.txt', 'not the probe')
        result = subprocess.run([PHP, str(directory / 'setup-probe.php'), '--action=delete'], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('directory kept', result.stdout)
        self.assertFalse((directory / 'setup-probe.php').exists())
        self.assertTrue((directory / 'keep.txt').exists())


@unittest.skipUnless(PHP, 'php is not installed')
class ProbeWebGateTest(unittest.TestCase):
    """Over HTTP (php -S): token, allowlist, expiry, empty constants and self-removal."""

    @classmethod
    def setUpClass(cls):
        (ROOT / '.test-work').mkdir(exist_ok=True)
        cls.work = tempfile.TemporaryDirectory(dir=ROOT / '.test-work')
        cls.root = Path(cls.work.name) / 'shop'
        make_ps8(cls.root)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M')
        far = (datetime.now(timezone.utc) + timedelta(days=30)).strftime('%Y-%m-%d %H:%M')
        configured_probe(cls.root / '_ok')
        configured_probe(cls.root / '_other_ip', ips='203.0.113.9')
        configured_probe(cls.root / '_expired', expires=past)
        configured_probe(cls.root / '_expired_remove', expires=past)
        configured_probe(cls.root / '_far', expires=far)
        configured_probe(cls.root / '_weak', token='a' * 32)
        configured_probe(cls.root / '_shared')
        write(cls.root / '_shared', 'keep.txt', 'not the probe')
        (cls.root / '_unconfigured').mkdir()
        shutil.copy(PROBE, cls.root / '_unconfigured/setup-probe.php')
        cls.port = free_port()
        cls.server = subprocess.Popen([PHP, '-S', f'127.0.0.1:{cls.port}', '-t', str(cls.root)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                socket.create_connection(('127.0.0.1', cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait(timeout=10)
        cls.work.cleanup()

    def get(self, directory, query='', headers=None):
        url = f'http://127.0.0.1:{self.port}/{directory}/setup-probe.php' + (f'?{query}' if query else '')
        request = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.read().decode(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode(), error.headers

    def test_refuses_without_valid_token(self):
        status, body, _ = self.get('_ok')
        self.assertEqual((status, body.splitlines()[0]), (403, '403 Forbidden: bad token'))
        self.assertIn('REMINDER', body)
        status, body, _ = self.get('_ok', 'token=wrong-token-of-sufficient-length')
        self.assertEqual((status, body.splitlines()[0]), (403, '403 Forbidden: bad token'))

    def test_valid_token_and_address_get_the_report(self):
        status, body, headers = self.get('_ok', f'token={TOKEN}')
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith('# setup LIVE probe'))
        self.assertEqual(headers['X-Robots-Tag'], 'noindex, nofollow')
        status, body, headers = self.get('_ok', 'format=json&root=/etc', {'X-Probe-Token': TOKEN})
        self.assertEqual(status, 200)
        self.assertTrue(headers['Content-Type'].startswith('application/json'))
        report = json.loads(body)
        self.assertEqual(report['_meta']['mode'], 'web')
        self.assertEqual(Path(report['_meta']['root']).resolve(), self.root.resolve())
        status, body, _ = self.get('_ok', f'token={TOKEN}&format=env')
        self.assertEqual(status, 200)
        self.assertIn('PS_VERSION=8.1.5', body)
        for output in (body, json.dumps(report)):
            for secret in SECRETS:
                self.assertNotIn(secret, output)

    def test_allowlist_uses_remote_addr_only(self):
        status, body, _ = self.get('_other_ip', f'token={TOKEN}', {'X-Forwarded-For': '203.0.113.9'})
        self.assertEqual(status, 403)
        self.assertIn('ip not allowed (REMOTE_ADDR 127.0.0.1)', body)

    def test_empty_constants_refuse_even_with_a_token(self):
        status, body, _ = self.get('_unconfigured', f'token={TOKEN}')
        self.assertEqual(status, 403)
        self.assertIn('probe not configured', body)
        for name in ('PROBE_TOKEN', 'PROBE_ALLOWED_IPS', 'PROBE_EXPIRES'):
            self.assertIn(name, body)

    def test_expiry_rules(self):
        status, body, _ = self.get('_expired', f'token={TOKEN}')
        self.assertEqual(status, 403)
        self.assertIn('probe expired', body)
        status, body, _ = self.get('_far', f'token={TOKEN}')
        self.assertEqual(status, 403)
        self.assertIn('more than 7 days ahead', body)
        status, body, _ = self.get('_weak', f'token={"a" * 32}')
        self.assertEqual(status, 403)
        self.assertIn('not random enough', body)

    def test_delete_removes_a_dedicated_directory_even_after_expiry(self):
        status, body, _ = self.get('_expired_remove', 'action=delete&token=wrong')
        self.assertEqual(status, 403)
        self.assertTrue((self.root / '_expired_remove/setup-probe.php').exists())
        status, body, _ = self.get('_expired_remove', f'action=delete&token={TOKEN}')
        self.assertEqual(status, 200, body)
        self.assertIn('removed directory _expired_remove', body)
        self.assertFalse((self.root / '_expired_remove').exists())
        # php -S answers a missing file with the root index.php (a web server would say 404): the probe is gone.
        status, body, _ = self.get('_expired_remove', f'token={TOKEN}')
        self.assertNotIn('probe', body)

    def test_delete_keeps_foreign_files(self):
        status, body, _ = self.get('_shared', f'action=delete&token={TOKEN}')
        self.assertEqual(status, 200, body)
        self.assertIn('directory kept', body)
        self.assertFalse((self.root / '_shared/setup-probe.php').exists())
        self.assertTrue((self.root / '_shared/.htaccess').exists())
        self.assertTrue((self.root / '_shared/keep.txt').exists())


@unittest.skipUnless(PHP, 'php is not installed')
class ProbeSetupTablesTest(unittest.TestCase):
    """The tables embedded in the probe follow setup's own files."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run([PHP, str(PROBE), '--setup-data'], capture_output=True, text=True, timeout=30, check=True)
        cls.data = json.loads(result.stdout)

    def test_compatibility_table(self):
        expected = json.loads((ROOT / 'docker/compatibility.json').read_text())['prestashop']
        self.assertEqual(self.data['compatibility'], expected)

    def test_php_branches_and_defaults(self):
        branches = sorted(path.name for path in (ROOT / 'docker/docker/php-fpm').iterdir()
                          if path.is_dir() and re.match(r'^\d+\.\d+$', path.name))
        self.assertEqual(self.data['php_branches'], branches)
        env = dict(re.findall(r'^([A-Z_]+)=(\S*)', (ROOT / 'docker/.env').read_text(), re.M))
        for key in ('PHP_VERSION', 'MYSQL_VERSION', 'MARIADB_VERSION', 'PHP_MEMORY_LIMIT', 'PHP_MAX_EXECUTION_TIME',
                    'PHP_UPLOAD_MAX_FILESIZE', 'PHP_POST_MAX_SIZE', 'DOCUMENT_ROOT'):
            self.assertEqual(self.data['defaults'][key], env[key].strip('"'), key)
        fpm = (ROOT / 'docker/docker/php-fpm/8.4/conf/php-fpm.conf').read_text()
        policy = ''.join(path.read_text() for path in (ROOT / 'docker/runtime/php-policy').glob('*.ini'))
        for ini_key, setting in self.data['env_ini'].items():
            self.assertTrue(f'php_admin_value[{ini_key}] = ${{{setting}}}' in fpm
                            or re.search(rf'^{re.escape(ini_key)}\s*=\s*"?\$\{{{setting}\}}', policy, re.M), ini_key)

    def test_core_ini_values(self):
        for branch in self.data['php_branches']:
            ini = {}
            for line in (ROOT / f'docker/docker/php-fpm/{branch}/conf/php.ini').read_text().splitlines():
                match = re.match(r'^\s*([A-Za-z0-9_.]+)\s*=\s*(.*?)\s*$', line)
                if match:
                    ini[match.group(1)] = match.group(2).strip('"')
            for key, (default, _type) in self.data['core_ini'].items():
                by_branch = self.data['core_ini_by_branch'].get(key)
                expected = by_branch.get(branch, by_branch['default']) if by_branch else default
                if key in ini:
                    self.assertEqual(ini[key], expected, f'{branch} {key}')

    def test_installed_extensions(self):
        for branch in self.data['php_branches']:
            lines = [line for line in (ROOT / f'docker/docker/php-fpm/{branch}/Dockerfile').read_text().splitlines()
                     if not line.lstrip().startswith('#')]
            text = '\n'.join(lines)
            always = set()
            for match in re.finditer(r'^RUN docker-php-ext-install ([a-z_ ]+)$', text, re.M):
                always |= set(match.group(1).split())
            if re.search(r'pecl install apcu', text):
                always.add('apcu')
            conditional = set(re.findall(r'then docker-php-ext-install ([a-z_]+)', text))
            probe = set(self.data['installed_extensions'][branch])
            self.assertLessEqual(always, probe, branch)
            self.assertLessEqual(probe, always | conditional, branch)

    def test_apache_modules(self):
        loaded = set(re.findall(r'^LoadModule\s+(\w+)_module', (ROOT / 'docker/docker/apache/conf/httpd.custom.conf').read_text(), re.M))
        self.assertLessEqual(loaded, set(self.data['apache_modules']))

    def test_prestashop_registry(self):
        known, skip = [], []
        for line in (ROOT / 'docker/database-profile/prestashop/table-registry.txt').read_text().splitlines():
            fields = line.split()
            if fields and not line.startswith('#'):
                known.append(fields[0].replace('{p}', ''))
                if fields[3] == 'skip':
                    skip.append(fields[0].replace('{p}', ''))
        self.assertEqual(self.data['ps_registry']['known'], known)
        self.assertEqual(self.data['ps_registry']['skip'], skip)


@unittest.skipUnless(PHP, 'php is not installed')
class ProbeSourceTest(unittest.TestCase):
    def test_lints_and_sends_only_select_or_show(self):
        result = subprocess.run([PHP, '-l', str(PROBE)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = PROBE.read_text()
        statements = re.findall(r"sp_db_(?:query|value)\(\$db,\s*(?:sprintf\(\$shop_sql|'(\w+))", source)
        self.assertGreater(len(statements), 10)
        self.assertEqual(sorted({word for word in statements if word}), ['SELECT', 'SHOW'])
        self.assertRegex(source, r"\$shop_sql = 'SELECT ")
        for constant in ('PROBE_TOKEN', 'PROBE_ALLOWED_IPS', 'PROBE_EXPIRES', 'PROBE_ROOT', 'PROBE_APP_CONFIG'):
            self.assertIn(f"define('{constant}', '');", source)

    @unittest.skipUnless(os.environ.get('SETUP_PROBE_PHP56') == '1', 'set SETUP_PROBE_PHP56=1 to lint with php:5.6-cli')
    def test_lints_on_php_5_6(self):
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '-v', f'{PROBE.parent}:/w:ro', 'php:5.6-cli',
                                 'php', '-l', '/w/setup-probe.php'], capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
