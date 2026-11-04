"""Code deploy: release export, rsync that spares writable paths and target-only modules, rollback, routing."""

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from fake_database import FakeDatabase
import project_deploy
from project_deploy import (build_code, code_rsync_options, deploy_code, module_pull, module_versions, newer,
                            protected_paths, upgrade_modules)
from project_target import LocalTarget, SshTarget


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)


def git_commit(directory, ref):
    return subprocess.run(["git", "-C", str(directory), "rev-parse", ref], check=True, capture_output=True,
                          text=True).stdout.strip()


def compose_model(root):
    """The containers' bind mounts: code, data, the environment repository's config and its .generated."""
    bind = lambda source, target: {"type": "bind", "source": str(source), "target": target}
    return {"services": {"php-fpm": {"volumes": [bind(root / "app/public", "/var/www/html"),
                                                   bind(root / "app/config/php", "/usr/local/etc/php/project"),
                                                   bind(root / "data/logs", "/var/log/php"),
                                                   bind(root / "app/.generated/backups", "/backups")]}}}


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
        # The environment repository's mounted config (copied to DEPLOY_PATH) and what never is.
        (self.root / "app/config/php").mkdir(parents=True)
        (self.root / "app/config/php/zz-project.ini").write_text("memory_limit = 256M\n")
        (self.root / "app/.generated/backups").mkdir(parents=True)
        (self.root / "app/.generated/backups/shop.sql.gz").write_text("private")
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
                      "APP_GID": "1013", "PS_VERSION": "9.0.2", "PROFILE": "prestashop"},
            process_env={"HOST_UID": "1012", "HOST_GID": "1013"}, run=lambda args, profiles=None: self.calls.append(args),
            data_directory=self.root / "data", active_model=lambda: compose_model(self.root), name="shop-stage")
        # Where the containers run from is checked against the target's Docker (test_release); here it is recorded.
        self.root_checks = []
        checker = patch("project_release.check_project_root",
                        lambda project, context, model, path: self.root_checks.append((context, path, sorted(model["services"]))))
        checker.start()
        self.addCleanup(checker.stop)
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

    def deploy(self, plan=([], False), **options):
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.in_release_image", lambda project, release, command, cache=None: self.calls.append(["image", command])), \
                patch("project_application.plan_application", return_value=plan), \
                patch("database_backup.backup_database", lambda project, registry=False: self.calls.append(["backup", registry]) or "b.sql.gz"), \
                patch("project_deploy.upgrade_modules", lambda project, release: self.calls.append(["upgrade"])):
            return deploy_code(self.project, **options)

    def test_release_files_reach_the_protected_paths_the_code_rsync_leaves_out(self):
        release, target = self.root / "release", self.root / "server"
        for name, text in {"cache/.htaccess": "Deny from all\n", "cache/index.php": "release\n", "img/index.php": "release\n",
                           "img/c/index.php": "release\n", "img/admin/add.gif": "new\n", "img/admin/new.gif": "new\n",
                           "upload/index.php": "release\n", "vendor/autoload.php": "code\n"}.items():
            (release / name).parent.mkdir(parents=True, exist_ok=True)
            (release / name).write_text(text)
        for name, text in {"cache/index.php": "server\n", "img/c/10.jpg": "content\n", "img/admin/add.gif": "old\n",
                           "img/admin/server-only.gif": "kept\n"}.items():
            (target / name).parent.mkdir(parents=True, exist_ok=True)
            (target / name).write_text(text)
        self.project.settings.update({"PS_RELEASE": "on", "PS_RELEASE_PATHS": "admin vendor img/admin"})
        self.assertEqual(project_deploy.protected_release_files(self.project, release),
                         [("img", False), ("upload", False), ("cache", False), ("img/admin", True)],
                         "the directories the release has, then its own paths inside them; admin and vendor travel as code")
        project_deploy.push_protected_release_files(self.project, release, LocalTarget(), str(target))
        self.assertEqual((target / "cache/.htaccess").read_text(), "Deny from all\n", "a missing guard arrives")
        self.assertEqual((target / "cache/index.php").read_text(), "server\n", "a file the target has stays")
        self.assertEqual((target / "img/c/10.jpg").read_text(), "content\n")
        self.assertEqual((target / "img/c/index.php").read_text(), "release\n")
        self.assertEqual((target / "img/admin/add.gif").read_text(), "new\n", "a release path follows the release")
        self.assertEqual((target / "img/admin/server-only.gif").read_text(), "kept\n", "never --delete")
        self.assertFalse((target / "vendor").exists())
        self.project.settings["PS_RELEASE"] = ""
        self.assertEqual(project_deploy.protected_release_files(self.project, release), [], "no release, nothing to send")

    def test_rsync_spares_writable_paths_and_target_only_modules(self):
        command = code_rsync_options(protected_paths(self.project) + ["/modules/serveronly/"])
        self.assertNotIn("--delete", command, "the target keeps files the code does not track")
        self.assertIn("--checksum", command)
        for pattern in ("/var/", "/img/", "/app/config/parameters.php", "/modules/*/cache/", "/modules/serveronly/", "/.git/",
                        "/modules/*/translations/", "/themes/*/modules/*/translations/", "/translations/export/",
                        "/mails/*/lang.php", "/themes/*/mails/*/lang.php"):
            self.assertIn(f"--exclude={pattern}", command)
        for tracked in ("/mails/", "/translations/", "/config/xml/"):
            self.assertNotIn(f"--exclude={tracked}", command, "paths the shop repository tracks are code")

    def test_a_server_target_gets_the_code_and_the_removals_over_ssh(self):
        calls = []

        def record(command, **kwargs):
            calls.append((command, kwargs))
            listed = "serveronly\nshop\n" if command[0] == "ssh" and command[-1].startswith("find ") else ""
            return subprocess.CompletedProcess(command, 0, listed, "")

        server = SshTarget("shop@host.example", run=record)
        with patch("project_deploy.deploy_target", lambda project: server), \
                patch("project_release.sync_environment", lambda project, path: []):
            self.deploy(ref="v1")
            self.commit("v2", {"index.php": "<?php // v2\n"}, removed=["old.php"])
            self.deploy(ref="v2")
        code = f"{self.target}/app/public"
        commands = [command for command, _ in calls]
        self.assertEqual(commands[0][-1], f"find {code}/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\\n'")
        pushes = [command for command in commands if command[0] == "rsync"]
        self.assertEqual(pushes[0][-1], f"shop@host.example:{code}/")
        self.assertIn("--exclude=/modules/serveronly/", pushes[0], "a module only the server has is left alone")
        self.assertIn("module serveronly exists only on the target", self.output.getvalue())
        removal = [(command, kwargs) for command, kwargs in calls if "xargs -0" in command[-1]]
        self.assertEqual([kwargs["input"] for _, kwargs in removal], [b"old.php\0"])
        self.assertFalse((self.target / "app/public/index.php").exists(), "nothing is written to this machine's DEPLOY_PATH")

    def test_module_versions_and_comparison(self):
        release, _ = self.build("v1")
        self.assertEqual(module_versions(release), {"shop": "1.0.0"})
        self.assertTrue(newer("1.10.0", "1.9.3"))
        self.assertFalse(newer("1.0.0", "1.0.0"))

    def test_only_installed_modules_with_a_newer_code_version_are_upgraded(self):
        release = self.root / "release"
        ran = []
        project = SimpleNamespace(settings={"PS_VERSION": "8.1.7", "PS_DATABASE_PREFIX": "fs_"}, run=ran.append,
                                  database=FakeDatabase(answer=lambda sql: [("shop", "0.9.0"), ("blog", "2.0.0")]))
        with patch("project_deploy.module_versions", return_value={"shop": "1.0.0", "blog": "2.0.0", "new": "1.0.0"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(upgrade_modules(project, release), ["shop"])
        self.assertIn("FROM `fs_module`", project.database.queried[0])
        self.assertEqual(ran, [["exec", "-T", "php-fpm", "php", "bin/console", "prestashop:module", "upgrade", "shop"]])

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

    def test_containers_of_another_project_root_stop_the_deploy_before_anything_changes(self):
        def foreign(project, context, model, path):
            raise ValueError("The shop-stage containers on shop-stage run from /home/other/shop/app/public")

        with patch("project_release.check_project_root", foreign), self.assertRaisesRegex(ValueError, "/home/other/shop"):
            self.deploy(ref="v1", backup=True)
        self.assertEqual(self.calls, [], "no backup, hook or upgrade in another project's containers")
        self.assertFalse((self.target / "app/public/index.php").exists(), "no code reached the target")

    def test_cache_auto_clears_only_when_the_deploy_changes_what_the_cache_holds(self):
        cleared = lambda: ["exec", "-T", "php-fpm", "php", "/opt/setup/runtime/prestashop-command.php", "cache-clear"] in self.calls
        self.deploy(ref="v1")
        self.assertTrue(cleared(), "the first deploy clears")
        self.assertIn("CACHE=auto: yes (the first deploy)", self.output.getvalue())
        steps = [
            ("v2", {"index.php": "<?php // v2\n"}, (), False, "nothing cache-related changed"),
            ("v3", {"modules/shop/config/services.yml": "services: {}\n"}, (), True, "modules/shop/config/services.yml"),
            ("v4", {"themes/t/templates/a.twig": "x\n"}, (), True, "themes/t/templates/a.twig"),
            ("v5", {"modules/shop/translations/lt-LT/Shop.lt-LT.xlf": "<xliff/>\n"}, (), True, ".xlf"),
            ("v6", {"override/classes/Cart.php": "<?php // new\n"}, (), True, "override/classes/Cart.php"),
            ("v7", {"override/classes/Cart.php": "<?php // changed\n"}, (), False, "nothing cache-related changed"),
            ("v8", {"index.php": "<?php // v8\n"}, ("override/classes/Cart.php",), True, "override/classes/Cart.php"),
        ]
        for tag, files, removed, expected, reason in steps:
            with self.subTest(tag=tag):
                self.commit(tag, files, removed=removed)
                self.calls.clear()
                self.output.truncate(0)
                self.output.seek(0)
                self.deploy(ref=tag)
                self.assertEqual(cleared(), expected, self.output.getvalue())
                self.assertIn(f"CACHE=auto: {'yes' if expected else 'no'} (", self.output.getvalue())
                self.assertIn(reason, self.output.getvalue())
                self.assertEqual(project_deploy.history(self.project)[-1]["cache"], expected)
        # A project's own paths; cache=yes and cache=no whatever changed.
        self.project.settings["DEPLOY_CACHE_CLEAR_PATHS"] = "config/*.php"
        self.commit("v9", {"config/local.php": "<?php\n"})
        self.calls.clear()
        self.deploy(ref="v9")
        self.assertTrue(cleared())
        self.commit("v10", {"modules/shop/config/routes.yml": "x: {}\n"})
        self.calls.clear()
        self.deploy(ref="v10", cache="no")
        self.assertFalse(cleared(), "cache=no leaves it")
        self.assertIn("CACHE=no", self.output.getvalue())
        self.commit("v11", {"index.php": "<?php // v11\n"})
        self.calls.clear()
        self.deploy(ref="v11", cache="yes")
        self.assertTrue(cleared(), "cache=yes clears it")
        with self.assertRaisesRegex(ValueError, "cache must be auto, yes or no"):
            self.deploy(ref="v11", cache="maybe")

    def test_cache_auto_reasons_beyond_the_code(self):
        entries = [{"commit": "a" * 40, "ps_version": "9.0.2"}]
        needed = lambda **options: project_deploy.cache_needed(self.project, options.pop("entries", entries), "b" * 40, **options)
        self.assertEqual(needed(rollback=True), (True, "a rollback"))
        self.assertEqual(needed(entries=[{**entries[0], "failed": "after-rsync"}])[0], True)
        self.assertEqual(needed(translations=["20261028-a.json"]), (True, "translation migrations applied: 20261028-a.json"))
        self.project.settings["PS_VERSION"] = "9.1.4"
        self.assertEqual(needed(), (True, "PrestaShop 9.0.2 -> 9.1.4"))
        self.assertEqual(needed(entries=[{"commit": "a" * 40}]), (True, "the previous deploy recorded no PrestaShop version"))
        self.project.settings["PS_VERSION"] = "9.0.2"
        self.assertTrue(needed()[1].startswith("the shop repository cannot compare"), "unknown commits clear, to be safe")
        self.project.settings["PROFILE"] = ""
        self.assertEqual(needed(), (True, "no DEPLOY_CACHE_CLEAR_PATHS: every deploy clears the cache"),
                         "a generic project without paths clears as before")

    def test_deploy_updates_code_keeps_server_files_and_rolls_back(self):
        self.deploy(ref="v1", backup=True)
        self.assertEqual(self.root_checks, [("shop-stage", str(self.target), ["php-fpm"])],
                         "the release model's containers are checked against DEPLOY_PATH on the target")
        public = self.target / "app/public"
        self.assertEqual((public / "index.php").read_text(), "<?php // v1\n")
        self.assertIn("First deploy: nothing removed", self.output.getvalue())
        self.assertEqual(self.calls[0], ["backup", True], "the backup follows the registry and comes first")
        self.assertIn(["upgrade"], self.calls)
        self.assertEqual(self.calls[-2][-1], "cache-clear")
        self.assertEqual(self.calls[-1], ["exec", "-T", "php-fpm", "sh", "-c", "kill -USR2 1"], "OPcache must not keep serving the old code")
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

    def test_translation_migrations_follow_the_module_upgrades_and_are_recorded_with_the_deploy(self):
        ran = []

        def migrate(project, dry=False):
            ran.append(list(self.calls))
            return ["translations:20261027100000-cart.json"]
        with patch("project_translations.migrate_translations", migrate):
            self.deploy(ref="v1")
            self.assertEqual(ran, [], "no translations/migrations/ in the environment repository: nothing to migrate")
            (self.root / "app/translations/migrations").mkdir(parents=True)
            self.calls.clear()
            self.deploy(ref="v1")
        self.assertEqual(len(ran), 1)
        self.assertIn(["upgrade"], ran[0], "after the module upgrades")
        self.assertFalse([call for call in ran[0] if call[-1:] == ["cache-clear"]], "before the cache is cleared")
        entries = json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())
        self.assertEqual(entries[-1]["translations"], ["translations:20261027100000-cart.json"])

    def test_a_profile_without_application_steps_still_deploys(self):
        self.deploy(ref="v1", plan=None)
        self.assertEqual((self.target / "app/public/index.php").read_text(), "<?php // v1\n")

    def test_module_pull_copies_a_target_only_module_into_the_checkout(self):
        module_pull(self.project, "serveronly")
        self.assertTrue((self.shop / "modules/serveronly/serveronly.php").is_file())
        with self.assertRaisesRegex(ValueError, "already exists"):
            module_pull(self.project, "serveronly")
        with self.assertRaisesRegex(ValueError, "Name the module"):
            module_pull(self.project, "../x")

    def test_prestashop_keeps_its_server_htaccess_robots_and_project_protected_paths(self):
        self.commit("v2", {".htaccess": "# shop repo rules\n", "robots.txt": "repo robots\n",
                           "themes/shop/local/notes.txt": "repo\n"})
        public = self.target / "app/public"
        (public / ".htaccess").write_text("# generated by PrestaShop\n")
        (public / "robots.txt").write_text("server robots\n")
        (public / "themes/shop/local").mkdir(parents=True)
        (public / "themes/shop/local/notes.txt").write_text("server\n")
        self.project.settings["DEPLOY_PROTECTED_PATHS"] = "themes/shop/local/"
        self.deploy(ref="v2")
        self.assertEqual((public / ".htaccess").read_text(), "# generated by PrestaShop\n", "PrestaShop writes its own .htaccess")
        self.assertEqual((public / "robots.txt").read_text(), "server robots\n")
        self.assertEqual((public / "themes/shop/local/notes.txt").read_text(), "server\n", "project paths add to the PS list")


    def test_translations_language_packs_and_exports_are_server_content_and_mails_are_code(self):
        # Language packs are written on the server too: installing or updating a pack in the back office, and translation
        # modules (ets_translate) that add their units to translations/<locale>/*.xlf.
        content = ("modules/shop/translations/lt.php", "themes/shop/modules/shop/translations/lt.php",
                   "translations/export/lt.zip", "translations/lt-LT/Admin.xlf")
        self.commit("v2", {**{name: "repo\n" for name in content}, "mails/lt/order.html": "mail v2\n"})
        public = self.target / "app/public"
        for name in content:
            (public / name).parent.mkdir(parents=True, exist_ok=True)
            (public / name).write_text("edited in the back office\n")
        self.deploy(ref="v2")
        for name in content:
            self.assertEqual((public / name).read_text(), "edited in the back office\n", f"{name} is never overwritten")
        self.assertEqual((public / "mails/lt/order.html").read_text(), "mail v2\n", "a mail template is code")
        self.commit("v3", {"index.php": "<?php // v3\n"}, removed=[*content, "mails/lt/order.html"])
        self.deploy(ref="v3")
        for name in content:
            self.assertTrue((public / name).is_file(), f"untracking {name} deletes nothing on the server")
        self.assertFalse((public / "mails/lt/order.html").exists(), "a dropped mail template leaves with the code")


class GenericDeployTest(unittest.TestCase):
    """An empty PROFILE deploys any PHP application: no PrestaShop step, every tracked file is code."""

    setUp = DeployTest.setUp

    def test_the_mounted_environment_files_follow_the_deploy_to_deploy_path(self):
        self.project.settings["PROFILE"] = ""
        self.deploy(ref="v1")
        self.assertEqual((self.target / "app/config/php/zz-project.ini").read_text(), "memory_limit = 256M\n")
        self.assertFalse((self.target / "app/.generated").exists(), ".generated never leaves this machine")
        (self.root / "app/config/php/zz-project.ini").unlink()
        (self.root / "app/config/php/yy.ini").write_text("x = 1\n")
        self.deploy(ref="v1")
        self.assertEqual(sorted(path.name for path in (self.target / "app/config/php").iterdir()), ["yy.ini"],
                         "the copy follows the repository, removals included")
        # A release model's sources are DEPLOY_PATH's already (deploy-images copies from it).
        from project_release import environment_mounts
        mapped = json.loads(json.dumps(compose_model(self.root)).replace(str(self.root), "/srv/site"))
        self.assertEqual(environment_mounts(self.project, mapped, "/srv/site"), ["app/config/php"])
        # The build root itself is the target: nothing to copy.
        from project_release import sync_environment
        self.assertEqual(sync_environment(self.project, str(self.root)), [])
    commit = DeployTest.commit

    def generic(self, **settings):
        self.project.settings.update({"PROFILE": "", **settings})
        self.project.settings.pop("PS_VERSION")
        self.commit("g1", {".htaccess": "RewriteEngine On\nAuthType Basic\n", "robots.txt": "User-agent: *\n",
                           "src/Kernel.php": "<?php // g1\n", "var/data/seed.json": "{}\n", "var/log/.gitkeep": "",
                           "config/local.php": "<?php // repo default\n"})
        public = self.target / "app/public"
        (public / ".htaccess").write_text("# old server copy\n")
        (public / "var/data").mkdir(parents=True)
        (public / "var/data/seed.json").write_text('{"live": true}\n')
        (public / "var/data/uploads.db").write_text("live data")
        (public / "var/log").mkdir(parents=True)
        (public / "var/log/app.log").write_text("live log")
        (public / "config").mkdir(parents=True, exist_ok=True)
        (public / "config/local.php").write_text("<?php // server copy\n")
        return public

    def deploy(self, **options):
        self.project.database = FakeDatabase()
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.in_release_image", lambda project, release, command, cache=None: self.calls.append(["image", command])), \
                patch("database_backup.backup_database", lambda project, registry=False: self.calls.append(["backup", registry]) or "b.sql.gz"), \
                patch("database_migrations_guarded.migrate", lambda project: self.calls.append(["migrate"])), \
                patch("database_schema.schema_report", lambda project: self.calls.append(["schema-report"])):
            try:
                return deploy_code(self.project, **options)
            finally:
                self.assertEqual(self.project.database.queried, [], "a generic deploy never queries the database for modules")

    def test_generic_deploy_reads_no_ps_module_and_upgrades_nothing(self):
        public = self.generic()
        self.deploy(ref="g1")
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g1\n")
        flat = json.dumps(self.calls)
        for prestashop in ("prestashop:module", "prestashop-command.php"):
            self.assertNotIn(prestashop, flat)
        self.assertNotIn("exists only on the target", self.output.getvalue(), "modules/ means nothing to a generic project")
        self.assertNotIn(["migrate"], self.calls, "setup SQL migrations run only when the project has them")
        self.assertNotIn(["schema-report"], self.calls)
        self.assertIn("No CACHE_CLEAR_COMMAND", self.output.getvalue())

    def test_every_profile_reloads_php_fpm_last_unless_the_project_opts_out(self):
        self.generic(CACHE_CLEAR_COMMAND="php bin/console cache:clear")
        self.deploy(ref="g1")
        self.assertEqual(self.calls[-2:], [["exec", "-T", "php-fpm", "php", "bin/console", "cache:clear"], ["exec", "-T", "php-fpm", "sh", "-c", "kill -USR2 1"]])
        self.assertIn("php-fpm reloaded", self.output.getvalue())
        self.calls.clear()
        self.project.settings["DEPLOY_PHP_RELOAD"] = "off"
        self.deploy(ref="g1")
        self.assertNotIn(["exec", "-T", "php-fpm", "sh", "-c", "kill -USR2 1"], self.calls)
        self.assertIn("DEPLOY_PHP_RELOAD=off", self.output.getvalue())
        self.project.settings["DEPLOY_PHP_RELOAD"] = "maybe"
        with self.assertRaisesRegex(ValueError, "DEPLOY_PHP_RELOAD must be on or off"):
            self.deploy(ref="g1")

    def test_cron_reload_runs_in_the_cron_container_only_when_the_project_has_cron(self):
        from project_deploy import CRON_RELOAD, reload_cron
        reload_cron(self.project)
        self.assertEqual(self.calls, [])
        self.assertIn("No cron service", self.output.getvalue())
        self.project.process_env["SETUP_ENABLE_CRON"] = "1"
        reload_cron(self.project)
        self.assertEqual(self.calls, [["exec", "-T", "cron", "sh", "-e", "-c", CRON_RELOAD]])

        def refuse(args, profiles=None):
            raise subprocess.CalledProcessError(1, args)

        self.project.run = refuse
        with self.assertRaisesRegex(ValueError, "previous schedule stays running.*make cron-reload ENV=stage"):
            reload_cron(self.project)

    def test_generic_projects_keep_translation_paths_as_code(self):
        self.project.settings["PROFILE"] = ""
        for pattern in ("/modules/*/translations/", "/themes/*/modules/*/translations/", "/translations/export/",
                        "/mails/*/lang.php"):
            self.assertNotIn(pattern, protected_paths(self.project))

    def test_generic_htaccess_and_robots_deploy_as_code(self):
        public = self.generic()
        self.deploy(ref="g1")
        self.assertEqual((public / ".htaccess").read_text(), "RewriteEngine On\nAuthType Basic\n", "routes and Basic auth reach the server")
        self.assertEqual((public / "robots.txt").read_text(), "User-agent: *\n")
        self.assertEqual((public / "var/data/seed.json").read_text(), "{}\n", "without a protected path var/ is code too")

    def test_project_protected_paths_are_never_overwritten_or_deleted(self):
        public = self.generic(DEPLOY_PROTECTED_PATHS="var/data/ /var/log/ config/local.php")
        self.deploy(ref="g1")
        self.assertEqual((public / "var/data/seed.json").read_text(), '{"live": true}\n')
        self.assertEqual((public / "config/local.php").read_text(), "<?php // server copy\n")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"}, removed=["var/data/seed.json", "config/local.php", "var/log/.gitkeep"])
        self.deploy(ref="g2")
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g2\n")
        for kept, text in (("var/data/seed.json", '{"live": true}\n'), ("var/data/uploads.db", "live data"),
                           ("var/log/app.log", "live log"), ("config/local.php", "<?php // server copy\n")):
            self.assertEqual((public / kept).read_text(), text, f"{kept} is protected")
        self.calls.clear()
        self.deploy(rollback=True, backup=False)
        self.assertEqual((public / "var/data/seed.json").read_text(), '{"live": true}\n', "a rollback respects them too")

    def test_protected_paths_must_stay_inside_the_code(self):
        for entry in ("../data/", "var/../../x", "/", "*", "var data/'", "~/x"):
            self.project.settings["DEPLOY_PROTECTED_PATHS"] = entry
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "DEPLOY_PROTECTED_PATHS"):
                protected_paths(self.project)

    def test_after_rsync_hook_runs_in_php_container_once_the_new_code_is_there(self):
        public = self.generic(CACHE_CLEAR_COMMAND="php bin/console cache:clear")
        (self.root / "app/config/deploy").mkdir(parents=True)
        (self.root / "app/config/deploy/after-rsync.sh").write_text("php bin/console doctrine:migrations:migrate -n\n")
        seen = []
        self.project.run = lambda args, profiles=None: seen.append(((public / "src/Kernel.php").read_text(), args))
        self.deploy(ref="g1")
        hook = [args for _, args in seen if "doctrine:migrations:migrate" in " ".join(args)]
        self.assertEqual(hook, [["exec", "-T", "php-fpm", "sh", "-e", "-c", "php bin/console doctrine:migrations:migrate -n\n"]])
        self.assertEqual(seen[0][0], "<?php // g1\n", "the hook sees the rsynced code")
        self.assertEqual([call for _, call in seen[-2:]], [["exec", "-T", "php-fpm", "php", "bin/console", "cache:clear"],
                                                          ["exec", "-T", "php-fpm", "sh", "-c", "kill -USR2 1"]],
                         "cache clear, then the php-fpm reload, come last")

    def test_a_failing_after_rsync_hook_stops_the_deploy(self):
        self.generic(CACHE_CLEAR_COMMAND="php bin/console cache:clear", SCHEMA_DIRECTORY="schema")
        (self.root / "app/config/deploy").mkdir(parents=True)
        (self.root / "app/config/deploy/after-rsync.sh").write_text("exit 3\n")
        (self.root / "schema/migrations").mkdir(parents=True)

        def run(args, profiles=None):
            self.calls.append(args)
            if args[:4] == ["exec", "-T", "php-fpm", "sh"] and args[-1] == "exit 3\n":
                raise subprocess.CalledProcessError(3, args)

        self.project.run = run
        hook = self.root / "app/config/deploy/after-rsync.sh"
        hook.write_text("true\n")
        self.deploy(ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n", "src/Broken.php": "<?php\n"})
        hook.write_text("exit 3\n")
        self.calls.clear()
        with patch("database_migrations_guarded.migrations_directory", return_value=self.root / "schema/migrations"), \
                self.assertRaisesRegex(ValueError, "after-rsync.sh failed .exit 3.*recorded as failed.*rollback=1"):
            self.deploy(ref="g2")
        self.assertNotIn(["migrate"], self.calls)
        self.assertNotIn(["exec", "-T", "php-fpm", "php", "bin/console", "cache:clear"], self.calls)
        entries = json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())
        self.assertEqual([(e["ref"], e.get("failed")) for e in entries], [("g1", None), ("g2", "after-rsync")],
                         "the history says which code is on the target")
        hook.write_text("true\n")
        self.commit("g3", {"src/Kernel.php": "<?php // g3\n"}, removed=["src/Broken.php"])
        self.deploy(ref="g3")
        public = self.target / "app/public"
        self.assertFalse((public / "src/Broken.php").exists(), "removals follow the failed release the target held")
        self.deploy(rollback=True, backup=False)
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g1\n", "rollback skips the failed release")
        self.assertIn("rolling back to g1", self.output.getvalue())
        self.deploy(ref="g3")
        self.deploy(ref="g3")
        self.deploy(rollback=True, backup=False)
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g1\n", "a redeploy of the same commit is not a rollback target")

    def test_an_attempt_gets_an_event_per_stage_and_its_history_entries(self):
        self.generic(CACHE_CLEAR_COMMAND="php bin/console cache:clear")
        (self.root / "app/config/deploy").mkdir(parents=True)
        hook = self.root / "app/config/deploy/after-rsync.sh"
        hook.write_text("true\n")
        backup = self.root / "app/.generated/backups/shop-stage-1.sql.gz"
        backup.write_text("dump")
        attempt = "6f1c2d3e-0000-4000-8000-000000000001"
        with patch("database_backup.backup_database", lambda project, registry=False: backup):
            with patch("project_deploy.php_image", return_value="php:test"), \
                    patch("project_deploy.in_release_image", lambda project, release, command, cache=None: None):
                deploy_code(self.project, ref="g1", backup=True, attempt=attempt)
        events = json.loads((self.root / f"app/.generated/release/stage/attempts/{attempt}.json").read_text())["events"]
        self.assertEqual([(event["stage"], event["state"]) for event in events],
                         [("backup", "ok"), ("code", "ok"), ("hook", "ok"), ("cache", "ok"), ("reload", "ok")])
        self.assertEqual((events[0]["file"], events[0]["bytes"]), ("shop-stage-1.sql.gz", 4), "a file name, not a path")
        self.assertEqual((events[3]["cleared"], events[3]["reason"]), (True, "the first deploy"))
        self.assertTrue(all(event["at"] for event in events))
        hook.write_text("exit 3\n")

        def run(args, profiles=None):
            if args[-1] == "exit 3\n":
                raise subprocess.CalledProcessError(3, args)

        self.project.run = run
        second = "6f1c2d3e-0000-4000-8000-000000000002"
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
        with self.assertRaisesRegex(ValueError, "after-rsync.sh failed"):
            self.deploy(ref="g2", attempt=second)
        events = json.loads((self.root / f"app/.generated/release/stage/attempts/{second}.json").read_text())["events"]
        self.assertEqual([(event["stage"], event["state"]) for event in events], [("code", "ok"), ("hook", "failed")])
        # A rollback's stages are one event, which make pipeline-deploy writes; its entry carries the attempt.
        hook.write_text("true\n")
        self.deploy(rollback=True, attempt=second)
        events = json.loads((self.root / f"app/.generated/release/stage/attempts/{second}.json").read_text())["events"]
        self.assertEqual(len(events), 2)
        entries = json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())
        self.assertEqual([(e["ref"], e.get("attempt"), e.get("failed")) for e in entries],
                         [("g1", attempt, None), ("g2", second, "after-rsync"), ("g1", second, None)])
        # Without an attempt nothing new is written: no attempt field, no file.
        self.deploy(ref="g1")
        self.assertNotIn("attempt", json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())[-1])
        self.assertEqual(len(list((self.root / "app/.generated/release/stage/attempts").glob("*.json"))), 2)
        with self.assertRaisesRegex(ValueError, "attempt must be 8-64"):
            self.deploy(ref="g1", attempt="../x")

    def test_the_code_on_the_target_is_recorded_right_after_the_copy(self):
        """Review 2: a failure after the copy (migrations, cache, reload) or a killed deploy still records the release
        the target holds, as failed, so the next deploy and the status know it."""
        self.generic(SCHEMA_DIRECTORY="schema")
        (self.root / "schema/migrations").mkdir(parents=True)
        self.deploy(ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
        history = self.root / "app/.generated/release/stage/deployments.json"

        def broken(project):
            raise ValueError("migration 2 failed")

        attempt = "6f1c2d3e-0000-4000-8000-000000000003"
        with patch("database_migrations_guarded.migrations_directory", return_value=self.root / "schema/migrations"), \
                patch("database_migrations_guarded.migrate", broken), self.assertRaisesRegex(ValueError, "migration 2"):
            with patch("project_deploy.php_image", return_value="php:test"), \
                    patch("project_deploy.in_release_image", lambda project, release, command, cache=None: None):
                deploy_code(self.project, ref="g2", attempt=attempt)
        last = json.loads(history.read_text())[-1]
        self.assertEqual((last["ref"], last["failed"], last["attempt"]), ("g2", "migrations", attempt))
        self.commit("g3", {"src/Kernel.php": "<?php // g3\n"})
        with patch("project_deploy.reload_php", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            self.deploy(ref="g3")
        self.assertEqual((json.loads(history.read_text())[-1]["ref"], json.loads(history.read_text())[-1]["failed"]),
                         ("g3", "incomplete"), "a deploy killed after the copy leaves its release marked")
        self.deploy(ref="g3")
        self.assertEqual([(e["ref"], e.get("failed")) for e in json.loads(history.read_text())],
                         [("g1", None), ("g2", "migrations"), ("g3", "incomplete"), ("g3", None)])

    def test_a_failure_right_after_the_copy_is_recorded_too(self):
        """Re-review 2: the removals (or the protected files, the manifest) fail after a successful rsync."""
        self.generic()
        self.deploy(ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"}, removed=["config/local.php"])
        with patch.object(LocalTarget, "remove", side_effect=OSError("disk full")), self.assertRaisesRegex(OSError, "disk full"):
            self.deploy(ref="g2")
        self.assertEqual((self.target / "app/public/src/Kernel.php").read_text(), "<?php // g2\n", "g2 is on the target")
        last = json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())[-1]
        self.assertEqual((last["ref"], last["failed"]), ("g2", "removals"))

    def test_repeated_failures_keep_the_release_a_rollback_needs(self):
        """Re-review A, without any new setting: a good g1, then four releases failing at the reload."""
        self.generic()
        self.deploy(ref="g1")
        for name in ("g2", "g3", "g4", "g5"):
            self.commit(name, {"src/Kernel.php": f"<?php // {name}\n"})
            with patch("project_deploy.reload_php", side_effect=ValueError("reload failed")), \
                    self.assertRaisesRegex(ValueError, "reload failed"):
                self.deploy(ref=name)
        self.deploy(rollback=True)
        self.assertEqual((self.target / "app/public/src/Kernel.php").read_text(), "<?php // g1\n")

    def test_the_release_build_has_the_host_network_unless_the_project_chooses_another(self):
        """RELEASE_BUILD_NETWORK: host by default (as before); bridge or none keep the build off the host's network."""
        from project_deploy import in_release_image
        commands = []
        release = self.root / "release"

        def build(network=None):
            if network is not None:
                self.project.settings["RELEASE_BUILD_NETWORK"] = network
            with patch("project_deploy.php_image", return_value="php:test"), \
                    patch("subprocess.run", lambda command, **options: commands.append(command)):
                in_release_image(self.project, release, "composer install --no-dev")
            return commands[-1][commands[-1].index("--network") + 1]

        self.assertEqual(build(), "host", "a project that sets nothing builds as before")
        self.assertEqual([build(network) for network in ("bridge", "none", "host", "")], ["bridge", "none", "host", "host"])
        self.assertTrue(all(command[:3] == ["docker", "run", "--rm"] for command in commands))
        count = len(commands)
        with self.assertRaisesRegex(ValueError, "RELEASE_BUILD_NETWORK must be host, bridge or none"):
            build("--privileged")
        self.assertEqual(len(commands), count, "nothing runs with a refused value")

    def planting(self, plant):
        """in_release_image as a hostile build: plant(release, command) runs where the build step would."""
        def build(project, release, command, cache=None):
            self.calls.append(["image", command])
            plant(Path(release), command)
        return patch("project_deploy.in_release_image", build)

    def test_the_build_cannot_make_setup_write_through_a_planted_link(self):
        """Hostile build 1: a Composer script links the files setup writes into the release to a host file."""
        from project_deploy import write_manifest  # noqa: F401 (the module under test)
        victim = self.root / "victim.txt"
        victim.write_text("host file\n")
        self.generic()
        self.commit("c1", {"composer.json": "{}\n"})
        (self.root / "app/config/deploy").mkdir(parents=True)
        (self.root / "app/config/deploy/build.sh").write_text("true\n")
        for planted in (".setup-build.sh", ".setup-release"):
            def plant(release, command, planted=planted):
                if command.startswith("composer") or planted == ".setup-release":
                    (release / planted).unlink(missing_ok=True)
                    (release / planted).symlink_to(victim)
            release = self.root / "app/.generated/release/stage/code" / git_commit(self.shop, "c1")[:12]
            with self.subTest(planted=planted), patch("project_deploy.php_image", return_value="php:test"), \
                    self.planting(plant):
                try:
                    build_code(self.project, "c1")
                except ValueError:
                    pass  # .setup-release planted after setup wrote it is no built release: see below
            self.assertEqual(victim.read_text(), "host file\n", f"{planted} never writes through the link")
            if planted == ".setup-build.sh":
                self.assertFalse((release / planted).exists(), "setup's own copy replaced the link, then went")
            with patch("project_deploy.php_image", return_value="php:test"), self.planting(lambda release, command: None):
                build_code(self.project, "c1")
            self.assertTrue((release / ".setup-release").is_file() and not (release / ".setup-release").is_symlink())
            import shutil
            shutil.rmtree(release)
        # The environment repository's composer files go into the export, which may hold a link of that name.
        (self.root / "app/config/composer").mkdir(parents=True)
        (self.root / "app/config/composer/composer.json").write_text('{"name": "env"}\n')
        (self.shop / "composer.json").unlink()
        (self.shop / "composer.json").symlink_to(victim)
        git(self.shop, "add", "-A")
        git(self.shop, "commit", "-q", "-m", "c2")
        with patch("project_deploy.php_image", return_value="php:test"), self.planting(lambda release, command: None):
            release, _ = build_code(self.project, "HEAD")
        self.assertEqual(victim.read_text(), "host file\n")
        self.assertEqual((release / "composer.json").read_text(), '{"name": "env"}\n', "a plain file replaced the link")
        self.assertFalse((release / "composer.json").is_symlink())

    def test_a_hard_link_swapped_in_while_setup_writes_is_never_truncated(self):
        """Re-check H2: the entry turns into a hard link to a victim between setup's check and its write."""
        from project_deploy import write_release_file
        release = self.root / "release"
        release.mkdir()
        victim = release / "victim.txt"  # a hard link needs the same file system: a file the build can reach
        victim.write_text("keep me\n")
        (release / ".setup-release").write_text("old\n")
        real_open, swapped = os.open, []

        def racing_open(path, flags, *args, **kwargs):
            if kwargs.get("dir_fd") is not None and not swapped:
                swapped.append(True)
                (release / ".setup-release").unlink()
                os.link(victim, release / ".setup-release")
            return real_open(path, flags, *args, **kwargs)

        with patch("os.open", racing_open):
            try:
                write_release_file(release, ".setup-release", b"new\n")
            except ValueError:
                pass
        self.assertTrue(swapped)
        self.assertEqual(victim.read_text(), "keep me\n", "the victim is never opened for writing")
        self.assertEqual((release / ".setup-release").read_text(), "new\n")

    def test_as_before_a_protected_directory_may_be_a_link_inside_the_release(self):
        """Opt-in guarantee (make deploy, nothing new set): PS_RELEASE=on with cache -> var/cache in the release and on
        the target, which the main rsync and the protected copy always handled."""
        release, target = self.root / "release", self.root / "server"
        (release / "var/cache").mkdir(parents=True)
        (release / "var/cache/index.php").write_text("release\n")
        (release / "cache").symlink_to("var/cache")
        (target / "var/cache").mkdir(parents=True)
        (target / "cache").symlink_to("var/cache")
        self.project.settings.update({"PS_RELEASE": "on", "PS_RELEASE_PATHS": ""})
        self.assertIn(("cache", False), project_deploy.protected_release_files(self.project, release))
        project_deploy.push_protected_release_files(self.project, release, LocalTarget(), str(target))
        self.assertEqual((target / "var/cache/index.php").read_text(), "release\n")
        # A link that leaves the release is refused before the main code is copied.
        secret = self.root / "host-secrets"
        secret.mkdir()

        def plant(project, built, command, cache=None):
            (Path(built) / "img").symlink_to(secret)

        with patch("project_deploy.php_image", return_value="php:test"), patch("project_deploy.in_release_image", plant), \
                patch("project_application.plan_application", return_value=(["true"], False)), \
                patch("project_deploy.upgrade_modules", lambda project, release: []), \
                self.assertRaisesRegex(ValueError, "release build left a link at img"):
            deploy_code(self.project, ref="v1")
        self.assertFalse((self.target / "app/public/index.php").exists(), "refused before the code was copied")

    def test_a_protected_directory_the_build_links_elsewhere_is_never_copied(self):
        """Re-check H1: PROFILE=prestashop, PS_RELEASE=on; the build makes release/cache a link to a host directory."""
        secret = self.root / "host-secrets"
        secret.mkdir()
        (secret / "secret.txt").write_text("host secret\n")
        release, target = self.root / "release", self.root / "server"
        (release / "img").mkdir(parents=True)
        (release / "img/index.php").write_text("release\n")
        (release / "cache").symlink_to(secret)
        target.mkdir()
        self.project.settings.update({"PS_RELEASE": "on", "PS_RELEASE_PATHS": ""})
        with self.assertRaisesRegex(ValueError, "release build left a link at cache"):
            project_deploy.push_protected_release_files(self.project, release, LocalTarget(), str(target))
        self.assertFalse((target / "cache/secret.txt").exists(), "nothing of the host directory reaches the target")

    def test_a_file_name_cannot_inject_a_path_into_the_removals(self):
        """Hostile build 2: a file named x<newline>/<host path> in the release; the next deploy drops it."""
        victim = self.root / "victim.txt"
        victim.write_text("host file\n")
        self.generic()
        (self.root / "app/config/deploy").mkdir(parents=True)
        (self.root / "app/config/deploy/build.sh").write_text("true\n")

        def plant(release, command):
            if "setup-build" in command and "g1" in (release / "src/Kernel.php").read_text():
                injected = release / "x\n" / str(victim.parent).lstrip("/") / victim.name
                injected.parent.mkdir(parents=True)
                injected.write_text("built\n")

        with patch("project_deploy.php_image", return_value="php:test"), self.planting(plant):
            deploy_code(self.project, ref="g1")
            self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
            deploy_code(self.project, ref="g2")
        self.assertEqual(victim.read_text(), "host file\n", "no host file is removed")
        manifest = self.root / f"app/.generated/release/stage/manifests/{git_commit(self.shop, 'g1')[:12]}.json"
        self.assertIn("x\n" + str(victim), json.loads(manifest.read_text()))
        self.assertIn("not removing", self.output.getvalue())

    def test_removals_never_leave_the_code_tree(self):
        """A legacy newline manifest, an absolute or .. name, or a directory the target links elsewhere."""
        from project_deploy import safe_names
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "kept.php").write_text("x")
        code = self.target / "app/public"
        (code / "linked").symlink_to(outside)
        (code / "gone.php").write_text("x")
        self.assertEqual(safe_names(["gone.php", "/etc/passwd", "../x", "a/../../b", "ok\x07.php", "", "a//b"]), ["gone.php"])
        LocalTarget().remove(str(code), ["gone.php", "linked/kept.php"])
        self.assertFalse((code / "gone.php").exists())
        self.assertTrue((outside / "kept.php").exists(), "a directory the target links elsewhere is not followed")
        # The same rule on a server: the removal script runs there (here: this machine's sh).
        (code / "gone.php").write_text("x")
        commands = []
        SshTarget("shop@host.example", run=lambda command, **options: commands.append((command, options)) or
                  subprocess.CompletedProcess(command, 0, b"", b"")).remove(
            str(code), ["gone.php", "linked/kept.php"])
        [(command, options)] = commands
        subprocess.run(["sh", "-c", command[-1]], input=options["input"], check=True, capture_output=True)
        self.assertFalse((code / "gone.php").exists())
        self.assertTrue((outside / "kept.php").exists())

    def test_redeploys_of_one_release_do_not_crowd_out_older_rollback_targets(self):
        """Re-check A: g1 ok, g2 ok three times, g3-g5 fail; two rollbacks: to g2, then to g1."""
        self.generic()
        self.deploy(ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
        for _ in range(3):
            self.deploy(ref="g2")
        for name in ("g3", "g4", "g5"):
            self.commit(name, {"src/Kernel.php": f"<?php // {name}\n"})
            with patch("project_deploy.reload_php", side_effect=ValueError("reload failed")), \
                    self.assertRaisesRegex(ValueError, "reload failed"):
                self.deploy(ref=name)
        public = self.target / "app/public"
        self.deploy(rollback=True)
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g2\n")
        self.deploy(rollback=True)
        self.assertEqual((public / "src/Kernel.php").read_text(), "<?php // g1\n")

    def test_a_legacy_newline_manifest_never_decides_a_removal(self):
        """Re-check M1: an older setup's g1 manifest says x<newline>victim.php; victim.php is the server's own file."""
        public = self.generic()
        self.deploy(ref="g1")
        manifests = self.root / "app/.generated/release/stage/manifests"
        g1 = git_commit(self.shop, "g1")[:12]
        names = json.loads((manifests / f"{g1}.json").read_text())
        (manifests / f"{g1}.json").unlink()
        (manifests / f"{g1}.txt").write_text("".join(f"{name}\n" for name in names) + "x\nvictim.php\n")
        (public / "victim.php").write_text("the server's own\n")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"})
        self.deploy(ref="g2")
        self.assertEqual((public / "victim.php").read_text(), "the server's own\n")
        self.assertTrue((manifests / f"{g1}.json").is_file(), "rebuilt from the kept release tree")
        # The kept release is gone too: no list to trust, so nothing is removed (stale files stay; safe).
        g2 = git_commit(self.shop, "g2")[:12]
        (manifests / f"{g2}.json").rename(manifests / f"{g2}.txt")
        import shutil
        shutil.rmtree(self.root / f"app/.generated/release/stage/code/{g2}")
        (public / "victim.php").touch()
        self.deploy(ref="g1")
        self.assertTrue((public / "victim.php").exists())
        self.assertIn("removes nothing", self.output.getvalue())

    def test_a_directory_swapped_for_a_link_during_the_removal_is_not_followed(self):
        """Re-check H3: between setup's look at code/a and its unlink, the application swaps a for a link outside."""
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "file.php").write_text("outside\n")
        code = self.target / "app/public"
        (code / "a").mkdir()
        (code / "a/file.php").write_text("inside\n")
        swapped = []

        def swap():
            if not swapped:
                swapped.append(True)
                (code / "a").rename(code / "a-moved")
                (code / "a").symlink_to(outside)

        real_realpath, real_open = os.path.realpath, os.open

        def racing_realpath(path, *args, **kwargs):
            result = real_realpath(path, *args, **kwargs)
            if str(path).rstrip("/").endswith("/a"):
                swap()
            return result

        def racing_open(path, flags, *args, **kwargs):
            result = real_open(path, flags, *args, **kwargs)
            if path in ("a", b"a"):
                swap()
            return result

        with patch("os.path.realpath", racing_realpath), patch("os.open", racing_open):
            LocalTarget().remove(str(code), ["a/file.php"])
        self.assertTrue(swapped)
        self.assertEqual((outside / "file.php").read_text(), "outside\n")
        self.assertFalse((code / "a-moved/file.php").exists(), "the directory setup opened, wherever it went")

    def test_a_server_removal_reports_every_failure(self):
        """Re-check M2: over ssh, an earlier failed removal is not hidden by a later one that worked."""
        code = self.target / "app/public"
        (code / "locked").mkdir()
        (code / "locked/a.php").write_text("x")
        (code / "b.php").write_text("x")
        commands = []
        SshTarget("shop@host.example", run=lambda command, **options: commands.append((command, options)) or
                  subprocess.CompletedProcess(command, 0, b"", b"")).remove(
            str(code), ["locked/a.php", "b.php"])
        [(command, options)] = commands
        (code / "locked").chmod(0o555)
        self.addCleanup((code / "locked").chmod, 0o755)
        result = subprocess.run(["sh", "-c", command[-1]], input=options["input"], capture_output=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse((code / "b.php").exists(), "the others are still removed")
        # A server without python3: the shell loop counts every failure too.
        import shutil
        bare = self.root / "bare-bin"
        bare.mkdir()
        for tool in ("sh", "dirname", "rm", "xargs"):
            (bare / tool).symlink_to(shutil.which(tool))
        (code / "b.php").write_text("x")
        result = subprocess.run(["/bin/sh", "-c", command[-1]], input=options["input"], capture_output=True,
                                env={"PATH": str(bare)})
        self.assertIn(b"no python3 3.3+ on the server", result.stderr)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertFalse((code / "b.php").exists())

    def test_as_before_a_failed_removal_is_retried_by_the_next_deploy(self):
        """Opt-in guarantee: g2 drops config/local.php; its removal step fails once; deploying g2 again removes it."""
        public = self.generic()
        self.deploy(ref="g1")
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"}, removed=["config/local.php"])
        with patch.object(LocalTarget, "remove", side_effect=OSError("target busy")), self.assertRaisesRegex(OSError, "busy"):
            self.deploy(ref="g2")
        self.assertTrue((public / "config/local.php").exists())
        self.deploy(ref="g2")
        self.assertFalse((public / "config/local.php").exists(), "the pending removal is retried")
        self.commit("g3", {"src/Kernel.php": "<?php // g3\n"})
        self.deploy(ref="g3")
        self.assertEqual(json.loads((self.root / "app/.generated/release/stage/deployments.json").read_text())[-1].get(
            "pending_removals"), None, "done once removed")

    def test_as_before_an_intermediate_link_inside_the_code_tree_is_followed(self):
        """Opt-in guarantee: public -> actual inside the code tree; the old code and the shell loop removed through it."""
        code = self.target / "app/public"
        (code / "actual").mkdir()
        (code / "actual/old.php").write_text("x")
        (code / "public").symlink_to("actual")
        self.assertEqual(LocalTarget().remove(str(code), ["public/old.php"]), [])
        self.assertFalse((code / "actual/old.php").exists())
        (code / "actual/old.php").write_text("x")
        commands = []
        SshTarget("shop@host.example", run=lambda command, **options: commands.append((command, options)) or
                  subprocess.CompletedProcess(command, 0, b"", b"")).remove(str(code), ["public/old.php"])
        [(command, options)] = commands
        subprocess.run(["sh", "-c", command[-1]], input=options["input"], check=True, capture_output=True)
        self.assertFalse((code / "actual/old.php").exists(), "the server's python3 helper follows it too")
        # A link that leaves the code tree is not followed, and its removal is not counted as done.
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "old.php").write_text("outside\n")
        (code / "out").symlink_to(outside)
        self.assertEqual(LocalTarget().remove(str(code), ["out/old.php"]), ["out/old.php"])
        self.assertTrue((outside / "old.php").exists())

    def test_a_removal_left_undone_stays_pending_until_it_is_done(self):
        """A skipped removal is no removal: the history keeps it and the next deploy tries again."""
        public = self.generic()
        self.commit("g1b", {"sub/old.php": "<?php\n"})
        self.deploy(ref="g1b")
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "old.php").write_text("outside\n")
        (public / "sub").rename(public / "sub-real")
        (public / "sub").symlink_to(outside)
        self.commit("g2", {"src/Kernel.php": "<?php // g2\n"}, removed=["sub/old.php"])
        self.deploy(ref="g2")
        history = self.root / "app/.generated/release/stage/deployments.json"
        self.assertEqual(json.loads(history.read_text())[-1]["pending_removals"], ["sub/old.php"])
        self.assertTrue((outside / "old.php").exists())
        (public / "sub").unlink()
        (public / "sub-real").rename(public / "sub")
        self.commit("g3", {"src/Kernel.php": "<?php // g3\n"})
        self.deploy(ref="g3")
        self.assertFalse((public / "sub/old.php").exists())
        self.assertNotIn("pending_removals", json.loads(history.read_text())[-1])

    def test_as_before_a_directory_without_read_permission_still_has_its_files_removed(self):
        """Opt-in guarantee: a 0300 directory (write and search, no read): a plain unlink always worked there."""
        code = self.target / "app/public"
        (code / "wo").mkdir()
        (code / "wo/a.php").write_text("x")
        (code / "wo").chmod(0o300)
        self.addCleanup((code / "wo").chmod, 0o755)
        self.assertEqual(LocalTarget().remove(str(code), ["wo/a.php"]), [])
        (code / "wo").chmod(0o755)
        self.assertFalse((code / "wo/a.php").exists())
        (code / "wo/a.php").write_text("x")
        (code / "wo").chmod(0o300)
        commands = []
        SshTarget("shop@host.example", run=lambda command, **options: commands.append((command, options)) or
                  subprocess.CompletedProcess(command, 0, b"", b"")).remove(str(code), ["wo/a.php"])
        [(command, options)] = commands
        result = subprocess.run(["sh", "-c", command[-1]], input=options["input"], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        (code / "wo").chmod(0o755)
        self.assertFalse((code / "wo/a.php").exists())

    def test_the_removal_helper_runs_on_python_3_5_servers(self):
        """Item 4: the helper's source uses nothing newer than Python 3.5, and the server's version is checked."""
        import io
        import tokenize
        from project_target import REMOVE_HELPER
        tokens = list(tokenize.generate_tokens(io.StringIO(REMOVE_HELPER).readline))
        fstrings = [token for token in tokens if token.type == getattr(tokenize, "FSTRING_START", -1)
                    or (token.type == tokenize.STRING and token.string[:2].lower().lstrip("rb").startswith("f"))]
        self.assertEqual(fstrings, [], "no f-strings (3.6)")
        self.assertNotIn(":=", REMOVE_HELPER)
        commands = []
        SshTarget("shop@host.example", run=lambda command, **options: commands.append((command, options)) or
                  subprocess.CompletedProcess(command, 0, b"", b"")).remove("/srv/shop/public", ["a.php"])
        self.assertIn("sys.version_info", commands[0][0][-1], "the interpreter's version is checked first")

    def test_make_deploy_waits_for_the_environments_lock(self):
        """Review 3: make deploy writes the history only under the lock make deploy-health and pipeline runs hold."""
        import fcntl
        import threading
        self.generic()
        release = self.root / "app/.generated/release/stage"
        release.mkdir(parents=True, exist_ok=True)
        lock = open(release / "deploy.lock", "a")
        fcntl.flock(lock, fcntl.LOCK_EX)
        errors = []

        def deploy():
            try:
                self.deploy(ref="g1")
            except BaseException as error:  # reported below
                errors.append(error)

        thread = threading.Thread(target=deploy)
        thread.start()
        thread.join(1.5)
        try:
            self.assertTrue(thread.is_alive(), "make deploy waits while another holds the lock")
            self.assertFalse((release / "deployments.json").exists())
        finally:
            lock.close()
        thread.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(json.loads((release / "deployments.json").read_text())[-1]["ref"], "g1")

    def test_setup_migrations_run_when_the_project_has_them(self):
        self.generic(SCHEMA_DIRECTORY="schema")
        (self.root / "schema/migrations").mkdir(parents=True)
        with patch("database_migrations_guarded.migrations_directory", return_value=self.root / "schema/migrations"):
            self.deploy(ref="g1")
        self.assertIn(["migrate"], self.calls)
        self.assertIn(["schema-report"], self.calls)

    def test_release_runs_the_project_build_step(self):
        self.generic()
        (self.root / "app/config/deploy").mkdir(parents=True)
        (self.root / "app/config/deploy/build.sh").write_text("npm ci && npm run build\n")
        self.deploy(ref="g1")
        self.assertIn(["image", "sh /var/www/html/.setup-build.sh"], self.calls)

    def test_the_build_step_keeps_its_cache_in_the_environment_repository(self):
        seen = []
        hook = self.root / "build.sh"
        hook.write_text("npm ci\n")
        release = self.root / "release"
        release.mkdir()
        with patch("project_deploy.in_release_image", lambda project, release, command, cache=None: seen.append((command, cache))):
            project_deploy._run_hook(self.project, release, hook)
        self.assertEqual(seen, [("sh /var/www/html/.setup-build.sh", self.root / "app/.generated/build-cache")],
                         "kept between releases, beside the backups, never in the code")
        self.assertFalse((release / ".setup-build.sh").exists(), "the copied hook leaves the release")

    def test_build_step_has_writable_tool_caches_in_the_release_image(self):
        calls = []
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.subprocess.run", lambda command, **kwargs: calls.append(command)):
            project_deploy.in_release_image(self.project, Path("/release"), "npm ci")
            project_deploy.in_release_image(self.project, Path("/release"), "sh build.sh", cache=self.root / "cache")
        command = " ".join(calls[0])
        for variable in ("HOME=/tmp", "NPM_CONFIG_CACHE=/tmp/setup-npm-cache", "COMPOSER_HOME=/tmp/composer"):
            self.assertIn(f"-e {variable}", command, "the user has no home directory in the image")
        self.assertIn(f"--user {os.getuid()}:{os.getgid()}", command,
                      "the release copy is this machine's: APP_UID (1012) owns the target's files, not these")
        self.assertNotIn("setup-build-cache", command)
        cached = " ".join(calls[1])
        self.assertIn(f"-v {self.root / 'cache'}:/setup-build-cache", cached)
        self.assertIn("-e SETUP_BUILD_CACHE=/setup-build-cache", cached)
        self.assertIn("-e NPM_CONFIG_CACHE=/setup-build-cache/npm", cached, "npm's cache stays between releases")
        self.assertTrue((self.root / "cache").is_dir())

    def test_module_pull_is_a_prestashop_command(self):
        self.generic()
        with self.assertRaisesRegex(ValueError, "PROFILE=prestashop"):
            module_pull(self.project, "serveronly")


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
                patch("database_schema.database_tables", return_value=["ps_orders", "ps_connections"]), \
                patch("database_schema.extra_tables", return_value=[]):
            passes = registry_passes(SimpleNamespace(), "shop")
        self.assertEqual(passes, [("shop", ["--ignore-table=shop.ps_connections"]),
                                  ("shop", ["--no-data", "--skip-add-drop-table", "--skip-routines", "--skip-events", "ps_connections"])])
        with patch("database_import.skip_rule", return_value=None):
            self.assertEqual(registry_passes(SimpleNamespace(), "shop"), [("shop", [])])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.environ.get("PHP_TEST_IMAGE"), "Set PHP_TEST_IMAGE to a built project PHP image")
class PhpReloadIntegrationTest(unittest.TestCase):
    """What a deploy sends: USR2 to PID 1 as the container user keeps php-fpm serving with new workers."""

    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60, check=check).stdout.strip()

    def workers(self, name):
        script = ('for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = php-fpm ] || continue; '
                  'n=${p#/proc/}; [ "$n" = 1 ] || echo "$n"; done')
        return set(self.docker("exec", name, "sh", "-c", script).split())

    def listening(self, name):
        for _ in range(50):
            if subprocess.run(["docker", "exec", name, "php", "-r", "exit(@fsockopen('127.0.0.1',9000)?0:1);"],
                              capture_output=True).returncode == 0:
                return
            time.sleep(.2)
        self.fail(self.docker("logs", name))

    def test_usr2_reloads_workers_and_keeps_the_master(self):
        name = "setup-php-reload-" + uuid.uuid4().hex[:8]
        conf = ROOT / "docker/docker/php-fpm/setup-global.conf"
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        self.docker("run", "-d", "--name", name, "--network", "none", "--memory", "128m", "--user", "1000:1000",
                    "-v", f"{conf}:/usr/local/etc/php-fpm.d/zz-setup-global.conf:ro", "-e", "SETUP_FPM_ACCESS_LOG=/proc/self/fd/2",
                    # The CORE image's defaults of zz-setup-global.conf's variables.
                    "-e", "SETUP_FPM_SLOWLOG_TIMEOUT=0",
                    "--entrypoint", "php-fpm", os.environ["PHP_TEST_IMAGE"], "-F", "-R")
        self.listening(name)
        self.assertIn("process_control_timeout = 30s", subprocess.run(
            ["docker", "exec", name, "php-fpm", "-tt"], capture_output=True, text=True).stderr)
        before = self.workers(name)
        self.assertTrue(before)
        self.docker("exec", name, "sh", "-c", "kill -USR2 1")
        for _ in range(50):
            after = self.workers(name)
            if after and not after & before:
                break
            time.sleep(.2)
        self.assertTrue(after and not after & before, "every worker is replaced")
        self.listening(name)
        self.assertEqual(self.docker("inspect", name, "--format", "{{.State.Running}}"), "true")
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        self.assertIn("reloading", logs.stdout + logs.stderr)
