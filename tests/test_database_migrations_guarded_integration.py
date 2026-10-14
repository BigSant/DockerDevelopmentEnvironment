"""Guarded migration drafts applied to a disposable MySQL/MariaDB container of the setup image."""

import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_migrations_guarded import OPEN, draft_text, split_rollback
from database_client import unescape_batch
from database_schema import normalize_ddl

IMAGES = [image for image in (os.environ.get("SETUP_MYSQL_IMAGE"), os.environ.get("SETUP_MARIADB_IMAGE")) if image]
PASSWORD = "migration-test-only"
BEFORE = """CREATE TABLE product (id int NOT NULL AUTO_INCREMENT, code varchar(32) DEFAULT NULL, note text,
  PRIMARY KEY (id), KEY code (code)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""
AFTER = """CREATE TABLE product (id int NOT NULL AUTO_INCREMENT, code varchar(64) DEFAULT NULL,
  name varchar(128) NOT NULL DEFAULT 'it''s', note text, PRIMARY KEY (id), KEY name (name)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE feed (id int NOT NULL, url varchar(255) NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""
# What a module install script may already have done before the migration runs.
MODULE_INSTALL = """ALTER TABLE product ADD COLUMN name varchar(128) NOT NULL DEFAULT 'it''s' AFTER code;
CREATE TABLE feed (id int NOT NULL, url varchar(255) NOT NULL, PRIMARY KEY (id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"""


@unittest.skipUnless(IMAGES, "Set SETUP_MYSQL_IMAGE and/or SETUP_MARIADB_IMAGE to built setup database images")
class GuardedMigrationIntegrationTest(unittest.TestCase):
    def start(self, image):
        name = f"setup-migrate-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--tmpfs", "/var/lib/mysql",
                        "-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}", "-e", f"MARIADB_ROOT_PASSWORD={PASSWORD}",
                        "-e", "MYSQL_DATABASE=shop", "-e", "MYSQL_USER=shop", "-e", "MYSQL_PASSWORD=shop-test-only",
                        "-e", "DATABASE_BUFFER_POOL=128M", "-e", "DATABASE_PERFORMANCE_SCHEMA=off", image], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if subprocess.run(["docker", "exec", name, "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"],
                              capture_output=True).returncode == 0 and self.sql(name, "", "SELECT 1").returncode == 0:
                return name
            time.sleep(2)
        self.fail("database did not start")

    def sql(self, name, database, text):
        return subprocess.run(["docker", "exec", "-i", "-e", f"MYSQL_PWD={PASSWORD}", name, "sh", "-c",
                               'c=$(command -v mariadb || command -v mysql); exec "$c" -h127.0.0.1 -uroot -N -B "$@"',
                               "sql", *([database] if database else [])],
                              input=text, capture_output=True, text=True)

    def run_sql(self, name, database, text):
        result = self.sql(name, database, text)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def schema(self, name, database):
        tables = self.run_sql(name, database, "SHOW TABLES;").split()
        definitions = {}
        for table in tables:
            row = self.run_sql(name, database, f"SHOW CREATE TABLE `{table}`;").rstrip("\n").split("\t", 1)[1]
            definitions[table] = normalize_ddl(unescape_batch(row))
        return definitions

    def test_draft_applies_twice_and_after_a_module_install(self):
        for image in IMAGES:
            with self.subTest(image=image):
                name = self.start(image)
                for database, script in (("ref_before", BEFORE), ("ref_after", AFTER), ("target", BEFORE),
                                         ("installed", BEFORE + "\n" + MODULE_INSTALL)):
                    self.run_sql(name, "", f"CREATE DATABASE {database};")
                    self.run_sql(name, database, script)
                before, after = self.schema(name, "ref_before"), self.schema(name, "ref_after")
                forward, back = split_rollback(draft_text(before, after))
                self.assertIsNone(OPEN.search(forward))
                for database in ("target", "installed"):
                    self.run_sql(name, database, forward)
                    self.assertEqual(self.schema(name, database), after, database)
                    self.run_sql(name, database, forward)
                    self.assertEqual(self.schema(name, database), after, f"{database}: second run changes nothing")
                # The way back restores the structure, and running it twice changes nothing either.
                self.run_sql(name, "target", back)
                self.assertEqual(self.schema(name, "target"), before, "rollback")
                self.run_sql(name, "target", back)
                self.assertEqual(self.schema(name, "target"), before, "rollback twice")


if __name__ == "__main__":
    unittest.main()
