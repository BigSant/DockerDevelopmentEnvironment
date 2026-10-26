"""make files-pull: setup-server's files backup over SSH (a stub ssh plays its backup-pull), and extract=1."""

import contextlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker")]
from project_files import listed, pull_files  # noqa: E402

# The stub ssh answers like setup-server-backup-pull: the request is its last argument. OLD_SERVER=1 plays a server
# from before files backups (it refuses the last word files).
SSH = r'''#!/bin/sh
for request; do :; done
echo "$*" >> "$SSH_LOG"
old() { echo "setup-server-backup-pull: the last word may only be full" >&2; exit 65; }
case "$request" in
    "info shop prod files") [ -n "${OLD_SERVER:-}" ] && old; echo "files-prod-20261012T033000Z.tar.gz $(wc -c < "$ARCHIVE")" ;;
    "info shop stage files") echo "setup-server-backup-pull: no stage files backup of shop yet" >&2; exit 65 ;;
    "fresh shop prod files"|"fresh shop stage files")
        [ -n "${NO_FRESH:-}" ] && { echo "setup-server-backup-pull: this key may not ask for a fresh backup of shop prod" >&2; exit 65; }
        echo "progress backup 5 10" >&2
        set -- $request; echo "files-$3-20261013T150000Z.tar.gz $(wc -c < "$ARCHIVE")" ;;
    "get shop "*) head -c "${CUT:-100000000}" "$ARCHIVE" ;;
    *) echo "setup-server-backup-pull: no project on this server" >&2; exit 65 ;;
esac
'''


class FilesPullTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="files-pull-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bin_directory = self.root / "bin"
        bin_directory.mkdir()
        (bin_directory / "ssh").write_text(SSH)
        (bin_directory / "ssh").chmod(0o755)
        self.archive = self.root / "server.tar.gz"
        self.write_archive({"modules/shop/translations/lt.php": b"<?php // server lt\n",
                            "modules/shop/translations/default.php": "lt.php",
                            "modules/shop/shop.php": b"<?php // not listed here\n",
                            "../escape.php": b"x"})
        self.log = self.root / "ssh.log"
        environment = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "SSH_LOG": str(self.log), "ARCHIVE": str(self.archive),
                       "OLD_SERVER": "", "NO_FRESH": "", "CUT": ""}
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage = patch("project_storage.require_space")
        storage.start()
        self.addCleanup(storage.stop)
        shared = patch("database_pull.SHARED", self.root / "setup.env.backup")
        shared.start()
        self.addCleanup(shared.stop)
        (self.root / "app/env").mkdir(parents=True)
        self.code = self.root / "app/public"
        (self.code / "modules/shop/translations").mkdir(parents=True)
        (self.code / "modules/shop/translations/lt.php").write_text("<?php // local lt\n")
        (self.code / "modules/shop/translations/en.php").write_text("<?php // local only\n")
        (self.code / "modules/shop/shop.php").write_text("<?php // code\n")
        self.project = SimpleNamespace(environment="local", directory=self.root / "app", web_directory=self.code,
                                       settings={"BACKUP_SSH": "setup@10.8.0.14", "PROJECT_NAME": "shop",
                                                 "BACKUP_FILE_PATHS": "modules/*/translations/"})

    def write_archive(self, members):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.mtime = 1_760_000_000
                if isinstance(content, str):
                    info.type, info.linkname = tarfile.SYMTYPE, content
                    archive.addfile(info)
                else:
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))

    def pull(self, source="prod", **options):
        options.setdefault("interactive", False)
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            target = pull_files(self.project, source, **options)
        self.errors = errors.getvalue()
        return target, output.getvalue()

    def calls(self):
        return [line.rsplit(" setup@10.8.0.14 ", 1)[1] for line in self.log.read_text().splitlines()]

    def test_the_newest_files_backup_lands_private_and_the_code_tree_is_untouched(self):
        target, output = self.pull()
        self.assertEqual(target, self.root / "app/.generated/backups/files-prod-20261012T033000Z.tar.gz")
        self.assertEqual(target.read_bytes(), self.archive.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.calls(), ["info shop prod files", "get shop prod files-prod-20261012T033000Z.tar.gz"])
        self.assertEqual((self.code / "modules/shop/translations/lt.php").read_text(), "<?php // local lt\n")
        _, again = self.pull()
        self.assertIn("Already downloaded", again)
        self.assertEqual(len(self.calls()), 3, "no second download")

    def test_extract_writes_only_listed_regular_files_and_touches_nothing_else(self):
        _, output = self.pull(extract=True)
        translations = self.code / "modules/shop/translations"
        self.assertEqual((translations / "lt.php").read_text(), "<?php // server lt\n")
        self.assertEqual((translations / "lt.php").stat().st_mtime, 1_760_000_000, "the server's time")
        self.assertEqual((translations / "en.php").read_text(), "<?php // local only\n", "nothing is deleted")
        self.assertEqual((self.code / "modules/shop/shop.php").read_text(), "<?php // code\n", "not in BACKUP_FILE_PATHS")
        self.assertFalse((translations / "default.php").exists(), "links are skipped")
        self.assertFalse((self.root / "app/escape.php").exists())
        self.assertIn("Extracted 1 files", output)
        self.assertIn("3 skipped", output)
        self.project.settings["BACKUP_FILE_PATHS"] = ""
        with self.assertRaisesRegex(ValueError, "BACKUP_FILE_PATHS is empty in this checkout"):
            self.pull(extract=True)

    def test_fresh_asks_the_server_for_a_new_files_backup(self):
        target, _ = self.pull(fresh=True)
        self.assertEqual(target.name, "files-prod-20261013T150000Z.tar.gz")
        self.assertEqual(self.calls(), ["info shop prod files", "fresh shop prod files",
                                        "get shop prod files-prod-20261013T150000Z.tar.gz"])
        self.assertIn("Backup on the server", self.errors)
        # No files backup of stage yet: a fresh one is still asked for.
        self.pull("stage", fresh=True)
        self.assertIn("fresh shop stage files", self.calls())
        os.environ["NO_FRESH"] = "1"
        with self.assertRaisesRegex(ValueError, "may not ask for a fresh backup of shop prod"):
            self.pull(fresh=True)

    def test_refusals_say_why(self):
        with self.assertRaisesRegex(ValueError, "no stage files backup of shop yet"):
            self.pull("stage")
        os.environ["OLD_SERVER"] = "1"
        with self.assertRaisesRegex(ValueError, "no files backups yet .update setup-server"):
            self.pull()
        os.environ["OLD_SERVER"] = ""
        os.environ["CUT"] = "20"
        with self.assertRaisesRegex(ValueError, "has 20 of"):
            self.pull()
        self.assertEqual(list((self.root / "app/.generated/backups").iterdir()), [], "nothing kept")
        self.project.environment = "prod"
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            self.pull()
        self.project.environment = "local"
        with self.assertRaisesRegex(ValueError, "from= must be"):
            self.pull("mdv")

    def test_listed_follows_the_patterns_as_rsync_does(self):
        patterns = ["/modules/*/translations/", "/config/local.php"]
        self.assertTrue(listed("modules/a/translations/lt.php", patterns))
        self.assertTrue(listed("modules/a/translations/sub/x.php", patterns))
        self.assertTrue(listed("config/local.php", patterns))
        for name in ("modules/a/b/translations/lt.php", "modules/a/translations", "config/local.php/x", "config/other.php"):
            with self.subTest(name=name):
                self.assertFalse(listed(name, patterns))


if __name__ == "__main__":
    unittest.main()
