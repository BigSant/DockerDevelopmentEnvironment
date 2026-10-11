"""make db-pull: the newest server backup over SSH (a stub ssh plays setup-server's backup-pull)."""

import contextlib
import gzip
import io
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker")]
from database_pull import pull

# The stub ssh answers like setup-server-backup-pull: the request is its last argument.
SSH = r'''#!/bin/sh
for request; do :; done
echo "$*" >> "$SSH_LOG"
case "$request" in
    "latest shop prod") echo "$LATEST" ;;
    "latest shop stage") echo "setup-server-backup-pull: no stage backup of shop yet" >&2; exit 65 ;;
    "get shop prod "*) cat "$BACKUP_FILE" ;;
    *) echo "setup-server-backup-pull: expected: latest PROJECT ENV | get PROJECT ENV FILE" >&2; exit 65 ;;
esac
'''


class DbPullTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        bin_directory = self.root / "bin"
        bin_directory.mkdir()
        (bin_directory / "ssh").write_text(SSH)
        (bin_directory / "ssh").chmod(0o755)
        self.backup = self.root / "server.sql.gz"
        self.backup.write_bytes(gzip.compress(b"CREATE TABLE `ps_x` (id int);\n"))
        self.log = self.root / "ssh.log"
        environment = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "SSH_LOG": str(self.log),
                       "BACKUP_FILE": str(self.backup), "LATEST": "worker-prod-20261012T033000Z.sql.gz"}
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage = patch("project_storage.require_space")
        storage.start()
        self.addCleanup(storage.stop)
        self.project = SimpleNamespace(environment="local", directory=self.root / "app",
                                       settings={"BACKUP_SSH": "setup@10.8.0.14", "PROJECT_NAME": "shop"})

    def pull(self, source="prod"):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            target = pull(self.project, source)
        return target, output.getvalue()

    def test_the_newest_backup_lands_private_in_generated_backups_once(self):
        target, output = self.pull()
        self.assertEqual(target, self.root / "app/.generated/backups/worker-prod-20261012T033000Z.sql.gz")
        self.assertEqual(target.read_bytes(), self.backup.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn("real customer data", output)
        calls = self.log.read_text().splitlines()
        self.assertTrue(calls[0].endswith("setup@10.8.0.14 latest shop prod"))
        self.assertIn("BatchMode=yes", calls[0])
        self.assertTrue(calls[1].endswith("get shop prod worker-prod-20261012T033000Z.sql.gz"))
        _, again = self.pull()
        self.assertIn("Already downloaded", again)
        self.assertEqual(len(self.log.read_text().splitlines()), 3, "no second download")

    def test_a_cut_download_is_not_kept(self):
        self.backup.write_bytes(gzip.compress(os.urandom(100000))[:5000])
        with self.assertRaisesRegex(ValueError, "not a complete .sql.gz"):
            self.pull()
        self.assertEqual(list((self.root / "app/.generated/backups").iterdir()), [])

    def test_the_server_project_name_and_key_come_from_the_settings(self):
        self.project.settings.update(BACKUP_SERVER_PROJECT="other", BACKUP_SSH_KEY="~/.ssh/backup_ed25519")
        with self.assertRaisesRegex(ValueError, "refused: setup-server-backup-pull: expected"):
            self.pull()
        call = self.log.read_text().splitlines()[0]
        self.assertIn(f"-i {Path('~/.ssh/backup_ed25519').expanduser()} -o IdentitiesOnly=yes", call)
        self.assertTrue(call.endswith("latest other prod"))

    def test_refusals_and_settings_are_clear(self):
        with self.assertRaisesRegex(ValueError, "no stage backup of shop yet"):
            self.pull("stage")
        with self.assertRaisesRegex(ValueError, "stage or prod"):
            self.pull("local")
        self.project.settings["BACKUP_SSH"] = ""
        with self.assertRaisesRegex(ValueError, "Set BACKUP_SSH"):
            self.pull()
        self.project.settings["BACKUP_SSH"] = "setup@10.8.0.14; rm -rf ~"
        with self.assertRaisesRegex(ValueError, "user@host"):
            self.pull()
        self.project.settings["BACKUP_SSH"] = "setup@10.8.0.14"
        self.project.environment = "prod"
        with self.assertRaisesRegex(ValueError, "ENV=local"):
            self.pull()

    def test_no_route_or_no_key_says_what_to_check(self):
        (self.root / "bin/ssh").write_text("#!/bin/sh\necho 'setup@10.8.0.14: Permission denied (publickey).' >&2\nexit 255\n")
        with self.assertRaisesRegex(ValueError, "Check the VPN, BACKUP_SSH, and that your public key was added"):
            self.pull()


if __name__ == "__main__":
    unittest.main()
