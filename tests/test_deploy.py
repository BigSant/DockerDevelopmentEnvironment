"""Code deploy: release export, rsync that spares writable paths and target-only modules, rollback, routing."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
import project_deploy
from project_deploy import build_code, deploy_code, module_pull, module_versions, newer, rsync_command, target_location


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)


class DeployTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="deploy-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.shop = self.root / "app/public"
        self.shop.mkdir(parents=True)
        git(self.shop, "init", "-q")
        git(self.shop, "config", "user.email", "t@example.invalid")
        git(self.shop, "config", "user.name", "T")
        git(self.shop, "config", "commit.gpgsign", "false")
        self.commit("v1", {"index.php": "<?php // v1\n", "old.php": "<?php\n",
                           "modules/shop/shop.php": "<?php class Shop { function __construct() { $this->version = '1.0.0'; } }\n"})
        self.target = self.root / "target"
        (self.target / "app/public/var/cache").mkdir(parents=True)
        (self.target / "app/public/var/cache/keep.txt").write_text("cache")
        (self.target / "app/public/img/p").mkdir(parents=True)
        (self.target / "app/public/img/p/1.jpg").write_text("image")
        (self.target / "app/public/app/config").mkdir(parents=True)
        (self.target / "app/public/app/config/parameters.php").write_text("<?php // server keys\n")
        (self.target / "app/public/modules/serveronly").mkdir(parents=True)
        (self.target / "app/public/modules/serveronly/serveronly.php").write_text("<?php\n")
        # Files a server has that the shop repository does not track: a module's vendor, core extras.
        (self.target / "app/public/modules/shop/vendor").mkdir(parents=True)
        (self.target / "app/public/modules/shop/vendor/lib.php").write_text("<?php // installed on the server\n")
        (self.target / "app/public/docs").mkdir()
        (self.target / "app/public/docs/readme.txt").write_text("core extra")
        self.calls = []
        self.project = SimpleNamespace(
            environment="stage", root=self.root, directory=self.root / "app", web_directory=self.shop,
            settings={"DEPLOY_PATH": str(self.target), "DEPLOY_CONTEXT": "shop-stage", "DEPLOY_SSH": "", "APP_UID": "1012",
                      "APP_GID": "1013", "PS_VERSION": "9.0.2"},
            process_env={"HOST_UID": "1012", "HOST_GID": "1013"}, run=lambda args, profiles=None: self.calls.append(args))
        output = contextlib.redirect_stdout(io.StringIO())
        self.output = output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def commit(self, tag, files, removed=()):
        for name, text in files.items():
            (self.shop / name).parent.mkdir(parents=True, exist_ok=True)
            (self.shop / name).write_text(text)
        for name in removed:
            git(self.shop, "rm", "-q", name)
        git(self.shop, "add", "-A")
        git(self.shop, "commit", "-q", "-m", tag)
        git(self.shop, "tag", tag)

    def deploy(self, **options):
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.in_release_image", lambda project, release, command: self.calls.append(["image", command])), \
                patch("project_application.plan_application", return_value=([], False)), \
                patch("database_backup.backup_database", lambda project, registry=False: self.calls.append(["backup", registry]) or "b.sql.gz"), \
                patch("project_deploy.upgrade_modules", lambda project, release: self.calls.append(["upgrade"])):
            return deploy_code(self.project, **options)

    def test_rsync_spares_writable_paths_and_target_only_modules(self):
        command = rsync_command(Path("/release"), "shop@host", "/srv/app/public", ["serveronly"])
        self.assertNotIn("--delete", command, "the target keeps files the code does not track")
        self.assertIn("--checksum", command)
        for pattern in ("/var/", "/img/", "/app/config/parameters.php", "/modules/*/cache/", "/modules/serveronly/", "/.git/"):
            self.assertIn(f"--exclude={pattern}", command)
        self.assertEqual(command[-2:], ["/release/", "shop@host:/srv/app/public/"])
        with self.assertRaisesRegex(ValueError, "user@host"):
            target_location(SimpleNamespace(settings={"DEPLOY_SSH": "host; rm -rf"}, web_directory=self.shop, root=self.root), "/srv")

    def test_module_versions_and_comparison(self):
        release, _ = self.build("v1")
        self.assertEqual(module_versions(release), {"shop": "1.0.0"})
        self.assertTrue(newer("1.10.0", "1.9.3"))
        self.assertFalse(newer("1.0.0", "1.0.0"))

    def build(self, ref):
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.in_release_image", lambda *args: None), \
                patch("project_application.plan_application", return_value=([], False)):
            return build_code(self.project, ref)

    def test_release_is_built_once_per_commit(self):
        (self.root / "app/config/composer").mkdir(parents=True)
        (self.root / "app/config/composer/composer.json").write_text("{}\n")
        release, commit = self.build("v1")
        self.assertEqual((release / "index.php").read_text(), "<?php // v1\n")
        self.assertEqual((release / "composer.json").read_text(), "{}\n", "the project composer files drive vendor")
        self.assertFalse((release / ".git").exists())
        self.assertEqual(self.build("v1")[0], release)
        self.assertIn("already built", self.output.getvalue())
        with self.assertRaisesRegex(ValueError, "no commit 'v9'"):
            self.build("v9")

    def test_deploy_updates_code_keeps_server_files_and_rolls_back(self):
        self.deploy(ref="v1")
        public = self.target / "app/public"
        self.assertEqual((public / "index.php").read_text(), "<?php // v1\n")
        self.assertIn("First deploy: nothing removed", self.output.getvalue())
        self.assertEqual(self.calls[0], ["backup", True], "the backup follows the registry and comes first")
        self.assertIn(["upgrade"], self.calls)
        self.assertEqual(self.calls[-1][-1], "cache-clear")
        self.assertIn("module serveronly exists only on the target", self.output.getvalue())
        self.commit("v2", {"index.php": "<?php // v2\n"}, removed=["old.php"])
        self.deploy(ref="v2")
        self.assertEqual((public / "index.php").read_text(), "<?php // v2\n")
        self.assertFalse((public / "old.php").exists(), "a file the previous release shipped and this one drops is removed")
        self.assertIn("Removed 1 files", self.output.getvalue())
        for kept, text in (("var/cache/keep.txt", "cache"), ("img/p/1.jpg", "image"),
                           ("app/config/parameters.php", "<?php // server keys\n"), ("modules/serveronly/serveronly.php", "<?php\n"),
                           ("modules/shop/vendor/lib.php", "<?php // installed on the server\n"), ("docs/readme.txt", "core extra")):
            self.assertEqual((public / kept).read_text(), text, kept)
        self.calls.clear()
        self.deploy(rollback=True, backup=False)
        self.assertEqual((public / "index.php").read_text(), "<?php // v1\n")
        self.assertTrue((public / "old.php").is_file(), "rolling back restores the file")
        self.assertNotIn(["backup", True], self.calls)
        entries = json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())
        self.assertEqual([(e["ref"], e["rollback"]) for e in entries], [("v1", False), ("v2", False), ("v1", True)])
        with self.assertRaisesRegex(ValueError, "ref=TAG"):
            self.deploy()

    def test_module_pull_copies_a_target_only_module_into_the_checkout(self):
        module_pull(self.project, "serveronly")
        self.assertTrue((self.shop / "modules/serveronly/serveronly.php").is_file())
        with self.assertRaisesRegex(ValueError, "already exists"):
            module_pull(self.project, "serveronly")
        with self.assertRaisesRegex(ValueError, "Name the module"):
            module_pull(self.project, "../x")


class RemoteRoutingTest(unittest.TestCase):
    def test_running_containers_live_on_the_target_and_images_build_here(self):
        from project import Project
        project = Project.__new__(Project)
        project.environment = "stage"
        project.profiles = ""
        project.process_env = {"DOCKER_CONTEXT": "shop-stage"}
        self.assertEqual(project.compose_env(["exec", "-T", "database"])["DOCKER_CONTEXT"], "shop-stage")
        self.assertNotIn("DOCKER_CONTEXT", project.compose_env(["build"]))
        with self.assertRaisesRegex(ValueError, "make deploy-images ENV=stage"):
            project.compose_env(["up", "-d"])

    def test_registry_backup_leaves_out_skipped_data_but_keeps_structure(self):
        from database_backup import registry_passes
        with patch("database_import.skip_rule", return_value=lambda table: table == "ps_connections"), \
                patch("database_schema.database_tables", return_value=["ps_orders", "ps_connections"]):
            passes = registry_passes(SimpleNamespace(), "shop")
        self.assertEqual(passes, [["--ignore-table=shop.ps_connections"],
                                  ["--no-data", "--skip-routines", "--skip-events", "ps_connections"]])
        with patch("database_import.skip_rule", return_value=None):
            self.assertEqual(registry_passes(SimpleNamespace(), "shop"), [[]])


if __name__ == "__main__":
    unittest.main()
