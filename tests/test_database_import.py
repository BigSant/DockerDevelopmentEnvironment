"""Import ordering and failure handling without connecting to a database."""

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
from database_import import MYSQL_IMPORT, import_database, plan_import


class DatabaseImportTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sql = self.root / "database/sql/after-import"
        for group in ("common", "local", "prod"):
            (self.sql / group).mkdir(parents=True)
        self.dump = self.root / "dump with spaces.sql"
        self.dump.write_text("SELECT 'dump';\n")
        for name in ("common/020-second.sql", "common/010-first.sql", "local/010-local.sql", "prod/010-prod.sql"):
            (self.sql / name).write_text(f"SELECT '{name}';\n")
        self.project = SimpleNamespace(
            root=self.root, directory=self.root / "docker", environment="local",
            settings={"DATABASE_NAME": "example", "POST_IMPORT_SQL_DIRECTORY": "database/sql/after-import"},
            command=["docker", "compose", "--project-name", "example-local"],
            child_env=lambda: {},
        )
        self.project.directory.mkdir()

    def test_plan_orders_common_then_only_selected_environment_without_outputs(self):
        plan = plan_import(self.project, str(self.dump))
        self.assertEqual([p.name for p in plan], [self.dump.name, "010-first.sql", "020-second.sql", "010-local.sql"])
        self.project.environment = "prod"
        self.assertEqual(plan_import(self.project, str(self.dump))[-1].name, "010-prod.sql")
        self.assertFalse((self.project.directory / ".generated").exists())

    def test_preflight_rejects_missing_empty_non_sql_dump_and_escaping_hooks(self):
        empty = self.root / "empty.sql"
        empty.touch()
        compressed = self.root / "dump.sql.gz"
        compressed.write_bytes(b"example")
        for source in (None, str(empty), str(compressed), str(self.root / "missing.sql")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                plan_import(self.project, source)
        (self.sql / "common/030-escape.sql").symlink_to(self.dump)
        with self.assertRaisesRegex(ValueError, "inside"):
            plan_import(self.project, str(self.dump))

    def test_streams_each_file_and_stops_after_first_failure(self):
        plan = plan_import(self.project, str(self.dump))
        for fail_at in (None, 0, 2):
            seen = []

            def execute(command, *, env, stdin, check):
                self.assertEqual(command[-1], "example")
                self.assertIn("-T", command)
                self.assertTrue(check)
                seen.append(stdin.read())
                if len(seen) - 1 == fail_at:
                    raise subprocess.CalledProcessError(1, ["mysql"])

            with self.subTest(fail_at=fail_at), patch("database_import.subprocess.run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
                if fail_at is None:
                    import_database(self.project, plan)
                else:
                    with self.assertRaises(subprocess.CalledProcessError):
                        import_database(self.project, plan)
                count = len(plan) if fail_at is None else fail_at + 1
                self.assertEqual(seen, [p.read_bytes() for p in plan[:count]])

    def test_deleted_hook_prevents_any_database_execution(self):
        plan = plan_import(self.project, str(self.dump))
        plan[-1].unlink()
        with patch("database_import.subprocess.run") as execute, self.assertRaises(FileNotFoundError):
            import_database(self.project, plan)
        execute.assert_not_called()

    def test_concurrent_import_is_rejected_before_execution(self):
        plan = plan_import(self.project, str(self.dump))
        output = self.project.directory / ".generated"
        output.mkdir()
        with (output / "db-import.local.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch("database_import.subprocess.run") as execute, self.assertRaisesRegex(ValueError, "already running"):
                import_database(self.project, plan)
            execute.assert_not_called()

    def test_running_database_name_mismatch_aborts_before_mysql(self):
        result = subprocess.run(["sh", "-c", MYSQL_IMPORT, "database-import", "expected"],
                                env={"MYSQL_DATABASE": "other"}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 64)
        self.assertIn("differs", result.stderr)

    def test_domain_substitution_is_only_for_hooks_and_does_not_rewrite_sources(self):
        self.project.settings["DOMAIN"] = "forsena.local"
        self.dump.write_text("SELECT '${DOMAIN}';\n")
        hook = self.sql / "local/010-local.sql"
        hook.write_text("UPDATE shop SET domain = '${DOMAIN}';\n")
        plan = plan_import(self.project, str(self.dump))
        inputs = []
        run_process = subprocess.run

        def consume_stdin(*args, **kwargs):
            result = run_process([sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                 stdin=kwargs["stdin"], capture_output=True, check=True)
            inputs.append(result.stdout)

        with patch("database_import.subprocess.run", side_effect=consume_stdin), contextlib.redirect_stdout(io.StringIO()):
            import_database(self.project, plan)
        self.assertIn(b"${DOMAIN}", inputs[0])
        self.assertEqual(inputs[-1], b"UPDATE shop SET domain = 'forsena.local';\n")
        self.assertIn("${DOMAIN}", hook.read_text())

    def test_invalid_hook_domain_fails_preflight_before_database_writes(self):
        hook = self.sql / "local/010-local.sql"
        hook.write_text("UPDATE shop SET domain = '${DOMAIN}';\n")
        for domain in ("", "bad'; DROP TABLE shop; --", "$(whoami)"):
            self.project.settings["DOMAIN"] = domain
            with self.subTest(domain=domain), self.assertRaisesRegex(ValueError, "valid DOMAIN"):
                plan_import(self.project, str(self.dump))


if __name__ == "__main__":
    unittest.main()
