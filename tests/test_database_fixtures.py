"""Fixture selection, shared import locking and SQL failure behavior."""

import contextlib
import fcntl
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from database_client import DatabaseError
from database_fixtures import plan_fixtures
from database_load import plan_load, run_load
from fake_database import FakeDatabase


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
            command=["docker", "compose"], child_env=lambda: {}, database=FakeDatabase())

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
        with contextlib.redirect_stdout(io.StringIO()) as output:
            run_load(self.project, plan_load(self.project, fixtures="empty"))
        self.assertEqual(self.project.database.loaded, [])
        self.assertIn("database unchanged", output.getvalue())
        self.assertFalse((self.project.directory / ".generated").exists(), "an empty set takes no lock")

    def test_all_files_render_including_first_and_failure_stops_later_sql(self):
        plan = plan_load(self.project, fixtures="test")
        database = self.project.database
        database.fail_on = lambda operation, text: len(database.loaded) == 2
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(DatabaseError):
            run_load(self.project, plan)
        self.assertEqual(database.loaded, [b"SELECT 'example.local';\n"] * 2)
        self.assertIn(b"${DOMAIN}", plan.fixtures[0].read_bytes())

    def test_removed_file_and_invalid_domain_prevent_execution(self):
        plan = plan_load(self.project, fixtures="test")
        plan.fixtures[-1].unlink()
        with self.assertRaises(FileNotFoundError):
            run_load(self.project, plan)
        self.assertEqual(self.project.database.loaded, [])
        self.project.settings["DOMAIN"] = "invalid'; SQL"
        with self.assertRaisesRegex(ValueError, "valid DOMAIN"):
            plan_fixtures(self.project, "local")

    def test_fixture_load_uses_same_lock_as_dump_import(self):
        plan = plan_load(self.project, fixtures="local")
        output = self.project.directory / ".generated"
        output.mkdir()
        with (output / "db-import.local.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "already running"):
                run_load(self.project, plan)
        self.assertEqual(self.project.database.loaded, [])


if __name__ == "__main__":
    unittest.main()
