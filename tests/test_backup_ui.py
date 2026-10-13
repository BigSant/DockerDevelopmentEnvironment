"""The backup service (table registry UI), the registry check after a backup and its /metrics group."""

import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from create_project import scaffold
from database_backup import record_unregistered
from database_registry import classify, load
from project import Project

UI = ROOT / "docker/runtime/backup"
PROD = ("PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\n"
        "WEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\n"
        "DATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test-only\n")


def php_array(values):
    return "array(" + ", ".join("'" + value + "'" for value in values) + ")"


def scratch():
    directory = ROOT / ".test-work"
    directory.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=directory)


class BackupServiceTest(unittest.TestCase):
    def setUp(self):
        temporary = scratch()
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold("backup-ui", temporary.name)
        (self.app / "database/schema").mkdir(parents=True)

    def services(self, environment="local"):
        return json.loads(Project(self.app, environment).capture(["config", "--format", "json"]))["services"]

    def enable(self, extra=""):
        with (self.app / "env/common.env").open("a") as env:
            env.write("SERVICES=backup\n" + extra)

    def test_local_ui_edits_the_registry_beside_the_schema_and_servers_only_read_it(self):
        self.enable()
        services = self.services()
        mount = next(v for v in services["backup"]["volumes"] if v["target"] == "/opt/setup/project-database")
        self.assertEqual((Path(mount["source"]), mount.get("read_only", False)), (self.app / "database", False))
        self.assertEqual(services["nginx-proxy"]["environment"]["SETUP_ENABLE_BACKUP"], "1")
        (self.app / "env/prod.env").write_text(PROD + "BACKUP_ALLOWED_IPS=203.0.113.7\n")
        prod = self.services("prod")
        mount = next(v for v in prod["backup"]["volumes"] if v["target"] == "/opt/setup/project-database")
        self.assertTrue(mount.get("read_only", False))

    def test_servers_need_an_allowlist_and_the_service_needs_database_as_code(self):
        self.enable()
        (self.app / "env/prod.env").write_text(PROD)
        with self.assertRaisesRegex(ValueError, "backup on prod requires BACKUP_ALLOWED_IPS or ALLOWED_IPS"):
            Project(self.app, "prod")
        with (self.app / "env/common.env").open("a") as env:
            env.write("DATABASE_AS_CODE=off\n")
        with self.assertRaisesRegex(ValueError, "DATABASE_AS_CODE=off"):
            Project(self.app)

    def test_a_missing_database_directory_is_not_created_by_compose(self):
        shutil.rmtree(self.app / "database")
        self.enable()
        mounts = {v["target"] for v in self.services()["backup"]["volumes"]}
        self.assertNotIn("/opt/setup/project-database", mounts)


class BackupRegistryCheckTest(unittest.TestCase):
    def setUp(self):
        temporary = scratch()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "app/database").mkdir(parents=True)
        (self.root / "app/database/table-registry.txt").write_text("{p}feed  feeds full import\n")
        self.project = SimpleNamespace(root=self.root, directory=self.root / "app", data_directory=self.root / "data",
                                       settings={"SCHEMA_DIRECTORY": "app/database/schema", "PROFILE": "prestashop"})

    def check(self, tables):
        with patch("database_schema.database_tables", return_value=tables), contextlib.redirect_stdout(io.StringIO()) as output:
            status = record_unregistered(self.project, Path("shop-local-1.sql.gz"))
        return status, output.getvalue()

    def test_tables_without_a_line_are_printed_and_kept_for_metrics(self):
        status, output = self.check(["ps_feed", "ps_product", "ps_zz_custom", "ps_aa_custom"])
        self.assertEqual(status["unregistered"], ["ps_aa_custom", "ps_zz_custom"])
        self.assertIn("2 tables are not in the table registry (ps_aa_custom, ps_zz_custom)", output)
        saved = json.loads((self.root / "data/table-registry/status.json").read_text())
        self.assertEqual((saved["backup"], saved["tables"], saved["unregistered"]), ("shop-local-1.sql.gz", 4, status["unregistered"]))

    def test_tables_of_extra_schemas_are_checked_as_schema_table(self):
        (self.root / "app/database/table-registry.txt").write_text("{p}feed  feeds full import\nasana_graph.*  graph full import\n")
        with patch("database_schema.extra_tables", return_value=["asana_graph.edges", "asana_git.refs"]):
            status, output = self.check(["ps_feed"])
        self.assertEqual(status["unregistered"], ["asana_git.refs"])

    def test_the_commit_hook_stops_on_an_unclassified_extra_schema_table(self):
        from database_schema import commit_schema
        (self.root / "app/database/table-registry.txt").write_text("{p}feed  feeds full import\n")
        with patch("database_schema.in_committing_checkout", side_effect=lambda project: project), \
                patch("database_schema.database_tables", return_value=["ps_feed"]), \
                patch("database_schema.extra_tables", return_value=["asana_git.refs"]), \
                self.assertRaisesRegex(ValueError, "1 tables are not in the table registry: asana_git.refs"):
            commit_schema(self.project)

    def test_a_classified_database_records_an_empty_list_quietly(self):
        status, output = self.check(["ps_feed", "ps_product"])
        self.assertEqual((status["unregistered"], output), ([], ""))

    def test_a_failed_check_or_database_as_code_off_never_fails_the_backup(self):
        with patch("database_schema.database_tables", side_effect=ValueError("Cannot read DB schema")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(record_unregistered(self.project, Path("x.sql.gz")))
        self.assertIn("not checked after the backup", output.getvalue())
        self.project.settings["SCHEMA_DIRECTORY"] = ""
        self.assertIsNone(record_unregistered(self.project, Path("x.sql.gz")))


def php(code, env=None):
    result = subprocess.run(["php", "-r", code], capture_output=True, text=True, env=env, timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class BackupRegistryPhpTest(unittest.TestCase):
    def test_lines_the_ui_writes_are_read_the_same_way_by_setup(self):
        with scratch() as temporary:
            registry = Path(temporary) / "table-registry.txt"
            registry.write_text("{p}tmp_*  review empty skip\n")
            env = {"PROFILE": "prestashop", "PS_DATABASE_PREFIX": "ps_", "BACKUP_PROFILE_DIRECTORY": str(ROOT / "docker/database-profile")}
            php(f"require '{UI}/registry.php'; tablesWrite({json.dumps(str(registry))}, array("
                "'ps_tmp_feed' => array('feeds', 'full', 'import'), 'ps_old' => array('drop', 'drop', '-')));", env)
            self.assertTrue(registry.read_text().splitlines()[0].startswith("{p}tmp_feed "), "a new line wins over wildcards")
            project = SimpleNamespace(root=Path(temporary), directory=Path(temporary),
                                      settings={"SCHEMA_DIRECTORY": "schema", "PROFILE": "prestashop", "PS_DATABASE_PREFIX": "ps_"})
            entries = load(project)
            self.assertEqual(classify(entries, "ps_tmp_feed")[1:], ("feeds", "full", "import"))
            self.assertEqual(classify(entries, "ps_tmp_other")[1:], ("review", "empty", "skip"))
            self.assertEqual(classify(entries, "ps_old")[2], "drop")
            matched = json.loads(php(f"require '{UI}/registry.php'; echo json_encode(tablesMatch(tablesLoad("
                                     f"{json.dumps(str(registry))}), 'ps_product'));", env))
            self.assertEqual(matched[0], "profile", "PrestaShop core tables come from the profile defaults")

    def test_a_project_overrides_a_setup_default_only_when_it_differs(self):
        with scratch() as temporary:
            registry = Path(temporary) / "table-registry.txt"
            registry.write_text("")
            env = {"PROFILE": "prestashop", "PS_DATABASE_PREFIX": "ps_", "BACKUP_PROFILE_DIRECTORY": str(ROOT / "docker/database-profile")}
            default = json.loads(php(f"require '{UI}/registry.php'; echo json_encode(tablesDefault(tablesLoad("
                                     f"{json.dumps(str(registry))}), 'ps_search_index'));", env))
            self.assertEqual(default[0], "profile")
            same = json.loads(php(f"require '{UI}/registry.php'; echo json_encode(tablesSetChange(tablesLoad("
                                  f"{json.dumps(str(registry))}), 'ps_search_index', {php_array(default[2:])}));", env))
            self.assertEqual(same, [], "the default's own values write nothing")
            php(f"require '{UI}/registry.php'; $f = {json.dumps(str(registry))}; "
                "tablesWrite($f, tablesSetChange(tablesLoad($f), 'ps_search_index', array('index', 'empty', 'import')));", env)
            self.assertTrue(registry.read_text().lstrip().startswith("{p}search_index "), registry.read_text())
            matched = json.loads(php(f"require '{UI}/registry.php'; $r = tablesLoad({json.dumps(str(registry))}); "
                                     "echo json_encode(array(tablesMatch($r, 'ps_search_index'), tablesDefault($r, 'ps_search_index')[0]));", env))
            self.assertEqual((matched[0][0], matched[0][4], matched[1]), ("project", "import", "profile"))
            project = SimpleNamespace(root=Path(temporary), directory=Path(temporary),
                                      settings={"SCHEMA_DIRECTORY": "schema", "PROFILE": "prestashop", "PS_DATABASE_PREFIX": "ps_"})
            self.assertEqual(classify(load(project), "ps_search_index")[3], "import", "setup reads the override the same way")

    def test_extra_schema_tables_are_schema_table_and_never_take_the_prefix(self):
        with scratch() as temporary:
            registry = Path(temporary) / "table-registry.txt"
            registry.write_text("*_log  logs empty skip\n")
            env = {"PROFILE": "prestashop", "PS_DATABASE_PREFIX": "ps_", "BACKUP_PROFILE_DIRECTORY": "/nonexistent"}
            php(f"require '{UI}/registry.php'; tablesWrite({json.dumps(str(registry))}, array("
                "'asana_git.ps_refs' => array('git', 'empty', 'skip')));", env)
            self.assertTrue(registry.read_text().startswith("asana_git.ps_refs "), "no {p} in an extra schema's name")
            matched = json.loads(php(f"require '{UI}/registry.php'; $r = tablesLoad({json.dumps(str(registry))}); "
                                     "echo json_encode(array(tablesMatch($r, 'asana_git.commit_log'), tablesMatch($r, 'ps_api_log')[2], "
                                     "tablesMatch($r, 'asana_git.ps_refs')[2]));", env))
            self.assertEqual(matched, [None, "logs", "git"], "a line without a dot does not reach an extra schema")

    def test_rules_are_validated(self):
        valid = php(f"require '{UI}/registry.php'; echo json_encode(array(tablesValidRule('catalog', 'slice', 'import'), "
                    "tablesValidRule('bad group', 'full', 'import'), tablesValidRule('x', 'drop', 'import'), tablesValidRule('x', 'full', '-')));")
        self.assertEqual(json.loads(valid), [True, False, False, False])


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class RegistryMetricsTest(unittest.TestCase):
    def test_the_last_backup_check_becomes_metrics(self):
        with scratch() as temporary:
            status = Path(temporary) / "status.json"
            status.write_text(json.dumps({"checked": 1760000000, "backup": "b.sql.gz", "tables": 3, "unregistered": ["ps_x"]}))
            env = {"METRICS_REGISTRY": "1", "METRICS_REGISTRY_STATUS": str(status)}
            text = php(f"require '{ROOT}/docker/runtime/metrics/metrics.php'; $m = new Metrics(); "
                       "metrics_collect($m, 'registry'); echo $m->render();", env)
        self.assertIn("setup_database_unregistered_tables 1", text)
        self.assertIn('setup_database_unregistered_table_info{table="ps_x"} 1', text)
        self.assertIn("setup_database_registry_check_timestamp_seconds 1760000000", text)
        self.assertIn('setup_metrics_group_up{group="registry"} 1', text)


if __name__ == "__main__":
    unittest.main()
