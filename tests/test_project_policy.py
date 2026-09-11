"""Environment/cache independence, validation boundaries and bounded operations."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'docker')]
from prepare_project import prepare
from project import Project
from project_policy import resolve_policy, validate_configuration
from project_health import start_project
from project_storage import require_space, prune_backups
from project_commands import composer, logs, restart


class PolicyTest(unittest.TestCase):
    def setUp(self):
        scratch=ROOT/'.test-work';scratch.mkdir(exist_ok=True)
        tmp=tempfile.TemporaryDirectory(dir=scratch);self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/'shop'
        with contextlib.redirect_stdout(io.StringIO()): prepare(self.root, layout='app')
        self.app=self.root/'app'
        for env in ('local','test','stage','prod'):
            (self.app/f'env/{env}.env').write_text('DOMAIN=shop.localhost\nDATABASE_NAME=shop\nDATABASE_USER=shop\nDATABASE_PASSWORD=secret-only\nLOCALHOST_PORT=32001\nLOCALHOST_PORT_SSL=32002\nCOMPOSE_PROFILES=\n')

    def test_defaults_and_hybrids_do_not_change_identity_mail_or_images(self):
        for env in ('local','test','stage','prod'):
            policy=resolve_policy({},env)
            self.assertEqual(policy['CACHE_MODE'], 'off' if env in ('local','test') else 'on')
            self.assertEqual(policy['MAIL_MODE'], 'preserve' if env=='prod' else 'off')
        local=Project(self.app);before=local.model()
        with (self.app/'env/local.env').open('a') as file: file.write('CACHE_MODE=on\nCOMPOSE_PROFILES=redis\n')
        hybrid=Project(self.app);after=hybrid.model()
        self.assertEqual(hybrid.name,local.name)
        self.assertEqual(hybrid.web_directory,local.web_directory)
        self.assertEqual(hybrid.data_directory,local.data_directory)
        self.assertEqual(after['services']['php-fpm']['image'],before['services']['php-fpm']['image'])
        php=after['services']['php-fpm']['environment']
        self.assertEqual((php['CACHE_MODE'],php['PHP_OPCACHE'],php['MAIL_MODE']),('on','on','off'))
        self.assertEqual(php['DATABASE_PASSWORD'],'secret-only')
        self.assertEqual(hybrid.process_env['BUILD_ENV'],'local')
        self.assertIn('redis',json.loads(hybrid.capture(['config','--format','json']))['services'])
        stage=Project(self.app,'stage')
        self.assertEqual((stage.settings['PHP_OPCACHE'],stage.settings['MAIL_MODE']),('on','off'))

    def test_individual_cache_overrides_and_explicit_debug_validation(self):
        policy=resolve_policy({'CACHE_MODE':'on','PHP_OPCACHE':'off','PS_SMARTY_COMPILE':'check'},'local')
        self.assertEqual(policy['PHP_OPCACHE'],'off')
        self.assertEqual(policy['PHP_APCU'],'on')
        self.assertEqual(policy['PS_SMARTY_COMPILE'],'check')
        for settings in ({'CACHE_MODE':'prod'},{'CONTAINER_CPUS':'0'},{'PHP_CONTAINER_MEMORY':'oops'},
                         {'MIN_FREE_DISK_MB':'-1'},{'PROFILE':'ps','PS_DEBUG_MODE':'ip'},
                         {'PROFILE':'ps','PS_DEBUG_MODE':'ip','PS_DEBUG_IPS':'10.0.0.0/8'}):
            with self.subTest(settings=settings), self.assertRaises(ValueError): resolve_policy(settings,'local')
        self.assertEqual(resolve_policy({'PROFILE':'akeneo','PS_DEBUG_MODE':'invalid'},'local')['PS_DEBUG_MODE'],'invalid')

    def test_new_prod_paths_and_optional_proxy_routes(self):
        project=Project(self.app,'prod');model=project.model()
        db=model['services']['database']
        mount=next(v for v in db['volumes'] if v['target']=='/var/lib/mysql')
        self.assertEqual(Path(mount['source']),self.root/'data/prod/mysql')
        redis=next(v for v in model['services']['redis']['volumes'] if v['target']=='/data')
        self.assertEqual(Path(redis['source']),self.root/'data/prod/redis')
        # The full legacy source includes both services; neither needs a profile.
        for selected in ('', 'cron'):
            values=Project(self.app,profiles=selected).model()['services']['nginx-proxy']['environment']
            self.assertEqual((values['SETUP_ENABLE_PMA'],values['SETUP_ENABLE_MAILPIT']),('1','1'))
        self.assertEqual(db['logging']['options'],{'max-size':'10m','max-file':'3'})
        self.assertEqual(int(db['mem_limit']),2*1024**3)

    def test_project_contract_rejects_values_without_printing_secrets(self):
        contract=self.app/'config/environment.json'
        contract.write_text(json.dumps({'php-fpm':{'TOKEN':{'required':True},'API':{'type':'url','required':True}}}))
        project=Project(self.app)
        with self.assertRaisesRegex(ValueError,'TOKEN'): validate_configuration(project)
        (self.app/'compose/local.yaml').write_text('services:\n  php-fpm:\n    environment:\n      TOKEN: private-only\n      API: secret-only\n')
        with self.assertRaises(ValueError) as failure: validate_configuration(Project(self.app))
        self.assertIn('php-fpm/API',str(failure.exception));self.assertNotIn('secret-only',str(failure.exception))

    def test_failed_preflight_never_stops_existing_containers(self):
        project=SimpleNamespace(run=Mock())
        with patch('project_policy.preflight_php',side_effect=ValueError('bad config')):
            with self.assertRaisesRegex(ValueError,'bad config'): start_project(project)
        project.run.assert_not_called()

    def test_disk_and_backup_pruning_keep_other_projects_and_explicit_files(self):
        project=Project(self.app);project.settings.update(MIN_FREE_DISK_MB='10',BACKUP_KEEP_LAST='1')
        with patch('project_storage.shutil.disk_usage',return_value=SimpleNamespace(free=9*1024**2)):
            with self.assertRaisesRegex(ValueError,'free disk'): require_space(project,self.root)
        directory=self.app/'.generated/backups';directory.mkdir(parents=True)
        old=directory/'shop-local-20260101T120000000000.sql.gz';old.write_bytes(b'old')
        newest=directory/'shop-local-20260102T120000000000.sql.gz';newest.write_bytes(b'new')
        other=directory/'shop-test-20260101T120000000000.sql.gz';other.write_bytes(b'other')
        named=directory/'important.sql.gz';named.write_bytes(b'keep')
        with contextlib.redirect_stdout(io.StringIO()): prune_backups(project)
        self.assertTrue(old.exists())
        with contextlib.redirect_stdout(io.StringIO()): prune_backups(project,True)
        self.assertFalse(old.exists())
        self.assertTrue(all(p.exists() for p in (newest,other,named)))

    def test_daily_commands_keep_arguments_and_reject_unknown_service(self):
        project=Project(self.app);project.run=Mock()
        composer(project, 'run-script "name with spaces"')
        self.assertEqual(project.run.call_args.args[0][-2:],['run-script','name with spaces'])
        logs(project,'php-fpm',True,25)
        self.assertEqual(project.run.call_args.args[0],['logs','--tail','25','--follow','php-fpm'])
        with self.assertRaisesRegex(ValueError,'not enabled'): restart(project,'not-a-service')
