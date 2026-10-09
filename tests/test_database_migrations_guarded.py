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
from database_migrations_guarded import commit_problems, draft_text, parse_table, untouched_draft, write_draft
from database_schema import commit_schema, export_schema, git
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
                          {"product": PRODUCT_AFTER, "feed": FEED}, "abc")
        self.assertIn("CREATE TABLE IF NOT EXISTS `feed`", text)
        self.assertIn("ADD COLUMN `name` varchar(128) NOT NULL DEFAULT ''it''''s'' AFTER `code`", text)
        self.assertIn("COLUMN_NAME = 'name'", text)
        self.assertIn("MODIFY COLUMN `code` varchar(64) DEFAULT NULL", text)
        self.assertIn("ADD KEY `name` (`name`)", text)
        self.assertIn("INDEX_NAME = 'code'", text, "a removed key is dropped only when it still exists")
        self.assertIn("-- TODO: drop or rename column old_note", text)
        self.assertIn("-- TODO: drop or rename table legacy", text)
        self.assertNotIn("\nDROP TABLE", text, "a DROP is only ever suggested in a comment")
        self.assertTrue(untouched_draft(text))
        self.assertFalse(untouched_draft(text.replace("-- TODO: drop or rename table legacy", "DROP TABLE legacy;")))

    def test_same_change_gives_the_same_marker(self):
        first = draft_text({}, {"feed": FEED}, "a").splitlines()[1]
        self.assertEqual(first, draft_text({}, {"feed": FEED}, "b").splitlines()[1])


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
        self.addCleanup(stack.close)

    def commit(self):
        commit_schema(self.project)

    def test_schema_change_needs_a_finished_staged_migration(self):
        self.tables = {"product": PRODUCT_AFTER, "feed": FEED}
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

    def test_staged_snapshot_without_a_migration_is_stopped(self):
        self.tables = {"product": PRODUCT, "feed": FEED}
        export_schema(self.project, snapshot(self.tables))
        git(self.root, "add", "-A", "database/schema")
        self.assertEqual(commit_problems(self.project, snapshot(self.tables)),
                         ["the schema changed but no new migration is staged"])
        with self.assertRaisesRegex(ValueError, "no new migration"):
            self.commit()

    def test_untouched_draft_is_regenerated_and_an_edited_one_is_kept(self):
        self.tables = {"product": PRODUCT, "feed": FEED}
        first = write_draft(self.project, snapshot(self.tables))
        self.tables = {"product": PRODUCT_AFTER, "feed": FEED}
        second = write_draft(self.project, snapshot(self.tables))
        self.assertEqual(first, second)
        self.assertIn("MODIFY COLUMN", second.read_text())
        second.write_text(second.read_text() + "-- reviewed\n")
        self.tables = {"product": PRODUCT_AFTER}
        self.assertIsNone(write_draft(self.project, snapshot(self.tables)))
        self.assertTrue(second.read_text().endswith("-- reviewed\n"))

    def test_unstaged_registry_and_bad_names_are_reported(self):
        self.registry.write_text(self.registry.read_text() + "extra config full import\n")
        (self.root / "database/migrations").mkdir()
        (self.root / "database/migrations/fix.sql").write_text("SELECT 1;\n")
        git(self.root, "add", "database/migrations")
        problems = commit_problems(self.project, snapshot({"product": PRODUCT}))
        self.assertIn("migration file name must look like 20260101120000-short-name.sql: fix.sql", problems)
        self.assertIn("stage the table registry: git add database/table-registry.txt", problems)

    def test_first_snapshot_is_a_baseline_without_a_migration(self):
        git(self.root, "rm", "-rq", "database")
        git(self.root, "commit", "-q", "-m", "remove")
        self.assertIsNone(write_draft(self.project, snapshot({"product": PRODUCT})))
        self.assertFalse((self.root / "database/migrations").exists())


class MigrateTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="migrate-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = SimpleNamespace(root=self.root, settings={"SCHEMA_DIRECTORY": "database/schema"})
        self.migrations = self.root / "database/migrations"
        self.migrations.mkdir(parents=True)
        self.applied = {}
        self.queries = []

    def query(self, project, sql, timeout=60):
        self.queries.append(sql)
        if sql.startswith("SELECT name, checksum"):
            return "".join(f"{name}\t{checksum}\n" for name, checksum in self.applied.items())
        if "INSERT INTO setup_migrations" in sql:
            name, checksum = sql.split("VALUES (")[1].split(", NOW()")[0].replace("'", "").split(", ")
            self.applied[name] = checksum
        return ""

    def migrate(self):
        with patch.object(guarded, "mysql_query", self.query), contextlib.redirect_stdout(io.StringIO()) as output:
            applied = guarded.migrate(self.project)
        return applied, output.getvalue()

    def test_pending_run_in_name_order_once(self):
        (self.migrations / "20260102000000-second.sql").write_text("SELECT 2;\n")
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 1;\n")
        applied, _ = self.migrate()
        self.assertEqual([path.name for path in applied], ["20260101000000-first.sql", "20260102000000-second.sql"])
        self.assertEqual(self.migrate()[0], [])
        (self.migrations / "20260101000000-first.sql").write_text("SELECT 11;\n")
        applied, output = self.migrate()
        self.assertEqual(applied, [])
        self.assertIn("changed after it was applied", output)

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
