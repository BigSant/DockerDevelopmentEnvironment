"""Translation migrations: drafted from what changed in the local shop since the base, applied once per database
(recorded in setup_migrations), writing only their own translations: legacy module files, mail subject files and
translation table rows."""

import contextlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT / "docker")]
from fake_database import FakeDatabase  # noqa: E402
from project_translations import (database_loaded, draft_migration, legacy_text, migrate_translations,  # noqa: E402
                                  parse_legacy, php_quote, read_target, take_base)

KEY = "<{shop}prestashop>shop_"
LT = legacy_text({KEY + "aaa": "Krepšelis", KEY + "bbb": "Kliento tekstas", KEY + "ccc": "Tik serveryje"})


class Shop:
    """setup_migrations, ps_lang and ps_translation for FakeDatabase: reads answered from this state, writes applied."""

    def __init__(self, rows=(), applied=()):
        self.rows = [list(row) for row in rows]  # [id_translation, id_lang, domain, theme, key, text]
        self.applied = dict(applied)

    def answer(self, sql):
        if "FROM setup_migrations" in sql:
            return list(self.applied.items())
        if "FROM `ps_lang`" in sql:
            return [("1", "lt-LT"), ("2", "en-US")]
        if "JOIN `ps_lang`" in sql:
            return [({"1": "lt-LT", "2": "en-US"}[str(row[1])], row[2], row[3], row[4], row[5]) for row in self.rows]
        if "FROM `ps_translation`" in sql:
            return [tuple(str(value) for value in row) for row in self.rows]
        return []

    def write(self, sql):
        for name, checksum in re.findall(r"INSERT INTO setup_migrations \(name, checksum, applied_at\) VALUES \('([^']+)', '(\w+)'", sql):
            self.applied[name] = checksum
        for text, ids in re.findall(r"SET translation = '((?:[^']|'')*)' WHERE id_translation IN \(([\d, ]+)\)", sql):
            for row in self.rows:
                if str(row[0]) in ids.split(", "):
                    row[5] = text.replace("''", "'")
        for id_lang, key, text, domain, theme in re.findall(
                r"VALUES \((\d+), '((?:[^']|'')*)', '((?:[^']|'')*)', '([^']+)', (NULL|'[^']*')\);", sql):
            self.rows.append([len(self.rows) + 100, int(id_lang), domain, "" if theme == "NULL" else theme.strip("'"),
                              key.replace("''", "'"), text.replace("''", "'")])


class TranslationMigrationsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="translations-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.local = self.root / "app/public"
        self.target = self.root / "target"
        self.server_code = self.target / "app/prod/public"
        for code in (self.local, self.server_code):
            (code / "modules/shop/translations").mkdir(parents=True)
            (code / "modules/shop/translations/lt.php").write_text(LT)
        self.shop = Shop(rows=[[7, 1, "Modules.Shop.Shop", "", "Add to cart", "Kliento krepšelis"]])
        self.local_project = self.project("local", self.shop)
        self.server = Shop(rows=[[7, 1, "Modules.Shop.Shop", "", "Add to cart", "Kliento krepšelis"],
                                 [8, 1, "Modules.Shop.Shop", "", "Checkout", "Kliento apmokėjimas"]])
        self.prod = self.project("prod", self.server)

    def project(self, environment, state):
        web = self.local if environment == "local" else self.root / "app/prod/public"
        return SimpleNamespace(
            environment=environment, root=self.root, directory=self.root / "app", web_directory=web,
            settings={"PROFILE": "prestashop", "PS_VERSION": "8.2.0", "DEPLOY_PATH": str(self.target), "DEPLOY_SSH": "",
                      "DEPLOY_CONTEXT": "default", "APP_UID": "1000", "APP_GID": "1000"},
            database=FakeDatabase(answer=state.answer, on_write=state.write))

    def quiet(self, function, *arguments, **options):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = function(*arguments, **options)
        self.output = output.getvalue()
        return result

    def translate_locally(self):
        """What a developer does in the local back office: one file text changed, one added, one table row changed, one new."""
        (self.local / "modules/shop/translations/lt.php").write_text(
            legacy_text({KEY + "aaa": "Pirkinių krepšelis", KEY + "bbb": "Kliento tekstas", KEY + "ccc": "Tik serveryje",
                         KEY + "ddd": "Naujas"}))
        self.shop.rows[0][5] = "Į krepšelį"
        self.shop.rows.append([9, 1, "Modules.Shop.Shop", "", "New", "Naujas"])

    def test_php_strings_round_trip_and_files_with_anything_else_are_not_understood(self):
        for text in ("plain", "it's", "back\\slash", "\\'", 'quote " and \\"', "ends with \\", "multi\nline"):
            with self.subTest(text=text):
                self.assertEqual(parse_legacy(legacy_text({"k": text})), {"k": text})
        self.assertEqual(php_quote("it's \\"), "'it\\'s \\\\'")
        self.assertIsNone(parse_legacy(LT + "$_MODULE['x'] = strtoupper('y');\n"))
        self.assertEqual(len(parse_legacy(LT + "// a comment\n?>\n")), 3)

    def test_a_draft_holds_only_what_the_developer_changed_since_the_base(self):
        self.assertIsNone(self.quiet(draft_migration, self.local_project, "cart texts"))
        self.assertIn("There was no base yet", self.output)
        self.translate_locally()
        path = self.quiet(draft_migration, self.local_project, "cart texts")
        self.assertRegex(path.name, r"^\d{14}-cart-texts\.json$")
        self.assertEqual(json.loads(path.read_text()), {
            "legacy": {"modules/shop/translations/lt.php": {KEY + "aaa": "Pirkinių krepšelis", KEY + "ddd": "Naujas"}},
            "database": {"lt-LT": {"Modules.Shop.Shop": {"Add to cart": "Į krepšelį", "New": "Naujas"}}}})
        self.assertEqual(path.parent, self.root / "app/translations/migrations")
        self.assertIsNone(self.quiet(draft_migration, self.local_project, "again"), "the base moved on: nothing new")
        self.assertIn("No translation changed since the base", self.output)
        with self.assertRaisesRegex(ValueError, "Name the migration"):
            draft_migration(self.local_project, " ")
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            draft_migration(self.prod, "x")

    def test_texts_the_local_shop_takes_from_the_server_are_the_base_not_a_change(self):
        self.quiet(take_base, self.local_project)
        # A db-pull import=1 of prod: the client's rows arrive; they are no developer's change.
        self.shop.rows = [list(row) for row in self.server.rows]
        self.quiet(database_loaded, self.local_project)
        self.assertIn("Translations base taken (table rows)", self.output)
        self.shop.rows[1][5] = "Apmokėti"
        path = self.quiet(draft_migration, self.local_project, "checkout")
        self.assertEqual(json.loads(path.read_text())["database"], {"lt-LT": {"Modules.Shop.Shop": {"Checkout": "Apmokėti"}}})
        self.assertEqual(json.loads(path.read_text())["legacy"], {})

    def test_a_migration_writes_its_translations_once_and_nothing_else(self):
        self.quiet(take_base, self.local_project)
        self.translate_locally()
        path = self.quiet(draft_migration, self.local_project, "cart")
        # On the server the client edited bbb and a row of its own since; the migration does not know them.
        (self.server_code / "modules/shop/translations/lt.php").write_text(
            legacy_text({KEY + "aaa": "Krepšelis", KEY + "bbb": "Kliento pataisa", KEY + "ccc": "Tik serveryje"}))
        self.quiet(migrate_translations, self.prod, dry=True)
        self.assertIn("would write 4", self.output)
        self.assertIn("'Krepšelis' -> 'Pirkinių krepšelis'", self.output)
        self.assertEqual(self.server.applied, {}, "dry=1 records nothing")
        self.assertIn("Krepšelis'", (self.server_code / "modules/shop/translations/lt.php").read_text())
        applied = self.quiet(migrate_translations, self.prod)
        self.assertEqual(applied, [f"translations:{path.name}"])
        entries = parse_legacy((self.server_code / "modules/shop/translations/lt.php").read_text())
        self.assertEqual(entries, {KEY + "aaa": "Pirkinių krepšelis", KEY + "bbb": "Kliento pataisa",
                                   KEY + "ccc": "Tik serveryje", KEY + "ddd": "Naujas"})
        rows = {row[4]: row[5] for row in self.server.rows}
        self.assertEqual(rows, {"Add to cart": "Į krepšelį", "Checkout": "Kliento apmokėjimas", "New": "Naujas"})
        # The client edits aaa afterwards: the migration is applied, so it never comes back.
        (self.server_code / "modules/shop/translations/lt.php").write_text(legacy_text({**entries, KEY + "aaa": "Kliento"}))
        self.assertEqual(self.quiet(migrate_translations, self.prod), [])
        self.assertIn("No pending translation migrations", self.output)
        self.assertEqual(parse_legacy((self.server_code / "modules/shop/translations/lt.php").read_text())[KEY + "aaa"], "Kliento")
        path.write_text(path.read_text().replace("Naujas", "Kitas"))
        self.quiet(migrate_translations, self.prod)
        self.assertIn("changed after it was applied here; it is not rerun", self.output)

    def test_a_teammates_migration_applied_locally_is_not_drafted_again(self):
        self.quiet(take_base, self.local_project)
        migrations = self.root / "app/translations/migrations"
        migrations.mkdir(parents=True)
        (migrations / "20261027100000-teammate.json").write_text(json.dumps(
            {"legacy": {"modules/shop/translations/lt.php": {KEY + "eee": "Kolegos"}}, "database": {}}))
        self.quiet(migrate_translations, self.local_project)
        self.assertEqual(parse_legacy((self.local / "modules/shop/translations/lt.php").read_text())[KEY + "eee"], "Kolegos")
        self.assertIsNone(self.quiet(draft_migration, self.local_project, "mine"))
        self.assertIn("No translation changed", self.output)

    def test_what_the_target_lacks_is_noted_and_what_it_cannot_parse_stops(self):
        migrations = self.root / "app/translations/migrations"
        migrations.mkdir(parents=True)
        (migrations / "20261027100000-other.json").write_text(json.dumps(
            {"legacy": {"modules/absent/translations/lt.php": {"<{absent}prestashop>x_1": "Nėra"}},
             "database": {"de-DE": {"Modules.Shop.Shop": {"Checkout": "Kasse"}}}}))
        self.quiet(migrate_translations, self.prod)
        self.assertIn("skipped: modules/absent/translations/lt.php: no module absent here", self.output)
        self.assertIn("skipped: de-DE: no such language here", self.output)
        self.assertFalse((self.server_code / "modules/absent").exists())
        self.assertIn("translations:20261027100000-other.json", self.server.applied)
        (migrations / "20261027110000-shop.json").write_text(json.dumps(
            {"legacy": {"modules/shop/translations/lt.php": {KEY + "aaa": "Naujas"}}}))
        (self.server_code / "modules/shop/translations/lt.php").write_text(LT + "include 'x.php';\n")
        with self.assertRaisesRegex(ValueError, "holds more than \\$_MODULE lines: fix it by hand"):
            self.quiet(migrate_translations, self.prod)
        self.assertNotIn("translations:20261027110000-shop.json", self.server.applied, "not recorded: it reruns once fixed")
        (migrations / "notes.txt").write_text("x")
        with self.assertRaisesRegex(ValueError, "is not named <14 digits>-<name>.json"):
            migrate_translations(self.prod)
        (migrations / "notes.txt").unlink()
        (migrations / "20261027120000-bad.json").write_text(json.dumps({"legacy": {"../x.php": {}}}))
        with self.assertRaisesRegex(ValueError, '"legacy" maps modules'):
            self.quiet(migrate_translations, self.prod)

    def test_a_server_targets_file_is_read_over_ssh_and_its_absence_told_apart(self):
        from project_target import SshTarget
        calls = []

        def run(command, **options):
            calls.append(command)
            path, owner = command[-1].split()[-2:]
            code = {("/p/modules/a/translations/lt.php", "/p/modules/a"): 0, ("/p/modules/b/translations/lt.php", "/p/modules/b"): 3,
                    ("/p/modules/c/translations/lt.php", "/p/modules/c"): 4,
                    ("/p/mails/lt/lang.php", "/p/mails/lt"): 3}.get((path, owner), 1)
            return subprocess.CompletedProcess(command, code, LT.encode() if code == 0 else b"", b"denied" if code == 1 else b"")
        target = SshTarget("deploy@shop.example", run)
        self.assertEqual(read_target(target, "/p/modules/a/translations/lt.php"), ("file", LT))
        self.assertEqual(read_target(target, "/p/modules/b/translations/lt.php"), ("missing", None))
        self.assertEqual(read_target(target, "/p/modules/c/translations/lt.php"), ("no-module", None))
        self.assertEqual(read_target(target, "/p/mails/lt/lang.php"), ("missing", None), "a mail subject file needs mails/<iso>/")
        with self.assertRaisesRegex(ValueError, "Cannot read deploy@shop.example:/p/x/translations/lt.php .exit 1.: denied"):
            read_target(target, "/p/x/translations/lt.php")
        self.assertIn("BatchMode=yes", calls[0])

    def test_mail_subjects_are_drafted_and_migrated_like_module_files(self):
        subjects = legacy_text({"Order confirmation": "Užsakymo patvirtinimas", "Client subject": "Kliento tema"}, "_LANGMAIL")
        self.assertIn("global $_LANGMAIL;\n$_LANGMAIL = array();\n$_LANGMAIL['Order confirmation'] = ", subjects)
        self.assertEqual(parse_legacy(subjects + "?>\n", "_LANGMAIL")["Client subject"], "Kliento tema")
        self.assertIsNone(parse_legacy(subjects), "a $_LANGMAIL file is no $_MODULE file")
        for code in (self.local, self.server_code):
            (code / "mails/lt").mkdir(parents=True)
            (code / "mails/lt/lang.php").write_text(subjects)
            (code / "mails/lt/order_conf.html").write_text("<p>template</p>")
        self.quiet(take_base, self.local_project)
        (self.local / "mails/lt/lang.php").write_text(legacy_text(
            {"Order confirmation": "Jūsų užsakymas", "Client subject": "Kliento tema", "New subject": "Nauja tema"}, "_LANGMAIL"))
        path = self.quiet(draft_migration, self.local_project, "subjects")
        self.assertEqual(json.loads(path.read_text())["legacy"],
                         {"mails/lt/lang.php": {"Order confirmation": "Jūsų užsakymas", "New subject": "Nauja tema"}},
                         "the templates beside it are code, not translations")
        (self.server_code / "mails/lt/lang.php").write_text(legacy_text(
            {"Order confirmation": "Užsakymo patvirtinimas", "Client subject": "Kliento pataisa"}, "_LANGMAIL"))
        self.quiet(migrate_translations, self.prod)
        self.assertEqual(parse_legacy((self.server_code / "mails/lt/lang.php").read_text(), "_LANGMAIL"),
                         {"Order confirmation": "Jūsų užsakymas", "Client subject": "Kliento pataisa", "New subject": "Nauja tema"})
        migrations = self.root / "app/translations/migrations"
        (migrations / "20991231000000-lv.json").write_text(json.dumps(
            {"legacy": {"mails/lv/lang.php": {"Order confirmation": "Pasūtījums"},
                        "themes/framework/mails/lt/lang.php": {"Theme subject": "Temos tema"}}}))
        (self.server_code / "themes/framework/mails/lt").mkdir(parents=True)
        self.quiet(migrate_translations, self.prod)
        self.assertIn("skipped: mails/lv/lang.php: no mails/lv/ here (language not installed)", self.output)
        self.assertFalse((self.server_code / "mails/lv").exists())
        self.assertEqual(parse_legacy((self.server_code / "themes/framework/mails/lt/lang.php").read_text(), "_LANGMAIL"),
                         {"Theme subject": "Temos tema"})
        (migrations / "20991231000001-bad.json").write_text(json.dumps({"legacy": {"mails/lt/order_conf.html": {"a": "b"}}}))
        with self.assertRaisesRegex(ValueError, '"legacy" maps modules/<module>/translations/<iso>.php or mails/<iso>/lang.php'):
            self.quiet(migrate_translations, self.prod)

    def test_generic_projects_and_prestashop_16(self):
        self.prod.settings["PROFILE"] = ""
        with self.assertRaisesRegex(ValueError, "belongs to PrestaShop"):
            migrate_translations(self.prod)
        self.prod.settings.update(PROFILE="prestashop", PS_VERSION="1.6.1.24")
        migrations = self.root / "app/translations/migrations"
        migrations.mkdir(parents=True)
        (migrations / "20261027100000-x.json").write_text(json.dumps({"database": {"lt-LT": {"Modules.Shop.Shop": {"a": "b"}}}}))
        with self.assertRaisesRegex(ValueError, "PrestaShop 1.6 has no translation table"):
            self.quiet(migrate_translations, self.prod)


if __name__ == "__main__":
    unittest.main()
