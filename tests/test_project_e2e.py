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
                         "PROJECT_DATA_DIRECTORY": str(self.root / "data"),
                         "PROJECT_E2E_MODULES_DIRECTORY": str(self.root / "app/.generated/e2e/node_modules")},
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
            "playwright", "playwright", "test", "--config=/e2e/config/playwright.config.cjs", "--grep=^(?=.*@stage(?![\\w-]))",
            "--grep-invert=@slow", "cart.spec.ts", "checkout/pay.spec.ts:12", "--reporter=list,html,json"])
        self.assertTrue((self.root / "app/tests/playwright/node_modules").is_dir(), "the mount point, made as this account")
        self.assertTrue((self.root / "app/.generated/e2e/node_modules").is_dir())

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
                         ["playwright-1.61.1-abc", "playwright", "test", "--config=/e2e/config/playwright.config.cjs",
                          "--grep=@stage", "--reporter=list,html,json"])
        for option in (["--memory", "2g"], ["--memory-swap", "2g"], ["--cpus", "2"], ["--pids-limit", "1024"],
                       ["--cap-drop", "ALL"], ["--security-opt", "no-new-privileges:true"]):
            self.assertIn(option, [docker[index:index + 2] for index in range(len(docker))], option)
        self.assertNotIn("0", docker[docker.index("--user") + 1].split(":"), "never root")
        self.assertIn(f"{self.root / 'app/.generated/e2e/node_modules'}:/e2e/tests/node_modules:ro", docker)
        test, _ = self.project("test")
        for url in ("ftp://x", "https://x/ y", "https://user@x/"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "url"):
                project_e2e.run(local, url=url)
        with self.assertRaisesRegex(ValueError, "ENV=test has its own"):
            project_e2e.run(test, url="https://x")

    def test_install_runs_npm_ci_once_per_install_files_outside_the_specs(self):
        local, calls = self.project()
        tests = self.root / "app/tests/playwright"
        target = self.root / "app/.generated/e2e"
        self.assertFalse(project_e2e.install(local), "no package.json: nothing to do")
        (tests / "package.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "package-lock.json is missing"):
            project_e2e.install(local)
        (tests / "package-lock.json").write_text('{"lockfileVersion": 3}')

        def npm_ci(args, **keywords):
            if args[:2] == ["docker", "run"]:
                calls.append(args)
                for package in ("ms", "@playwright/test", "playwright", "playwright-core"):
                    (target / "node_modules" / package).mkdir(parents=True, exist_ok=True)
            return SimpleNamespace(returncode=0, stdout=b"")
        installer = patch("subprocess.run", npm_ci)
        installer.start()
        self.addCleanup(installer.stop)
        self.assertTrue(project_e2e.install(local))
        args = calls[0]
        self.assertEqual(args[-5:], ["playwright-1.61.1-abc", "ci", "--no-audit", "--no-fund", "--ignore-scripts"])
        for option in (["--cap-drop", "ALL"], ["--read-only"], ["--pids-limit", "1024"], ["--memory", "2g"]):
            self.assertIn(option, [args[index:index + len(option)] for index in range(len(args))], option)
        self.assertNotIn("compose", args, "a one-off container: no Compose project, no network left behind")
        self.assertIn(f"{target}:/e2e/install", args)
        self.assertNotIn(f"{tests}:/e2e/install", args, "the specs are never written")
        self.assertEqual(sorted(path.name for path in (target / "node_modules").iterdir()), ["@playwright", "ms"])
        self.assertEqual((target / "package-lock.json").read_text(), '{"lockfileVersion": 3}')
        self.assertEqual(list((tests / "node_modules").iterdir()), [], "only the empty mount point, made as this account")
        self.assertFalse(project_e2e.install(local), "the same install files: cached")
        for change in (lambda: (tests / "package-lock.json").write_text('{"lockfileVersion": 3, "x": 1}'),
                       lambda: (tests / "package.json").write_text('{"dependencies": {}}'),
                       lambda: (tests / ".npmrc").write_text("registry=https://registry.example/\n")):
            change()
            self.assertTrue(project_e2e.install(local), "each install file is part of the cache key")
        self.assertEqual(len(calls), 4)
        self.assertEqual((target / ".npmrc").read_text(), "registry=https://registry.example/\n")
        (tests / "package.json").unlink()
        self.assertFalse(project_e2e.install(local))
        self.assertEqual(list((target / "node_modules").iterdir()), [], "no manifest: no modules of an earlier commit")
        self.assertFalse((target / ".setup-e2e-install").exists())
        (tests / "package.json").write_text('{"dependencies": {}}')
        self.assertTrue(project_e2e.install(local), "and the next manifest installs again")
        test, _ = self.project("test")
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            project_e2e.install(test)

    def test_install_refuses_symlinked_install_files_and_marker(self):
        local, calls = self.project()
        tests = self.root / "app/tests/playwright"
        outside = self.root / "outside.json"
        outside.write_text("{}")
        (tests / "package.json").write_text("{}")
        (tests / "package-lock.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "package-lock.json must be a regular file"):
            project_e2e.install(local)
        (tests / "package-lock.json").unlink()
        (tests / "package-lock.json").write_text("{}")
        target = self.root / "app/.generated/e2e"
        target.mkdir(parents=True)
        (target / ".setup-e2e-install").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "must be a regular file"):
            project_e2e.install(local)
        self.assertEqual(outside.read_text(), "{}", "nothing written through the symlink")
        (target / ".setup-e2e-install").unlink()
        (target / "node_modules").symlink_to(self.root)
        with self.assertRaisesRegex(ValueError, "symlink"):
            project_e2e.install(local)
        self.assertEqual(calls, [])


class MakeArgumentsTest(unittest.TestCase):
    """make e2e / test-prepare: each value is one --OPTION=VALUE argument, and only a value given on make's command line
    counts (an exported url= or grep= of the caller's shell never reaches the runner)."""

    def make(self, *arguments, environment=None):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            printer = work / "print-arguments"
            printer.write_text("#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
            printer.chmod(0o755)
            (work / "Makefile").write_text(f"PROJECT_DOCKER_DIRECTORY := $(CURDIR)\nSETUP_DIRECTORY := {ROOT}\n"
                                           "include $(SETUP_DIRECTORY)/docker/project.mk\n")
            result = subprocess.run(["make", "-s", "-C", str(work), *arguments, f"PYTHON={printer}"], capture_output=True,
                                    text=True, env={**os.environ, **(environment or {})})
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)[7:]  # after the runner, --docker-directory, --project-directory, --env

    def test_values_are_single_arguments_and_only_from_the_command_line(self):
        self.assertEqual(self.make("e2e", "grep=-foo", "grep_invert=--help", "files=a.spec.ts b.spec.ts", "json=1"),
                         ["e2e", "--grep=-foo", "--grep-invert=--help", "--files=a.spec.ts b.spec.ts", "--json"])
        self.assertEqual(self.make("e2e", environment={"url": "https://live.example", "data": "/tmp", "grep": "x", "json": "1",
                                                       "storage_states": "a=/b.json", "container": "c", "cmd": "sh"}),
                         ["e2e"], "nothing exported reaches make e2e")
        self.assertEqual(self.make("test-prepare", environment={"dump": "/etc/shadow"}), ["test-prepare"])
        self.assertEqual(self.make("test-prepare", "dump=/backups/x.sql"), ["test-prepare", "--dump=/backups/x.sql"])


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
