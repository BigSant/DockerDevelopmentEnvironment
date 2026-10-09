"""A real mysqldump filtered by the registry skip rule and loaded into a disposable database."""

import io
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_import import filter_dump

IMAGE = os.environ.get("SETUP_MYSQL_IMAGE")
PASSWORD = "import-test-only"


@unittest.skipUnless(IMAGE, "Set SETUP_MYSQL_IMAGE to a built setup MySQL/MariaDB image")
class SkippedTableImportIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.name = f"setup-import-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.name, "--network", "none", "--tmpfs", "/var/lib/mysql",
                        "-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}", "-e", "MYSQL_DATABASE=shop", "-e", "MYSQL_USER=shop",
                        "-e", "MYSQL_PASSWORD=shop-test-only", "-e", "DATABASE_BUFFER_POOL=128M", IMAGE],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.name], capture_output=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if subprocess.run(["docker", "exec", self.name, "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"],
                              capture_output=True).returncode == 0 and self.client("", b"SELECT 1").returncode == 0:
                return
            time.sleep(2)
        self.fail("database did not start")

    def client(self, database, data, program="mysql"):
        script = (f'c=$(command -v {"mariadb-dump || command -v mysqldump" if program == "dump" else "mariadb || command -v mysql"}); '
                  'exec "$c" -h127.0.0.1 -uroot "$@"')
        return subprocess.run(["docker", "exec", "-i", "-e", f"MYSQL_PWD={PASSWORD}", self.name, "sh", "-c", script, "sql",
                               *(["-N", "-B"] if program == "mysql" else []), *([database] if database else [])],
                              input=data, capture_output=True)

    def query(self, database, sql):
        result = self.client(database, sql.encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode().split()

    def test_skipped_table_keeps_structure_without_data_and_full_imports_it(self):
        self.query("", "CREATE DATABASE source;")
        self.query("source", "CREATE TABLE ps_orders (id int PRIMARY KEY); INSERT INTO ps_orders VALUES (1),(2);"
                             "CREATE TABLE ps_connections (id int PRIMARY KEY, page text);"
                             "INSERT INTO ps_connections VALUES (1, 'a;\\nb'),(2, 'INSERT INTO `ps_orders` VALUES (9);');")
        dump = self.client("source", b"", program="dump")
        self.assertEqual(dump.returncode, 0, dump.stderr)
        for database, skip in (("filtered", lambda table: table == "ps_connections"), ("full", lambda table: False)):
            out = io.BytesIO()
            skipped = filter_dump(io.BytesIO(dump.stdout), out, skip)
            self.query("", f"CREATE DATABASE {database};")
            loaded = self.client(database, out.getvalue())
            self.assertEqual(loaded.returncode, 0, loaded.stderr)
            self.assertEqual(self.query(database, "SELECT COUNT(*) FROM ps_orders;"), ["2"])
            expected = "0" if database == "filtered" else "2"
            self.assertEqual(self.query(database, "SELECT COUNT(*) FROM ps_connections;"), [expected])
            self.assertEqual(skipped, {"ps_connections"} if database == "filtered" else set())


if __name__ == "__main__":
    unittest.main()
