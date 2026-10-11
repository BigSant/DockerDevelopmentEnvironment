"""Extra database schemas (DATABASE_EXTRA_SCHEMAS) without connecting to a database."""

import contextlib
import gzip
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from create_project import scaffold
from database_backup import backup_database
from database_client import DUMP, extra_schemas
from database_grants import RESTRICT, restrict_grants
from database_import import check_dump_tables, dump_tables, filter_dump, plan_import
from project import Project

INIT_SCRIPTS = {"mysql": ROOT / "docker/docker/mysql/conf/init.sh", "mariadb": ROOT / "docker/docker/mariadb/conf/init.sh"}
# What the first-start hook ran before extra schemas existed; a project without them still runs exactly this.
INIT_SQL = ("SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';\n"
            "CREATE USER IF NOT EXISTS 'app'@'localhost' IDENTIFIED BY 'app-secret';\n"
            "CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY 'app-secret';\n"
            "GRANT ALL PRIVILEGES ON `asana`.* TO 'app'@'localhost';\n"
            "GRANT ALL PRIVILEGES ON `asana`.* TO 'app'@'%';\n")


def scratch_directory(test):
    scratch = ROOT / ".test-work"
    scratch.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=scratch)
    test.addCleanup(temporary.cleanup)
    return Path(temporary.name)


def fake_clients(directory, replies=""):
    """mysql/mariadb stand-ins that record argv and stdin per call and answer SELECTs from `replies`."""
    log = directory / "calls"
    log.mkdir()
    script = directory / "bin/mysql"
    script.parent.mkdir()
    script.write_text(f"""#!/bin/sh
n=$(find '{log}' -name '*.sql' | wc -l)
printf '%s\\n' "$@" > '{log}'/$n.argv
cat > '{log}'/$n.sql
case $(cat '{log}'/$n.sql) in
  *"User = 'root'"*) printf '%s\\n' {replies or "localhost"} ;;
  *"SELECT Host"*) printf '%s\\n' '%' localhost ;;
esac
""")
    script.chmod(0o755)
    (directory / "bin/mariadb").symlink_to(script)
    return log


def calls(log):
    return [((log / f"{n}.argv").read_text(), (log / f"{n}.sql").read_text()) for n in range(len(list(log.glob("*.sql"))))]


class ExtraSchemaSettingTest(unittest.TestCase):
    def test_names_are_split_on_whitespace_and_empty_means_none(self):
        self.assertEqual(extra_schemas({"DATABASE_NAME": "asana", "DATABASE_EXTRA_SCHEMAS": " asana_graph\tasana_git  "}),
                         ["asana_graph", "asana_git"])
        self.assertEqual(extra_schemas({"DATABASE_NAME": "asana", "DATABASE_EXTRA_SCHEMAS": ""}), [])
        self.assertEqual(extra_schemas({"DATABASE_NAME": "asana"}), [])

    def test_unsafe_duplicate_main_and_system_names_are_rejected(self):
        for value in ("bad-name", "a;b", "`x`", "x" * 65, "asana_git asana_git", "asana", "mysql", "Information_Schema",
                      "sys", "performance_schema", "a/b", "ą"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                extra_schemas({"DATABASE_NAME": "asana", "DATABASE_EXTRA_SCHEMAS": value})


class ExtraSchemaModelTest(unittest.TestCase):
    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold("schemas-demo", scratch_directory(self))

    def configure(self, line):
        with (self.app / "env/common.env").open("a") as env:
            env.write(line)

    def test_the_database_service_receives_the_list_for_both_engines(self):
        self.configure('DATABASE_EXTRA_SCHEMAS="demo_graph demo_git"\n')
        for engine in ("mysql", "mariadb"):
            with self.subTest(engine=engine):
                if engine == "mariadb":
                    self.configure("DATABASE_ENGINE=mariadb\n")
                project = Project(self.app)
                database = project.model()["services"]["database"]
                self.assertTrue(database["image"].startswith(f"{engine}-"))
                self.assertEqual(database["environment"]["DATABASE_EXTRA_SCHEMAS"], "demo_graph demo_git")
                self.assertEqual(extra_schemas(project.settings), ["demo_graph", "demo_git"])

    def test_without_the_setting_the_list_is_empty(self):
        project = Project(self.app)
        self.assertEqual(project.model()["services"]["database"]["environment"]["DATABASE_EXTRA_SCHEMAS"], "")
        self.assertEqual(extra_schemas(project.settings), [])

    def test_an_invalid_list_stops_the_runner(self):
        self.configure("DATABASE_EXTRA_SCHEMAS='demo_graph other-db'\n")
        with self.assertRaisesRegex(ValueError, "DATABASE_EXTRA_SCHEMAS"):
            Project(self.app)


class FirstStartScriptTest(unittest.TestCase):
    def run_init(self, engine, extras=None):
        directory = scratch_directory(self)
        log = fake_clients(directory)
        env = {"PATH": f"{directory / 'bin'}:/usr/bin:/bin", "MYSQL_USER": "app", "MYSQL_PASSWORD": "app-secret",
               "MYSQL_DATABASE": "asana", "MYSQL_ROOT_PASSWORD": "root-secret"}
        if extras is not None:
            env["DATABASE_EXTRA_SCHEMAS"] = extras
        result = subprocess.run(["bash", str(INIT_SCRIPTS[engine])], env=env, capture_output=True, text=True)
        return result, calls(log)

    def test_without_extra_schemas_the_hook_runs_the_same_sql_as_before(self):
        for engine in INIT_SCRIPTS:
            for extras in (None, "", "  "):
                with self.subTest(engine=engine, extras=extras):
                    result, executed = self.run_init(engine, extras)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(len(executed), 1)
                    self.assertEqual(executed[0][1].replace("\n\n", "\n"), INIT_SQL + "FLUSH PRIVILEGES;\n")

    def test_extra_schemas_are_created_and_granted_without_wildcards_or_global_rights(self):
        for engine in INIT_SCRIPTS:
            with self.subTest(engine=engine):
                result, executed = self.run_init(engine, "asana_graph asana_git")
                self.assertEqual(result.returncode, 0, result.stderr)
                argv, sql = executed[0]
                self.assertNotIn("secret", argv)
                self.assertTrue(sql.startswith(INIT_SQL), sql)
                for schema, pattern in (("asana_graph", r"asana\_graph"), ("asana_git", r"asana\_git")):
                    self.assertIn(f"CREATE DATABASE IF NOT EXISTS `{schema}`;", sql)
                    for host in ("localhost", "%"):
                        self.assertIn(f"GRANT ALL PRIVILEGES ON `{pattern}`.* TO 'app'@'{host}';", sql)
                self.assertNotIn("*.*", sql)
                self.assertNotIn("GRANT OPTION", sql)
                self.assertTrue(sql.rstrip().endswith("FLUSH PRIVILEGES;"))

    def test_an_invalid_name_fails_before_any_sql(self):
        for engine in INIT_SCRIPTS:
            with self.subTest(engine=engine):
                result, executed = self.run_init(engine, "asana_graph `x`;DROP")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DATABASE_EXTRA_SCHEMAS", result.stderr)
                self.assertEqual(executed, [])


OLD_BASH_IMAGE = os.environ.get("SETUP_OLD_BASH_IMAGE")  # e.g. mysql:5.7, which ships bash 4.2


class OldBashFirstStartTest(unittest.TestCase):
    """Bash < 4.4 treats an empty array as unset under set -u; mysql:5.7 runs the hook with bash 4.2."""

    def test_no_hook_expands_a_possibly_empty_array_bare(self):
        for engine, script in INIT_SCRIPTS.items():
            with self.subTest(engine=engine):
                self.assertNotRegex(script.read_text(), r'(?<!\+)"\$\{extra_schemas\[@\]\}"')

    @unittest.skipUnless(OLD_BASH_IMAGE, "Set SETUP_OLD_BASH_IMAGE to an image with bash < 4.4 (mysql:5.7)")
    def test_the_hook_runs_under_bash_4_2_with_and_without_extra_schemas(self):
        for engine, script in INIT_SCRIPTS.items():
            for extras in ("", "asana_graph"):
                with self.subTest(engine=engine, extras=extras):
                    directory = scratch_directory(self)
                    log = fake_clients(directory)
                    result = subprocess.run(
                        ["docker", "run", "--rm", "--network", "none", "--memory", "64m", "--user", f"{os.getuid()}:{os.getgid()}",
                         "-v", f"{directory}:{directory}", "-v", f"{script}:/hook.sh:ro", "--entrypoint", "bash",
                         "-e", f"PATH={directory / 'bin'}:/usr/bin:/bin", "-e", "MYSQL_USER=app", "-e", "MYSQL_PASSWORD=app-secret",
                         "-e", "MYSQL_DATABASE=asana", "-e", "MYSQL_ROOT_PASSWORD=root-secret",
                         "-e", f"DATABASE_EXTRA_SCHEMAS={extras}", OLD_BASH_IMAGE, "-c",
                         'case $BASH_VERSION in 4.[0-3].*|3.*) ;; *) echo "bash $BASH_VERSION is not old" >&2; exit 99 ;; esac\n'
                         'exec bash /hook.sh'], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    sql = calls(log)[0][1]
                    self.assertIn("GRANT ALL PRIVILEGES ON `asana`.* TO 'app'@'%';", sql)
                    self.assertEqual(r"asana\_graph" in sql, bool(extras))


class RestrictScriptTest(unittest.TestCase):
    def run_restrict(self, *schemas):
        directory = scratch_directory(self)
        log = fake_clients(directory)
        env = {"PATH": f"{directory / 'bin'}:/usr/bin:/bin", "MYSQL_USER": "app", "MYSQL_PASSWORD": "app-secret",
               "MYSQL_DATABASE": "asana", "MYSQL_ROOT_PASSWORD": "root-secret"}
        result = subprocess.run(["sh", "-c", RESTRICT, "restrict-grants", *schemas], env=env, capture_output=True, text=True)
        return result, calls(log)

    def grant_statements(self, executed):
        return [sql for _, sql in executed if "REVOKE" in sql][0]

    def test_extra_schemas_are_created_and_granted_to_every_application_host(self):
        result, executed = self.run_restrict("asana_graph", "asana_git")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all("secret" not in argv for argv, _ in executed))
        sql = self.grant_statements(executed)
        for host in ("%", "localhost"):
            self.assertIn(f"REVOKE ALL PRIVILEGES ON *.* FROM 'app'@'{host}';", sql)
            self.assertIn(f"REVOKE GRANT OPTION ON *.* FROM 'app'@'{host}';", sql)
            self.assertIn(f"GRANT ALL PRIVILEGES ON `asana`.* TO 'app'@'{host}';", sql)
            for pattern in (r"asana\_graph", r"asana\_git"):
                self.assertIn(f"GRANT ALL PRIVILEGES ON `{pattern}`.* TO 'app'@'{host}';", sql)
        for schema in ("asana_graph", "asana_git"):
            self.assertIn(f"CREATE DATABASE IF NOT EXISTS `{schema}`;", sql)
        self.assertNotIn("WITH GRANT OPTION", sql)
        self.assertEqual(sql.count("GRANT ALL PRIVILEGES"), 6)
        self.assertIn("asana and 2 extra schemas", result.stdout)

    def test_without_extra_schemas_only_the_main_database_is_granted(self):
        result, executed = self.run_restrict()
        self.assertEqual(result.returncode, 0, result.stderr)
        sql = self.grant_statements(executed)
        self.assertEqual(sql.count("GRANT ALL PRIVILEGES"), 2)
        self.assertNotIn("CREATE DATABASE", sql)
        self.assertIn("app has privileges on asana only", result.stdout)

    def test_an_invalid_name_changes_nothing(self):
        for name in ("bad-name", "x`;DROP DATABASE asana;--", ""):
            with self.subTest(name=name):
                result, executed = self.run_restrict("asana_graph", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(executed, [])

    def test_the_runner_passes_the_project_list_as_arguments(self):
        commands = []
        project = SimpleNamespace(settings={"DATABASE_NAME": "asana", "DATABASE_EXTRA_SCHEMAS": "asana_graph asana_git"},
                                  capture=lambda args: "database\nphp-fpm\n", run=commands.append)
        restrict_grants(project)
        self.assertEqual(commands, [["exec", "-T", "database", "sh", "-c", RESTRICT, "restrict-grants",
                                     "asana_graph", "asana_git"]])


class FakeDump:
    """Popen stand-in for the dump client: records the command and returns a small dump."""
    def __init__(self, commands):
        self.commands = commands

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        process = SimpleNamespace(stdout=io.BytesIO(b"-- dump\nCREATE TABLE `t` (id int);\n"))
        process.wait = lambda: 0
        process.poll = lambda: 0
        return process


class BackupPlanTest(unittest.TestCase):
    def setUp(self):
        self.root = scratch_directory(self)
        self.project = SimpleNamespace(
            name="asana-local", directory=self.root, command=["compose"], child_env=lambda: {},
            settings={"DATABASE_NAME": "asana", "MIN_FREE_DISK_MB": "0"})

    def dump_commands(self, registry=False, skipped=()):
        commands = []
        with patch("database_backup.subprocess.Popen", side_effect=FakeDump(commands)), \
                patch("database_import.skip_rule", return_value=(lambda table: table in skipped) if skipped else None), \
                patch("database_schema.database_tables", return_value=["asana_log", "asana_task"]), \
                patch("database_schema.extra_tables", return_value=["asana_graph.edges", "asana_graph.nodes"]), \
                contextlib.redirect_stdout(io.StringIO()):
            target = backup_database(self.project, self.root / f"backup-{len(list(self.root.iterdir()))}.sql.gz",
                                     registry=registry)
        self.written = gzip.decompress(target.read_bytes())
        self.assertTrue(self.written)
        return [command[command.index("database-backup") + 1:] for command in commands]

    def test_without_extra_schemas_the_backup_runs_as_before(self):
        self.assertEqual(self.dump_commands(), [["asana"]])
        self.assertEqual(self.dump_commands(registry=True, skipped={"asana_log"}),
                         [["asana", "--ignore-table=asana.asana_log"],
                          ["asana", "--no-data", "--skip-add-drop-table", "--skip-routines", "--skip-events", "asana_log"]])

    def test_extra_schemas_join_the_main_database_in_one_dump_run(self):
        self.project.settings["DATABASE_EXTRA_SCHEMAS"] = "asana_graph asana_git"
        self.assertEqual(self.dump_commands(), [["asana", "--databases", "asana_graph", "asana_git"]])

    def test_registry_structure_comes_first_so_it_restores_into_the_main_database(self):
        # The multi-schema run ends inside the last schema (USE), so the header-less structure pass leads.
        self.project.settings["DATABASE_EXTRA_SCHEMAS"] = "asana_graph"
        self.assertEqual(self.dump_commands(registry=True, skipped={"asana_log"}),
                         [["asana", "--no-data", "--skip-add-drop-table", "--skip-routines", "--skip-events", "asana_log"],
                          ["asana", "--ignore-table=asana.asana_log", "--databases", "asana_graph"]])

    def test_an_extra_schemas_skip_tables_keep_structure_under_their_own_schema(self):
        self.project.settings["DATABASE_EXTRA_SCHEMAS"] = "asana_graph"
        structure = ["--no-data", "--skip-add-drop-table", "--skip-routines", "--skip-events"]
        self.assertEqual(self.dump_commands(registry=True, skipped={"asana_log", "asana_graph.edges"}),
                         [["asana", *structure, "asana_log"], ["asana_graph", *structure, "edges"],
                          ["asana", "--ignore-table=asana.asana_log", "--ignore-table=asana_graph.edges", "--databases", "asana_graph"]])
        # The main structure restores into the target database; the extra schema's names its schema first.
        first, second = self.written.split(b"USE `asana_graph`;", 1)
        self.assertIn(b"CREATE TABLE IF NOT EXISTS `t`", first)
        self.assertIn(b"CREATE TABLE IF NOT EXISTS `t`", second)

    def test_the_dump_client_still_checks_the_main_database_name(self):
        self.assertIn('if [ "$MYSQL_DATABASE" != "$1" ]', DUMP)


class MultiSchemaImportTest(unittest.TestCase):
    DUMP = (b"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `asana` /*!40100 DEFAULT CHARACTER SET utf8mb4 */;\n"
            b"USE `asana`;\n"
            b"CREATE TABLE `asana_log` (\n  `id` int\n);\nINSERT INTO `asana_log` VALUES (1);\n"
            b"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `asana_graph`;\n"
            b"USE `asana_graph`;\n"
            b"CREATE TABLE `asana_log` (\n  `node` int\n);\nINSERT INTO `asana_log` VALUES (2);\n"
            b"CREATE TABLE `edges` (\n  `id` int\n);\nINSERT INTO `edges` VALUES (3);\n"
            b"USE `asana`;\nINSERT INTO `asana_log` VALUES (4);\n")

    def setUp(self):
        self.root = scratch_directory(self)
        (self.root / "docker").mkdir()
        self.dump = self.root / "all-schemas.sql"
        self.dump.write_bytes(self.DUMP)
        self.project = SimpleNamespace(root=self.root, directory=self.root / "docker", environment="local",
                                       settings={"DATABASE_NAME": "asana", "DATABASE_EXTRA_SCHEMAS": "asana_graph"})

    def test_registry_skip_names_an_extra_schemas_table_schema_table(self):
        out = io.BytesIO()
        skipped = filter_dump(io.BytesIO(self.DUMP), out, lambda table: table in ("asana_log", "asana_graph.edges"), main="asana")
        self.assertEqual(skipped, {"asana_log", "asana_graph.edges"})
        kept = out.getvalue()
        self.assertNotIn(b"VALUES (1)", kept)
        self.assertIn(b"INSERT INTO `asana_log` VALUES (2);", kept, "asana_log of asana_graph is another table")
        self.assertNotIn(b"VALUES (3)", kept)
        self.assertNotIn(b"VALUES (4)", kept)
        # A header-less (single schema) dump is the main database.
        out = io.BytesIO()
        filter_dump(io.BytesIO(b"INSERT INTO `asana_log` VALUES (1);\n"), out, lambda table: True, main="asana")
        self.assertEqual(out.getvalue(), b"")

    def test_without_extra_schemas_the_filter_ignores_use_statements_as_before(self):
        out = io.BytesIO()
        filter_dump(io.BytesIO(self.DUMP), out, lambda table: table == "asana_log")
        self.assertNotIn(b"VALUES (2)", out.getvalue())

    def test_the_registry_check_reads_every_schema(self):
        self.assertEqual(dump_tables(self.dump, main="asana"), {"asana_log": {"id"}})
        self.assertEqual(dump_tables(self.dump, main="asana", qualified=True),
                         {"asana_log": {"id"}, "asana_graph.asana_log": {"node"}, "asana_graph.edges": {"id"}})
        self.assertEqual(set(dump_tables(self.dump)), {"asana_log", "edges"})
        self.project.settings.update(SCHEMA_DIRECTORY="database/schema")
        (self.root / "database").mkdir()
        registry = self.root / "database/table-registry.txt"
        registry.write_text("asana_log logs empty skip\n")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "2 tables are not classified"):
            check_dump_tables(self.project, self.dump, interactive=False)
        registry.write_text("asana_log logs empty skip\nasana_graph.*  graph full import\n")
        check_dump_tables(self.project, self.dump, interactive=False)

    def test_a_dump_that_switches_to_an_unlisted_schema_is_refused_before_loading(self):
        self.assertEqual(plan_import(self.project, str(self.dump)), [self.dump.resolve()])
        foreign = self.root / "foreign.sql.gz"
        foreign.write_bytes(gzip.compress(self.DUMP + b"USE `mysql`;\nDELETE FROM `user`;\n"))
        with self.assertRaisesRegex(ValueError, "mysql"):
            plan_import(self.project, str(foreign))
        renamed = self.root / "renamed.sql"
        renamed.write_bytes(self.DUMP.replace(b"`asana_graph`", b"`other_graph`"))
        with self.assertRaisesRegex(ValueError, "other_graph"):
            plan_import(self.project, str(renamed))

    def test_without_extra_schemas_the_import_preflight_is_unchanged(self):
        self.project.settings["DATABASE_EXTRA_SCHEMAS"] = ""
        foreign = self.root / "foreign.sql"
        foreign.write_bytes(b"USE `other`;\nSELECT 1;\n")
        self.assertEqual(plan_import(self.project, str(foreign)), [foreign.resolve()])


if __name__ == "__main__":
    unittest.main()
