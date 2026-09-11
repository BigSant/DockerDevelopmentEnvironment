"""Apply PS cache/mail policy to real MySQL/MariaDB without booting a shop."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('SETUP_PHP_DB_TEST_IMAGE'), 'set SETUP_PHP_DB_TEST_IMAGE for real PS DB policy checks')
class DatabasePolicyTest(unittest.TestCase):
    def test_cache_policy_multishop_idempotence_and_mail_independence(self):
        scratch=ROOT/'.test-work';scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root=Path(temporary)
            parameters=root/'app/config/parameters.php';parameters.parent.mkdir(parents=True)
            parameters.write_text("<?php return array('parameters'=>array('database_prefix'=>'custom_')); ")
            network='setup-policy-'+uuid.uuid4().hex[:12]
            subprocess.run(['docker','network','create','--internal',network],check=True,capture_output=True)
            container=None
            try:
                container=subprocess.check_output(['docker','run','-d','--rm','--network',network,'--network-alias','database',
                    '--tmpfs','/var/lib/mysql','-e','MYSQL_ROOT_PASSWORD=test-only','-e','MYSQL_DATABASE=shop',
                    '-e','MYSQL_USER=shop','-e','MYSQL_PASSWORD=test-only',os.environ.get('SETUP_DB_TEST_IMAGE','mysql:8.4')],text=True).strip()
                env=os.environ.copy();env.update(PROFILE='ps',DATABASE_HOST='database',DATABASE_PORT='3306',DATABASE_NAME='shop',DATABASE_USER='shop',DATABASE_PASSWORD='test-only')
                command=['docker','run','--rm','--network',network,'--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{ROOT}:{ROOT}']
                for name in ('PROFILE','DATABASE_HOST','DATABASE_PORT','DATABASE_NAME','DATABASE_USER','DATABASE_PASSWORD'):
                    command+=['-e',name]
                command+=['--entrypoint','php',os.environ['SETUP_PHP_DB_TEST_IMAGE'],'-r']
                connect="$db=new PDO('mysql:host=database;dbname=shop','shop','test-only',array(PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION));"
                def php(code):
                    return subprocess.run(command+[code],env=env,capture_output=True,text=True)
                deadline=time.monotonic()+90
                while php(connect).returncode:
                    if time.monotonic()>deadline: self.fail('Disposable DB did not become ready')
                    time.sleep(0.5)
                keys=['PS_SMARTY_CACHE','PS_SMARTY_FORCE_COMPILE','PS_CSS_THEME_CACHE','PS_JS_THEME_CACHE',
                      'PS_HTML_THEME_COMPRESSION','PS_JS_HTML_THEME_COMPRESSION','PS_MAIL_METHOD','SUPPLIER_API_URL']
                setup=connect+"$db->exec('CREATE TABLE custom_configuration (id_configuration INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), value VARCHAR(100), date_upd DATETIME) ENGINE=InnoDB');"
                setup+="$insert=$db->prepare('INSERT INTO custom_configuration (name,value,date_upd) VALUES (?, ?, ?)');"
                for key in keys:
                    setup+="$insert->execute(array("+json.dumps(key)+",'1','2000-01-01'));"
                setup+="$insert->execute(array('PS_MAIL_METHOD','2','2000-01-01'));"
                result=php(setup);self.assertEqual(result.returncode,0,result.stderr)
                def apply(cache,mail='off'):
                    code='require '+json.dumps(str(ROOT/'docker/runtime/prestashop.php'))+';'
                    for key,value in {'PS_SMARTY_CACHE':cache,'PS_SMARTY_COMPILE':'check' if cache=='off' else 'never','PS_ASSET_CACHE':cache,'MAIL_MODE':mail}.items():
                        code+='putenv('+json.dumps(key+'='+value)+');'
                    return php(code+'applyPrestashopPolicy('+json.dumps(str(root))+');')
                def rows():
                    result=php(connect+"echo json_encode($db->query('SELECT name,value,date_upd FROM custom_configuration ORDER BY id_configuration')->fetchAll(PDO::FETCH_NUM));")
                    self.assertEqual(result.returncode,0,result.stderr)
                    return json.loads(result.stdout)
                for cache in ('off','on','off'):
                    result=apply(cache);self.assertEqual(result.returncode,0,result.stderr)
                    actual=rows()
                    self.assertEqual(actual[0][1],'0' if cache=='off' else '1')
                    self.assertEqual([row[1] for row in actual if row[0]=='PS_MAIL_METHOD'],['3','3'])
                    self.assertEqual(next(row for row in actual if row[0]=='SUPPLIER_API_URL')[1:],["1","2000-01-01 00:00:00"])
                    result=apply(cache);self.assertEqual(result.returncode,0,result.stderr)
                    self.assertEqual(rows(),actual)
                result=apply('on','preserve');self.assertEqual(result.returncode,0,result.stderr)
                self.assertTrue(all(row[1]=='3' for row in rows() if row[0]=='PS_MAIL_METHOD'))
                # Missing required schema must fail before writing any other selected key.
                result=php(connect+"$db->exec(\"DELETE FROM custom_configuration WHERE name='PS_SMARTY_FORCE_COMPILE'\");")
                self.assertEqual(result.returncode,0,result.stderr)
                before=rows();self.assertNotEqual(apply('off').returncode,0);self.assertEqual(rows(),before)
            finally:
                if container: subprocess.run(['docker','rm','-f',container],capture_output=True)
                subprocess.run(['docker','network','rm',network],capture_output=True)
