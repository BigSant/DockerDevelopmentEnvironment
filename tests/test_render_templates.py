"""The drop-in renderer every core container runs at start, exercised with real sh/awk."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from create_project import scaffold
from project import Project
from project_environment import initialize_directories

RENDER = ROOT / "docker/runtime/render-templates.sh"


class RenderTemplatesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.output = self.base / "output"
        (self.source / "server.d").mkdir(parents=True)
        (self.source / "prod").mkdir()

    def render(self, **environment):
        env = {"PATH": os.environ["PATH"], **environment}
        return subprocess.run(["sh", str(RENDER), str(self.source), str(self.output)],
                              env=env, capture_output=True, text=True)

    def test_templates_are_rendered_and_plain_files_copied_unchanged(self):
        (self.source / "server.d/site.conf.template").write_text(
            "server_name ${DOMAIN};\nreturn 301 https://$host$request_uri; # ${PROJECT_NAME}\n")
        (self.source / "plain.conf").write_text("set $x ${NOT_RENDERED};\n")
        result = self.render(DOMAIN="shop.local", PROJECT_NAME="shop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "server.d/site.conf").read_text(),
                         "server_name shop.local;\nreturn 301 https://$host$request_uri; # shop\n")
        self.assertEqual((self.output / "plain.conf").read_text(), "set $x ${NOT_RENDERED};\n")
        self.assertFalse((self.output / "server.d/site.conf.template").exists())

    def test_unknown_variable_fails_and_names_it(self):
        (self.source / "x.cnf.template").write_text("value=${MISSING_SETTING}\n")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MISSING_SETTING", result.stderr)

    def test_environment_directories_are_not_rendered_into_the_common_copy(self):
        (self.source / "prod/only.conf.template").write_text("${PROD_ONLY}\n")
        result = self.render()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.output / "prod").exists())

    def test_rerender_replaces_stale_output_and_missing_source_is_empty(self):
        self.output.mkdir()
        (self.output / "stale.conf").write_text("old\n")
        self.assertEqual(self.render().returncode, 0)
        self.assertFalse((self.output / "stale.conf").exists())
        missing = subprocess.run(["sh", str(RENDER), str(self.base / "absent"), str(self.base / "empty")],
                                 capture_output=True, text=True)
        self.assertEqual(missing.returncode, 0, missing.stderr)
        self.assertEqual(list((self.base / "empty").iterdir()), [])


class ExtensionContractTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold("extension-contract", temporary.name)

    def test_core_services_render_and_read_only_the_effective_directories(self):
        services = Project(self.app).model()["services"]
        php = services["php-fpm"]["environment"]
        self.assertIn("/usr/local/etc/php/project.r:/usr/local/etc/php/project.r.env", php["PHP_INI_SCAN_DIR"])
        for name in ("php-fpm", "webserver", "nginx-proxy", "database"):
            environment = services[name]["environment"]
            self.assertEqual(environment["SETUP_ENV"], "local", name)
            self.assertEqual(environment["PROJECT_NAME"], "extension-contract", name)
            self.assertEqual(environment["DOMAIN"], "extension-contract.local", name)
        self.assertEqual(services["database"]["environment"]["DATABASE_NAME"], "extension_contract")

    def test_init_creates_runtime_data_but_no_configuration_directories(self):
        project = Project(self.app)
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_directories(project)
        self.assertFalse((self.app / "config").exists())
        self.assertTrue((self.app.parent / "data/mysql").is_dir())


if __name__ == "__main__":
    unittest.main()
