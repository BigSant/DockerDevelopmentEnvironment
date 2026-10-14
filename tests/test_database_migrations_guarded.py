"""Guarded migration drafts, the commit gate and migrate ordering, with real Git and a fake DB."""

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
import database_migrations_guarded as guarded
from database_migrations_guarded import commit_problems, draft_text, parse_table, split_rollback, untouched_draft, write_drafts
from database_schema import commit_schema, export_schema, git
from fake_database import FakeDatabase
from test_database_schema import snapshot

PRODUCT = """CREATE TABLE `product` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(32) DEFAULT NULL,
  `old_note` text,
  PRIMARY KEY (`id`),
  KEY `code` (`code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
PRODUCT_AFTER = """CREATE TABLE `product` (
  `id` int NOT NULL AUTO_INCREMENT,
  `code` varchar(64) DEFAULT NULL,
  `name` varchar(128) NOT NULL DEFAULT 'it''s',
  PRIMARY KEY (`id`),
  KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
FEED = "CREATE TABLE `feed` (\n  `id` int NOT NULL,\n  PRIMARY KEY (`id`)\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;\n"


class DraftTest(unittest.TestCase):
    def test_parse_table_splits_columns_keys_and_options(self):
        table = parse_table(PRODUCT)
        self.assertEqual(list(table["columns"]), ["id", "code", "old_note"])
        self.assertEqual(table["keys"], {"PRIMARY": "PRIMARY KEY (`id`)", "code": "KEY `code` (`code`)"})
        self.assertEqual(table["options"], ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")

    def test_additions_are_guarded_and_losses_are_todo(self):
        text = draft_text({"product": PRODUCT, "legacy": FEED.replace("feed", "legacy")},
                          {"product": PRODUCT_AFTER, "feed": FEED})
        self.assertIn("CREATE TABLE IF NOT EXISTS `feed`", text)
        self.assertIn("ADD COLUMN `name` varchar(128) NOT NULL DEFAULT ''it''''s'' AFTER `code`", text)
        self.assertIn("COLUMN_NAME = 'name'", text)
        self.assertIn("MODIFY COLUMN `code` varchar(64) DEFAULT NULL", text)
        self.assertIn("ADD KEY `name` (`name`)", text)
        self.assertIn("INDEX_NAME = 'code'", text, "a removed key is dropped only when it still exists")
        forward, back = split_rollback(text)
        self.assertIn("'ALTER TABLE `product` DROP COLUMN `old_note`', 'DO 0');\nPREPARE", forward, "a drop is a statement like any other")
        self.assertIn("\nDROP TABLE IF EXISTS `legacy`;", forward, "a dropped table is dropped, even beside a lookalike")
        self.assertNotIn("-- TODO:", text.split("\n", 3)[3].replace('"-- TODO:"', ""), "nothing left to a person here")
        # The way back: drop what was added, restore what changed, recreate what was removed (structure only).
        self.assertIn("DROP TABLE IF EXISTS `feed`;", back)
        self.assertIn("ALTER TABLE `product` DROP COLUMN `name`", back)
        self.assertIn("MODIFY COLUMN `code` varchar(32) DEFAULT NULL", back)
        self.assertIn("ADD COLUMN `old_note` text AFTER `code`", back)
        self.assertIn("CREATE TABLE IF NOT EXISTS `legacy`", back)
        self.assertNotIn("TODO", back)
        self.assertTrue(untouched_draft(text))
        self.assertFalse(untouched_draft(text.replace("DROP TABLE IF EXISTS `legacy`;", "RENAME TABLE legacy TO feed;", 1)))

    def test_same_change_gives_the_same_marker(self):
        first = draft_text({}, {"feed": FEED}).splitlines()[0]
        self.assertEqual(first, draft_text({}, {"feed": FEED}).splitlines()[0])


class CommitGateTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="guarded-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "commit.gpgsign", "false")
        self.project = SimpleNamespace(root=self.root, directory=self.root / "docker", environment="local",
                                       settings={"SCHEMA_DIRECTORY": "database/schema", "DATABASE_NAME": "shop"})
        self.registry = self.root / "database/table-registry.txt"
        self.registry.parent.mkdir(parents=True)
        self.registry.write_text("product catalog slice import\nfeed config full import\n")
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.database({"product": PRODUCT})
        export_schema(self.project, snapshot({"product": PRODUCT}))
        git(self.root, "add", "-A", "database")
        git(self.root, "commit", "-q", "-m", "baseline")

    def database(self, tables):
        self.tables = tables
        stack = contextlib.ExitStack()
        stack.enter_context(patch("database_schema.database_tables", lambda project: sorted(self.tables)))
        stack.enter_context(patch("database_schema.read_schema",
                                  lambda project, require_classified=False: snapshot(self.tables)))
        self.project.database = FakeDatabase()
        self.addCleanup(stack.close)

    def commit(self):
        commit_schema(self.project)

    def test_schema_change_needs_a_finished_staged_migration(self):
        # A changed key is still left to a person (it may reject existing rows).
        self.tables = {"product": PRODUCT.replace("KEY `code` (`code`)", "UNIQUE KEY `code` (`code`)"), "feed": FEED}
        with self.assertRaisesRegex(ValueError, "schema different: table-product.sql") as stopped:
            self.commit()
        drafts = sorted((self.root / "database/migrations").glob("*.sql"))
        self.assertEqual(len(drafts), 1)
        self.assertIn(str(drafts[0]), str(stopped.exception))
        git(self.root, "add", "-A", "database")
        with self.assertRaisesRegex(ValueError, "TODO left in migration"):
            self.commit()
        self.assertEqual(sorted((self.root / "database/migrations").glob("*.sql")), drafts, "no second draft")
        text = drafts[0].read_text()
        drafts[0].write_text("\n".join(line for line in text.splitlines() if not line.startswith("-- TODO:")) + "\n")
        with self.assertRaisesRegex(ValueError, "TODO left"):
            self.commit()  # the edited file is not staged yet
        git(self.root, "add", "-A", "database")
        self.commit()
        self.assertIn(drafts[0].name, self.project.database.executed[-1],
                      "this DB has the change: the migration is recorded as applied")

    def test_staged_snapshot_without_a_migration_is_stopped(self):
        self.tables = {"product": PRODUCT, "feed": FEED}
        export_schema(self.project, snapshot(self.tables))
        git(self.root, "add", "-A", "database/schema")
        self.assertEqual(commit_problems(self.project, snapshot(self.tables)),
                         ["the schema of the main database changed but no new migration is staged in migrations/"])
        with self.assertRaisesRegex(ValueError, "no new migration"):
            self.commit()

    def test_untouched_draft_is_regenerated_and_an_edited_one_is_kept(self):
        self.tables = {"product": PRODUCT, "feed": FEED}
        [first] = write_drafts(self.project, snapshot(self.tables))
        self.tables = {"product": PRODUCT_AFTER, "feed": FEED}
        [second] = write_drafts(self.project, snapshot(self.tables))
        self.assertEqual(first, second)
        self.assertIn("MODIFY COLUMN", second.read_text())
        second.write_text(second.read_text() + "-- reviewed\n")
        self.tables = {"product": PRODUCT_AFTER}
        self.assertEqual(write_drafts(self.project, snapshot(self.tables)), [])
        self.assertTrue(second.read_text().endswith("-- reviewed\n"))

    def test_pulled_migrations_must_be_applied_before_the_export(self):
        migrations = self.root / "database/migrations"
        migrations.mkdir()
        (migrations / "20260101000000-pulled.sql").write_text("SELECT 1;\n")
        git(self.root, "add", "-A", "database")
        git(self.root, "commit", "-q", "--no-verify", "-m", "a colleague's migration")
        before = (self.root / "database/schema/table-product.sql").read_text()
        self.tables = {"product": PRODUCT_AFTER}
        with self.assertRaisesRegex(ValueError, "(?s)1 committed migrations are not applied .*make db-migrate first"):
            self.commit()
        self.assertEqual((self.root / "database/schema/table-product.sql").read_text(), before, "nothing was exported")

    def test_off_keeps_the_snapshot_check_without_migrations(self):
        self.project.settings["DATABASE_MIGRATIONS"] = "off"
        self.tables = {"product": PRODUCT, "feed": FEED}
        with self.assertRaisesRegex(ValueError, "schema not staged: table-feed.sql") as stopped:
            self.commit()
        self.assertNotIn("migration", str(stopped.exception).lower().replace("git add", ""))
        self.assertFalse((self.root / "database/migrations").exists())
        git(self.root, "add", "-A", "database")
        self.commit()
        self.assertEqual(self.project.database.executed, [])

    def test_each_changed_schema_gets_its_own_draft_and_needs_its_own_migration(self):
        self.project.settings.update(DATABASE_NAME="shop", DATABASE_EXTRA_SCHEMAS="shop_git")
        refs = FEED.replace("feed", "refs")
        self.tables = {"product": PRODUCT, "shop_git.refs": refs}
        export_schema(self.project, snapshot(self.tables))
        git(self.root, "add", "-A", "database")
        self.commit()  # The schema joins the snapshot: its baseline, no migration.
        git(self.root, "commit", "-q", "--no-verify", "-m", "shop_git joins the snapshot")
        self.tables = {"product": PRODUCT_AFTER, "shop_git.refs": refs.replace("NOT NULL,", "NOT NULL,\n  `sha` char(40) DEFAULT NULL,")}
        with self.assertRaises(ValueError) as stopped:
            self.commit()
        drafts = sorted(path.relative_to(self.root / "database/migrations").as_posix()
                        for path in (self.root / "database/migrations").rglob("*.sql"))
        self.assertEqual([path.split("/")[0] for path in drafts], ["shop", "shop_git"])
        git_draft = next((self.root / "database/migrations/shop_git").glob("*.sql")).read_text()
        self.assertIn("ALTER TABLE `refs` ADD COLUMN `sha`", git_draft, "it runs in its schema: no qualifier needed")
        self.assertIn("ALTER TABLE `refs` DROP COLUMN `sha`", split_rollback(git_draft)[1])
        (self.root / "database/migrations/shop_git").rename(self.root / "shop_git-later")
        git(self.root, "add", "-A", "database")
        problems = commit_problems(self.project, snapshot(self.tables))
        self.assertIn("the schema of shop_git changed but no new migration is staged in migrations/shop_git/", problems)

    def test_the_draft_command_needs_no_commit_and_ignores_what_is_staged(self):
        from database_migrations_guarded import draft_migrations
        self.assertEqual(draft_migrations(self.project), [], "the DB matches the last commit")
        self.tables = {"product": PRODUCT}
        self.tables = {"feed": FEED, "product": PRODUCT}
        export_schema(self.project, snapshot(self.tables))
        git(self.root, "add", "-A", "database")  # already exported and staged: the draft still compares with HEAD
        [draft] = draft_migrations(self.project, name="add-feed")
        self.assertTrue(draft.name.endswith("-add-feed.sql"))
        self.assertIn("CREATE TABLE IF NOT EXISTS `feed`", draft.read_text())
        self.assertEqual(draft_migrations(self.project, name="add-feed"), [draft], "the untouched draft is refreshed in place")
        draft.write_text(draft.read_text().replace("CREATE TABLE IF NOT EXISTS", "CREATE TABLE  IF NOT EXISTS"))
        draft.write_text(guarded.re.sub(r"(?m)^-- Draft checksum: .*$", "-- Draft checksum: " + guarded.hashlib.sha256(
            draft.read_text().split("-- Draft checksum: ", 1)[1].split("\n", 1)[1].encode()).hexdigest(), draft.read_text()))
        draft_migrations(self.project, name="add-feed")
        self.assertIn("CREATE TABLE IF NOT EXISTS `feed`", draft.read_text(), "an untouched older draft gets the current wording")
        with self.assertRaisesRegex(ValueError, "lowercase letters"):
            draft_migrations(self.project, name="Bad Name")
        self.project.settings["DATABASE_MIGRATIONS"] = "off"
        with self.assertRaisesRegex(ValueError, "DATABASE_MIGRATIONS=off"):
            draft_migrations(self.project)

    def test_a_dropped_table_takes_its_registry_line_with_it(self):
        from database_migrations_guarded import draft_migrations
        self.registry.write_text("product catalog slice import\nfeed config full import\nfe* config full import\n")
        self.tables = {"product": PRODUCT, "feed": FEED}
        export_schema(self.project, snapshot(self.tables))
        git(self.root, "add", "-A", "database")
        git(self.root, "commit", "-q", "--no-verify", "-m", "feed")
        self.tables = {"product": PRODUCT}
        export_schema(self.project, snapshot(self.tables))
        self.assertEqual(self.registry.read_text(), "product catalog slice import\nfe* config full import\n",
                         "the export drops the exact line; a wildcard stays")
        self.registry.write_text("product catalog slice import\nfeed config full import\n")
        [draft] = draft_migrations(self.project, name="drop-feed")
        self.assertEqual(self.registry.read_text(), "product catalog slice import\n", "already exported: the draft does it")
        self.assertIn("DROP TABLE IF EXISTS `feed`;", draft.read_text())

    def test_unstaged_registry_and_bad_names_are_reported(self):
        self.registry.write_text(self.registry.read_text() + "extra config full import\n")
        (self.root / "database/migrations").mkdir()
        (self.root / "database/migrations/fix.sql").write_text("SELECT 1;\n")
        git(self.root, "add", "database/migrations")
        problems = commit_problems(self.project, snapshot({"product": PRODUCT}))
        self.assertIn("migration file name must look like [<schema>/]20260101120000-short-name.sql: fix.sql", problems)
        self.assertIn("stage the table registry: git add database/table-registry.txt", problems)

    def test_first_snapshot_is_a_baseline_without_a_migration(self):
        git(self.root, "rm", "-rq", "database")
        git(self.root, "commit", "-q", "-m", "remove")
        self.assertEqual(write_drafts(self.project, snapshot({"product": PRODUCT})), [])
        self.assertFalse((self.root / "database/migrations").exists())


class MigrateTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="migrate-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.applied = {}
        self.queries = []
        self.project = SimpleNamespace(root=self.root, settings={"SCHEMA_DIRECTORY": "database/schema"},
                                       database=FakeDatabase(answer=self.answer, on_write=self.write))
        self.migrations = self.root / "database/migrations"
        self.migrations.mkdir(parents=True)

    def answer(self, sql):
        """setup_migrations as the fake database's state: reads come from self.applied, writes change it."""
        self.queries.append(sql)
        if sql.startswith("SELECT name, checksum"):
            return list(self.applied.items())
        if sql.startswith("SELECT name FROM setup_migrations ORDER BY"):
            return [(name,) for name in reversed(list(self.applied))]
        return []

    def write(self, sql):
        self.queries.append(sql)
        if "INSERT INTO setup_migrations" in sql:
            name, checksum = sql.split("VALUES (")[1].split(", NOW()")[0].replace("'", "").split(", ")
            self.applied[name] = checksum
        if "DELETE FROM setup_migrations" in sql:
            del self.applied[sql.split("name = '")[1].split("'")[0]]

    def migrate(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            applied = guarded.migrate(self.project)
        return applied, output.getvalue()

    def test_pending_run_in_name_order_once(self):
        (self.migrations / "20260102000000-second.sql").write_text("SELECT 2;\n")
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n")
        applied, _ = self.migrate()
        self.assertEqual(applied, ["20260101000000-first.sql", "20260102000000-second.sql"])
        self.assertEqual(self.migrate()[0], [])
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 11;\n")
        applied, output = self.migrate()
        self.assertEqual(applied, [])
        self.assertIn("changed after it was applied", output)

    def rollback(self, **arguments):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            undone = guarded.rollback(self.project, **arguments)
        return undone, output.getvalue()

    def test_rollback_runs_the_rollback_part_newest_first_and_forgets_the_migration(self):
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 'up 1';\n-- Rollback: back\nSELECT 'down 1';\n")
        (self.migrations / "20260102000000-second.sql").write_text("SELECT 'up 2';\n-- Rollback: back\nSELECT 'down 2';\n")
        self.migrate()
        self.assertFalse([sql for sql in self.queries if "down" in sql], "db-migrate never runs the rollback part")
        undone, _ = self.rollback(steps=1, dry=True)
        self.assertEqual((undone, list(self.applied)), ([], ["20260101000000-first.sql", "20260102000000-second.sql"]))
        undone, _ = self.rollback()
        self.assertEqual(undone, ["20260102000000-second.sql"])
        self.assertIn("SELECT 'down 2';", self.queries[-1])
        self.assertEqual(list(self.applied), ["20260101000000-first.sql"])
        self.migrate()
        undone, _ = self.rollback(to="20260101000000-first.sql")
        self.assertEqual(undone, ["20260102000000-second.sql"], "to= keeps the named migration")

    def test_a_migration_without_a_finished_rollback_is_refused_before_anything_runs(self):
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n-- Rollback: back\nSELECT 'down 1';\n")
        (self.migrations / "20260102000000-old.sql").write_text("SELECT 2;\n")
        self.migrate()
        with self.assertRaisesRegex(ValueError, "has no \"-- Rollback:\" part"):
            self.rollback(steps=2)
        self.assertEqual(len(self.applied), 2, "nothing was rolled back")
        (self.migrations / "20260102000000-old.sql").write_text("SELECT 2;\n-- Rollback:\n-- TODO: restore\n")
        with self.assertRaisesRegex(ValueError, "TODO in its rollback"):
            self.rollback()

    def test_a_failing_migration_names_the_reason_and_is_not_recorded(self):
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n")
        (self.migrations / "20260102000000-broken.sql").write_text("SELECT 'broken';\n")
        self.project.database.fail_on = lambda operation, text: operation == "write" and "'broken'" in text
        with self.assertRaisesRegex(ValueError, r"^Migration 20260102000000-broken.sql failed \(write failed \(fake\)\); "
                                                r"it is not recorded"):
            self.migrate()
        self.assertEqual(list(self.applied), ["20260101000000-first.sql"])

    def test_completing_the_rollback_later_does_not_change_the_applied_migration(self):
        path = self.migrations / "20260101000000-first.sql"
        path.write_text("SELECT 1;\n")
        self.migrate()
        path.write_text("SELECT 1;\n-- Rollback: added later\nSELECT 'down';\n")
        applied, output = self.migrate()
        self.assertNotIn("changed after it was applied", output)

    def test_each_schema_has_its_migrations_directory_and_runs_in_that_schema(self):
        self.project.settings.update(DATABASE_NAME="asana", DATABASE_EXTRA_SCHEMAS="asana_git")
        (self.migrations / "20260101000000-flat.sql").write_text("SELECT 'old flat main';\n")
        (self.migrations / "asana").mkdir()
        (self.migrations / "asana/20260102000000-main.sql").write_text("SELECT 'main';\n")
        (self.migrations / "asana_git").mkdir()
        (self.migrations / "asana_git/20260102000000-git.sql").write_text("SELECT 'git';\n-- Rollback:\nSELECT 'git down';\n")
        applied, _ = self.migrate()
        self.assertEqual(applied, ["20260101000000-flat.sql", "20260102000000-main.sql", "asana_git/20260102000000-git.sql"])
        git_sql = next(sql for sql in self.queries if "'git'" in sql)
        self.assertTrue(git_sql.startswith("USE `asana_git`;"))
        self.assertIn("USE `asana`;\n\nINSERT INTO setup_migrations", git_sql, "recorded in the main database")
        self.rollback()
        self.assertTrue(self.queries[-1].startswith("USE `asana_git`;\n"))
        self.assertIn("SELECT 'git down';", self.queries[-1])
        self.assertIn("DELETE FROM setup_migrations WHERE name = 'asana_git/20260102000000-git.sql'", self.queries[-1])
        (self.migrations / "other").mkdir()
        (self.migrations / "other/20260103000000-x.sql").write_text("SELECT 1;\n")
        with self.assertRaisesRegex(ValueError, "an extra schema"):
            self.migrate()

    def test_a_main_database_migration_keeps_its_identity_when_the_layout_moves(self):
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n")
        self.migrate()
        self.project.settings.update(DATABASE_NAME="asana", DATABASE_EXTRA_SCHEMAS="asana_git")
        (self.migrations / "asana").mkdir()
        (self.migrations / "20260101000000-first.sql").rename(self.migrations / "asana/20260101000000-first.sql")
        self.assertEqual(self.migrate()[0], [], "already applied under the same name")

    def test_off_leaves_migrations_to_the_project(self):
        self.project.settings["DATABASE_MIGRATIONS"] = "off"
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n")
        with self.assertRaisesRegex(ValueError, "DATABASE_MIGRATIONS=off"):
            self.migrate()
        with self.assertRaisesRegex(ValueError, "DATABASE_MIGRATIONS=off"):
            self.rollback()
        self.assertEqual(self.queries, [])

    def test_todo_and_unexpected_names_are_refused(self):
        (self.migrations / "20260101000000-open.sql").write_text("-- TODO: drop\n")
        with self.assertRaisesRegex(ValueError, "still has a TODO"):
            self.migrate()
        (self.migrations / "20260101000000-open.sql").unlink()
        (self.migrations / "notes.sql").write_text("SELECT 1;\n")
        with self.assertRaisesRegex(ValueError, "Unexpected migration file"):
            self.migrate()


if __name__ == "__main__":
    unittest.main()
