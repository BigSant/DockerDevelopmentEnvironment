"""Real Compose bridge and non-destructive PhpStorm metadata preparation."""

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
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from prepare_project import prepare
from project import Project
from project_ide import docker_server_name, initialize_ide, refresh_ide


class ProjectIdeTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "example"
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(self.root, layout="app")
        self.app = self.root / "app"
        (self.app / "env/local.env").write_text("DOMAIN=example.local\nLOCALHOST_PORT=3301\nLOCALHOST_PORT_SSL=3302\n"
                                              "DATABASE_USER=test\nDATABASE_NAME=test\nDATABASE_PASSWORD='secret $value'\n")
        (self.app / ".git").mkdir()
        (self.app / "public/.git").mkdir(parents=True)
        self.project = Project(self.app)

    def run_init(self):
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_ide(self.project, docker_server="Docker test")

    def test_portable_metadata_private_bridge_and_idempotent_rerun(self):
        self.run_init()
        idea = self.app / ".idea"
        snapshot = self.app / ".generated/phpstorm-compose.local.yaml"
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o600)
        self.assertEqual((idea / ".name").read_text(), "example\n")
        php = ET.parse(idea / "php.xml").getroot()
        self.assertEqual(php.find("component[@name='PhpProjectSharedConfiguration']").get("php_language_level"),
                         self.project.model()["services"]["php-fpm"]["build"]["args"]["PHP_VERSION"])
        remote = php.find(".//remote_data")
        self.assertEqual(remote.get("DOCKER_ACCOUNT_NAME"), "Docker test")
        self.assertEqual(remote.find("type_data").get("command"), "EXEC")
        self.assertIn("$PROJECT_DIR$", remote.find("dockerComposeConfigurationPaths/item").get("value"))
        xml = b"".join(p.read_bytes() for p in idea.rglob("*") if p.is_file())
        self.assertNotIn(b"secret", xml)
        self.assertNotIn(str(self.root).encode(), xml)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in idea.rglob("*") if p.is_file()}
        self.run_init()
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        result = subprocess.run(["docker", "compose", "--project-name", self.project.name, "-f", str(snapshot),
                                 "config", "--format", "json"], env=self.project.child_env(),
                                capture_output=True, text=True, check=True)
        original = json.loads(self.project.capture(["config", "--format", "json"]))
        self.assertEqual(json.loads(result.stdout), original)
        model = json.loads(result.stdout)
        mounts = {m["target"]: m["source"] for m in model["services"]["php-fpm"]["volumes"]}
        self.assertEqual(Path(mounts["/var/www/html"]), self.app / "public")

    def test_old_module_migration_preserves_personal_and_custom_settings(self):
        idea = self.app / ".idea"
        idea.mkdir()
        (idea / "modules.xml").write_text('<project version="4"><component name="ProjectModuleManager"><modules>'
            '<module filepath="$PROJECT_DIR$/.idea/docker.iml" fileurl="file://$PROJECT_DIR$/.idea/docker.iml" />'
            '</modules></component></project>')
        (idea / "docker.iml").write_text('<module type="WEB_MODULE" version="4"><component name="NewModuleRootManager">'
            '<content url="file://$MODULE_DIR$"><sourceFolder url="file://$MODULE_DIR$/examples/php-redis/database/doctrine/versions" />'
            '<excludeFolder url="file://$PROJECT_DIR$/custom-cache" /></content></component></module>')
        workspace = b'<project version="4"><component name="Personal"><option name="keep" value="yes" /></component></project>'
        (idea / "workspace.xml").write_bytes(workspace)
        (idea / "runConfigurations").mkdir()
        custom = idea / "runConfigurations/custom.xml"
        custom.write_text('<component name="Custom" />')
        self.run_init()
        self.assertFalse((idea / "docker.iml").exists())
        module = (idea / "example.iml").read_text()
        self.assertNotIn("examples/php-redis", module)
        self.assertIn("custom-cache", module)
        self.assertIn('name="Personal"', (idea / "workspace.xml").read_text())
        self.assertEqual(custom.read_text(), '<component name="Custom" />')
        backups = list((self.app / ".generated/phpstorm-backups").glob("*/.idea/workspace.xml"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), workspace)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_conflicting_run_or_output_symlink_aborts_before_writes(self):
        idea = self.app / ".idea"
        (idea / "runConfigurations").mkdir(parents=True)
        conflict = idea / "runConfigurations/setup_up.xml"
        conflict.write_text('<component><configuration name="User command" /></component>')
        with self.assertRaisesRegex(ValueError, "user run configuration"):
            self.run_init()
        self.assertFalse((idea / ".name").exists())
        conflict.unlink()
        target = self.root / "untouched"
        target.write_text("keep")
        (idea / "php.xml").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.run_init()
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse((idea / "modules.xml").exists())

    def test_startup_prepares_only_and_survives_ide_removing_comments(self):
        self.run_init()
        idea = self.app / ".idea"
        startup = ET.parse(idea / "startup.xml").getroot()
        self.assertEqual([c.get("name") for c in startup.findall(".//configuration")], ["Setup: Prepare IDE"])
        run = idea / "runConfigurations/setup_prepare_ide.xml"
        document = ET.parse(run)  # Default parser removes comments, as the IDE does.
        document.write(run)
        self.run_init()
        command = ET.parse(run).find(".//option[@name='SCRIPT_TEXT']").get("value")
        self.assertEqual(command, "make ENV=local ide-init")
        self.assertNotIn("up", command.split())
        self.assertNotIn("db-import", command)

    def test_refresh_does_not_modify_ide_user_settings(self):
        self.run_init()
        php = self.app / ".idea/php.xml"
        before = php.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            refresh_ide(self.project)
        self.assertEqual(php.read_bytes(), before)

    def test_docker_discovery_matches_current_endpoint_not_first_connection(self):
        config = self.root / "ide-config/options"
        config.mkdir(parents=True)
        (config / "remote-servers.xml").write_text('<application><component><remote-server name="Other" type="docker">'
            '<configuration><option name="apiUrl" value="unix:///wrong.sock" /></configuration></remote-server>'
            '<remote-server name="Desktop" type="docker"><configuration><option name="apiUrl" '
            'value="unix://$USER_HOME$/.docker/desktop/docker.sock" /></configuration></remote-server></component></application>')
        result = SimpleNamespace(returncode=0, stdout=f"unix://{Path.home()}/.docker/desktop/docker.sock\n")
        with patch("project_ide.subprocess.run", return_value=result), patch.dict(os.environ, {"DOCKER_HOST": "", "DOCKER_CONTEXT": ""}):
            self.assertEqual(docker_server_name(config.parent), ("Desktop", True))


if __name__ == "__main__":
    unittest.main()
