"""ENV=test entry points: suite discovery, PHPUnit choice, refusal outside ENV=test, IDE test interpreter."""

import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
import project_testing
from project_ide import add_interpreter, phpunit_settings
from project_testing import find_suites, phpunit_binary, run_phpunit, setup_phpunit, test_project


class TestingTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="testing-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.web = self.root / "web"
        for relative in ("modules/alpha/phpunit.xml.dist", "modules/beta/tests/phpunit.xml.dist",
                         "modules/beta/tests/phpunit.xml", "modules/gamma/README.md", "phpunit.xml.dist"):
            (self.web / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.web / relative).write_text("<phpunit/>\n")
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_suites_of_modules_their_tests_folder_and_the_shop(self):
        suites = find_suites(self.web, prestashop=True)
        self.assertEqual(sorted(suites), ["alpha", "beta", "shop"])
        self.assertEqual(suites["beta"].name, "phpunit.xml", "a local phpunit.xml overrides the .dist")
        self.assertEqual(suites["alpha"], self.web / "modules/alpha/phpunit.xml.dist")

    def test_the_environment_repository_suite_reaches_the_test_copy(self):
        directory = self.root / "docker"
        (directory / "tests/playwright").mkdir(parents=True)
        (directory / "tests/phpunit.xml.dist").write_text("<phpunit/>\n")
        (directory / "tests/Unit/ShopTest.php").parent.mkdir()
        (directory / "tests/Unit/ShopTest.php").write_text("<?php\n")
        (directory / "tests/playwright/home.spec.cjs").write_text("// e2e\n")
        project_testing.copy_project_suite(SimpleNamespace(directory=directory), self.web)
        self.assertEqual(find_suites(self.web, prestashop=True)["project"], self.web / ".setup-tests/project/phpunit.xml.dist")
        self.assertTrue((self.web / ".setup-tests/project/Unit/ShopTest.php").is_file())
        self.assertFalse((self.web / ".setup-tests/project/playwright").exists(), "e2e specs stay with Playwright")
        (directory / "tests/phpunit.xml.dist").unlink()
        project_testing.copy_project_suite(SimpleNamespace(directory=directory), self.web)
        self.assertNotIn("project", find_suites(self.web, prestashop=True))

    def test_phpunit_comes_from_the_suite_module_shop_then_setup(self):
        project = SimpleNamespace(settings={"PHP_VERSION": "8.4"})
        config = self.web / "modules/beta/tests/phpunit.xml"
        with patch("project_testing.setup_phpunit", return_value="/var/www/html/.setup-tools/phpunit-10.5.phar"):
            self.assertEqual(phpunit_binary(project, config, self.web), "/var/www/html/.setup-tools/phpunit-10.5.phar")
            for relative in ("vendor/bin/phpunit", "modules/beta/vendor/bin/phpunit", "modules/beta/tests/.tools/phpunit.phar"):
                (self.web / relative).parent.mkdir(parents=True, exist_ok=True)
                (self.web / relative).write_text("#!/usr/bin/env php\n")
                self.assertEqual(phpunit_binary(project, config, self.web), "/var/www/html/" + relative)

    def test_setup_phpunit_matches_the_php_version(self):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        for php, expected in (("8.4", "10.5"), ("8.0", "9.6"), ("7.2", "8.5"), ("7.1", "7.5"), ("5.6", "5.7")):
            with self.subTest(php=php), patch("project_testing.urllib.request.urlopen",
                                              return_value=Response(b"#!/usr/bin/env php\n<?php")) as download:
                path = setup_phpunit(SimpleNamespace(settings={"PHP_VERSION": php}), self.web)
                self.assertEqual(path, f"/var/www/html/.setup-tools/phpunit-{expected}.phar")
                self.assertIn(f"phpunit-{expected}.phar", download.call_args.args[0])
        with patch("project_testing.urllib.request.urlopen", return_value=Response(b"<html>not found")), \
                self.assertRaisesRegex(ValueError, "Unexpected PHPUnit download"):
            setup_phpunit(SimpleNamespace(settings={"PHP_VERSION": "7.4"}), self.root / "other")

    def test_tests_refuse_anything_but_an_isolated_test_database(self):
        directory = self.root / "docker"
        (directory / "env").mkdir(parents=True)
        local = SimpleNamespace(environment="local", directory=directory, root=self.root, settings={"DATABASE_NAME": "shop"})
        with self.assertRaisesRegex(ValueError, "make test-prepare first"):
            test_project(local)
        with self.assertRaisesRegex(ValueError, "only in the isolated ENV=test"):
            test_project(SimpleNamespace(environment="stage"))
        (directory / "env/test.env").write_text("")
        with patch("project.Project", lambda *args: SimpleNamespace(settings={"DATABASE_NAME": "shop"})), \
                self.assertRaisesRegex(ValueError, "its own database"):
            test_project(local)

    def test_run_selects_a_module_passes_the_filter_and_reports_failures(self):
        calls = []

        def run(arguments):
            calls.append(arguments)
            if "/var/www/html/modules/beta/tests/phpunit.xml" in arguments:
                raise subprocess.CalledProcessError(1, arguments)

        test = SimpleNamespace(environment="test", web_directory=self.web, settings={"PHP_VERSION": "8.4", "PROFILE": "prestashop"},
                               run=run)
        with patch("project_testing.setup_phpunit", return_value="/var/www/html/.setup-tools/phpunit-10.5.phar"):
            self.assertEqual(run_phpunit(test, "alpha", "Import"), ["alpha"])
            self.assertEqual(calls[0], ["exec", "-T", "-w", "/var/www/html", "-e", "SYMFONY_DEPRECATIONS_HELPER=weak",
                                        "php-fpm", "php", "/var/www/html/.setup-tools/phpunit-10.5.phar",
                                        "-c", "/var/www/html/modules/alpha/phpunit.xml.dist", "--filter", "Import"])
            with self.assertRaisesRegex(ValueError, "No PHPUnit suite for delta; found: alpha, beta, shop"):
                run_phpunit(test, "delta")
            calls.clear()
            with self.assertRaisesRegex(ValueError, "PHPUnit failed: beta"):
                run_phpunit(test)
        self.assertEqual(len(calls), 3, "a failing suite does not stop the others")

    def test_ide_test_interpreter_and_phpunit_loader(self):
        root = ET.Element("project")
        entries = ET.SubElement(root, "interpreters")
        project = SimpleNamespace(environment="test", name="shop-test", profiles="")
        identifier = add_interpreter(entries, "shop-test PHP", "Docker", project)
        self.assertEqual(add_interpreter(entries, "shop-test PHP", "Docker", project), identifier, "stable and replaced")
        self.assertEqual(len(entries), 1)
        remote = entries.find("interpreter/remote_data")
        self.assertEqual(remote.find("dockerComposeConfigurationPaths/item").get("value"),
                         "$PROJECT_DIR$/.generated/phpstorm-compose.test.yaml")
        self.assertEqual(remote.find("envs/env[@name='COMPOSE_PROJECT_NAME']").get("value"), "shop-test")
        phpunit_settings(root, identifier)
        phpunit_settings(root, identifier)
        settings = root.findall("component[@name='PhpUnit']/phpunit_settings/PhpUnitSettings")
        self.assertEqual([(s.get("interpreter_id"), s.get("custom_loader_path")) for s in settings],
                         [(identifier, "/var/www/html/vendor/autoload.php")])


if __name__ == "__main__":
    unittest.main()
