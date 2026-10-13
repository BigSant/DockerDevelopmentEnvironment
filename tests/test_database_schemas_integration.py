"""Extra schemas in a disposable container of the setup MySQL/MariaDB image (worktree first-start hook)."""

import contextlib
import gzip
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_backup import backup_database
from database_grants import RESTRICT
from database_import import import_database, plan_import

IMAGE = os.environ.get("SETUP_MYSQL_IMAGE")
ENGINE = "mariadb" if "mariadb" in (IMAGE or "") else "mysql"
ROOT_PASSWORD = "root-test-only"
APP_PASSWORD = "app-test-only"
EXTRAS = "shop_graph shop_git"


@unittest.skipUnless(IMAGE, "Set SETUP_MYSQL_IMAGE to a built setup MySQL/MariaDB image")
class ExtraSchemaIntegrationTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="db-schemas-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        # The image may predate this change: run the worktree's first-start hook in its place.
        self.init = self.directory / "init.sh"
        shutil.copy(ROOT / f"docker/docker/{ENGINE}/conf/init.sh", self.init)
        self.init.chmod(0o755)
        os.chmod(self.directory, 0o755)

    def start(self, extras=EXTRAS, root_password=ROOT_PASSWORD, database="shop", tests=""):
        self.name = f"setup-db-schemas-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.name, "--network", "none", "--tmpfs", "/var/lib/mysql",
                        "-e", f"MYSQL_ROOT_PASSWORD={root_password}", "-e", f"MYSQL_DATABASE={database}", "-e", "MYSQL_USER=shop",
                        "-e", f"MYSQL_PASSWORD={APP_PASSWORD}", "-e", f"DATABASE_EXTRA_SCHEMAS={extras}",
                        "-e", f"DATABASE_TEST_SCHEMAS={tests}",
                        "-e", "DATABASE_BUFFER_POOL=128M", "-e", "DATABASE_PERFORMANCE_SCHEMA=off",
                        "-v", f"{self.init}:/docker-entrypoint-initdb.d/01-init-app-user.sh:ro", IMAGE],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.name], capture_output=True)
        deadline = time.time() + 120
        while time.time() < deadline:
            if subprocess.run(["docker", "exec", self.name, "sh", "-c",
                               "(command -v mariadb-admin || command -v mysqladmin) >/dev/null && "
                               "$(command -v mariadb-admin || command -v mysqladmin) ping -h 127.0.0.1 --silent"],
                              capture_output=True).returncode == 0 and \
                    self.sql("root", root_password, "SELECT 1").returncode == 0:
                return
            time.sleep(2)
        logs = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        self.fail("database did not start:\n" + logs.stdout[-2000:] + logs.stderr[-2000:])

    def sql(self, user, password, query):
        return subprocess.run(["docker", "exec", "-i", "-e", f"MYSQL_PWD={password}", self.name, "sh", "-c",
                               'c=$(command -v mariadb || command -v mysql); exec "$c" --protocol=TCP -h127.0.0.1 -u"$0" -N -B',
                               user], input=query, capture_output=True, text=True)

    def app(self, query):
        return self.sql("shop", APP_PASSWORD, query)

    def root(self, query, password=ROOT_PASSWORD):
        result = self.sql("root", password, query)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.split()

    def assert_owns_listed_schemas_only(self):
        for schema in ("shop", "shop_graph", "shop_git"):
            with self.subTest(schema=schema):
                result = self.app(f"CREATE TABLE {schema}.owned (id INT); INSERT INTO {schema}.owned VALUES (1);"
                                  f"SELECT COUNT(*) FROM {schema}.owned;")
                self.assertEqual((result.returncode, result.stdout.split()), (0, ["1"]), result.stderr)
        for query in ("CREATE TABLE other.t (id INT)", "SELECT * FROM other.secret", "INSERT INTO other.secret VALUES (2)",
                      "CREATE DATABASE elsewhere", "CREATE DATABASE shopXgraph", "CREATE DATABASE shop_gitX",
                      "CREATE USER x IDENTIFIED BY 'x-test-only'", "GRANT SELECT ON shop.* TO 'shop'@'localhost'"):
            with self.subTest(query=query):
                self.assertNotEqual(self.app(query).returncode, 0, f"{query} must be refused")
        grants = self.app("SHOW GRANTS FOR CURRENT_USER()").stdout
        self.assertNotIn("GRANT OPTION", grants)
        self.assertNotIn("ALL PRIVILEGES ON *.*", grants)
        self.assertEqual(self.root("SELECT COUNT(*) FROM other.secret"), ["1"])

    def test_first_start_creates_the_listed_schemas_for_the_application_user_only(self):
        self.start()
        self.assertTrue({"shop", "shop_graph", "shop_git"} <= set(self.root("SHOW DATABASES")))
        self.root("CREATE DATABASE other; CREATE TABLE other.secret (id INT); INSERT INTO other.secret VALUES (1);")
        self.assert_owns_listed_schemas_only()

    def test_without_extra_schemas_the_application_user_has_its_database_only(self):
        self.start(extras="")
        self.assertNotIn("shop_graph", self.root("SHOW DATABASES"))
        self.assertEqual(self.app("CREATE TABLE shop.t (id INT)").returncode, 0)
        self.assertNotEqual(self.app("CREATE DATABASE shop_graph").returncode, 0)

    def test_a_main_database_name_with_underscore_grants_that_database_only(self):
        # An unescaped _ in a database grant matches any character: shop_main would cover shopXmain.
        self.start(extras="", database="shop_main")
        self.assertEqual(self.app("CREATE TABLE shop_main.t (id INT)").returncode, 0)
        self.assertNotEqual(self.app("CREATE DATABASE shopXmain").returncode, 0, "look-alike database must be refused")
        self.assertNotIn("shop_main`", self.app("SHOW GRANTS FOR CURRENT_USER()").stdout.replace("\\_", ""))

    def test_restrict_grants_escapes_the_main_database_name(self):
        self.start(extras="", root_password=APP_PASSWORD, database="shop_main")
        self.root("GRANT ALL PRIVILEGES ON *.* TO 'shop'@'%' WITH GRANT OPTION;", password=APP_PASSWORD)
        result = subprocess.run(["docker", "exec", "-e", f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}", self.name,
                                 "sh", "-c", RESTRICT, "restrict-grants"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.app("CREATE TABLE shop_main.t (id INT)").returncode, 0)
        self.assertNotEqual(self.app("CREATE DATABASE shopXmain").returncode, 0, "look-alike database must be refused")

    def test_restrict_grants_covers_the_extra_schemas_of_a_pre_v2_database(self):
        # Pre-v2: root shares the application password, the application holds everything, the code made its schemas.
        self.start(extras="", root_password=APP_PASSWORD)
        self.root("GRANT ALL PRIVILEGES ON *.* TO 'shop'@'%' WITH GRANT OPTION;"
                  "CREATE DATABASE shop_graph; CREATE TABLE shop_graph.kept (id INT); INSERT INTO shop_graph.kept VALUES (7);"
                  "CREATE DATABASE other; CREATE TABLE other.secret (id INT); INSERT INTO other.secret VALUES (1);",
                  password=APP_PASSWORD)
        for _ in range(2):  # idempotent
            result = subprocess.run(["docker", "exec", "-e", f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}", self.name,
                                     "sh", "-c", RESTRICT, "restrict-grants", *EXTRAS.split()], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("shop and 2 extra schemas only", result.stdout)
        self.assertEqual(self.root("SELECT id FROM shop_graph.kept"), ["7"])
        self.assertNotEqual(self.sql("root", APP_PASSWORD, "SELECT 1").returncode, 0)
        self.assert_owns_listed_schemas_only()

    def assert_manages_test_schemas_only(self):
        """The application user creates, fills and drops what the shop*_test pattern names, and nothing else."""
        for schema in ("shop_test", "shop_graph_test", "shop_x1_test"):
            with self.subTest(schema=schema):
                result = self.app(f"CREATE DATABASE {schema}; CREATE TABLE {schema}.t (id INT); INSERT INTO {schema}.t VALUES (1);"
                                  f"SELECT COUNT(*) FROM {schema}.t; DROP DATABASE {schema};")
                self.assertEqual((result.returncode, result.stdout.split()), (0, ["1"]), result.stderr)
        for query in ("CREATE DATABASE other_test", "CREATE DATABASE shop_test_copy", "CREATE DATABASE shop_testing",
                      "CREATE DATABASE elsewhere"):
            with self.subTest(query=query):
                self.assertNotEqual(self.app(query).returncode, 0, f"{query} must be refused")
        grants = self.app("SHOW GRANTS FOR CURRENT_USER()").stdout
        self.assertNotIn("GRANT OPTION", grants)
        self.assertNotIn("ALL PRIVILEGES ON *.*", grants)

    def test_first_start_grants_the_test_schema_patterns_without_creating_them(self):
        self.start(tests="shop*_test")
        self.assertFalse([name for name in self.root("SHOW DATABASES") if name.endswith("_test")])
        self.assert_manages_test_schemas_only()
        self.root("CREATE DATABASE other; CREATE TABLE other.secret (id INT); INSERT INTO other.secret VALUES (1);")
        self.assert_owns_listed_schemas_only()

    def test_restrict_grants_adds_the_test_schema_patterns(self):
        self.start(extras="", root_password=APP_PASSWORD)
        self.root("GRANT ALL PRIVILEGES ON *.* TO 'shop'@'%' WITH GRANT OPTION;"
                  "CREATE DATABASE other; CREATE TABLE other.secret (id INT); INSERT INTO other.secret VALUES (1);",
                  password=APP_PASSWORD)
        result = subprocess.run(["docker", "exec", "-e", f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}", self.name,
                                 "sh", "-c", RESTRICT, "restrict-grants", *EXTRAS.split(), "--test", "shop*_test"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("shop and 2 extra schemas (+ 1 test schema patterns) only", result.stdout)
        self.assert_manages_test_schemas_only()
        self.assert_owns_listed_schemas_only()

    def project(self, extras=EXTRAS):
        """The runner's view of the project; its Compose command reaches this container."""
        compose = self.directory / "compose"
        compose.write_text(f'#!/bin/sh\n[ "$1 $2 $3" = "exec -T database" ] || exit 64\nshift 3\n'
                           f'exec docker exec -i {self.name} "$@"\n')
        compose.chmod(0o755)
        (self.directory / "docker").mkdir(exist_ok=True)
        return SimpleNamespace(name="shop-local", root=self.directory, directory=self.directory / "docker",
                               environment="local", command=[str(compose)], child_env=lambda: dict(os.environ),
                               settings={"DATABASE_NAME": "shop", "DATABASE_EXTRA_SCHEMAS": extras, "MIN_FREE_DISK_MB": "0"})

    def populate(self):
        self.root("CREATE TABLE shop.orders (id INT PRIMARY KEY); INSERT INTO shop.orders VALUES (1),(2);"
                  "CREATE TABLE shop.connections (id INT PRIMARY KEY); INSERT INTO shop.connections VALUES (1),(2),(3);"
                  "CREATE TABLE shop_graph.nodes (id INT PRIMARY KEY, label TEXT);"
                  "INSERT INTO shop_graph.nodes VALUES (1, 'a;\\nb'), (2, 'USE `shop`;');"
                  "CREATE VIEW shop_graph.labels AS SELECT label FROM shop_graph.nodes;"
                  "CREATE TABLE shop_git.commits (sha CHAR(4) PRIMARY KEY); INSERT INTO shop_git.commits VALUES ('abcd');")

    def counts(self):
        return [self.root(f"SELECT COUNT(*) FROM {table}")[0] for table in
                ("shop.orders", "shop.connections", "shop_graph.nodes", "shop_graph.labels", "shop_git.commits")]

    def test_backup_and_restore_cover_every_schema(self):
        self.start()
        self.populate()
        project = self.project()
        with contextlib.redirect_stdout(io.StringIO()):
            backup = backup_database(project)
        text = gzip.decompress(backup.read_bytes())
        for schema in (b"shop", b"shop_graph", b"shop_git"):
            self.assertIn(b"USE `" + schema + b"`;", text)
        self.root("DROP DATABASE shop_graph; DROP DATABASE shop_git; DROP TABLE shop.orders;")
        with contextlib.redirect_stdout(io.StringIO()):
            import_database(project, plan_import(project, str(backup)))
        self.assertEqual(self.counts(), ["2", "3", "2", "2", "1"])
        self.assertEqual(self.root("SELECT label FROM shop_graph.nodes WHERE id = 2"), ["USE", "`shop`;"])
        # The restored schemas are still the application's.
        self.assert_owns_listed_schemas_only_after_restore()

    def assert_owns_listed_schemas_only_after_restore(self):
        result = self.app("INSERT INTO shop_git.commits VALUES ('ef01'); SELECT COUNT(*) FROM shop_graph.labels;")
        self.assertEqual((result.returncode, result.stdout.split()), (0, ["2"]), result.stderr)

    def test_registry_backup_restores_every_schema_and_keeps_skipped_main_data(self):
        self.start()
        self.populate()
        project = self.project()
        with patch("database_import.skip_rule", return_value=lambda table: table == "connections"), \
                patch("database_schema.database_tables", return_value=["connections", "orders"]), \
                contextlib.redirect_stdout(io.StringIO()):
            backup = backup_database(project, registry=True)
        self.root("INSERT INTO shop.connections VALUES (4); DELETE FROM shop.orders;"
                  "DROP DATABASE shop_graph; DELETE FROM shop_git.commits;")
        with contextlib.redirect_stdout(io.StringIO()):
            import_database(project, plan_import(project, str(backup)))
        # Main data and every extra schema come back; the skipped table keeps what the server has.
        self.assertEqual(self.counts(), ["2", "4", "2", "2", "1"])

    def test_a_single_schema_backup_restores_into_the_main_database_as_before(self):
        self.start(extras="")
        self.root("CREATE TABLE shop.orders (id INT); INSERT INTO shop.orders VALUES (1);")
        project = self.project(extras="")
        with contextlib.redirect_stdout(io.StringIO()):
            backup = backup_database(project)
        self.assertNotIn(b"USE `", gzip.decompress(backup.read_bytes()))
        self.root("DROP TABLE shop.orders;")
        with contextlib.redirect_stdout(io.StringIO()):
            import_database(project, plan_import(project, str(backup)))
        self.assertEqual(self.root("SELECT COUNT(*) FROM shop.orders"), ["1"])


    def test_snapshot_migration_and_after_import_reach_the_extra_schemas(self):
        from database_import import after_import_hooks, execute_sql
        from database_migrations_guarded import draft_text, migrate
        from database_migrations import definitions
        from database_schema import read_schema
        self.start()
        self.root("CREATE TABLE shop.orders (id INT PRIMARY KEY);"
                  "CREATE TABLE shop_git.commits (sha CHAR(4) PRIMARY KEY);")
        project = self.project()
        project.settings["SCHEMA_DIRECTORY"] = "database/schema"
        before = definitions(read_schema(project))
        self.assertEqual(sorted(before), ["orders", "shop_git.commits"])
        self.assertTrue(before["shop_git.commits"].startswith("CREATE TABLE `commits`"))
        # The same change made by hand, drafted, undone, then applied (twice: every statement is guarded).
        self.root("ALTER TABLE shop_git.commits ADD COLUMN author VARCHAR(40);"
                  "CREATE TABLE shop_graph.edges (id INT PRIMARY KEY);")
        after = definitions(read_schema(project))
        draft = draft_text(before, after)
        self.root("ALTER TABLE shop_git.commits DROP COLUMN author; DROP TABLE shop_graph.edges;")
        migrations = self.directory / "database/migrations"
        migrations.mkdir(parents=True)
        (migrations / "20261013120000-schema.sql").write_text(draft)
        with contextlib.redirect_stdout(io.StringIO()):
            migrate(project)
        self.assertEqual(definitions(read_schema(project)), after)
        self.assertEqual(self.root("SHOW TABLES FROM shop"), ["orders", "setup_migrations"], "nothing landed in the main database")
        (migrations / "20261013120001-again.sql").write_text(draft.replace("-- Draft checksum", "-- again"))
        with contextlib.redirect_stdout(io.StringIO()):
            migrate(project)
        self.assertEqual(definitions(read_schema(project)), after)
        # An extra schema's after-import script runs in that schema.
        hook = self.directory / "docker/database/after-import/local/shop_git/010-mark.sql"
        hook.parent.mkdir(parents=True)
        hook.write_text("INSERT INTO commits (sha, author) VALUES ('ab12', 'setup');\n")
        with contextlib.redirect_stdout(io.StringIO()):
            execute_sql(project, after_import_hooks(project))
        self.assertEqual(self.root("SELECT author FROM shop_git.commits WHERE sha = 'ab12'"), ["setup"])

    def test_per_schema_migrations_apply_and_roll_back_on_a_real_database(self):
        from database_migrations_guarded import draft_text, migrate, rollback, schema_tables
        from database_migrations import definitions
        from database_schema import read_schema
        self.start()
        self.root("CREATE TABLE shop.orders (id INT PRIMARY KEY);"
                  "CREATE TABLE shop_git.commits (sha CHAR(4) PRIMARY KEY);")
        project = self.project()
        project.settings["SCHEMA_DIRECTORY"] = "database/schema"
        before = definitions(read_schema(project))
        self.root("ALTER TABLE shop.orders ADD COLUMN total INT; ALTER TABLE shop_git.commits ADD COLUMN author VARCHAR(40);"
                  "CREATE TABLE shop_git.tags (name VARCHAR(20) PRIMARY KEY);")
        after = definitions(read_schema(project))
        migrations = self.directory / "database/migrations"
        for schema, folder in ((None, "shop"), ("shop_git", "shop_git")):
            (migrations / folder).mkdir(parents=True)
            (migrations / folder / "20261013120000-schema.sql").write_text(
                draft_text(schema_tables(before, schema, False), schema_tables(after, schema, False)))
        self.root("ALTER TABLE shop.orders DROP COLUMN total; ALTER TABLE shop_git.commits DROP COLUMN author;"
                  "DROP TABLE shop_git.tags;")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(migrate(project), ["20261013120000-schema.sql", "shop_git/20261013120000-schema.sql"])
        self.assertEqual(definitions(read_schema(project)), after)
        self.assertEqual(self.root("SHOW TABLES FROM shop"), ["orders", "setup_migrations"])
        with contextlib.redirect_stdout(io.StringIO()):
            rollback(project, steps=2)
        self.assertEqual(definitions(read_schema(project)), before, "both schemas are back where they were")
        self.assertEqual(self.root("SELECT COUNT(*) FROM shop.setup_migrations"), ["0"])
        with contextlib.redirect_stdout(io.StringIO()):
            migrate(project)
        self.assertEqual(definitions(read_schema(project)), after, "and forward again")

if __name__ == "__main__":
    unittest.main()
