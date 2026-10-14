"""Grouped scaffolding and readiness checks without changing running containers."""

import contextlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from prepare_project import prepare
from project import Project
from project_environment import doctor, initialize_directories, initialize_env, pull_images


class ProjectEnvironmentTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "example"
        self.directory = self.root / "app"
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(self.root)

    def initialize(self):
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_env(self.directory, "local")
        private = self.directory / "env/local.env"
        private.write_text("DOMAIN=example.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
                           "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD='private $value'\n")
        return private

    def test_grouped_default_preserves_custom_sources_and_private_settings(self):
        private = self.initialize()
        before = private.read_bytes()
        for relative in ("compose/local.yaml", "Dockerfile", ".gitignore", "qa/README.md"):
            (self.directory / relative).write_text("custom project source\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(prepare(self.root), [])
            initialize_env(self.directory, "local")
        self.assertEqual(private.read_bytes(), before)
        self.assertEqual(private.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.directory / ".env").exists())
        self.assertIn("PROJECT_NAME=example", (self.directory / "env/common.env").read_text())
        self.assertTrue((self.directory / "qa/playwright/tests/homepage.spec.cjs").is_file())
        for relative in ("compose/local.yaml", "Dockerfile", ".gitignore", "qa/README.md"):
            self.assertEqual((self.directory / relative).read_text(), "custom project source\n")

    def test_app_layout_merges_existing_tree_and_is_detected_on_rerun(self):
        root = Path(self.temporary.name) / "consolidated"
        app = root / "app"
        (app / "public").mkdir(parents=True)
        (app / "public/index.php").write_text("existing application\n")
        (app / "config/sql").mkdir(parents=True)
        (app / "config/sql/existing.sql").write_text("SELECT 1;\n")
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(root, layout="app")
            initialize_env(app, "local")
            self.assertEqual(prepare(root), [])
        (app / "env/local.env").write_text("DOMAIN=example.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
                                          "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test\n")
        project = Project(app)
        model = project.model()
        self.assertEqual(project.root, root)
        self.assertEqual(project.settings["SCHEMA_DIRECTORY"], "app/database/schema")
        self.assertEqual(project.settings["POST_IMPORT_SQL_DIRECTORY"], "app/database/after-import")
        # Without a project Dockerfile the CORE pass-through extends the CORE image.
        self.assertEqual(Path(model["services"]["php-fpm"]["build"]["context"]), ROOT / "docker/docker/php-fpm/project")
        mounts = {v["target"]: v["source"] for v in model["services"]["php-fpm"]["volumes"]}
        self.assertEqual(Path(mounts["/var/www/html"]), app / "public")
        self.assertEqual(Path(mounts["/usr/local/etc/php/project.d"]), app / "config/php")
        database = {v["target"]: v["source"] for v in model["services"]["database"]["volumes"]}
        self.assertEqual(Path(database["/var/lib/mysql"]), root / "data/mysql")
        self.assertEqual((app / "public/index.php").read_text(), "existing application\n")
        self.assertEqual((app / "config/sql/existing.sql").read_text(), "SELECT 1;\n")
        self.assertFalse((root / "docker").exists())

    def test_database_as_code_is_on_by_default_and_off_on_request(self):
        root = Path(self.temporary.name) / "schema-default"
        app = root / "app"
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(root, layout="app")
        (app / "env/local.env").write_text("DOMAIN=example.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
                                          "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test\n")
        common = app / "env/common.env"
        lines = [line for line in common.read_text().splitlines() if not line.startswith("SCHEMA_DIRECTORY=")]
        common.write_text("\n".join(lines) + "\n")
        project = Project(app)
        self.assertEqual(project.settings["DATABASE_AS_CODE"], "on")
        self.assertEqual(project.settings["SCHEMA_DIRECTORY"], "app/database/schema")
        common.write_text("\n".join(lines) + "\nSCHEMA_DIRECTORY=app/db/snapshot\n")
        self.assertEqual(Project(app).settings["SCHEMA_DIRECTORY"], "app/db/snapshot")
        common.write_text("\n".join(lines) + "\nSCHEMA_DIRECTORY=app/db/snapshot\nDATABASE_AS_CODE=off\n")
        self.assertEqual(Project(app).settings["SCHEMA_DIRECTORY"], "")
        common.write_text("\n".join(lines) + "\nDATABASE_AS_CODE=maybe\n")
        with self.assertRaisesRegex(ValueError, "DATABASE_AS_CODE"):
            Project(app)

    def test_grouped_model_and_init_preserve_application_and_data(self):
        self.initialize()
        project = Project(self.directory)
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_directories(project)
        self.assertFalse((self.root / "app/public").exists())
        self.assertTrue((self.root / "data/mysql").is_dir())
        marker = self.root / "data/mysql/existing-data"
        marker.write_text("preserve")
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_directories(project)
        self.assertEqual(marker.read_text(), "preserve")
        model = project.model()
        self.assertEqual(Path(model["services"]["php-fpm"]["build"]["context"]), ROOT / "docker/docker/php-fpm/project")
        # The full template selects the CORE redis service, which starts with up.
        self.assertIn("redis", project.active_model()["services"])

    def test_the_active_model_leaves_out_what_only_a_profile_starts(self):
        self.initialize()
        project = Project(self.directory)
        active, full = project.active_model()["services"], project.model()["services"]
        self.assertIn("php-fpm-base", full, "the base image is built through its build_only profile")
        self.assertNotIn("php-fpm-base", active, "up, ps and exec never see it")
        self.assertLess(set(active), set(full))

    def test_mixed_sources_and_escaping_parent_symlinks_fail_before_writes(self):
        (self.directory / ".env").write_text("PROJECT_NAME=old\n")
        with self.assertRaisesRegex(ValueError, "no longer supported"):
            prepare(self.root)
        (self.directory / ".env").unlink()
        target = self.directory / "database/schema/README.md"
        target.unlink()
        target.parent.rmdir()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        target.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            prepare(self.root)
        self.assertEqual(list(outside.iterdir()), [])

    def test_doctor_accepts_a_server_without_certificates_but_not_half_a_pair(self):
        # On stage/prod nginx-proxy generates a self-signed pair when data/<env>/ssl is empty.
        certificates = self.root / "data/prod/ssl"
        certificates.mkdir(parents=True)
        private = self.root / "prod.env"
        private.write_text("DOMAIN=example.test\n")
        private.chmod(0o600)
        model = {"services": {"nginx-proxy": {"image": "available", "volumes": [
            {"type": "bind", "source": str(certificates), "target": "/etc/ssl/certs", "read_only": True}]}}}
        project = SimpleNamespace(name="example-prod", environment="prod", env_files=[private], directory=self.directory,
                                  data_directory=self.root / "data/prod", settings={}, active_model=lambda: model)
        available = lambda *arguments: SimpleNamespace(
            returncode=0, stdout="5.0.2" if arguments[:2] == ("compose", "version") else "29.2.1")
        with patch("project_environment.docker", side_effect=available), \
                patch("project_environment.owned_ports", return_value=set()):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                doctor(project)
            self.assertIn("self-signed", output.getvalue())
            (certificates / "domain.crt").write_text("present")
            with self.assertRaisesRegex(ValueError, "domain.key"):
                doctor(project)

    def test_doctor_reports_conflicting_port_and_missing_tls_without_secrets(self):
        private = self.initialize()
        certificates = self.root / "data/ssl"
        certificates.mkdir(parents=True)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            published = listener.getsockname()[1]
            model = {"services": {"nginx-proxy": {"image": "available",
                     "ports": [{"published": str(published), "host_ip": "127.0.0.1"}],
                     "volumes": [{"type": "bind", "source": str(certificates),
                                  "target": "/etc/ssl/certs", "read_only": True}]}}}
            project = SimpleNamespace(name="example-local", env_files=[private],
                                      directory=self.directory, data_directory=self.root / 'data', settings={},
                                      active_model=lambda: model)
            def available(*arguments):
                output = "5.0.2" if arguments[:2] == ("compose", "version") else "29.2.1"
                return SimpleNamespace(returncode=0, stdout=output)
            with patch("project_environment.docker", side_effect=available), \
                    patch("project_environment.owned_ports", return_value=set()):
                with self.assertRaises(ValueError) as failure:
                    doctor(project)
                message = str(failure.exception)
                self.assertIn("unavailable", message)
                self.assertIn("domain.crt", message)
                self.assertIn("domain.key", message)
                self.assertNotIn("private $value", message)
            # A port already owned by this project is valid during recreation.
            for name in ("domain.crt", "domain.key"):
                (certificates / name).write_text("present")
            with patch("project_environment.docker", side_effect=available), \
                    patch("project_environment.owned_ports", return_value={(published, "tcp")}), \
                    contextlib.redirect_stdout(io.StringIO()):
                doctor(project)

    def test_pull_skips_images_reused_from_buildable_services(self):
        model = {"services": {"php": {"image": "project-php", "build": {"context": "."}},
                              "cron": {"image": "project-php"},
                              "redis": {"image": "redis:7.4-alpine"}}}
        active = {"services": {name: value for name, value in model["services"].items()
                               if name != "php"}}
        project = SimpleNamespace(model=lambda: model, active_model=lambda: active, run=Mock())
        pull_images(project)
        project.run.assert_called_once_with(["pull", "--ignore-buildable", "redis"])
        active["services"].pop("redis")
        project.run.reset_mock()
        with contextlib.redirect_stdout(io.StringIO()):
            pull_images(project)
        project.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
