"""The base data set: recipe, anonymisation rules, file layout and load planning, with a fake DB."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
import database_dataset
from database_dataset import (DEV_PASSWORD_BCRYPT, anonymised, build_dataset, load_dataset, load_recipe, plan_load,
                              select_sql, slice_condition)
from database_schema import export_schema
from test_database_schema import snapshot


class DatasetTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="dataset-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = SimpleNamespace(root=self.root, directory=self.root / "docker", environment="local",
                                       settings={"SCHEMA_DIRECTORY": "database/schema", "PROFILE": "prestashop",
                                                 "DATABASE_NAME": "shop", "PS_VERSION": "9.0.2"})
        self.project.directory.mkdir()
        (self.root / "database").mkdir()
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_project_recipe_wins_and_profile_supplies_the_sample(self):
        (self.root / "database/data").mkdir()
        (self.root / "database/data/recipe.txt").write_text("# comment\n{p}feature_value id_feature_value < 10\n"
                                                            "{p}tecdoc_* uid IN (SELECT uid FROM x WHERE id_product IN {products})\n")
        products, recipe = load_recipe(self.project)
        self.assertIn("FROM ps_product WHERE active = 1", products)
        self.assertEqual(recipe("ps_feature_value"), "id_feature_value < 10")
        self.assertIn("FROM ps_feature_product", recipe("ps_feature_value_lang"))
        self.assertIsNone(recipe("ps_orders"))
        columns = [("uid", "varchar", False, False)]
        self.assertEqual(slice_condition("ps_tecdoc_article", columns, "(1,2)", recipe),
                         "uid IN (SELECT uid FROM x WHERE id_product IN (1,2))")
        self.assertEqual(slice_condition("ps_stock", [("id_product", "int", False, False)], "(1,2)", recipe),
                         "`id_product` IN (1,2)")
        with self.assertRaisesRegex(ValueError, "ps_odd has no id_product column; add a condition"):
            slice_condition("ps_odd", columns, "(1)", recipe)

    def test_personal_columns_are_replaced_and_others_kept(self):
        key = "`id_customer`"
        self.assertEqual(anonymised("ps_customer", "email", "varchar", False, key, None),
                         "CONCAT('customer', `id_customer`, '@example.test')")
        self.assertEqual(anonymised("ps_employee", "email", "varchar", False, "`id_employee`", None),
                         "CONCAT('employee', `id_employee`, '@example.test')")
        self.assertEqual(anonymised("ps_customer", "passwd", "varchar", False, key, None), f"'{DEV_PASSWORD_BCRYPT}'")
        self.assertEqual(anonymised("ps_customer", "passwd", "varchar", False, key, "legacykey"), "MD5('legacykeydev12345')")
        self.assertEqual(anonymised("ps_address", "phone_mobile", "varchar", True, key, None),
                         "IF(`phone_mobile` IS NULL, NULL, '+37060000000')")
        self.assertEqual(anonymised("ps_connections", "ip_address", "bigint", True, key, None),
                         "IF(`ip_address` IS NULL, NULL, 2130706433)")
        self.assertEqual(anonymised("ps_order_invoice", "delivery_address", "text", True, key, None),
                         "IF(`delivery_address` IS NULL, NULL, 'Anonymised address')")
        self.assertIsNone(anonymised("ps_orders", "total_paid", "decimal", False, key, None))

    def test_configuration_secrets_are_blank_but_settings_stay(self):
        columns = [("id_configuration", "int", False, False), ("name", "varchar", False, False),
                   ("value", "text", True, False), ("generated", "int", True, True)]
        names, sql = select_sql(self.project, "ps_configuration", columns, ["id_configuration"], "full", None, None)
        self.assertEqual(names, ["id_configuration", "name", "value"], "generated columns are left to the server")
        self.assertIn("THEN `value`", sql)
        self.assertIn("_ENABLE$", sql)
        self.assertIn("PASSWORD$", sql)
        self.assertIn("'shop@example.test'", sql)
        self.assertTrue(sql.rstrip().endswith("ORDER BY `id_configuration`;"))

    def build(self, rows):
        (self.root / "database/table-registry.txt").write_text(
            "ps_product catalog slice import\nps_customer customers faker import\nps_log cache empty skip\n")
        def query(project, sql, timeout=60, raw=False):
            if sql.startswith("SELECT id_product"):
                return "3\n1\n"
            if "information_schema.COLUMNS" in sql:
                return ("ps_customer\tid_customer\tint\tNO\t\nps_customer\temail\tvarchar\tNO\t\n"
                        "ps_log\tid\tint\tNO\t\nps_product\tid_product\tint\tNO\t\nps_product\tnote\ttext\tYES\t\n")
            if "information_schema.STATISTICS" in sql:
                return "ps_customer\tid_customer\nps_product\tid_product\n"
            table = sql.split(" FROM `")[1].split("`")[0]
            if table == "ps_product":
                self.assertIn("WHERE `id_product` IN (3,1)", sql)
            return rows.get(table, b"")
        with patch("database_dataset.mysql_query", query), \
                patch("database_dataset.database_tables", lambda project: ["ps_customer", "ps_log", "ps_product"]), \
                patch("database_schema.database_tables", lambda project: ["ps_customer", "ps_log", "ps_product"]):
            return build_dataset(self.project)

    def test_build_groups_files_one_row_per_line_and_is_repeatable(self):
        rows = {"ps_product": b"('1','line one\nline two')\x00\n('3',NULL)\x00\n",
                "ps_customer": b"('5','customer5@example.test')\x00\n"}
        files = self.build(rows)
        self.assertEqual(sorted(files), ["catalog.sql", "customers.sql"])
        catalog = files["catalog.sql"].decode()
        self.assertIn("SET SESSION sql_mode = 'NO_AUTO_VALUE_ON_ZERO';", catalog)
        self.assertIn("-- ps_product: slice, 2 rows\nINSERT INTO `ps_product` (`id_product`, `note`) VALUES\n"
                      "('1','line one\\nline two'),\n('3',NULL);\n", catalog)
        directory = self.root / "database/data/dev"
        (directory / "stale.sql").write_text("old")
        before = {path.name: path.stat().st_mtime_ns for path in directory.glob("*.sql") if path.name != "stale.sql"}
        self.assertEqual(self.build(rows), files)
        self.assertFalse((directory / "stale.sql").exists())
        self.assertEqual({path.name: path.stat().st_mtime_ns for path in directory.glob("*.sql")}, before,
                         "unchanged files are not rewritten")

    def test_build_is_local_only_and_needs_every_table_classified(self):
        self.project.environment = "stage"
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            build_dataset(self.project)

    def test_load_never_replaces_a_server_database(self):
        for environment in ("stage", "prod"):
            self.project.environment = environment
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "only into local and test"):
                load_dataset(self.project, replace=True)

    def test_a_ps16_set_records_a_key_fingerprint_not_the_key(self):
        self.project.settings.update(PS_VERSION="1.6.1.24", PS_COOKIE_KEY="secret-cookie-key")
        self.build({"ps_product": b"('1','x')\x00\n"})
        recorded = (self.root / "database/data/dev/dataset.env").read_text()
        self.assertNotIn("secret-cookie-key", recorded)
        self.assertEqual(recorded, f"PS_COOKIE_KEY_SHA256={database_dataset.key_fingerprint('secret-cookie-key')}\n")

    def test_load_plan_and_refusal_to_overwrite(self):
        export_schema(self.project, snapshot({"ps_product": "CREATE TABLE `ps_product` (`id_product` int);\n"}))
        with self.assertRaisesRegex(ValueError, "make db-dataset set=dev"):
            plan_load(self.project, "dev")
        (self.root / "database/data/dev").mkdir(parents=True)
        (self.root / "database/data/dev/catalog.sql").write_text("INSERT INTO `ps_product` VALUES (1);\n")
        self.project.settings.update(FIXTURES_DIRECTORY="database/fixtures", DOMAIN="shop.local")
        (self.root / "database/fixtures/dev").mkdir(parents=True)
        (self.root / "database/fixtures/dev/010-extra.sql").write_text("SELECT 1;\n")
        files, hooks = plan_load(self.project, "dev")
        self.assertEqual([path.name for path in files], ["catalog.sql"])
        self.assertEqual([path.name for path in hooks], ["010-shop-url.sql", "020-payments-sandbox.sql", "010-extra.sql"])
        executed = []
        with patch("database_dataset.database_tables", return_value=["ps_product"]), \
                self.assertRaisesRegex(ValueError, "replace=1 drops them first"):
            load_dataset(self.project)
        with patch("database_dataset.database_tables", return_value=[]), \
                patch("database_import.execute_sql", lambda project, plan, **options: executed.append(
                    [Path(plan[0]).read_bytes(), [p.name for p in plan[1:]], options])), \
                patch("database_import.after_load", lambda project: executed.append("after_load")):
            load_dataset(self.project)
        combined, hooks, options = executed[0]
        self.assertTrue(combined.startswith(database_dataset.HEADER.encode()))
        self.assertIn(b"CREATE TABLE `ps_product`", combined)
        self.assertTrue(combined.endswith(b"INSERT INTO `ps_product` VALUES (1);\n"))
        self.assertEqual(hooks, ["010-shop-url.sql", "020-payments-sandbox.sql", "010-extra.sql"])
        self.assertEqual(options, {"first_is_dump": True})
        self.assertEqual(executed[1], "after_load")


if __name__ == "__main__":
    unittest.main()
