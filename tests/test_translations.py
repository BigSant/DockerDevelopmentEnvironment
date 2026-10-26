"""Translations in the environment repository: legacy module files and translation table rows, written on the target
when the repository brings them new or changed since the last deploy (setup_translations), and exported locally."""

import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "docker")]
from fake_database import FakeDatabase  # noqa: E402
from project_translations import (apply_translations, entry_id, checksum, export_translations, legacy_text,  # noqa: E402
                                  module_domains, parse_legacy, php_quote)

LT = """<?php

global $_MODULE;
$_MODULE = array();
$_MODULE['<{shop}prestashop>shop_aaa'] = 'Krepšelis';
$_MODULE['<{shop}prestashop>shop_bbb'] = 'Kliento tekstas';
$_MODULE['<{shop}prestashop>shop_ccc'] = 'Tik serveryje';
"""


class Bookkeeping:
    """A setup_translations table and a translation table for FakeDatabase: reads answered, writes applied."""

    def __init__(self, written=None, rows=None):
        self.written = dict(written or {})  # id -> checksum
        self.rows = list(rows or [])  # [id_translation, id_lang, domain, theme, key, text]
        self.table = bool(written)

    def answer(self, sql):
        if "information_schema.TABLES" in sql:
            return [("1",)] if self.table else []
        if "FROM setup_translations" in sql:
            return list(self.written.items())
        if "FROM `ps_lang`" in sql:
            return [("1", "lt-LT"), ("2", "en-US")]
        if "FROM `ps_translation`" in sql and "JOIN" in sql:  # the export's read of the local shop
            return [("lt-LT", row[2], row[3], row[4], row[5]) for row in self.rows if str(row[1]) == "1"]
        if "FROM `ps_translation`" in sql:
            return [tuple(str(value) for value in row) for row in self.rows]
        return []

    def write(self, sql):
        if sql.startswith("CREATE TABLE IF NOT EXISTS setup_translations"):
            self.table = True
        for identity, value in re.findall(r"\('([0-9a-f]{64})', '([0-9a-f]{64})', NOW\(\)\)", sql):
            self.written[identity] = value


class TranslationsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="translations-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "target"
        self.code = self.target / "app/prod/public"
        (self.code / "modules/shop/translations").mkdir(parents=True)
        (self.code / "modules/shop/translations/lt.php").write_text(LT)
        self.repository = self.root / "app/translations"
        self.book = Bookkeeping()
        self.project = SimpleNamespace(
            environment="prod", root=self.root, directory=self.root / "app", web_directory=self.root / "app/prod/public",
            settings={"PROFILE": "prestashop", "PS_VERSION": "8.2.0", "DEPLOY_PATH": str(self.target), "DEPLOY_SSH": "",
                      "DEPLOY_CONTEXT": "default", "APP_UID": "1000", "APP_GID": "1000"},
            database=FakeDatabase(answer=self.book.answer, on_write=self.book.write))

    def repository_file(self, relative, entries):
        path = self.repository / "legacy" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(legacy_text(entries))

    def apply(self, dry=False):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            report = apply_translations(self.project, dry=dry)
        return report, output.getvalue()

    def target_entries(self):
        return parse_legacy((self.code / "modules/shop/translations/lt.php").read_text())

    def test_php_strings_round_trip_and_files_with_anything_else_are_not_understood(self):
        for text in ("plain", "it's", "back\\slash", "\\'", 'quote " and \\"', "ends with \\", "multi\nline"):
            with self.subTest(text=text):
                self.assertEqual(parse_legacy(legacy_text({"k": text})), {"k": text})
        self.assertEqual(php_quote("it's \\"), "'it\\'s \\\\'")
        self.assertIsNone(parse_legacy(LT + "$_MODULE['x'] = strtoupper('y');\n"))
        self.assertIsNone(parse_legacy(LT + "echo 'hi';\n"))
        self.assertEqual(len(parse_legacy(LT + "// a comment\n?>\n")), 3)

    def test_a_new_or_changed_translation_is_written_an_unchanged_one_leaves_the_clients_edit(self):
        key = "<{shop}prestashop>shop_"
        # The last deploy wrote aaa and bbb; the client edited bbb in the back office since.
        self.book = Bookkeeping({entry_id("file", "modules/shop/translations/lt.php", key + "aaa"): checksum("Krepšelis"),
                                 entry_id("file", "modules/shop/translations/lt.php", key + "bbb"): checksum("Senas tekstas")})
        self.project.database = FakeDatabase(answer=self.book.answer, on_write=self.book.write)
        self.repository_file("modules/shop/translations/lt.php",
                             {key + "aaa": "Pirkinių krepšelis", key + "bbb": "Senas tekstas", key + "ddd": "Naujas"})
        report, output = self.apply(dry=True)
        self.assertEqual(self.target_entries()[key + "aaa"], "Krepšelis", "dry=1 changes nothing")
        self.assertIn("would overwrite modules/shop/translations/lt.php '<{shop}prestashop>shop_aaa': 'Krepšelis' -> "
                      "'Pirkinių krepšelis'", output)
        self.assertFalse([sql for sql in self.project.database.executed if "setup_translations" in sql])
        report, output = self.apply()
        entries = self.target_entries()
        self.assertEqual(entries[key + "aaa"], "Pirkinių krepšelis", "changed in the repository: written over the target")
        self.assertEqual(entries[key + "bbb"], "Kliento tekstas", "unchanged in the repository: the client's edit stays")
        self.assertEqual(entries[key + "ccc"], "Tik serveryje", "a key only the target has stays")
        self.assertEqual(entries[key + "ddd"], "Naujas")
        self.assertEqual(list(entries), [key + "aaa", key + "bbb", key + "ccc", key + "ddd"], "the target's order, new ones last")
        self.assertEqual((report.written, len(report.over), report.kept), (2, 1, 1))
        # The next deploy with the same repository writes nothing: the client may edit aaa now.
        (self.code / "modules/shop/translations/lt.php").write_text(
            legacy_text({**entries, key + "aaa": "Kliento krepšelis"}))
        report, _ = self.apply()
        self.assertEqual(self.target_entries()[key + "aaa"], "Kliento krepšelis")
        self.assertEqual((report.written, report.kept), (0, 3))

    def test_a_first_deploy_writes_every_translation_and_records_the_equal_ones(self):
        key = "<{shop}prestashop>shop_"
        self.repository_file("modules/shop/translations/lt.php", {key + "aaa": "Krepšelis", key + "bbb": "Programuotojo"})
        self.repository_file("modules/shop/translations/en.php", {key + "aaa": "Cart"})
        self.repository_file("modules/absent/translations/lt.php", {"<{absent}prestashop>x_1": "Nėra"})
        report, output = self.apply()
        self.assertEqual(self.target_entries()[key + "bbb"], "Programuotojo")
        self.assertEqual(parse_legacy((self.code / "modules/shop/translations/en.php").read_text()), {key + "aaa": "Cart"})
        self.assertFalse((self.code / "modules/absent").exists(), "no translation file for a module the target lacks")
        self.assertIn("skipped: modules/absent/translations/lt.php: the target has no module absent", output)
        self.assertEqual((report.written, report.equal), (2, 1))
        self.assertEqual(len(self.book.written), 3, "equal ones are recorded too; the absent module's wait")

    def test_a_target_file_with_more_than_entries_is_not_rewritten(self):
        (self.code / "modules/shop/translations/lt.php").write_text(LT + "include 'other.php';\n")
        self.repository_file("modules/shop/translations/lt.php", {"<{shop}prestashop>shop_aaa": "Naujas"})
        _, output = self.apply()
        self.assertIn("include 'other.php'", (self.code / "modules/shop/translations/lt.php").read_text())
        self.assertIn("holds more than $_MODULE lines; not rewritten", output)
        self.assertEqual(self.book.written, {})

    def test_translation_table_rows_are_updated_or_inserted_the_same_way(self):
        texts = {"Add to cart": "Į krepšelį", "Checkout": "Apmokėti", "New": "Naujas"}
        path = self.repository / "database/lt-LT/Modules.Shop.Shop.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"locale": "lt-LT", "domain": "Modules.Shop.Shop", "theme": None, "translations": texts}))
        theme = self.repository / "database/en-US/Shop.Theme.Actions@classic.json"
        theme.parent.mkdir(parents=True)
        theme.write_text(json.dumps({"locale": "en-US", "domain": "Shop.Theme.Actions", "theme": "classic",
                                     "translations": {"Buy": "Buy now"}}))
        missing = self.repository / "database/de-DE/Modules.Shop.Shop.json"
        missing.parent.mkdir(parents=True)
        missing.write_text(json.dumps({"locale": "de-DE", "domain": "Modules.Shop.Shop", "translations": {"Checkout": "Kasse"}}))
        self.book = Bookkeeping({entry_id("database", "lt-LT", "Modules.Shop.Shop", "", "Checkout"): checksum("Apmokėti")},
                                rows=[[7, 1, "Modules.Shop.Shop", "", "Add to cart", "Kliento"],
                                      [8, 1, "Modules.Shop.Shop", "", "Checkout", "Kliento apmokėjimas"]])
        self.project.database = FakeDatabase(answer=self.book.answer, on_write=self.book.write)
        report, output = self.apply()
        sql = "\n".join(self.project.database.executed)
        self.assertIn("UPDATE `ps_translation` SET translation = 'Į krepšelį' WHERE id_translation IN (7);", sql)
        self.assertNotIn("Apmokėti", sql.split("REPLACE INTO")[0], "unchanged since the last deploy: the client's row stays")
        self.assertIn("INSERT INTO `ps_translation` (id_lang, `key`, translation, domain, theme) VALUES (1, 'New', 'Naujas', "
                      "'Modules.Shop.Shop', NULL);", sql)
        self.assertIn("VALUES (2, 'Buy', 'Buy now', 'Shop.Theme.Actions', 'classic');", sql)
        self.assertNotIn("Kasse", sql)
        self.assertIn("skipped: de-DE: the target has no such language", output)
        self.assertEqual((report.written, len(report.over), report.kept), (3, 1, 1))
        self.project.settings["PS_VERSION"] = "1.6.1.24"
        with self.assertRaisesRegex(ValueError, "PrestaShop 1.6 has no translation table"):
            self.apply()

    def test_a_server_targets_file_is_read_over_ssh_and_its_absence_told_apart(self):
        import subprocess
        from project_target import SshTarget
        from project_translations import read_target
        calls = []

        def run(command, **options):
            calls.append(command)
            code = {"/p/modules/a/translations/lt.php": 0, "/p/modules/b/translations/lt.php": 3,
                    "/p/modules/c/translations/lt.php": 4}.get(command[-1].split()[-1], 1)
            return subprocess.CompletedProcess(command, code, LT.encode() if code == 0 else b"", b"denied" if code == 1 else b"")
        target = SshTarget("deploy@shop.example", run)
        self.assertEqual(read_target(target, "/p/modules/a/translations/lt.php"), ("file", LT))
        self.assertEqual(read_target(target, "/p/modules/b/translations/lt.php"), ("missing", None))
        self.assertEqual(read_target(target, "/p/modules/c/translations/lt.php"), ("no-module", None))
        with self.assertRaisesRegex(ValueError, "Cannot read deploy@shop.example:/p/x/translations/lt.php .exit 1.: denied"):
            read_target(target, "/p/x/translations/lt.php")
        self.assertIn("BatchMode=yes", calls[0])

    def test_nothing_to_apply_and_generic_projects(self):
        self.assertIsNone(self.apply()[0])
        self.project.settings["PROFILE"] = ""
        with self.assertRaisesRegex(ValueError, "belongs to PrestaShop"):
            self.apply()

    def test_export_writes_the_local_modules_files_and_domains_into_the_repository(self):
        local = self.root / "app/public"
        (local / "modules/shop/translations").mkdir(parents=True)
        (local / "modules/shop/translations/lt.php").write_text(LT)
        (local / "modules/shop/translations/en.php").write_text(legacy_text({"<{shop}prestashop>shop_aaa": "Cart"}))
        (local / "modules/shop/translations/index.php").write_text("<?php\n")
        (local / "themes/classic/modules/shop/translations").mkdir(parents=True)
        (local / "themes/classic/modules/shop/translations/lt.php").write_text(legacy_text({"<{shop}classic>shop_x": "Tema"}))
        self.repository_file("modules/shop/translations/fr.php", {"old": "gone"})
        self.book = Bookkeeping(rows=[[1, 1, "Modules.Shop.Shop", "", "Checkout", "Apmokėti"],
                                      [2, 1, "Modules.Other.Shop", "", "X", "Y"]])
        self.project.database = FakeDatabase(answer=self.book.answer)
        self.project.environment, self.project.web_directory = "local", local
        with contextlib.redirect_stdout(io.StringIO()) as output:
            export_translations(self.project, ["shop"], [], ["lt"])
        exported = sorted(path.relative_to(self.repository).as_posix() for path in self.repository.rglob("*") if path.is_file())
        self.assertEqual(exported, ["database/lt-LT/Modules.Shop.Shop.json", "legacy/modules/shop/translations/fr.php",
                                    "legacy/modules/shop/translations/lt.php",
                                    "legacy/themes/classic/modules/shop/translations/lt.php"],
                         "only lt: fr stays as it was, en is not exported")
        self.assertEqual(parse_legacy((self.repository / "legacy/modules/shop/translations/lt.php").read_text()),
                         parse_legacy(LT))
        data = json.loads((self.repository / "database/lt-LT/Modules.Shop.Shop.json").read_text())
        self.assertEqual(data, {"locale": "lt-LT", "domain": "Modules.Shop.Shop", "theme": None,
                                "translations": {"Checkout": "Apmokėti"}})
        self.assertIn("t.domain LIKE 'Modules.Shop.%'", self.project.database.queried[-1])
        self.assertIn("Exported 2 legacy files and 1 translation table rows", output.getvalue())
        self.assertEqual(module_domains("ps_shoppingcart"), "Modules.Shoppingcart.*")
        self.assertEqual(module_domains("my_module"), "Modules.Mymodule.*")
        for arguments, message in (((["shop"], [], []), "ENV=local"),):
            self.project.environment = "prod"
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                export_translations(self.project, *arguments)
        self.project.environment = "local"
        for arguments, message in ((([], [], []), "Name what to export"), ((["../x"], [], []), "Not a module name"),
                                   (([], ["Modules.%"], []), "Not a domain")):
            with self.subTest(arguments=arguments), self.assertRaisesRegex(ValueError, message):
                export_translations(self.project, *arguments)


if __name__ == "__main__":
    unittest.main()
