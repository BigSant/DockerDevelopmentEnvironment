"""Project MySQL drop-ins in a disposable container of the setup MySQL image."""

import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("SETUP_MYSQL_IMAGE")


@unittest.skipUnless(IMAGE, "Set SETUP_MYSQL_IMAGE to a built setup MySQL/MariaDB image")
class DatabaseProjectInitIntegrationTest(unittest.TestCase):
    def test_init_d_runs_rendered_sql_and_scripts_once_and_cnf_drop_ins_apply(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="db-init-", dir=scratch) as temporary:
            config = Path(temporary) / "mysql"
            (config / "init.d").mkdir(parents=True)
            (config / "init.d/010-table.sql.template").write_text(
                "CREATE TABLE ${DATABASE_NAME}_marker (domain VARCHAR(64));\n"
                "INSERT INTO ${DATABASE_NAME}_marker VALUES ('${DOMAIN}');\n")
            (config / "init.d/020-script.sh").write_text(
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket -uroot "$MYSQL_DATABASE" '
                '-e "INSERT INTO example_marker VALUES (\'from-script\')"\n')
            (config / "zz-test.cnf").write_text("[mysqld]\nmax_connections=321\n")
            os.chmod(temporary, 0o755)
            for path in config.rglob("*"):
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            name = f"setup-db-init-{uuid.uuid4().hex[:8]}"
            subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none",
                            "--tmpfs", "/var/lib/mysql",
                            "-e", "MYSQL_ROOT_PASSWORD=root-test-only", "-e", "MYSQL_DATABASE=example",
                            "-e", "MYSQL_USER=example", "-e", "MYSQL_PASSWORD=example-test-only",
                            "-e", "DATABASE_NAME=example", "-e", "DOMAIN=shop.local", "-e", "DATABASE_BUFFER_POOL=128M",
                            "-v", f"{config}:/etc/mysql/project.d:ro", IMAGE],
                           check=True, capture_output=True)
            self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)

            def sql(query):
                return subprocess.run(["docker", "exec", name, "sh", "-c",
                                       f'MYSQL_PWD=root-test-only mysql -uroot -N -e "{query}" example'],
                                      capture_output=True, text=True)
            deadline = time.time() + 120
            # The final server (not the init server) answers on TCP once initialisation finished.
            while time.time() < deadline:
                ready = subprocess.run(["docker", "exec", name, "mysqladmin", "ping", "-h", "127.0.0.1", "--silent"],
                                       capture_output=True)
                if ready.returncode == 0 and sql("SELECT 1").returncode == 0:
                    break
                time.sleep(2)
            else:
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                self.fail("database did not start:\n" + logs.stdout[-2000:] + logs.stderr[-2000:])
            rows = sql("SELECT domain FROM example_marker ORDER BY domain").stdout.split()
            self.assertEqual(rows, ["from-script", "shop.local"])
            self.assertEqual(sql("SELECT @@max_connections").stdout.strip(), "321")
