"""Real Doctrine generation and migration execution on disposable MySQL/MariaDB."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
from database_schema import export_schema, git, mysql_query, read_schema
from database_migrations import generate_migration, compare_in_container
from project import Project


@unittest.skipUnless(os.environ.get('SETUP_DOCTRINE_TEST_IMAGE'), 'set SETUP_DOCTRINE_TEST_IMAGE for disposable Doctrine integration')
class DoctrineIntegrationTest(unittest.TestCase):
    def test_unsupported_table_options_do_not_silently_generate_an_empty_migration(self):
        (ROOT / '.test-work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.test-work') as temporary:
            config = Path(temporary)
            (config / 'migrations.php').write_bytes((ROOT / 'templates/grouped/database/doctrine/migrations.php').read_bytes())
            payload = {'before': {'t': 'CREATE TABLE t (id int) ENGINE=InnoDB ROW_FORMAT=COMPACT;'},
                       'after': {'t': 'CREATE TABLE t (id int) ENGINE=InnoDB ROW_FORMAT=DYNAMIC;'},
                       'charset': 'utf8mb4', 'collation': 'utf8mb4_unicode_ci',
                       'class': 'Version20260101000000', 'fingerprint': 'a' * 64}
            with self.assertRaisesRegex(ValueError, 'verifying generated SQL'):
                compare_in_container(os.environ.get('SETUP_DB_TEST_IMAGE', 'mysql:5.7'),
                                     os.environ['SETUP_DOCTRINE_TEST_IMAGE'], config, payload)

    def test_generate_verify_apply_and_repeat_without_changing_source_database(self):
        (ROOT / '.test-work').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='doctrine-integration-', dir=ROOT / '.test-work') as temporary:
            root = Path(temporary)
            app = root / 'app'
            config = app / 'database/doctrine'
            (config / 'versions').mkdir(parents=True)
            (app / 'env').mkdir()
            (app / 'compose').mkdir()
            (app / 'Makefile').write_bytes((ROOT / 'templates/project/Makefile').read_bytes())
            (app / 'env/common.env').write_text(f'PROJECT_NAME={root.name}\nSCHEMA_DIRECTORY=app/database/schema\n')
            (app / 'env/local.env').write_text('DATABASE_NAME=example\nDATABASE_USER=example\nDATABASE_PASSWORD=doctrine-test-only\n')
            (config / 'migrations.php').write_bytes((ROOT / 'templates/grouped/database/doctrine/migrations.php').read_bytes())
            (config / 'connection.php').write_text("<?php return ['driver'=>'pdo_mysql','host'=>'127.0.0.1','user'=>'example','password'=>'doctrine-test-only','dbname'=>'example'];")
            model = {'services': {
                'database': {'image': os.environ.get('SETUP_DB_TEST_IMAGE', 'mysql:5.7'),
                             'network_mode': 'none', 'mem_limit': '512m', 'tmpfs': ['/var/lib/mysql', '/docker-entrypoint-initdb.d'],
                             'command': ['--innodb-buffer-pool-size=64M', '--performance-schema=OFF'],
                             'environment': {'MYSQL_DATABASE': 'example', 'MYSQL_USER': 'example',
                                             'MYSQL_PASSWORD': 'doctrine-test-only', 'MYSQL_ROOT_PASSWORD': 'root-test-only'}},
                'php-doctrine-migrations': {'image': os.environ['SETUP_DOCTRINE_TEST_IMAGE'], 'profiles': ['doctrine'],
                    'network_mode': 'service:database', 'entrypoint': ['doctrine-migrations',
                        '--configuration=/tmp/doctrine-migrations/config/migrations.php',
                        '--db-configuration=/tmp/doctrine-migrations/config/connection.php'],
                    'volumes': [str(config) + ':/tmp/doctrine-migrations/config:ro']}}}
            (app / 'compose/base.yaml').write_text(json.dumps(model))
            git(root, 'init', '-q'); git(root, 'config', 'user.name', 'Doctrine Test')
            git(root, 'config', 'user.email', 'doctrine@example.invalid'); git(root, 'config', 'commit.gpgsign', 'false')
            project = Project(app)
            try:
                project.run(['up', '-d', '--no-build', '--pull', 'never'])
                deadline = time.monotonic() + 90
                while True:
                    try:
                        mysql_query(project, 'SELECT 1;')
                        break
                    except ValueError:
                        if time.monotonic() >= deadline: raise
                        time.sleep(0.5)
                mysql_query(project, "CREATE TABLE product (id int PRIMARY KEY, name varchar(32), state enum('new','old') DEFAULT 'new');\nINSERT INTO product VALUES (1, 'keep this row', 'new');")
                with contextlib.redirect_stdout(io.StringIO()): export_schema(project)
                git(root, 'add', 'app'); git(root, 'commit', '-qm', 'baseline'); git(root, 'tag', 'v1')
                baseline = read_schema(project)
                mysql_query(project, "CREATE TABLE supplier (id int PRIMARY KEY);\nALTER TABLE product ADD supplier_id int DEFAULT NULL, ADD supplier_code varchar(64) DEFAULT NULL, ADD UNIQUE KEY product_code (supplier_code), ADD CONSTRAINT fk_supplier FOREIGN KEY (supplier_id) REFERENCES supplier(id);")
                target = read_schema(project)
                output = io.StringIO()
                generated = subprocess.run(['make', '-s', '-C', str(app), f'SETUP_DIRECTORY={ROOT}',
                                            'doctrine-diff', 'ref=v1'], capture_output=True, text=True, timeout=240)
                self.assertEqual(generated.returncode, 0, generated.stderr)
                migration, = (config / 'versions').glob('Version*.php')
                with contextlib.redirect_stdout(output):
                    self.assertEqual(generate_migration(project, 'v1'), migration)
                self.assertEqual(read_schema(project), target)
                self.assertEqual(mysql_query(project, 'SELECT name FROM product WHERE id=1;').strip(), 'keep this row')
                self.assertEqual(git(root, 'diff', '--', 'app/database/schema'), b'')
                code = migration.read_text()
                self.assertIn('supplier_code', code)
                self.assertNotIn("addSql('DROP TABLE doctrine_migration_versions", code)
                lint = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--mount',
                    f'type=bind,source={migration},target=/migration.php,readonly', '--entrypoint', 'php',
                    os.environ['SETUP_DOCTRINE_TEST_IMAGE'], '-l', '/migration.php'], capture_output=True, text=True)
                self.assertEqual(lint.returncode, 0, lint.stderr)
                # Restore only this disposable test DB, then execute the actual generated Doctrine class.
                mysql_query(project, 'SET FOREIGN_KEY_CHECKS=0; DROP TABLE product; DROP TABLE supplier;\n' + baseline['table-product.sql'].decode())
                project.run(['run', '--rm', '--no-deps', 'php-doctrine-migrations', 'migrate', '--no-interaction'], profiles='doctrine')
                actual = read_schema(project)
                actual.pop('table-doctrine_migration_versions.sql')
                actual.pop('schema.manifest.json')
                expected = {name: ddl for name, ddl in target.items() if name != 'schema.manifest.json'}
                self.assertEqual(actual, expected)
                # Commit result and its migration together; the metadata table does not generate another migration.
                with contextlib.redirect_stdout(io.StringIO()): export_schema(project)
                git(root, 'add', 'app'); git(root, 'commit', '-qm', 'schema and migration')
                with contextlib.redirect_stdout(io.StringIO()): self.assertIsNone(generate_migration(project))
            finally:
                project.run(['down', '--volumes', '--remove-orphans'])


if __name__ == '__main__': unittest.main()
