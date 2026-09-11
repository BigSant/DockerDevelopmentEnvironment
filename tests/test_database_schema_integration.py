"""Opt-in real MySQL + Compose + Git hook test, using only a disposable DB."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_schema import check_schema, export_schema, git, install_schema_hook, mysql_query, read_schema
from project import Project


@unittest.skipUnless(os.environ.get("SETUP_MYSQL_SCHEMA_INTEGRATION") == "1",
                     "set SETUP_MYSQL_SCHEMA_INTEGRATION=1 to run a disposable MySQL container")
class SchemaIntegrationTest(unittest.TestCase):
    def test_real_commit_blocks_drift_unstaged_export_and_offline_database(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="schema-integration-", dir=scratch) as temporary:
            root = Path(temporary)
            directory = root / "docker"
            directory.mkdir()
            (directory / ".env").write_text(f"PROJECT_NAME={root.name}\nSCHEMA_DIRECTORY=database/schema\n")
            (directory / ".env.local").write_text("DATABASE_NAME=example\nDATABASE_USER=example\nDATABASE_PASSWORD=schema-test-only\nCOMPOSE_PROFILES=\n")
            # No published ports, no host DB bind mount, no access to other project networks.
            (directory / "compose.yaml").write_text("""services:
  database:
    image: mysql-local-8.4.0:latest
    network_mode: none
    tmpfs:
      - /var/lib/mysql
    environment:
      MYSQL_DATABASE: ${DATABASE_NAME}
      MYSQL_USER: ${DATABASE_USER}
      MYSQL_PASSWORD: ${DATABASE_PASSWORD}
      MYSQL_ROOT_PASSWORD: schema-root-test-only
""")
            git(root, "init", "-q")
            git(root, "config", "user.name", "Schema Test")
            git(root, "config", "user.email", "schema-test@example.invalid")
            git(root, "config", "commit.gpgsign", "false")
            project = Project(directory)
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
                mysql_query(project, """CREATE TABLE parent (id int NOT NULL PRIMARY KEY);
CREATE TABLE product (
  id int NOT NULL AUTO_INCREMENT PRIMARY KEY,
  parent_id int,
  code varchar(32) DEFAULT 'AUTO_INCREMENT=123',
  CONSTRAINT fk_parent FOREIGN KEY (parent_id) REFERENCES parent(id),
  UNIQUE KEY product_code (code)
) ENGINE=InnoDB;
""")
                with contextlib.redirect_stdout(io.StringIO()):
                    export_schema(project)
                    git(root, "add", "database/schema")
                    install_schema_hook(project)

                def commit():
                    return subprocess.run(["git", "commit", "--allow-empty", "-m", "schema integration"],
                                          cwd=root, capture_output=True, text=True)

                result = commit()
                self.assertEqual(result.returncode, 0, result.stderr)
                before = read_schema(project)
                mysql_query(project, "INSERT INTO product (id, code) VALUES (501, 'sample');")
                self.assertEqual(read_schema(project), before, "row data and next AUTO_INCREMENT must not create drift")
                mysql_query(project, "ALTER TABLE product ADD COLUMN description text;")
                result = commit()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("different: table-product.sql", result.stderr)
                with contextlib.redirect_stdout(io.StringIO()):
                    export_schema(project)
                result = commit()
                self.assertNotEqual(result.returncode, 0, "export without git add must remain blocked")
                git(root, "add", "-A", "database/schema")
                result = commit()
                self.assertEqual(result.returncode, 0, result.stderr)
                project.run(["stop", "database"])
                result = commit()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Cannot read DB schema", result.stderr)
            finally:
                project.run(["down", "--volumes", "--remove-orphans"])


if __name__ == "__main__":
    unittest.main()
