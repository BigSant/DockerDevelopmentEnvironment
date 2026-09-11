"""Schema normalization, staged-file checks and hook installation using real Git."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_schema import (MANIFEST, MYSQL_READ, check_schema, export_schema, git,
                             install_schema_hook, mysql_query, normalize_ddl,
                             read_schema, schema_directory, staged_schema, table_file,
                             unescape_batch)


def snapshot(tables):
    files = {table_file(name): ddl.encode() for name, ddl in tables.items()}
    manifest = {"format": 1, "dialect": "mysql", "tables": [
        {"name": name, "file": table_file(name)} for name in sorted(tables)]}
    files[MANIFEST] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return files


class DatabaseSchemaTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="schema space ", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        git(self.root, "init", "-q")
        self.project = SimpleNamespace(root=self.root, directory=self.root / "docker", environment="local",
                                       settings={"SCHEMA_DIRECTORY": "database/schema", "DATABASE_NAME": "example"},
                                       command=["docker", "compose"], child_env=lambda: {})
        self.project.directory.mkdir()
        self.directory = schema_directory(self.project)
        self.files = snapshot({"product": "CREATE TABLE `product` (`id` int);\n"})
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__, None, None, None)

    def export(self, files=None):
        with patch("database_schema.read_schema", return_value=files or self.files):
            export_schema(self.project)

    def check(self, files=None):
        with patch("database_schema.read_schema", return_value=files or self.files):
            check_schema(self.project)

    def stage(self):
        git(self.root, "add", "-A", "database/schema")

    def test_normalization_retains_defaults_comments_and_column_auto_increment(self):
        ddl = "CREATE TABLE `t` (\n  `id` int AUTO_INCREMENT,\n  `note` varchar(80) DEFAULT 'AUTO_INCREMENT=123'\n) ENGINE=InnoDB AUTO_INCREMENT=84 DEFAULT CHARSET=utf8mb4 COMMENT='AUTO_INCREMENT=456'"
        normalized = normalize_ddl(ddl)
        self.assertNotIn("AUTO_INCREMENT=84", normalized)
        self.assertIn("`id` int AUTO_INCREMENT", normalized)
        self.assertIn("DEFAULT 'AUTO_INCREMENT=123'", normalized)
        self.assertIn("COMMENT='AUTO_INCREMENT=456'", normalized)
        self.assertEqual(unescape_batch(r"a\tb\nback\\n"), "a\tb\nback\\n")
        self.assertLess(len(table_file("ą" * 64).encode()), 255)

    def test_read_collects_ordered_table_files_without_rows(self):
        name = "unusual`table"
        tables = name.encode().hex() + "\n"
        ddl = "CREATE TABLE `unusual``table` (\\n  `id` int NOT NULL\\n) ENGINE=InnoDB AUTO_INCREMENT=8"
        with patch("database_schema.mysql_query", side_effect=[tables, name + "\t" + ddl + "\n", tables]) as query:
            files = read_schema(self.project)
        self.assertIn(table_file(name), files)
        self.assertNotIn(b"AUTO_INCREMENT=8", files[table_file(name)])
        self.assertIn("SHOW CREATE TABLE `unusual``table`", query.call_args_list[1].args[1])
        self.assertEqual(json.loads(files[MANIFEST])["tables"][0]["name"], name)

    def test_table_disappearing_during_read_fails(self):
        with patch("database_schema.mysql_query", side_effect=["74\n", "t\tCREATE TABLE `t` (`id` int)\n", ""]):
            with self.assertRaisesRegex(ValueError, "changed"):
                read_schema(self.project)

    def test_worktree_updates_do_not_replace_required_staged_updates(self):
        self.export()
        with self.assertRaisesRegex(ValueError, "not staged"):
            self.check()
        self.stage()
        self.check()
        changed = snapshot({"product": "CREATE TABLE `product` (`id` int, `code` text);\n"})
        self.export(changed)
        with self.assertRaisesRegex(ValueError, "different"):
            self.check(changed)
        self.stage()
        self.check(changed)
        # An unrelated unstaged edit must not alter the index being checked.
        (self.directory / "table-product.sql").write_text("unstaged user edit\n")
        self.check(changed)

    def test_deleted_tables_must_be_staged_and_export_preserves_readme(self):
        self.export()
        (self.directory / "README.md").write_text("user documentation\n")
        self.stage()
        empty = snapshot({})
        self.export(empty)
        self.assertFalse((self.directory / "table-product.sql").exists())
        self.assertTrue((self.directory / "README.md").exists())
        with self.assertRaisesRegex(ValueError, "no longer in DB"):
            self.check(empty)
        self.stage()
        self.check(empty)

    def test_connection_failure_preserves_export_and_fails_check(self):
        self.export()
        self.stage()
        before = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        with patch("database_schema.read_schema", side_effect=ValueError("DB offline")):
            for operation in (export_schema, check_schema):
                with self.assertRaisesRegex(ValueError, "DB offline"):
                    operation(self.project)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_check_honors_alternate_commit_index(self):
        self.export()
        self.stage()
        alternate = self.root / ".git/commit-index"
        alternate.write_bytes((self.root / ".git/index").read_bytes())
        git(self.root, "rm", "--cached", "database/schema/table-product.sql")
        with self.assertRaisesRegex(ValueError, "not staged"):
            self.check()
        with patch.dict(os.environ, {"GIT_INDEX_FILE": str(alternate)}):
            self.check()

    def test_schema_symlink_in_git_index_is_rejected(self):
        self.export()
        table = self.directory / "table-product.sql"
        table.unlink()
        table.symlink_to("README.md")
        self.stage()
        with self.assertRaisesRegex(ValueError, "regular"):
            staged_schema(self.directory)

    def test_relative_hook_git_paths_survive_schema_subdirectory_discovery(self):
        self.export()
        self.stage()
        cwd = Path.cwd()
        try:
            os.chdir(self.root)
            with patch.dict(os.environ, {"GIT_DIR": ".git", "GIT_WORK_TREE": ".", "GIT_INDEX_FILE": ".git/index"}):
                self.check()
        finally:
            os.chdir(cwd)

    def test_export_rejects_symlinks_and_unmanaged_reserved_files(self):
        self.directory.mkdir(parents=True)
        unknown = self.directory / "table-manual.sql"
        unknown.write_text("handwritten SQL\n")
        with self.assertRaisesRegex(ValueError, "Unmanaged"):
            self.export()
        self.assertEqual(unknown.read_text(), "handwritten SQL\n")
        unknown.unlink()
        unknown.symlink_to(self.root / "outside.sql")
        with self.assertRaisesRegex(ValueError, "non-regular"):
            self.export()

    def test_hook_is_executable_idempotent_and_preserves_existing_hooks(self):
        self.directory.mkdir(parents=True)
        install_schema_hook(self.project)
        hook = self.root / ".git/hooks/pre-commit"
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertIn("schema-check", hook.read_text())
        subprocess.run(["sh", "-n", str(hook)], check=True)
        install_schema_hook(self.project)
        hook.write_text("#!/bin/sh\nexit 3\n")
        with self.assertRaisesRegex(ValueError, "Existing hook preserved"):
            install_schema_hook(self.project)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 3\n")

    def test_custom_hook_manager_and_outside_project_directory_are_rejected(self):
        git(self.root, "config", "core.hooksPath", ".githooks")
        with self.assertRaisesRegex(ValueError, "core.hooksPath"):
            install_schema_hook(self.project)
        self.project.settings["SCHEMA_DIRECTORY"] = "../outside"
        with self.assertRaisesRegex(ValueError, "inside"):
            schema_directory(self.project)

    def test_failed_mysql_and_wrong_database_do_not_pass(self):
        failure = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"private diagnostics")
        with patch("database_schema.subprocess.run", return_value=failure), self.assertRaisesRegex(ValueError, "Cannot read DB"):
            mysql_query(self.project, "SELECT 1")
        mismatch = subprocess.run(["sh", "-c", MYSQL_READ, "schema-read", "expected"],
                                  env={"MYSQL_DATABASE": "other"}, capture_output=True)
        self.assertEqual(mismatch.returncode, 64)


if __name__ == "__main__":
    unittest.main()
