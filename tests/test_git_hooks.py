"""The git hooks setup writes: schema check and project scripts, run through real Git and sh."""

import contextlib
import io
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

from git_hooks import MARKER, install_git_hooks  # noqa: E402
import gitignore_check  # noqa: E402


class GitHooksTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="hooks space ", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.project = SimpleNamespace(root=self.root.parent, directory=self.root, environment="local",
                                       settings={"SCHEMA_DIRECTORY": "database/schema"})
        self.hooks = self.root / ".git/hooks"
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def script(self, name, body):
        path = self.root / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def run_hook(self, name, *args, stdin=""):
        return subprocess.run([str(self.hooks / name), *args], input=stdin, capture_output=True, text=True)

    def test_pre_commit_runs_the_schema_check_and_pre_push_only_with_a_project_script(self):
        self.assertEqual(install_git_hooks(self.project), ["pre-commit"])
        hook = self.hooks / "pre-commit"
        self.assertTrue(os.access(hook, os.X_OK))
        self.assertIn("schema-commit", hook.read_text())
        self.assertIn(MARKER, hook.read_text())
        subprocess.run(["sh", "-n", str(hook)], check=True)
        self.assertFalse((self.hooks / "pre-push").exists())
        self.script("pre-push", "exit 0\n")
        self.assertEqual(install_git_hooks(self.project), ["pre-commit", "pre-push"])

    def test_nothing_to_run_writes_nothing(self):
        self.project.settings = {"SCHEMA_DIRECTORY": ""}  # DATABASE_AS_CODE=off
        self.assertEqual(install_git_hooks(self.project), [])
        self.assertFalse((self.hooks / "pre-commit").exists())

    def test_the_project_script_gets_the_arguments_stdin_and_decides_the_status(self):
        self.project.settings = {}
        seen = self.root / "seen"
        self.script("pre-push", f'echo "$@" > "{seen}"; cat >> "{seen}"; exit 3\n')
        install_git_hooks(self.project)
        result = self.run_hook("pre-push", "origin", "git@example:repo.git", stdin="refs/heads/master a refs/heads/master b\n")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(seen.read_text(), "origin git@example:repo.git\nrefs/heads/master a refs/heads/master b\n")
        # Removed later: the hook passes, no reinstall needed.
        (self.root / "bin/pre-push").unlink()
        self.assertEqual(self.run_hook("pre-push").returncode, 0)

    def test_a_failed_schema_check_stops_before_the_project_script(self):
        ran = self.root / "ran"
        self.script("pre-commit", f'touch "{ran}"\n')
        with patch("git_hooks.sys.executable", "false"):
            install_git_hooks(self.project)
        self.assertNotEqual(self.run_hook("pre-commit").returncode, 0)
        self.assertFalse(ran.exists())
        with patch("git_hooks.sys.executable", "true"):
            install_git_hooks(self.project)
        self.assertEqual(self.run_hook("pre-commit").returncode, 0)
        self.assertTrue(ran.exists())

    def test_an_earlier_setup_hook_is_replaced_and_anyone_elses_is_preserved(self):
        self.hooks.mkdir(parents=True, exist_ok=True)
        (self.hooks / "pre-commit").write_text("#!/bin/sh\n# Shared setup schema pre-commit hook v2\nexec old\n")
        (self.hooks / "pre-push").write_text("#!/bin/sh\nexit 7\n")
        self.script("pre-push", "exit 0\n")
        with self.assertRaisesRegex(ValueError, "Existing hook preserved: .*pre-push"):
            install_git_hooks(self.project)
        self.assertIn(MARKER, (self.hooks / "pre-commit").read_text())
        self.assertEqual((self.hooks / "pre-push").read_text(), "#!/bin/sh\nexit 7\n")
        (self.hooks / "pre-push").unlink()
        (self.hooks / "pre-push").symlink_to(self.root / "bin/pre-push")
        with self.assertRaisesRegex(ValueError, "Existing hook preserved"):
            install_git_hooks(self.project)
        self.assertTrue((self.hooks / "pre-push").is_symlink())

    def test_a_hook_manager_is_left_alone(self):
        subprocess.run(["git", "-C", str(self.root), "config", "core.hooksPath", ".githooks"], check=True)
        with self.assertRaisesRegex(ValueError, "core.hooksPath"):
            install_git_hooks(self.project)
        subprocess.run(["git", "-C", str(self.root), "config", "core.hooksPath", str(self.hooks)], check=True)
        self.assertEqual(install_git_hooks(self.project), ["pre-commit"])


def git(directory, *args, check=True):
    return subprocess.run(["git", "-C", str(directory), "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
                          capture_output=True, text=True, check=check)


class ShopGitignoreTest(unittest.TestCase):
    """A PrestaShop shop keeps one .gitignore, at its root: the pre-commit check and its hooks, through real Git."""

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="shop gitignore ", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def shop(self, directory, files):
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (directory / name).parent.mkdir(parents=True, exist_ok=True)
            (directory / name).write_text(text)
        return directory

    def check(self, directory, prefix=""):
        cwd = os.getcwd()
        os.chdir(directory)
        try:
            return gitignore_check.nested_gitignores(prefix)
        finally:
            os.chdir(cwd)

    def test_only_the_root_and_vendor_packages_may_have_a_gitignore(self):
        shop = self.shop(self.root / "shop", {".gitignore": "/var/\n", "index.php": "<?php\n",
                                              "modules/a/.gitignore": "tests\n", "modules/a/a.php": "<?php\n",
                                              "modules/a/vendor/lib/x/.gitignore": "composer.lock\n",
                                              "var/cache/.gitignore": "*\n"})
        git(shop, "init", "-q")
        git(shop, "add", "-A")
        self.assertEqual(self.check(shop), ["modules/a/.gitignore"],
                         "a vendor/ package keeps its own; one in an ignored folder is never committed")
        git(shop, "commit", "-qm", "v1", "--no-verify")
        (shop / "modules/b").mkdir()
        (shop / "modules/b/.gitignore").write_text("*.png\n")
        self.assertEqual(self.check(shop), ["modules/a/.gitignore", "modules/b/.gitignore"], "a new one counts, not added yet")
        git(shop, "rm", "-q", "modules/a/.gitignore")
        (shop / "modules/b/.gitignore").unlink()
        self.assertEqual(self.check(shop), [], "removed (git rm) or never added: nothing to merge")
        text = gitignore_check.message(["modules/a/.gitignore"])
        for words in ("Merge their rules into .gitignore", "tidy them", "git rm", "Nothing was committed"):
            self.assertIn(words, text)

    def test_a_shop_folder_of_the_environment_repository_is_checked_below_its_prefix(self):
        repo = self.shop(self.root / "app", {".gitignore": "/.generated/\n", "config/.gitignore": "*.local\n",
                                             "public/.gitignore": "/var/\n", "public/modules/a/.gitignore": "x\n"})
        git(repo, "init", "-q")
        self.assertEqual(self.check(repo, "public/"), ["public/modules/a/.gitignore"],
                         "the environment repository's own .gitignore files are not the shop's")

    def install(self, environment, web, profile="prestashop"):
        project = SimpleNamespace(root=environment.parent, directory=environment, environment="local", web_directory=web,
                                  settings={"SCHEMA_DIRECTORY": "", "PROFILE": profile})
        return install_git_hooks(project)

    def test_a_shop_repository_of_its_own_gets_a_pre_commit_that_stops_a_second_gitignore(self):
        environment = self.shop(self.root / "app", {".gitignore": "/public/\n"})
        shop = self.shop(environment / "public", {".gitignore": "/var/\n", "modules/a/.gitignore": "tests\n",
                                                  "modules/a/a.php": "<?php\n"})
        for directory in (environment, shop):
            git(directory, "init", "-q")
        self.assertEqual(self.install(environment, shop, profile=""), [], "a generic application may keep several")
        self.assertFalse((shop / ".git/hooks/pre-commit").exists())
        self.assertEqual(self.install(environment, shop), ["shop pre-commit"])
        self.assertFalse((environment / ".git/hooks/pre-commit").exists(), "the environment repository has nothing to run")
        hook = (shop / ".git/hooks/pre-commit").read_text()
        self.assertIn("gitignore_check.py", hook)
        self.assertNotIn("bin/pre-commit", hook, "the environment repository's scripts are not the shop's")
        git(shop, "add", "-A")
        stopped = git(shop, "commit", "-qm", "v1", check=False)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("modules/a/.gitignore", stopped.stderr)
        self.assertIn("Merge their rules into .gitignore", stopped.stderr)
        self.assertEqual(git(shop, "rev-list", "--all").stdout, "", "nothing was committed")
        (shop / ".gitignore").write_text("/var/\n/modules/a/**/tests\n")
        git(shop, "rm", "-q", "--cached", "modules/a/.gitignore")
        (shop / "modules/a/.gitignore").unlink()
        git(shop, "add", "-A")
        self.assertEqual(git(shop, "commit", "-qm", "v1", check=False).returncode, 0)

    def test_a_shop_inside_the_environment_repository_is_checked_by_its_pre_commit(self):
        environment = self.shop(self.root / "app", {".gitignore": "/.generated/\n", "public/.gitignore": "/var/\n",
                                                    "public/modules/a/.gitignore": "tests\n"})
        git(environment, "init", "-q")
        self.assertEqual(self.install(environment, environment / "public"), ["pre-commit"])
        self.assertIn("--prefix public/", (environment / ".git/hooks/pre-commit").read_text())
        git(environment, "add", "-A")
        stopped = git(environment, "commit", "-qm", "v1", check=False)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("public/modules/a/.gitignore", stopped.stderr)


if __name__ == "__main__":
    unittest.main()
