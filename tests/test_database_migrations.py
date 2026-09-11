"""Git baselines, generation boundaries and safe output for Doctrine schema diffs."""
import contextlib
import io
import fcntl
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
from database_schema import export_schema, git
from database_migrations import committed_schema, definitions, generate_migration, compare_in_container
from test_database_schema import snapshot


class MigrationTest(unittest.TestCase):
    def setUp(self):
        (ROOT / '.test-work').mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.test-work')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app = self.root / 'app'
        self.config = self.app / 'database/doctrine'
        (self.config / 'versions').mkdir(parents=True)
        (self.config / 'migrations.php').write_text('<?php return [];')
        git(self.root, 'init', '-q')
        git(self.root, 'config', 'user.name', 'Migration Test')
        git(self.root, 'config', 'user.email', 'test@example.invalid')
        git(self.root, 'config', 'commit.gpgsign', 'false')
        self.schema = snapshot({'product': 'CREATE TABLE `product` (`id` int);\n'})
        self.model = {'services': {'php-doctrine-migrations': {'image': 'doctrine-test', 'volumes': [
            {'type': 'bind', 'source': str(self.config), 'target': '/tmp/doctrine-migrations/config'}]}}}
        self.project = SimpleNamespace(root=self.root, directory=self.app, environment='local',
            settings={'SCHEMA_DIRECTORY': 'app/database/schema'}, model=lambda: self.model,
            capture=lambda args: 'container-id')
        with patch('database_schema.read_schema', return_value=self.schema), contextlib.redirect_stdout(io.StringIO()):
            export_schema(self.project)
        git(self.root, 'add', 'app')
        git(self.root, 'commit', '-qm', 'baseline')
        self.changed = snapshot({'product': 'CREATE TABLE `product` (`id` int, `code` text);\n'})
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def generate(self, files=None, result=None, reference='HEAD'):
        def worker(*args):
            payload = args[-1]
            return result or {'sql_count': 1, 'directory': 'versions',
                              'code': '<?php\n// Schema diff: ' + payload['fingerprint'] + '\n'}
        with patch('database_migrations.read_schema', return_value=files or self.changed), \
                patch('database_migrations.mysql_query', return_value='utf8mb4\tutf8mb4_unicode_ci\n'), \
                patch('database_migrations.docker', return_value='sha256:fixture'), \
                patch('database_migrations.compare_in_container', side_effect=worker):
            return generate_migration(self.project, reference)

    def test_baseline_is_commit_not_index_or_worktree(self):
        before = committed_schema(self.app / 'database/schema', 'HEAD')
        path = self.app / 'database/schema/table-product.sql'
        path.write_text('staged user edit')
        git(self.root, 'add', str(path))
        path.write_text('unstaged user edit')
        self.assertEqual(committed_schema(self.app / 'database/schema', 'HEAD'), before)
        self.assertEqual(before[2], definitions(self.schema))
        with self.assertRaises(ValueError): committed_schema(self.app / 'database/schema', '--all')

    def test_generation_and_retry_preserve_schema_and_index(self):
        before = git(self.root, 'status', '--porcelain')
        path = self.generate()
        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_uid, self.app.stat().st_uid)
        self.assertEqual(self.generate(), path)
        self.assertEqual(len(list((self.config / 'versions').glob('*.php'))), 1)
        self.assertEqual(git(self.root, 'diff', '--', 'app/database/schema'), b'')
        self.assertEqual(git(self.root, 'diff', '--cached'), b'')
        self.assertEqual(before, b'')

    def test_pending_migration_blocks_overlapping_generation(self):
        draft = self.config / 'versions/Version20000101000000.php'
        draft.write_text('<?php // existing draft')
        with self.assertRaisesRegex(ValueError, 'Migration files differ'):
            self.generate()
        self.assertEqual(list((self.config / 'versions').iterdir()), [draft])

    def test_no_change_and_nonlocal_generation(self):
        self.assertIsNone(self.generate(result={'sql_count': 0, 'directory': 'versions'}))
        self.assertEqual(list((self.config / 'versions').iterdir()), [])
        for environment in ('test', 'stage', 'prod'):
            self.project.environment = environment
            with patch('database_migrations.read_schema') as read, self.assertRaisesRegex(ValueError, 'ENV=local'):
                generate_migration(self.project)
            read.assert_not_called()

    def test_missing_manifest_and_symlink_git_entry_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Baseline schema is missing'): definitions({})
        path = self.app / 'database/schema/table-product.sql'
        path.unlink(); path.symlink_to('schema.manifest.json')
        git(self.root, 'add', str(path)); git(self.root, 'commit', '-qm', 'invalid schema')
        with self.assertRaisesRegex(ValueError, 'regular files'):
            committed_schema(self.app / 'database/schema', 'HEAD')

    def test_concurrent_generation_cannot_create_duplicate_migrations(self):
        directory = self.app / '.generated'
        directory.mkdir()
        with (directory / 'doctrine-diff.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'already running'):
                self.generate()
        self.assertEqual(list((self.config / 'versions').iterdir()), [])

    def test_output_and_config_symlinks_cannot_escape_project(self):
        with self.assertRaisesRegex(ValueError, 'inside Doctrine'):
            self.generate(result={'sql_count': 1, 'directory': '../outside', 'code': '<?php'})
        (self.config / 'versions').rmdir()
        (self.config / 'versions').symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.generate()

    def test_worker_failure_always_removes_only_owned_containers(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            if args[1:3] == ['run', '--rm']:
                raise ValueError('worker failed')
            return SimpleNamespace(returncode=0, stdout='ok', stderr=b'')
        with patch('database_migrations.docker', side_effect=['started', ValueError('worker failed')]), \
                patch('database_migrations.subprocess.run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'worker failed'):
                compare_in_container('db-image', 'php-image', self.config, {})
        self.assertEqual(len(calls), 2)
        for args in calls:
            self.assertEqual(args[:4], ['docker', 'rm', '-f', '--volumes'])
            self.assertTrue(args[-1].startswith('setup-schema-diff-'))


if __name__ == '__main__': unittest.main()
