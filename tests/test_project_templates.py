"""Filesystem and real Compose config tests; no Docker Engine mutations."""

from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docker"))
from prepare_project import prepare
from project import Project, main as run_project
from project_ports import allocate


class ProjectTemplatesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.projects = Path(self.temporary.name)

    def prepare(self, name="example", layout="root", extra=""):
        root = self.projects / name
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(root, layout=layout)
        directory = root / ("app/docker" if layout == "legacy" else "docker")
        (directory / ".env.local").write_text(
            f"DOMAIN={name}.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
            "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_PASSWORD='literal $PWD # quote'\n" + extra)
        return root, directory

    def test_sources_are_identical_and_rerun_preserves_settings(self):
        first, one = self.prepare("one")
        _, two = self.prepare("two")
        for name in ("Makefile", "compose.yaml", ".gitignore", "README.md"):
            self.assertEqual((one / name).read_bytes(), (two / name).read_bytes())
        (one / ".env").write_text("PROJECT_NAME=custom\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepare(first), [])
        self.assertEqual((one / ".env").read_text(), "PROJECT_NAME=custom\n")

    def test_custom_bootstrap_is_not_overwritten(self):
        root, directory = self.prepare()
        (directory / "Makefile").write_text("custom user file\n")
        with self.assertRaisesRegex(ValueError, "differs from the template"):
            prepare(root)
        self.assertEqual((directory / "Makefile").read_text(), "custom user file\n")

    def test_batch_preflight_and_preview_do_not_leave_partial_projects(self):
        first = self.projects / "first"
        second = self.projects / "second"
        command = [sys.executable, str(ROOT / "prepare_project.py"), str(first), str(second)]
        preview = subprocess.run(command + ["--check"], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        (second / "docker").mkdir(parents=True)
        (second / "docker/Makefile").write_text("user-owned\n")
        failed = subprocess.run(command, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(first.exists())
        self.assertEqual((second / "docker/Makefile").read_text(), "user-owned\n")

    def test_legacy_copy_does_not_import_generated_compose_or_modify_original(self):
        root, legacy = self.prepare(layout="legacy")
        (legacy / "docker-compose.yml").write_text("name: obsolete\n")
        (legacy / "config/php/local/custom.ini").write_text("memory_limit=768M\n")
        before = {p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(root, from_legacy=True)
        directory = root / "docker"
        self.assertFalse((directory / "docker-compose.yml").exists())
        self.assertEqual((directory / ".env.local").read_bytes(), (legacy / ".env.local").read_bytes())
        self.assertEqual((directory / ".env.local").stat().st_mode & 0o777, 0o600)
        self.assertEqual(before, {p.relative_to(legacy): p.read_bytes() for p in legacy.rglob("*") if p.is_file()})

    def test_native_model_paths_profiles_and_literal_credentials(self):
        root, directory = self.prepare()
        project = Project(directory)
        self.assertEqual(project.profiles, "mailpit,pma,cron")
        model = project.model()
        self.assertEqual(model["name"], "example-local")
        database = model["services"]["database"]
        # Compose escapes a literal dollar for safe serialization of its model.
        self.assertEqual(database["environment"]["MYSQL_PASSWORD"], "literal $$PWD # quote")
        mounts = {m["target"]: m["source"] for m in database["volumes"]}
        self.assertEqual(Path(mounts["/var/lib/mysql"]), root / "data/mysql")
        self.assertEqual(Path(mounts["/etc/mysql/project.d"]), directory / "config/mysql")

    def test_explicit_empty_profiles_override_defaults_and_ambient_exports(self):
        _, directory = self.prepare(extra="COMPOSE_PROFILES=\n")
        with patch.dict(os.environ, {"PROJECT_NAME": "other", "COMPOSE_PROJECT_NAME": "other",
                                    "COMPOSE_PROFILES": "cron", "DATABASE_PASSWORD": "other"}):
            project = Project(directory)
            self.assertEqual(project.name, "example-local")
            self.assertEqual(project.profiles, "")
            active = json.loads(project.capture(["config", "--format", "json"]))["services"]
            self.assertNotIn("cron", active)
            self.assertNotIn("php-fpm-base", active)
            self.assertEqual(active["database"]["environment"]["MYSQL_PASSWORD"], "literal $$PWD # quote")
        self.assertEqual(Project(directory, profiles="mailpit").profiles, "mailpit")

    def test_environment_override_and_multiline_value_do_not_confuse_settings(self):
        _, directory = self.prepare(extra="COMPOSE_PROFILES=\n")
        (directory / ".env.stage").write_text(
            "DOMAIN=stage.example.test\nLOCALHOST_PORT=3401\nLOCALHOST_PORT_SSL=3402\n"
            "DATABASE_PASSWORD='line one\nCOMPOSE_PROFILES=cron\nline three'\n")
        project = Project(directory, environment="stage")
        self.assertEqual(project.name, "example-stage")
        self.assertEqual(project.profiles, "mailpit,cron")
        self.assertIn("\nCOMPOSE_PROFILES=cron\n", project.model()["services"]["database"]["environment"]["MYSQL_PASSWORD"])

    def test_real_compose_overrides_can_customize_included_service(self):
        _, directory = self.prepare()
        (directory / "compose.override.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 768M\n")
        (directory / "compose.local.override.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 1024M\n")
        model = Project(directory).model()
        self.assertEqual(model["services"]["php-fpm"]["environment"]["PHP_MEMORY_LIMIT"], "1024M")

    def test_parallel_render_never_rewrites_or_reads_source_snapshots(self):
        _, one = self.prepare("one")
        _, two = self.prepare("two")
        original = (one / "compose.yaml").read_bytes()
        (one / "docker-compose.yml").write_text("invalid obsolete snapshot")
        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(lambda d: Project(d).render(), (one, two, one)))
        self.assertEqual(outputs[0], outputs[2])
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertIn("name: one-local", outputs[0].read_text())
        self.assertIn("name: two-local", outputs[1].read_text())
        self.assertEqual(outputs[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((one / "compose.yaml").read_bytes(), original)

    def test_identical_makefile_supports_both_layouts(self):
        for name, layout in (("old", "legacy"), ("new", "root")):
            root, directory = self.prepare(name, layout)
            result = subprocess.run(["make", "-s", "-C", str(directory), "check",
                                     f"SETUP_DIRECTORY={ROOT}"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            model = Project(directory).model()
            sources = {Path(v["source"]) for v in model["services"]["php-fpm"]["volumes"]}
            self.assertIn(root / "app/public", sources)

    def test_missing_private_settings_fail_before_actions(self):
        _, directory = self.prepare()
        (directory / ".env.local").unlink()
        with self.assertRaisesRegex(ValueError, "from its example"):
            Project(directory)

    def test_e2e_overrides_idle_entrypoint_and_preserves_argument_boundaries(self):
        _, directory = self.prepare()
        with patch.object(Project, "run") as execute, patch.object(sys, "argv", [
            "project.py", "--docker-directory", str(directory), "e2e", "--command",
            "npx playwright test --grep 'product page'",
        ]):
            self.assertEqual(run_project(), 0)
        execute.assert_called_once_with(
            ["run", "--rm", "--entrypoint", "npx", "playwright", "playwright", "test", "--grep", "product page"],
            profiles="playwright")

    def test_ports_scan_both_layouts_and_reject_conflicting_migration(self):
        root, directory = self.prepare("old", "legacy")
        self.prepare("new")
        (directory / ".env.local").write_text("LOCALHOST_PORT=3001\nLOCALHOST_PORT_SSL=3002\n")
        new_env = self.projects / "new/docker/.env.local"
        new_env.write_text("LOCALHOST_PORT=3003\nLOCALHOST_PORT_SSL=3004\n")
        self.assertEqual(allocate(self.projects / "next"), (3005, 3006))
        self.assertEqual(allocate(root), (3001, 3002))
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(root, from_legacy=True)
        self.assertEqual(allocate(root), (3001, 3002))
        (root / "docker/.env.local").write_text("LOCALHOST_PORT=4001\nLOCALHOST_PORT_SSL=4002\n")
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            allocate(root)

    def test_project_tool_settings_select_baseline_and_playwright_config(self):
        _, directory = self.prepare(extra=(
            "PHPSTAN_BASELINE_FILE=/tmp/phpstan/baselines/phpstan.neon\n"
            "PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs\n"))
        cases = [
            ("phpstan-baseline", [], ["run", "--rm", "--no-deps", "--entrypoint", "phpstan", "php-phpstan",
                                    "analyse", "--configuration=/tmp/phpstan/config/phpstan.neon",
                                    "--generate-baseline=/tmp/phpstan/baselines/phpstan.neon",
                                    "--allow-empty-baseline"], "phpstan"),
            ("e2e", [], ["run", "--rm", "--entrypoint", "npx", "playwright", "playwright", "test",
                         "--config=/e2e/config/playwright.config.cjs"], "playwright"),
            ("doctrine", ["--command", "migrate --dry-run"],
             ["run", "--rm", "--no-deps", "php-doctrine-migrations", "migrate", "--dry-run"], "doctrine"),
        ]
        for action, arguments, expected, profile in cases:
            with self.subTest(action=action), patch.object(Project, "run") as execute, patch.object(sys, "argv", [
                "project.py", "--docker-directory", str(directory), action, *arguments,
            ]):
                self.assertEqual(run_project(), 0)
                execute.assert_called_once_with(expected, profiles=profile)

    def prepare_grouped(self):
        root, directory = self.prepare()
        (directory / "env").mkdir()
        (directory / "compose").mkdir()
        (directory / ".env").rename(directory / "env/common.env")
        (directory / ".env.local").rename(directory / "env/local.env")
        (directory / "compose.yaml").rename(directory / "compose/base.yaml")
        return root, directory

    def test_grouped_files_select_only_one_environment_and_explicit_component(self):
        _, directory = self.prepare_grouped()
        with (directory / "env/common.env").open("a") as handle:
            handle.write("PHP_VERSION=8.1\nPROJECT_COMPOSE_FILES=compose/redis.yaml\n")
        (directory / "env/prod.env").write_text(
            "DATABASE_PASSWORD=prod-demo\nDOMAIN=example.test\nCOMPOSE_PROFILES=\n")
        (directory / "compose/redis.yaml").write_text(
            "services:\n  redis:\n    image: redis:7.4-alpine\n    networks: [network_app]\n")
        (directory / "compose/local.yaml").write_text(
            "services:\n  redis:\n    ports: ['127.0.0.1:6389:6379']\n"
            "  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 1024M\n")
        (directory / "compose/prod.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 512M\n")
        local = Project(directory).model()["services"]
        prod = Project(directory, environment="prod").model()["services"]
        self.assertEqual(local["redis"]["ports"][0]["published"], "6389")
        self.assertNotIn("ports", prod["redis"])
        self.assertEqual(prod["database"]["environment"]["MYSQL_PASSWORD"], "prod-demo")
        self.assertNotEqual(local["database"]["environment"]["MYSQL_PASSWORD"], "prod-demo")
        self.assertEqual(local["php-fpm"]["environment"]["PHP_MEMORY_LIMIT"], "1024M")
        self.assertEqual(prod["php-fpm"]["environment"]["PHP_MEMORY_LIMIT"], "512M")
        self.assertEqual(local["php-fpm"]["build"]["args"]["PHP_VERSION"], "8.1")
        self.assertEqual(prod["php-fpm"]["build"]["args"]["PHP_VERSION"], "8.1")

    def test_grouped_custom_dockerfile_uses_project_context_and_selected_target(self):
        _, directory = self.prepare_grouped()
        (directory / "Dockerfile").write_text("FROM scratch AS env-local\nFROM scratch AS env-prod\n")
        (directory / "env/prod.env").write_text("DATABASE_PASSWORD=prod-demo\nDOMAIN=example.test\n")
        (directory / "compose/common.yaml").write_text(
            "services:\n  php-fpm:\n    build:\n      args:\n        BASE_IMAGE: custom-${ENV}-${PHP_VERSION}\n")
        for env in ("local", "prod"):
            build = Project(directory, environment=env).model()["services"]["php-fpm"]["build"]
            self.assertEqual(Path(build["context"]), directory)
            self.assertEqual(Path(build["dockerfile"]), directory / "Dockerfile")
            self.assertEqual(build["target"], f"env-{env}")
            self.assertTrue(build["args"]["BASE_IMAGE"].startswith(f"custom-{env}-"))

    def test_mixed_source_layouts_and_missing_component_fail_closed(self):
        _, directory = self.prepare_grouped()
        (directory / ".env").write_text("PROJECT_NAME=wrong\n")
        with self.assertRaisesRegex(ValueError, "one source layout"):
            Project(directory)
        (directory / ".env").unlink()
        with (directory / "env/common.env").open("a") as handle:
            handle.write("PROJECT_COMPOSE_FILES=compose/missing.yaml\n")
        with self.assertRaisesRegex(ValueError, "existing files inside"):
            Project(directory)

    def test_port_allocator_recognizes_grouped_env_directory(self):
        root, _ = self.prepare_grouped()
        self.assertEqual(allocate(root), (3301, 3302))
        candidate = self.projects / "next"
        (root / "docker/env/local.env").write_text("LOCALHOST_PORT=3001\nLOCALHOST_PORT_SSL=3002\n")
        self.assertEqual(allocate(candidate), (3003, 3004))


if __name__ == "__main__":
    unittest.main()
