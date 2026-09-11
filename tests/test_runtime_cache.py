"""PHP 5.6+ adapter rules, runtime INI precedence and readonly preflight."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
KEYS=('PROFILE','DATABASE_HOST','DATABASE_PORT','DATABASE_NAME','DATABASE_USER','DATABASE_PASSWORD',
      'PS_SMARTY_CACHE','PS_SMARTY_COMPILE','PS_ASSET_CACHE','PS_OBJECT_CACHE','MAIL_MODE',
      'PHP_OPCACHE','PHP_APCU','PHP_OPCACHE_VALIDATE_TIMESTAMPS','PHP_OPCACHE_REVALIDATE_FREQ')


class RuntimeCacheTest(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'.test-work';scratch.mkdir(exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        self.env=os.environ.copy()
        for key in KEYS: self.env.pop(key,None)
        self.env.update(PROFILE='ps',DATABASE_HOST='database',DATABASE_PORT='3306',DATABASE_NAME='shop',DATABASE_USER='user',DATABASE_PASSWORD='private')

    def php(self, code, *args):
        command=['php']
        if self.env.get('PHP_TEST_IMAGE'):
            command=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{ROOT}:{ROOT}']
            for key in KEYS: command+=['-e',key]
            command += [self.env['PHP_TEST_IMAGE'],'php']
        result=subprocess.run(command+['-r',code]+list(args),env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def library(self,code,*args):
        return self.php('require $argv[1]; '+code,str(ROOT/'docker/runtime/prestashop.php'),*args)

    def test_cache_and_mail_policies_are_independent(self):
        self.env.update(PS_SMARTY_CACHE='on',PS_SMARTY_COMPILE='never',PS_ASSET_CACHE='on',MAIL_MODE='off')
        policy=json.loads(self.library('echo json_encode(psPolicyValues());'))
        self.assertEqual(policy['PS_SMARTY_CACHE'],'1')
        self.assertEqual(policy['PS_SMARTY_FORCE_COMPILE'],'0')
        self.assertEqual(policy['PS_MAIL_METHOD'],'3')
        self.env.update(PS_SMARTY_CACHE='off',PS_SMARTY_COMPILE='check',PS_ASSET_CACHE='off')
        policy=json.loads(self.library('echo json_encode(psPolicyValues());'))
        self.assertEqual(policy['PS_CSS_THEME_CACHE'],'0')
        self.assertEqual(policy['PS_SMARTY_FORCE_COMPILE'],'1')
        self.assertEqual(policy['PS_MAIL_METHOD'],'3')
        self.env['MAIL_MODE']='preserve'
        self.assertNotIn('PS_MAIL_METHOD',json.loads(self.library('echo json_encode(psPolicyValues());')))

    def test_object_cache_modern_and_legacy_and_readonly_validation(self):
        modern=self.root/'app/config/parameters.php';modern.parent.mkdir(parents=True)
        modern.write_text("<?php return array('parameters'=>array('cookie_key'=>'keep','ps_cache_enable'=>true,'ps_caching'=>'CustomRedis'));")
        self.env['PS_OBJECT_CACHE']='off'
        before=modern.read_bytes()
        self.library('validatePrestashop($argv[2], $argv[2]);',str(self.root))
        self.assertEqual(before,modern.read_bytes())
        self.library('updatePrestashopParameters($argv[2]);',str(self.root))
        result=json.loads(self.php('echo json_encode(require $argv[1]);',str(modern)))['parameters']
        self.assertFalse(result['ps_cache_enable']);self.assertEqual(result['ps_caching'],'CustomRedis')
        legacy=self.root/'config/settings.inc.php';legacy.parent.mkdir()
        legacy.write_text("<?php define('_PS_VERSION_', '1.6.1.24'); define('_PS_CACHE_ENABLED_', true); define('_COOKIE_KEY_', 'keep');")
        self.library('updateLegacyPrestashop($argv[2], null);',str(self.root))
        self.assertEqual(json.loads(self.php('require $argv[1]; echo json_encode(_PS_CACHE_ENABLED_);',str(legacy))),False)

    def test_php_ini_reads_environment(self):
        # parse_ini_file verifies the real PHP env expansion even in plain CLI images
        # where OPcache/APCu extensions are not installed.
        self.env.update(PHP_OPCACHE='off',PHP_APCU='off',PHP_OPCACHE_VALIDATE_TIMESTAMPS='on',PHP_OPCACHE_REVALIDATE_FREQ='0')
        config=ROOT/'docker/runtime/php-policy/90-cache.ini'
        values=json.loads(self.php('echo json_encode(parse_ini_file($argv[1]));',str(config)))
        self.assertIn(values['opcache.enable'],('', 'off'))
        self.assertEqual(values['opcache.revalidate_freq'],'0')
        self.env['PHP_OPCACHE']='on'
        values=json.loads(self.php('echo json_encode(parse_ini_file($argv[1]));',str(config)))
        self.assertIn(values['opcache.enable'],('1','on'))

    def test_cache_clear_preserves_source_and_rejects_symlink(self):
        modern=self.root/'app/config/parameters.php';modern.parent.mkdir(parents=True)
        modern.write_text("<?php return array('parameters'=>array());")
        cache=self.root/'var/cache/dev';cache.mkdir(parents=True);(cache/'compiled.php').write_text('cache')
        self.library('clearPrestashopCache($argv[2]);',str(self.root))
        self.assertFalse(cache.exists());self.assertTrue(modern.exists())
        cache.symlink_to(modern.parent,target_is_directory=True)
        result=self.library('try { clearPrestashopCache($argv[2]); } catch (PrestashopSetupException $e) { echo "rejected"; }',str(self.root))
        self.assertEqual(result,'rejected');self.assertTrue(modern.exists())
