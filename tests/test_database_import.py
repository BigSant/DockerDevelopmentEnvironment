"""Import ordering and failure handling without connecting to a database."""

import contextlib
import fcntl
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
import database_import
from database_import import (MYSQL_IMPORT, check_dump_tables, dump_tables, filter_dump, import_database, plan_import,
                             render_hook, skip_rule, stream_filtered)


class ImportFixture(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sql = self.root / "database/after-import"
        for group in ("common", "local", "prod"):
            (self.sql / group).mkdir(parents=True)
        self.dump = self.root / "dump with spaces.sql"
        self.dump.write_text("SELECT 'dump';\n")
        for name in ("common/020-second.sql", "common/010-first.sql", "local/010-local.sql", "prod/010-prod.sql"):
            (self.sql / name).write_text(f"SELECT '{name}';\n")
        self.project = SimpleNamespace(
            root=self.root, directory=self.root / "docker", environment="local",
            settings={"DATABASE_NAME": "example", "POST_IMPORT_SQL_DIRECTORY": "database/after-import"},
            command=["docker", "compose", "--project-name", "example-local"],
            child_env=lambda: {},
        )
        self.project.directory.mkdir()
        # Keep streaming deterministic whether or not the suite runs in a terminal.
        meter = patch("database_import.progress_meter", return_value=None)
        meter.start()
        self.addCleanup(meter.stop)

    def fake_pv(self, status=0):
        script = self.root / "pv"
        script.write_text(f"#!/bin/sh\necho \"$@\" > '{self.root}/pv.args'\ncat\nexit {status}\n")
        script.chmod(0o755)
        return str(script)



class DatabaseImportTest(ImportFixture):
    def test_dump_streams_through_pv_with_its_size_and_hooks_do_not(self):
        plan = plan_import(self.project, str(self.dump))
        seen = []

        def execute(command, *, env, stdin, check):
            seen.append((stdin.read(), stat.S_ISFIFO(os.fstat(stdin.fileno()).st_mode)))

        with patch("database_import.progress_meter", return_value=self.fake_pv()), \
                patch("database_import.subprocess.run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
            import_database(self.project, plan)
        self.assertEqual([content for content, _ in seen], [p.read_bytes() for p in plan])
        self.assertEqual((self.root / "pv.args").read_text().split(), ["-s", str(self.dump.stat().st_size)])
        # Only the dump goes through pv; hooks are read straight from their files.
        self.assertEqual([piped for _, piped in seen], [True] + [False] * (len(plan) - 1))

    def test_failing_pv_stops_before_hooks(self):
        plan = plan_import(self.project, str(self.dump))
        with patch("database_import.progress_meter", return_value=self.fake_pv(status=3)), \
                patch("database_import.subprocess.run", side_effect=lambda command, **kwargs: kwargs["stdin"].read()) as execute, \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            import_database(self.project, plan)
        self.assertEqual(execute.call_count, 1)

    def test_plan_orders_common_then_only_selected_environment_without_outputs(self):
        plan = plan_import(self.project, str(self.dump))
        self.assertEqual([p.name for p in plan], [self.dump.name, "010-first.sql", "020-second.sql", "010-local.sql"])
        self.project.environment = "prod"
        self.assertEqual(plan_import(self.project, str(self.dump))[-1].name, "010-prod.sql")
        self.assertFalse((self.project.directory / ".generated").exists())

    def test_unset_directory_defaults_to_project_after_import_or_dump_only(self):
        del self.project.settings["POST_IMPORT_SQL_DIRECTORY"]
        self.assertEqual(plan_import(self.project, str(self.dump)), [self.dump.resolve()])
        default = self.project.directory / "database/after-import/local"
        default.mkdir(parents=True)
        (default / "010-default.sql").write_text("SELECT 'default';\n")
        self.assertEqual([p.name for p in plan_import(self.project, str(self.dump))], [self.dump.name, "010-default.sql"])
        self.project.settings["POST_IMPORT_SQL_DIRECTORY"] = ""
        self.assertEqual(plan_import(self.project, str(self.dump))[-1].name, "010-default.sql")

    def test_explicit_missing_directory_is_still_rejected(self):
        self.project.settings["POST_IMPORT_SQL_DIRECTORY"] = "database/missing"
        with self.assertRaisesRegex(ValueError, "inside the project"):
            plan_import(self.project, str(self.dump))

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


class ImportSequenceTest(ImportFixture):
    """Skipped table data, safe defaults, integration switches and the post-load steps."""

    def test_filter_drops_only_data_of_skipped_tables(self):
        dump = io.BytesIO(b"CREATE TABLE `ps_log` (id int);\n"
                          b"INSERT INTO `ps_log` VALUES (1),(2);\n"
                          b"INSERT IGNORE INTO `ps_orders` VALUES (1);\n"
                          b"INSERT INTO `ps_log` (`id`) VALUES\n(3),\n('a;b'),\n(4);\n"
                          b"REPLACE INTO `odd``name` VALUES (1);\n"
                          b"/*!40000 ALTER TABLE `ps_log` ENABLE KEYS */;\n")
        out = io.BytesIO()
        skipped = filter_dump(dump, out, lambda table: table in ("ps_log", "odd`name"))
        self.assertEqual(skipped, {"ps_log", "odd`name"})
        self.assertEqual(out.getvalue(), b"CREATE TABLE `ps_log` (id int);\n"
                                         b"INSERT IGNORE INTO `ps_orders` VALUES (1);\n"
                                         b"/*!40000 ALTER TABLE `ps_log` ENABLE KEYS */;\n")

    def test_skip_rule_follows_the_registry_live_rule(self):
        self.assertIsNone(skip_rule(self.project))
        self.project.settings.update(SCHEMA_DIRECTORY="database/schema", PROFILE="prestashop")
        (self.root / "database/table-registry.txt").write_text("ps_feed_cache cache empty skip\nps_old drop drop -\n")
        rule = skip_rule(self.project)
        self.assertEqual([rule(t) for t in ("ps_feed_cache", "ps_old", "ps_orders", "ps_connections", "unknown")],
                         [True, True, False, True, False])

    def test_an_import_loads_every_table_unless_skip_is_asked_for(self):
        self.project.settings.update(SCHEMA_DIRECTORY="database/schema", PROFILE="")
        (self.root / "database/table-registry.txt").write_text("ps_feed_cache cache empty skip\n")
        plan = plan_import(self.project, str(self.dump))
        for skip, expected in ((False, None), (True, True)):
            with self.subTest(skip=skip), patch("database_import.execute_sql", return_value=set()) as execute, \
                    patch("database_import.after_load"), contextlib.redirect_stdout(io.StringIO()):
                import_database(self.project, plan, skip=skip)
            rule = execute.call_args.kwargs["skip"]
            self.assertEqual(rule if rule is None else rule("ps_feed_cache"), expected)

    def test_a_dump_table_the_registry_does_not_know_stops_the_import(self):
        self.project.settings.update(SCHEMA_DIRECTORY="database/schema", PROFILE="prestashop")
        registry = self.root / "database/table-registry.txt"
        registry.write_text("ps_feed_cache cache empty skip\n")
        self.dump.write_bytes(b"DROP TABLE IF EXISTS `ps_orders`;\nCREATE TABLE `ps_orders` (\n  `id_order` int,\n"
                              b"  PRIMARY KEY (`id_order`)\n);\nCREATE TABLE IF NOT EXISTS `zz_old_feed` (\n  `id` int\n);\n"
                              b"CREATE TABLE `ps_supplier_log` (\n  `id_order` int\n);\nINSERT INTO `ps_orders` VALUES (1);\n"
                              b"CREATE TABLE `setup_migrations` (\n  `name` varchar(255)\n);\n")
        self.assertEqual(dump_tables(self.dump), {"ps_orders": {"id_order"}, "zz_old_feed": {"id"},
                                                  "ps_supplier_log": {"id_order"}, "setup_migrations": {"name"}})
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                self.assertRaisesRegex(ValueError, "2 tables are not classified"):
            check_dump_tables(self.project, self.dump, interactive=False)
        self.assertIn("zz_old_feed", output.getvalue())
        self.assertIn("suggested: review empty skip", output.getvalue())
        self.assertEqual(registry.read_text(), "ps_feed_cache cache empty skip\n", "nothing is written without a person")
        answers = iter(["i", "e", "feeds empty skip"])
        with contextlib.redirect_stdout(io.StringIO()):
            check_dump_tables(self.project, self.dump, ask=lambda prompt: next(answers), interactive=True)
        self.assertIn("ps_supplier_log", registry.read_text())
        check_dump_tables(self.project, self.dump, interactive=False)
        answers = iter(["s"])
        self.dump.write_bytes(b"CREATE TABLE `zz_other` (`id` int);\n")
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "1 tables are not classified"):
            check_dump_tables(self.project, self.dump, ask=lambda prompt: next(answers), interactive=True)

    def test_without_a_registry_every_dump_table_is_accepted(self):
        self.dump.write_bytes(b"CREATE TABLE `anything` (`id` int);\n")
        check_dump_tables(self.project, self.dump, interactive=False)

    def test_filtered_stream_reaches_the_client_and_reports_its_failure(self):
        target = self.root / "received.sql"
        source = self.root / "dump.sql"
        source.write_bytes(b"INSERT INTO `skip` VALUES (1);\nINSERT INTO `keep` VALUES (2);\n")
        with source.open("rb") as handle:
            skipped = stream_filtered(self.project, ["sh", "-c", f"cat > '{target}'"], handle, lambda t: t == "skip")
        self.assertEqual((skipped, target.read_bytes()), ({"skip"}, b"INSERT INTO `keep` VALUES (2);\n"))
        with source.open("rb") as handle, self.assertRaises(subprocess.CalledProcessError):
            stream_filtered(self.project, ["sh", "-c", "exit 5"], handle, lambda t: False)

    def test_profile_defaults_then_project_hooks_then_integration_switches(self):
        self.project.settings.update(PROFILE="prestashop", DOMAIN="shop.local")
        (self.sql / "integrations").mkdir()
        for name in ("gtm", "tecdoc"):
            (self.sql / f"integrations/{name}.sql").write_text(f"SELECT '{name} off';\n")
        names = [p.name for p in plan_import(self.project, str(self.dump))]
        self.assertEqual(names, [self.dump.name, "010-shop-url.sql", "020-payments-sandbox.sql", "010-first.sql",
                                 "020-second.sql", "010-local.sql", "gtm.sql", "tecdoc.sql"])
        self.project.settings["INTEGRATIONS_ENABLED"] = "tecdoc"
        self.assertNotIn("tecdoc.sql", [p.name for p in plan_import(self.project, str(self.dump))])
        self.project.settings["INTEGRATIONS_ENABLED"] = "omnisend"
        with self.assertRaisesRegex(ValueError, "unknown integrations: omnisend; available: gtm, tecdoc"):
            plan_import(self.project, str(self.dump))
        self.project.settings["INTEGRATIONS_ENABLED"] = ""
        self.project.environment = "prod"
        self.assertEqual([p.name for p in plan_import(self.project, str(self.dump))],
                         [self.dump.name, "010-first.sql", "020-second.sql", "010-prod.sql"],
                         "a prod restore keeps the shop's own URL, payments and integrations")

    def test_profile_hooks_render_prefix_ssl_and_domain(self):
        self.project.settings.update(PROFILE="prestashop", DOMAIN="shop.local", PS_DATABASE_PREFIX="fs_", PS_SSL="off")
        url = database_import.DOCKER_ROOT / "database-profile/prestashop/after-import/010-shop-url.sql"
        rendered = render_hook(self.project, url).decode()
        self.assertIn("UPDATE `fs_shop_url` SET `domain` = 'shop.local'", rendered)
        self.assertIn("SET `value` = '0'\nWHERE `name` IN ('PS_SSL_ENABLED'", rendered)
        self.assertNotIn("${", rendered)
        self.project.settings["PS_DATABASE_PREFIX"] = "fs`; DROP"
        with self.assertRaisesRegex(ValueError, "PS_DATABASE_PREFIX"):
            render_hook(self.project, url)

    def test_after_load_migrates_then_applies_policy_then_reports(self):
        calls = []
        self.project.settings.update(SCHEMA_DIRECTORY="database/schema", PROFILE="prestashop")
        (self.root / "database/migrations").mkdir(parents=True)
        self.project.capture = lambda args: calls.append(args[-1]) or "php-fpm\n"
        self.project.run = lambda args: calls.append(args[0])
        with patch("database_migrations_guarded.migrate", lambda project: calls.append("migrate")), \
                patch("database_schema.schema_report", lambda project: calls.append("report")):
            database_import.after_load(self.project)
        self.assertEqual(calls, ["migrate", "--services", "policy", "cache-clear", "restart", "report"])


if __name__ == "__main__":
    unittest.main()
