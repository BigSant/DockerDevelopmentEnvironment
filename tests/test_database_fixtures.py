"""Fixture selection, shared import locking and SQL failure behavior."""

import contextlib
import fcntl
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from database_fixtures import load_fixtures, plan_fixtures


class FixturesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixtures = self.root / "app/database/fixtures"
        for group in ("common", "local", "test"):
            (self.fixtures / group).mkdir(parents=True)
        for name in ("common/020-second.sql", "common/010-first.sql", "local/010-local.sql", "test/010-test.sql"):
            (self.fixtures / name).write_text("SELECT '${DOMAIN}';\n")
        self.project = SimpleNamespace(root=self.root, directory=self.root / "app", environment="local",
            settings={"DATABASE_NAME": "example", "DOMAIN": "example.local", "FIXTURES_DIRECTORY": "app/database/fixtures"},
            command=["docker", "compose"], child_env=lambda: {})

    def test_order_and_set_are_independent_of_docker_environment(self):
        for environment in ("local", "prod"):
            self.project.environment = environment
            plan = plan_fixtures(self.project, "test")
            self.assertEqual([p.relative_to(self.fixtures).as_posix() for p in plan],
                             ["common/010-first.sql", "common/020-second.sql", "test/010-test.sql"])
        self.assertEqual(len(plan_fixtures(self.project, "common")), 2)
        self.assertFalse((self.project.directory / ".generated").exists())

    def test_missing_set_invalid_name_and_symlinks_fail_before_db_work(self):
        for selected in (None, "", "missing", "../local", "local test", "$(id)"):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                plan_fixtures(self.project, selected)
        (self.fixtures / "test/link.sql").symlink_to(self.fixtures / "local/010-local.sql")
        with self.assertRaisesRegex(ValueError, "regular SQL file"):
            plan_fixtures(self.project, "test")
        (self.fixtures / "linked").symlink_to(self.fixtures / "local", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            plan_fixtures(self.project, "linked")

    def test_empty_set_is_noop_and_missing_common_is_optional(self):
        for path in (self.fixtures / "common").iterdir():
            path.unlink()
        (self.fixtures / "common").rmdir()
        self.assertEqual(len(plan_fixtures(self.project, "local")), 1)
        (self.fixtures / "empty").mkdir()
        with patch("database_fixtures.execute_sql") as execute, contextlib.redirect_stdout(io.StringIO()):
            load_fixtures(self.project, plan_fixtures(self.project, "empty"))
        execute.assert_not_called()

    def test_all_files_render_including_first_and_failure_stops_later_sql(self):
        plan = plan_fixtures(self.project, "test")
        seen = []
        def execute(command, *, env, stdin, check):
            seen.append(stdin.read())
            if len(seen) == 2:
                raise subprocess.CalledProcessError(1, ["mysql"])
        with patch("database_import.subprocess.run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            load_fixtures(self.project, plan)
        self.assertEqual(seen, [b"SELECT 'example.local';\n"] * 2)
        self.assertIn(b"${DOMAIN}", plan[0].read_bytes())

    def test_removed_file_and_invalid_domain_prevent_execution(self):
        plan = plan_fixtures(self.project, "test")
        plan[-1].unlink()
        with patch("database_import.subprocess.run") as execute, self.assertRaises(FileNotFoundError):
            load_fixtures(self.project, plan)
        execute.assert_not_called()
        self.project.settings["DOMAIN"] = "invalid'; SQL"
        with self.assertRaisesRegex(ValueError, "valid DOMAIN"):
            plan_fixtures(self.project, "local")

    def test_fixture_load_uses_same_lock_as_dump_import(self):
        plan = plan_fixtures(self.project, "local")
        output = self.project.directory / ".generated"
        output.mkdir()
        with (output / "db-import.local.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("database_import.subprocess.run") as execute, self.assertRaisesRegex(ValueError, "already running"):
                load_fixtures(self.project, plan)
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
