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
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" "$(command -v mariadb || command -v mysql)" --protocol=socket -uroot "$MYSQL_DATABASE" '
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
                            "-e", "DATABASE_NAME=example", "-e", "DOMAIN=shop.local", "-e", "DATABASE_BUFFER_POOL=128M", "-e", "DATABASE_PERFORMANCE_SCHEMA=off",
                            "-v", f"{config}:/etc/mysql/project.d:ro", IMAGE],
                           check=True, capture_output=True)
            self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)

            def sql(query):
                return subprocess.run(["docker", "exec", name, "sh", "-c",
                                       f'MYSQL_PWD=root-test-only "$(command -v mariadb || command -v mysql)" -uroot -N -e "{query}" example'],
                                      capture_output=True, text=True)
            deadline = time.time() + 120
            # The final server (not the init server) answers on TCP once initialisation finished.
            while time.time() < deadline:
                ready = subprocess.run(["docker", "exec", name, "sh", "-c", 'exec "$(command -v mariadb-admin || command -v mysqladmin)" ping -h 127.0.0.1 --silent'],
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


@unittest.skipUnless(IMAGE, "Set SETUP_MYSQL_IMAGE to a built setup MySQL/MariaDB image")
class DatabaseGrantsIntegrationTest(unittest.TestCase):
    def start(self, root_password, app_password="app-test-only"):
        name = f"setup-db-grants-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--tmpfs", "/var/lib/mysql",
                        "-e", f"MYSQL_ROOT_PASSWORD={root_password}", "-e", "MYSQL_DATABASE=shop",
                        "-e", "MYSQL_USER=shop", "-e", f"MYSQL_PASSWORD={app_password}", "-e", "DATABASE_BUFFER_POOL=128M", "-e", "DATABASE_PERFORMANCE_SCHEMA=off",
                        IMAGE], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.sql(name, "root", root_password, "SELECT 1").returncode == 0 and \
                    subprocess.run(["docker", "exec", name, "sh", "-c", 'exec "$(command -v mariadb-admin || command -v mysqladmin)" ping -h 127.0.0.1 --silent'],
                                   capture_output=True).returncode == 0:
                return name
            time.sleep(2)
        self.fail("database did not start")

    def sql(self, name, user, password, query, env=()):
        return subprocess.run(["docker", "exec", *env, name, "sh", "-c",
                               f'MYSQL_PWD="{password}" "$(command -v mariadb || command -v mysql)" --protocol=TCP -h127.0.0.1 -u{user} -N -e "{query}"'],
                              capture_output=True, text=True)

    def test_new_database_scopes_the_application_user(self):
        name = self.start("root-test-only")
        self.assertNotEqual(self.sql(name, "shop", "app-test-only", "CREATE USER x IDENTIFIED BY 'x'").returncode, 0)
        self.assertNotEqual(self.sql(name, "shop", "app-test-only", "CREATE DATABASE other").returncode, 0)
        self.assertEqual(self.sql(name, "shop", "app-test-only", "CREATE TABLE shop.t (id INT)").returncode, 0)
        self.assertNotEqual(self.sql(name, "root", "app-test-only", "SELECT 1").returncode, 0)

    def test_restrict_grants_works_through_the_application_user_when_root_is_unknown(self):
        import sys
        sys.path.insert(0, str(ROOT / "docker"))
        from database_grants import RESTRICT
        name = self.start("unknown-historic-root")
        self.sql(name, "root", "unknown-historic-root", "GRANT ALL PRIVILEGES ON *.* TO 'shop'@'%' WITH GRANT OPTION")
        result = subprocess.run(["docker", "exec", "-e", "MYSQL_ROOT_PASSWORD=new-root-test-only", name, "sh", "-c", RESTRICT],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("changed as shop", result.stdout)
        self.assertEqual(self.sql(name, "root", "new-root-test-only", "SELECT 1").returncode, 0)
        self.assertNotEqual(self.sql(name, "shop", "app-test-only", "CREATE DATABASE after_restrict").returncode, 0)
        self.assertEqual(self.sql(name, "shop", "app-test-only", "CREATE TABLE shop.t3 (id INT)").returncode, 0)

    def test_restrict_grants_narrows_a_pre_v2_database(self):
        import sys
        sys.path.insert(0, str(ROOT / "docker"))
        from database_grants import RESTRICT
        # Pre-v2 layout: root shares the application password; the application has everything.
        name = self.start("app-test-only")
        self.sql(name, "root", "app-test-only", "GRANT ALL PRIVILEGES ON *.* TO 'shop'@'%' WITH GRANT OPTION")
        self.assertEqual(self.sql(name, "shop", "app-test-only", "CREATE DATABASE before_restrict").returncode, 0)
        env = ["-e", "MYSQL_ROOT_PASSWORD=new-root-test-only"]
        for _ in range(2):  # idempotent
            result = subprocess.run(["docker", "exec", *env, name, "sh", "-c", RESTRICT], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sql(name, "root", "new-root-test-only", "SELECT 1").returncode, 0)
        self.assertNotEqual(self.sql(name, "root", "app-test-only", "SELECT 1").returncode, 0)
        self.assertNotEqual(self.sql(name, "shop", "app-test-only", "CREATE DATABASE after_restrict").returncode, 0)
        self.assertEqual(self.sql(name, "shop", "app-test-only", "CREATE TABLE shop.t2 (id INT)").returncode, 0)
