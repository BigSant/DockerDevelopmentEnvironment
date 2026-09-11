"""Actual Make/Compose/MySQL fixture loading in a disposable isolated database."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_schema import mysql_query
from project import Project


@unittest.skipUnless(os.environ.get("SETUP_MYSQL_FIXTURES_INTEGRATION") == "1",
                     "set SETUP_MYSQL_FIXTURES_INTEGRATION=1 to run a disposable MySQL container")
class FixturesIntegrationTest(unittest.TestCase):
    def test_make_loads_selected_repeatable_data_and_appends_after_import_hooks(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fixtures-integration-", dir=scratch) as temporary:
            root = Path(temporary)
            directory = root / "docker"
            directory.mkdir()
            (directory / ".env").write_text(f"PROJECT_NAME={root.name}\nFIXTURES_DIRECTORY=database/fixtures\nPOST_IMPORT_SQL_DIRECTORY=database/hooks\n")
            (directory / ".env.local").write_text("DATABASE_NAME=example\nDATABASE_USER=example\nDATABASE_PASSWORD=fixture-test-only\nCOMPOSE_PROFILES=\n")
            (directory / "Makefile").write_text(f"PROJECT_DOCKER_DIRECTORY := $(CURDIR)\nSETUP_DIRECTORY := {ROOT}\ninclude $(SETUP_DIRECTORY)/docker/project.mk\n")
            (directory / "compose.yaml").write_text("""services:
  database:
    image: __DB_TEST_IMAGE__
    network_mode: none
    tmpfs:
      - /var/lib/mysql
    environment:
      MYSQL_DATABASE: ${DATABASE_NAME}
      MYSQL_USER: ${DATABASE_USER}
      MYSQL_PASSWORD: ${DATABASE_PASSWORD}
      MYSQL_ROOT_PASSWORD: fixture-root-test-only
""".replace("__DB_TEST_IMAGE__", os.environ.get("SETUP_DB_TEST_IMAGE", "mysql-local-8.4.0:latest")))
            # Exercise the shared first-start hook even with an official CI image.
            # MYSQL_HOST previously broke its explicit socket connection.
            engine = 'mariadb' if 'mariadb' in os.environ.get('SETUP_DB_TEST_IMAGE', '') else 'mysql'
            with (directory / 'compose.yaml').open('a') as compose:
                compose.write('      MYSQL_HOST: 127.0.0.1\n')
                compose.write(f'    volumes:\n      - {ROOT}/docker/docker/{engine}/conf/init.sh:/docker-entrypoint-initdb.d/01-init-app-user.sh:ro\n')
            fixtures = root / "database/fixtures"
            for group, identifier in (("common", 1), ("local", 2), ("test", 3)):
                (fixtures / group).mkdir(parents=True)
                (fixtures / group / "010-data.sql").write_text(
                    f"INSERT INTO seed VALUES ({identifier}, '{group}') ON DUPLICATE KEY UPDATE value='{group}';\n")
            hooks = root / "database/hooks/local"
            hooks.mkdir(parents=True)
            (hooks / "010-hook.sql").write_text("UPDATE seed SET value='hook' WHERE id=1;\n")
            dump = root / "dump.sql"
            dump.write_text("DELETE FROM seed; INSERT INTO seed VALUES (1, 'dump');\n")
            project = Project(directory)
            def make(*args):
                return subprocess.run(["make", "--no-print-directory", *args], cwd=directory,
                                      capture_output=True, text=True)
            def rows():
                return mysql_query(project, "SELECT id, value FROM seed ORDER BY id;").strip()
            try:
                project.run(["up", "-d", "--no-build", "--pull", "never"])
                deadline = time.monotonic() + 90
                while True:
                    try:
                        mysql_query(project, "SELECT 1;")
                        break
                    except ValueError:
                        if time.monotonic() > deadline:
                            raise
                        time.sleep(0.5)
                mysql_query(project, "CREATE TABLE seed (id int PRIMARY KEY, value varchar(64));")
                grants = mysql_query(project, 'SHOW GRANTS;')
                self.assertIn('ON *.*', grants)
                result = make("db-fixtures-plan", "set=local")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rows(), "")
                for _ in range(2):
                    result = make("db-fixtures-load", "set=local")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(rows(), "1\tcommon\n2\tlocal")
                mysql_query(project, "DELETE FROM seed;")
                result = make("db-fixtures-load", "set=test", "ENV=local")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rows(), "1\tcommon\n3\ttest")
                # Invalid fixture selection must fail before the dump erases data.
                for selection in ("db-fixtures=missing", "fixtures=local"):
                    result = make("db-import", f"file={dump}", selection)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(rows(), "1\tcommon\n3\ttest")
                result = make("db-import-plan", f"file={dump}", "db-fixtures=local")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(result.stdout.index("010-hook.sql"), result.stdout.index("common/010-data.sql"))
                self.assertEqual(rows(), "1\tcommon\n3\ttest")
                result = make("db-import", f"file={dump}", "db-fixtures=local")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rows(), "1\tcommon\n2\tlocal")
                backup = root / 'roundtrip.sql.gz'
                result = make('db-backup', f'file={backup}')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
                broken = root/'truncated.sql.gz'
                broken.write_bytes(backup.read_bytes()[:25])
                result = make('db-import', f'file={broken}', 'backup=1')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(rows(), '1\tcommon\n2\tlocal')
                result = make('db-backup', f'file={backup}')
                self.assertNotEqual(result.returncode, 0)
                mysql_query(project, 'DELETE FROM seed;')
                result = make('db-import', f'file={backup}', 'backup=1')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(rows(), '1\thook\n2\tlocal')
                self.assertEqual(len(list((directory/'.generated/backups').glob('*.sql.gz'))), 1)
                (fixtures / "test/020-fail.sql").write_text("INSERT INTO missing_table VALUES (1);\n")
                (fixtures / "test/030-never.sql").write_text("INSERT INTO seed VALUES (99, 'must not run');\n")
                result = make("db-fixtures-load", "set=test")
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("99\t", rows())
            finally:
                project.run(["down", "--volumes", "--remove-orphans"])


if __name__ == "__main__":
    unittest.main()
