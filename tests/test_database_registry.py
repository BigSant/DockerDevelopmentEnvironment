"""The table registry: parsing, classification, profile defaults and schema-resolve."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
import database_registry
from database_registry import classify, dropped, guess, load, parse, registry_file, resolve, unknown_tables
from database_schema import registry_exclusions


class RegistryTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="registry-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = SimpleNamespace(root=self.root, directory=self.root / "docker",
                                       settings={"SCHEMA_DIRECTORY": "database/schema", "PROFILE": "prestashop",
                                                 "PS_DATABASE_PREFIX": "fs_"})

    def write(self, text):
        path = registry_file(self.project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_parse_expands_prefix_and_rejects_bad_rules(self):
        self.assertEqual(parse("{p}orders  orders faker import  # comment\n\n", "r", "fs_"),
                         [("fs_orders", "orders", "faker", "import")])
        for line in ("x config full", "x config everything import", "x config full maybe"):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "r:1"):
                parse(line, "r", "ps_")

    def test_project_entries_win_over_profile_defaults(self):
        self.write("fs_configuration config empty skip\nfs_tmp_* review empty skip\n")
        entries = load(self.project)
        self.assertEqual(classify(entries, "fs_configuration")[1:], ("config", "empty", "skip"))
        self.assertEqual(classify(entries, "fs_tmp_feed")[1], "review")
        self.assertIsNotNone(classify(entries, "fs_product"), "the prestashop profile classifies core tables")
        self.assertEqual(unknown_tables(entries, ["fs_product", "fs_custom_widget"]), ["fs_custom_widget"])

    def test_profile_defaults_cover_the_core_tables(self):
        self.project.settings["PS_DATABASE_PREFIX"] = "ps_"
        entries = load(self.project)
        for table in ("ps_orders", "ps_customer", "ps_product_lang", "ps_configuration", "ps_connections"):
            self.assertIsNotNone(classify(entries, table), table)

    def test_dropped_tables_are_left_out_of_the_snapshot(self):
        self.write("fs_old_feed drop drop -\n")
        with contextlib.redirect_stdout(io.StringIO()):
            excluded = registry_exclusions(self.project, ["fs_product", "fs_old_feed"])
        self.assertEqual(excluded, {"fs_old_feed"})
        self.assertEqual(dropped(load(self.project), ["fs_old_feed", "fs_product"]), ["fs_old_feed"])

    def test_unknown_tables_fail_only_when_classification_is_required(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(registry_exclusions(self.project, ["fs_custom"]), set())
        self.assertIn("not classified", output.getvalue())
        with self.assertRaisesRegex(ValueError, "fs_custom.*\n.*make schema-resolve"):
            registry_exclusions(self.project, ["fs_custom"], require_classified=True)
        self.assertEqual(registry_exclusions(SimpleNamespace(root=self.root, settings={"SCHEMA_DIRECTORY": "db/schema"}), ["x"], True), set(),
                         "a project without a profile or registry is not asked to classify")

    def test_guess_offers_a_starting_point(self):
        self.assertEqual(guess("fs_product_bak_2024", set()), ("review", "empty", "skip"))
        self.assertEqual(guess("tmp_import", set()), ("review", "empty", "skip"))
        self.assertEqual(guess("fs_search_cache", set()), ("cache", "empty", "skip"))
        self.assertEqual(guess("fs_x", {"id_order"}), ("orders", "faker", "import"))
        self.assertEqual(guess("fs_x", {"email"}), ("customers", "faker", "import"))
        self.assertEqual(guess("fs_x", {"id_product"}), ("catalog", "slice", "import"))
        self.assertEqual(guess("fs_x", {"name"}), ("config", "full", "import"))

    def resolve(self, answers, interactive=True):
        replies = iter(answers)
        with patch("database_schema.database_tables", return_value=["fs_product", "fs_a", "fs_b", "fs_c", "fs_d"]), \
                patch.object(database_registry, "table_columns", return_value={"fs_a": {"id_order"}}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            rows = resolve(self.project, ask=lambda prompt: next(replies), interactive=interactive)
        return rows, output.getvalue()

    def test_resolve_in_a_terminal_appends_decisions(self):
        rows, _ = self.resolve(["", "e", "feeds empty skip", "d", "s"])
        self.assertEqual(rows, [("fs_a", "orders", "faker", "import"), ("fs_b", "feeds", "empty", "skip"),
                                ("fs_c", "drop", "drop", "-")])
        entries = load(self.project)
        self.assertEqual(unknown_tables(entries, ["fs_a", "fs_b", "fs_c", "fs_d"]), ["fs_d"])
        self.assertTrue(registry_file(self.project).read_text().startswith("# Project table registry"))
        rows, output = self.resolve(["i"])
        self.assertEqual(rows, [("fs_d", "config", "full", "import")])

    def test_resolve_without_a_terminal_lists_suggestions_and_fails(self):
        with self.assertRaisesRegex(ValueError, "4 tables are not classified"):
            self.resolve([], interactive=False)
        self.assertFalse(registry_file(self.project).exists())

    def test_registry_symlink_is_refused(self):
        registry_file(self.project).parent.mkdir(parents=True)
        registry_file(self.project).symlink_to(self.root / "elsewhere.txt")
        with self.assertRaisesRegex(ValueError, "symlink"):
            database_registry.append_entries(self.project, [("x", "config", "full", "import")])


if __name__ == "__main__":
    unittest.main()
