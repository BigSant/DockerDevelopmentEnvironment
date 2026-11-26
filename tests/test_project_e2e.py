"""make e2e's selection, JSON report, storage states and url= runs; make e2e-install; test-prepare dump=; ENV=test's
self-signed TLS and host route settings."""

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
import project_e2e
import project_testing
from project_bootstrap import certificate_is_valid, self_signed_certificate, test_host_options


class E2eTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="e2e-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "app/tests/playwright").mkdir(parents=True)
        (self.root / "app/config/playwright").mkdir(parents=True)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def project(self, environment="local", command="npx playwright test --config=/e2e/config/playwright.config.cjs"):
        calls = []
        project = SimpleNamespace(
            environment=environment, settings={"PLAYWRIGHT_COMMAND": command, "PROJECT_NAME": "alpha", "PLAYWRIGHT_VERSION": "1.61.1"},
            process_env={"PROJECT_APP_DIRECTORY": str(self.root / "app"), "PROJECT_CONFIG_DIRECTORY": str(self.root / "app/config"),
                         "PROJECT_DATA_DIRECTORY": str(self.root / "data")},
            run=lambda args, profiles=None: calls.append((args, profiles)), child_env=lambda: {"PATH": os.environ["PATH"]},
            model=lambda: {"services": {"playwright": {"image": "playwright-1.61.1-abc"}}})
        return project, calls

    def test_nothing_given_runs_the_old_command_in_env_test(self):
        local, _ = self.project()
        test, calls = self.project("test")
        with patch("project_testing.test_project", lambda given: test if given is local else None):
            project_e2e.run(local)
        self.assertEqual(calls, [(["run", "--rm", "--entrypoint", "npx", "playwright", "playwright", "test",
                                   "--config=/e2e/config/playwright.config.cjs"], "playwright")])

    def test_selection_report_states_and_name_are_added_to_the_command(self):
        local, _ = self.project()
        test, calls = self.project("test")
        with patch("project_testing.test_project", lambda given: test):
            project_e2e.run(local, grep="^(?=.*@stage(?![\\w-]))", grep_invert="@slow", files="cart.spec.ts checkout/pay.spec.ts:12",
                            json_report=True, states="guest=/run/vault-handoff/stage/1.json", name="alpha-run-1")
        args, profile = calls[0]
        self.assertEqual(profile, "playwright")
        self.assertEqual(args[:5], ["run", "--rm", "--entrypoint", "npx", "--name"])
        self.assertIn("PLAYWRIGHT_JSON_OUTPUT_FILE=/e2e/data/report.json", args)
        self.assertIn("/run/vault-handoff/stage/1.json:/e2e/auth/guest.json:ro", args)
        self.assertIn("E2E_STORAGE_STATE_GUEST=/e2e/auth/guest.json", args)
        self.assertIn('E2E_STORAGE_STATES={"guest":"/e2e/auth/guest.json"}', args)
        self.assertEqual(args[args.index("playwright"):], [
            "playwright", "playwright", "test", "--config=/e2e/config/playwright.config.cjs", "--grep", "^(?=.*@stage(?![\\w-]))",
            "--grep-invert", "@slow", "cart.spec.ts", "checkout/pay.spec.ts:12", "--reporter=list,html,json"])

    def test_bad_selections_are_refused_before_anything_runs(self):
        local, calls = self.project()
        for keywords in ({"files": "../secret.spec.ts"}, {"files": "-x"}, {"files": "/etc/passwd"}, {"grep": "a\nb"},
                         {"grep": "x" * 1001}, {"states": "guest=relative.json"}, {"states": "Admin=/a.json"},
                         {"states": "guest=/a.json guest=/b.json"}, {"name": "Bad Name"}, {"data": "/tmp/x"}):
            with self.subTest(keywords=keywords), self.assertRaises(ValueError):
                project_e2e.run(local, **keywords)
        self.assertEqual(calls, [])

    def test_url_runs_the_image_on_the_bridge_network_with_the_real_address(self):
        local, _ = self.project()
        commands = []

        def fake(command, **options):
            commands.append(command)
            return SimpleNamespace(returncode=0, stdout=b"")
        with patch("subprocess.run", fake):
            project_e2e.run(local, url="https://stage.example.com/", grep="@stage", data=str(self.root / "run-1"), json_report=True,
                            name="alpha-run-1")
        docker = commands[-1]
        self.assertEqual(docker[:6], ["docker", "run", "--rm", "--network", "bridge", "--user"])
        self.assertIn("BASE_URL=https://stage.example.com", docker)
        self.assertIn(f"{self.root / 'app/tests/playwright'}:/e2e/tests:ro", docker)
        self.assertIn(f"{self.root / 'app/config/playwright'}:/e2e/config:ro", docker)
        self.assertIn(f"{self.root / 'run-1'}:/e2e/data", docker)
        self.assertEqual(docker[docker.index("playwright-1.61.1-abc"):],
                         ["playwright-1.61.1-abc", "playwright", "test", "--config=/e2e/config/playwright.config.cjs", "--grep",
                          "@stage", "--reporter=list,html,json"])
        test, _ = self.project("test")
        for url in ("ftp://x", "https://x/ y", "https://user@x/"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "url"):
                project_e2e.run(local, url=url)
        with self.assertRaisesRegex(ValueError, "ENV=test has its own"):
            project_e2e.run(test, url="https://x")

    def test_install_runs_npm_ci_once_per_lock_file_and_drops_the_image_packages(self):
        local, calls = self.project()
        tests = self.root / "app/tests/playwright"
        self.assertFalse(project_e2e.install(local), "no package.json: nothing to do")
        (tests / "package.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "package-lock.json is missing"):
            project_e2e.install(local)
        (tests / "package-lock.json").write_text('{"lockfileVersion": 3}')

        def npm_ci(args, profiles=None):
            calls.append((args, profiles))
            for package in ("ms", "@playwright/test", "playwright", "playwright-core"):
                (tests / "node_modules" / package).mkdir(parents=True, exist_ok=True)
        local.run = npm_ci
        self.assertTrue(project_e2e.install(local))
        args, profile = calls[0]
        self.assertEqual((profile, args[-5:]), ("playwright", ["playwright", "ci", "--no-audit", "--no-fund", "--ignore-scripts"]))
        self.assertIn(f"{tests}:/e2e/install", args)
        self.assertEqual(sorted(path.name for path in (tests / "node_modules").iterdir()), [".setup-e2e-install", "@playwright", "ms"])
        self.assertFalse((tests / "node_modules/@playwright/test").exists())
        self.assertFalse(project_e2e.install(local), "the same lock file: cached")
        (tests / "package-lock.json").write_text('{"lockfileVersion": 3, "x": 1}')
        self.assertTrue(project_e2e.install(local))
        self.assertEqual(len(calls), 2)
        test, _ = self.project("test")
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            project_e2e.install(test)


class TestPrepareDumpTest(unittest.TestCase):
    def test_a_dump_replaces_the_data_set(self):
        local = SimpleNamespace(environment="local", directory=Path("/nonexistent"))
        test = SimpleNamespace(settings={"DATABASE_NAME": "alpha_test", "DOMAIN": "alpha.test.local", "LOCALHOST_PORT_SSL": "3"},
                               run=lambda args: None)
        planned = []
        with patch("project_bootstrap.initialize_test", lambda *a, **k: None), \
                patch("project_testing.test_project", lambda project: test), \
                patch("database_load.plan_load", lambda project, **keywords: planned.append(keywords) or "plan"), \
                patch("database_load.run_load", lambda project, plan: None), \
                patch("project_health.start_project", lambda project, timeout: None), \
                contextlib.redirect_stdout(io.StringIO()):
            project_testing.prepare_tests(local, "dev", dump="/backups/prod.sql.gz")
            project_testing.prepare_tests(local, "demo")
        self.assertEqual(planned, [{"dump": "/backups/prod.sql.gz", "interactive": False}, {"dataset": "demo", "replace": True}])
        with self.assertRaisesRegex(ValueError, "dump= needs"):
            project_testing.prepare_tests(local, "dev", dump="")


class TestTlsTest(unittest.TestCase):
    def test_options_are_read_in_env_test_only(self):
        self.assertEqual(test_host_options(SimpleNamespace(environment="local", settings={"TEST_TLS": "self-signed"})), ("mkcert", True))
        self.assertEqual(test_host_options(SimpleNamespace(environment="test", settings={})), ("mkcert", True))
        self.assertEqual(test_host_options(SimpleNamespace(environment="test", settings={"TEST_TLS": "self-signed",
                                                                                         "TEST_HOST_ROUTE": "off"})), ("self-signed", False))
        for settings in ({"TEST_TLS": "letsencrypt"}, {"TEST_HOST_ROUTE": "no"}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                test_host_options(SimpleNamespace(environment="test", settings=settings))

    def test_a_self_signed_certificate_covers_the_test_names(self):
        with tempfile.TemporaryDirectory() as directory:
            certificate, key = Path(directory) / "domain.crt", Path(directory) / "domain.key"
            self_signed_certificate(["alpha.test.local", "*.alpha.test.local"], certificate, key)
            self.assertTrue(certificate_is_valid(certificate, "alpha.test.local"))
            self.assertTrue(certificate_is_valid(certificate, "pma.alpha.test.local"))
            self.assertFalse(certificate_is_valid(certificate, "other.local"))


if __name__ == "__main__":
    unittest.main()
