"""Filesystem and real Compose config tests; no Docker Engine mutations."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REMOTE_LIMITS = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\n'
                 'WEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\n')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "docker"))
from project import Project, main as run_project


class ProjectTemplatesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.projects = Path(self.temporary.name)

    def prepare(self, name="example", extra=""):
        """A minimal project: env/ + compose/ beside app/public."""
        root = self.projects / name
        directory = root / "app"
        (directory / "env").mkdir(parents=True)
        (directory / "compose").mkdir()
        (root / "app/public").mkdir()
        (directory / "env/common.env").write_text(f"PROJECT_NAME={name}\n")
        (directory / "env/local.env").write_text(
            f"DOMAIN={name}.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
            "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD='literal $PWD # quote'\n" + extra)
        (directory / "Makefile").write_bytes((ROOT / "templates/shared/Makefile").read_bytes())
        return root, directory

    def test_root_level_sources_are_rejected(self):
        _, directory = self.prepare()
        (directory / ".env").write_text("PROJECT_NAME=wrong\n")
        with self.assertRaisesRegex(ValueError, "no longer supported"):
            Project(directory)

    def test_native_model_paths_profiles_and_literal_credentials(self):
        root, directory = self.prepare()
        project = Project(directory)
        self.assertEqual(project.profiles, "")
        model = project.model()
        self.assertEqual(model["name"], "example-local")
        database = model["services"]["database"]
        # Compose escapes a literal dollar for safe serialization of its model.
        self.assertEqual(database["environment"]["MYSQL_PASSWORD"], "literal $$PWD # quote")
        mounts = {m["target"]: m["source"] for m in database["volumes"]}
        self.assertEqual(Path(mounts["/var/lib/mysql"]), root / "data/mysql")

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

    def test_pipeline_secrets_from_the_process_environment_reach_the_model(self):
        # Bitbucket Deployment variables arrive only as process environment; a blank placeholder
        # in an env file (or setup's own defaults) must not hide them.
        _, directory = self.prepare()
        (directory / "env/prod.env").write_text(
            REMOTE_LIMITS + "DOMAIN=prod.example.test\nDATABASE_USER=shop\nDATABASE_NAME=shop\n"
            "DATABASE_ROOT_PASSWORD=\nPS_COOKIE_KEY=''\n")
        secrets = {"DATABASE_PASSWORD": "pipeline-user-secret", "DATABASE_ROOT_PASSWORD": "pipeline-root-secret",
                   "PS_COOKIE_KEY": "pipeline-cookie-key", "PS_SECRET": "pipeline-ps-secret"}
        with patch.dict(os.environ, {**secrets, "DOMAIN": "leaked.example.test", "PHP_VERSION": "7.4"}):
            project = Project(directory, environment="prod")
            services = project.model()["services"]
        database, php = services["database"]["environment"], services["php-fpm"]["environment"]
        self.assertEqual((database["MYSQL_PASSWORD"], database["MYSQL_ROOT_PASSWORD"]),
                         ("pipeline-user-secret", "pipeline-root-secret"))
        self.assertEqual((php["PS_COOKIE_KEY"], php["PS_SECRET"]), ("pipeline-cookie-key", "pipeline-ps-secret"))
        # Other settings named in env files still ignore a previous project's exports.
        self.assertEqual(project.settings["DOMAIN"], "prod.example.test")
        self.assertNotEqual(project.settings["PHP_VERSION"], "7.4")

    def test_secrets_written_in_the_private_env_file_win_over_the_shell(self):
        _, directory = self.prepare()
        with patch.dict(os.environ, {"DATABASE_ROOT_PASSWORD": "previous-project-root"}):
            database = Project(directory).model()["services"]["database"]["environment"]
        self.assertEqual(database["MYSQL_ROOT_PASSWORD"], "root-test-only")
        self.assertEqual(database["MYSQL_PASSWORD"], "literal $$PWD # quote")

    def test_environment_override_and_multiline_value_do_not_confuse_settings(self):
        _, directory = self.prepare(extra="COMPOSE_PROFILES=\n")
        (directory / "env/stage.env").write_text(
            REMOTE_LIMITS + "DOMAIN=stage.example.test\nLOCALHOST_PORT=3401\nLOCALHOST_PORT_SSL=3402\n"
            "DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD='line one\nCOMPOSE_PROFILES=cron\nline three'\n")
        project = Project(directory, environment="stage")
        self.assertEqual(project.name, "example-stage")
        self.assertEqual(project.profiles, "")
        self.assertIn("\nCOMPOSE_PROFILES=cron\n", project.model()["services"]["database"]["environment"]["MYSQL_PASSWORD"])

    def test_real_compose_overrides_can_customize_included_service(self):
        _, directory = self.prepare()
        (directory / "compose/common.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 768M\n")
        (directory / "compose/local.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 1024M\n")
        model = Project(directory).model()
        self.assertEqual(model["services"]["php-fpm"]["environment"]["PHP_MEMORY_LIMIT"], "1024M")

    def test_parallel_render_never_rewrites_or_reads_source_snapshots(self):
        _, one = self.prepare("one")
        _, two = self.prepare("two")
        (one / "compose/base.yaml").write_text("services:\n  php-fpm:\n    environment:\n      PHP_MEMORY_LIMIT: 1024M\n")
        original = (one / "compose/base.yaml").read_bytes()
        (one / "docker-compose.yml").write_text("invalid obsolete snapshot")
        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(lambda d: Project(d).render(), (one, two, one)))
        self.assertEqual(outputs[0], outputs[2])
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertIn("name: one-local", outputs[0].read_text())
        self.assertIn("name: two-local", outputs[1].read_text())
        self.assertEqual(outputs[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual((one / "compose/base.yaml").read_bytes(), original)

    def test_shared_makefile_runs_the_runner(self):
        for name in ("old", "new"):
            root, directory = self.prepare(name)
            parameters = root / 'app/public/app/config/parameters.php'
            parameters.parent.mkdir(parents=True)
            parameters.write_text("<?php return array('parameters' => array());")
            result = subprocess.run(["make", "-s", "-C", str(directory), "check",
                                     f"SETUP_DIRECTORY={ROOT}"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            model = Project(directory).model()
            sources = {Path(v["source"]) for v in model["services"]["php-fpm"]["volumes"]}
            self.assertIn(root / "app/public", sources)

    def test_missing_private_settings_fail_before_actions(self):
        _, directory = self.prepare()
        (directory / "env/local.env").unlink()
        with self.assertRaisesRegex(ValueError, "from its example"):
            Project(directory)

    def test_e2e_overrides_idle_entrypoint_and_preserves_argument_boundaries(self):
        _, directory = self.prepare()
        with patch.object(Project, "run") as execute, patch("project_testing.test_project", lambda project: project), \
                patch.object(sys, "argv", [
            "project.py", "--docker-directory", str(directory), "e2e", "--command",
            "npx playwright test --grep 'product page'",
        ]):
            self.assertEqual(run_project(), 0)
        execute.assert_called_once_with(
            ["run", "--rm", "--entrypoint", "npx", "playwright", "playwright", "test", "--grep", "product page"],
            profiles="playwright")

    def test_project_tool_settings_select_baseline_and_playwright_config(self):
        _, directory = self.prepare(extra=(
            "PHPSTAN_BASELINE_FILE=/tmp/phpstan/config/baseline.neon\n"
            "PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs\n"))
        cases = [
            ("doctrine-build", [], ["build", "php-doctrine-migrations"], "doctrine"),
            ("phpstan-baseline", [], ["run", "--rm", "--no-deps", "--entrypoint", "phpstan", "php-phpstan",
                                    "analyse", "--configuration=/tmp/phpstan/config/phpstan.neon",
                                    "--generate-baseline=/tmp/phpstan/config/baseline.neon",
                                    "--allow-empty-baseline"], "phpstan"),
            ("e2e", [], ["run", "--rm", "--entrypoint", "npx", "playwright", "playwright", "test",
                         "--config=/e2e/config/playwright.config.cjs"], "playwright"),
            ("doctrine", ["--command", "migrate --dry-run"],
             ["run", "--rm", "--no-deps", "php-doctrine-migrations", "migrate", "--dry-run"], "doctrine"),
        ]
        for action, arguments, expected, profile in cases:
            with self.subTest(action=action), patch.object(Project, "run") as execute, \
                    patch("project_testing.test_project", lambda project: project), patch.object(sys, "argv", [
                "project.py", "--docker-directory", str(directory), action, *arguments,
            ]):
                self.assertEqual(run_project(), 0)
                execute.assert_called_once_with(expected, profiles=profile)

    def test_compose_files_select_only_one_environment_and_explicit_component(self):
        _, directory = self.prepare()
        with (directory / "env/common.env").open("a") as handle:
            handle.write("PHP_VERSION=8.1\nPROJECT_COMPOSE_FILES=compose/redis.yaml\n")
        (directory / "env/prod.env").write_text(
            REMOTE_LIMITS + "DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=prod-demo\nDOMAIN=example.test\nCOMPOSE_PROFILES=\n")
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

    def test_project_dockerfile_extends_the_core_image(self):
        _, directory = self.prepare()
        (directory / "Dockerfile").write_text(
            "# extension\nARG SETUP_PHP_IMAGE\nFROM ${SETUP_PHP_IMAGE}\nRUN echo extra\n")
        (directory / "env/prod.env").write_text(REMOTE_LIMITS + "DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=prod-demo\nDOMAIN=example.test\n")
        for env in ("local", "prod"):
            project = Project(directory, environment=env)
            services = project.model()["services"]
            build = services["php-fpm"]["build"]
            self.assertEqual(Path(build["context"]), directory)
            self.assertEqual(Path(build["dockerfile"]), directory / "Dockerfile")
            self.assertNotIn("target", build)
            self.assertEqual(build["args"]["SETUP_PHP_IMAGE"], services["php-fpm-core"]["image"])
            self.assertEqual(services["php-fpm-core"]["build"]["target"], f"env-{env}")
            self.assertTrue(services["php-fpm-core"]["image"].startswith(f"example-php-fpm-core-{env}-s"))

    def test_project_dockerfile_cannot_replace_the_core_image(self):
        _, directory = self.prepare()
        for text in ("FROM php:8.4-fpm\n", "FROM scratch AS env-local\n", "ARG X\nFROM ${X}\n"):
            (directory / "Dockerfile").write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "must extend the CORE image"):
                Project(directory)

    def test_services_list_enables_catalogue_services(self):
        _, directory = self.prepare()
        core = {"nginx-proxy", "webserver", "php-fpm", "database"}
        active = lambda project: set(json.loads(project.capture(["config", "--format", "json"]))["services"])
        self.assertEqual(active(Project(directory)), core)
        with (directory / "env/common.env").open("a") as handle:
            handle.write("SERVICES=pma mailpit cron php-phpstan\n")
        project = Project(directory)
        # cron is long-running and starts with up; php-phpstan is a tool started by its command.
        self.assertEqual(active(project), core | {"pma", "mailpit", "cron"})
        self.assertIn("php-phpstan", project.model()["services"])
        self.assertEqual(project.process_env["SETUP_ENABLE_PMA"], "1")

    def test_environment_file_overrides_services(self):
        _, directory = self.prepare(extra="SERVICES=mailpit\n")
        with (directory / "env/common.env").open("a") as handle:
            handle.write("SERVICES=pma\n")
        services = json.loads(Project(directory).capture(["config", "--format", "json"]))["services"]
        self.assertIn("mailpit", services)
        self.assertNotIn("pma", services)

    def test_unknown_or_core_service_names_are_rejected(self):
        for value in ("pma unknown", "database"):
            with self.subTest(value=value):
                _, directory = self.prepare(name=f"x{abs(hash(value)) % 1000}", extra=f"SERVICES={value}\n")
                with self.assertRaisesRegex(ValueError, "Unknown SERVICES entry"):
                    Project(directory)

    def test_compose_difference_files_merge_maps_and_override_lists(self):
        _, directory = self.prepare()
        (directory / "compose/common.yaml").write_text(
            "services:\n  php-fpm:\n    environment:\n      EXTRA: one\n"
            "  database:\n    volumes: !override\n      - ${PROJECT_DATA_DIRECTORY}/mysql-alt:/var/lib/mysql\n")
        services = Project(directory).model()["services"]
        self.assertEqual(services["php-fpm"]["environment"]["EXTRA"], "one")
        self.assertIn("DATABASE_HOST", services["php-fpm"]["environment"])
        self.assertEqual([v["target"] for v in services["database"]["volumes"]], ["/var/lib/mysql"])
        self.assertTrue(services["database"]["volumes"][0]["source"].endswith("mysql-alt"))

    def test_missing_component_fails_closed(self):
        _, directory = self.prepare()
        with (directory / "env/common.env").open("a") as handle:
            handle.write("PROJECT_COMPOSE_FILES=compose/missing.yaml\n")
        with self.assertRaisesRegex(ValueError, "existing files inside"):
            Project(directory)


if __name__ == "__main__":
    unittest.main()
