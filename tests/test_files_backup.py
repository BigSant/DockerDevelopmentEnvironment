"""make files-backup: BACKUP_FILE_PATHS copied from the Deploy target into a tar.gz through a mirror kept beside it."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from project_deploy import code_tree_patterns
from project_files import backup_files, include_filters


class FilesBackupTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="files-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = self.root / "target"
        public = self.target / "app/public"
        self.public = public
        for name, text in {"modules/shop/translations/lt.php": "<?php // lt\n", "modules/shop/translations/en.php": "<?php // en\n",
                           "modules/shop/shop.php": "<?php // code\n", "modules/blog/blog.php": "<?php\n",
                           "themes/classic/lang/lt.php": "<?php\n", "config/local.php": "<?php // local\n",
                           "config/other.php": "<?php\n", "img/p/1.jpg": "image"}.items():
            (public / name).parent.mkdir(parents=True, exist_ok=True)
            (public / name).write_text(text)
        os.symlink("lt.php", public / "modules/shop/translations/default.php")
        self.backups = self.root / "app/.generated/backups"
        self.project = SimpleNamespace(
            environment="prod", root=self.root, directory=self.root / "app", web_directory=self.root / "app/public",
            settings={"DEPLOY_PATH": str(self.target), "DEPLOY_SSH": "", "APP_UID": "1012", "APP_GID": "1013",
                      "MIN_FREE_DISK_MB": "0",
                      "BACKUP_FILE_PATHS": "modules/*/translations/ themes/*/lang/ config/local.php var/missing/"})

    def backup(self, name):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            path = backup_files(self.project, self.backups / name)
        return path, output.getvalue()

    def members(self, path):
        with tarfile.open(path) as archive:
            return {member.name: member for member in archive.getmembers()}

    def test_patterns_share_the_protected_paths_validation(self):
        self.assertEqual(code_tree_patterns(self.project, "BACKUP_FILE_PATHS"),
                         ["/modules/*/translations/", "/themes/*/lang/", "/config/local.php", "/var/missing/"])
        for entry in ("../data/", "var/../../x", "/", "*", "*/lang/", "var data/'", "~/x"):
            self.project.settings["BACKUP_FILE_PATHS"] = entry
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "BACKUP_FILE_PATHS entry"):
                code_tree_patterns(self.project, "BACKUP_FILE_PATHS")

    def test_a_prestashop_project_always_backs_up_its_translations(self):
        from project_deploy import backup_file_patterns
        self.assertEqual(backup_file_patterns(self.project),
                         ["/modules/*/translations/", "/themes/*/lang/", "/config/local.php", "/var/missing/"],
                         "a generic project: its own paths only")
        self.project.settings["PROFILE"] = "prestashop"
        self.assertEqual(backup_file_patterns(self.project),
                         ["/modules/*/translations/", "/themes/*/modules/*/translations/", "/translations/*-*/",
                          "/themes/*/lang/", "/config/local.php", "/var/missing/"], "the profile's first, each path once")
        self.project.settings["BACKUP_FILE_PATHS"] = ""
        (self.target / "app/public/translations/lt-LT").mkdir(parents=True)
        (self.target / "app/public/translations/lt-LT/Admin.lt-LT.xlf").write_text("<xliff/>\n")
        (self.target / "app/public/translations/default").mkdir()
        (self.target / "app/public/translations/default/Admin.xlf").write_text("<xliff/>\n")
        path, _ = self.backup("prestashop.tar.gz")
        names = self.members(path)
        self.assertIn("translations/lt-LT/Admin.lt-LT.xlf", names, "an installed language pack, without BACKUP_FILE_PATHS")
        self.assertIn("modules/shop/translations/lt.php", names)
        self.assertNotIn("translations/default/Admin.xlf", names, "the release's own catalogue is no content")

    def test_include_chain_descends_only_where_a_pattern_leads(self):
        self.assertEqual(include_filters(["/modules/*/translations/", "/modules/*/mails/", "/config/local.php"]),
                         ["--include=/modules/", "--include=/modules/*/", "--include=/modules/*/translations/***",
                          "--include=/modules/*/mails/***", "--include=/config/", "--include=/config/local.php",
                          "--exclude=*"])

    def test_archive_holds_only_the_listed_paths_named_from_the_code_root(self):
        path, output = self.backup("files-prod-20261026T010000Z.tar.gz")
        members = self.members(path)
        self.assertEqual(sorted(name for name, member in members.items() if not member.isdir()),
                         ["config/local.php", "modules/shop/translations/default.php", "modules/shop/translations/en.php",
                          "modules/shop/translations/lt.php", "themes/classic/lang/lt.php"])
        self.assertIn("modules/shop/translations", members, "directories are members too")
        self.assertNotIn("modules/blog", members, "a module without translations leaves no empty directory")
        self.assertTrue(members["modules/shop/translations/default.php"].issym(), "symlinks stay links")
        self.assertFalse(any(name.startswith("./") for name in members))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.backups / ".files-prod").stat().st_mode & 0o777, 0o700)
        self.assertEqual(list(self.backups.glob("tmp*")), [], "the temporary file is renamed")
        self.assertIn("warning: BACKUP_FILE_PATHS entry var/missing/ matched nothing on the target", output)
        self.assertNotIn("config/local.php matched", output)
        self.assertIn(f"Backed up 5 files", output)
        self.assertIn(str(path), output)

    def test_a_file_removed_on_the_target_leaves_the_next_backup(self):
        first, _ = self.backup("files-prod-20261026T010000Z.tar.gz")
        (self.public / "modules/shop/translations/en.php").unlink()
        (self.public / "modules/blog/translations").mkdir()
        (self.public / "modules/blog/translations/lt.php").write_text("<?php // new\n")
        second, _ = self.backup("files-prod-20261027T010000Z.tar.gz")
        self.assertIn("modules/shop/translations/en.php", self.members(first), "an earlier archive keeps it")
        names = self.members(second)
        self.assertNotIn("modules/shop/translations/en.php", names)
        self.assertIn("modules/blog/translations/lt.php", names)
        self.assertFalse((self.backups / ".files-prod/modules/shop/translations/en.php").exists(), "the mirror follows")

    def test_local_test_and_empty_setting_are_refused(self):
        for environment in ("local", "test"):
            self.project.environment = environment
            with self.subTest(environment=environment), self.assertRaisesRegex(ValueError, "server environment"):
                backup_files(self.project)
        self.project.environment = "prod"
        self.project.settings["BACKUP_FILE_PATHS"] = " "
        with self.assertRaisesRegex(ValueError, "BACKUP_FILE_PATHS is empty: nothing to back up"):
            backup_files(self.project)
        self.assertFalse(self.backups.exists(), "nothing is written")

    def test_failed_rsync_publishes_nothing(self):
        self.project.settings["DEPLOY_PATH"] = str(self.root / "nowhere")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaisesRegex(ValueError, "no backup written"):
            backup_files(self.project, self.backups / "files-prod-20261026T010000Z.tar.gz")
        self.assertEqual([path.name for path in self.backups.iterdir()], [".files-prod"])

    def test_destination_must_be_a_new_tar_gz(self):
        with self.assertRaisesRegex(ValueError, r"\.tar\.gz"):
            backup_files(self.project, self.backups / "files.zip")
        self.backups.mkdir(parents=True)
        (self.backups / "taken.tar.gz").write_text("")
        with self.assertRaisesRegex(ValueError, "already exists"):
            backup_files(self.project, self.backups / "taken.tar.gz")


if __name__ == "__main__":
    unittest.main()
