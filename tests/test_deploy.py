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
from project_target import SshTarget


def git(directory, *args):
    subprocess.run(["git", "-C", str(directory), *args], check=True, capture_output=True)


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
            data_directory=self.root / "data", capture=lambda args, profiles=None: json.dumps(compose_model(self.root)))
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
                patch("project_deploy.in_release_image", lambda project, release, command: self.calls.append(["image", command])), \
                patch("project_application.plan_application", return_value=plan), \
                patch("database_backup.backup_database", lambda project, registry=False: self.calls.append(["backup", registry]) or "b.sql.gz"), \
                patch("project_deploy.upgrade_modules", lambda project, release: self.calls.append(["upgrade"])):
            return deploy_code(self.project, **options)

    def test_rsync_spares_writable_paths_and_target_only_modules(self):
        command = code_rsync_options(protected_paths(self.project) + ["/modules/serveronly/"])
        self.assertNotIn("--delete", command, "the target keeps files the code does not track")
        self.assertIn("--checksum", command)
        for pattern in ("/var/", "/img/", "/app/config/parameters.php", "/modules/*/cache/", "/modules/serveronly/", "/.git/"):
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
        removal = [(command, kwargs) for command, kwargs in calls if command[-1].endswith("xargs -0 rm -f --")]
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

    def test_deploy_updates_code_keeps_server_files_and_rolls_back(self):
        self.deploy(ref="v1", backup=True)
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
                patch("project_deploy.in_release_image", lambda project, release, command: self.calls.append(["image", command])), \
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

    def test_build_step_has_writable_tool_caches_in_the_release_image(self):
        calls = []
        with patch("project_deploy.php_image", return_value="php:test"), \
                patch("project_deploy.subprocess.run", lambda command, **kwargs: calls.append(command)):
            project_deploy.in_release_image(self.project, Path("/release"), "npm ci")
        command = " ".join(calls[0])
        for variable in ("HOME=/tmp", "NPM_CONFIG_CACHE=/tmp/setup-npm-cache", "COMPOSER_HOME=/tmp/composer"):
            self.assertIn(f"-e {variable}", command, "the hosting account has no home directory in the image")
        self.assertIn("--user 1012:1013", command)

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
                    "-v", f"{conf}:/usr/local/etc/php-fpm.d/zz-setup-global.conf:ro",
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
