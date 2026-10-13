"""make db-pull: a server backup over SSH (a stub ssh plays setup-server's backup-pull)."""

import contextlib
import gzip
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
sys.path[:0] = [str(ROOT / "docker")]
import database_import  # noqa: E402
from database_pull import connection, pull, read_shared  # noqa: E402

# The stub ssh answers like setup-server-backup-pull: the request is its last argument. OLD_SERVER=1 plays a
# server from before info/fresh (it answers them with its usage line).
SSH = r'''#!/bin/sh
for request; do :; done
echo "$*" >> "$SSH_LOG"
usage() { echo "setup-server-backup-pull: expected: latest PROJECT ENV | get PROJECT ENV FILE" >&2; exit 65; }
case "$request" in
    "info shop prod") [ -n "${OLD_SERVER:-}" ] && usage; echo "$LATEST $(wc -c < "$BACKUP_FILE")" ;;
    "info shop prod full") [ -n "${OLD_SERVER:-}" ] && usage; echo "full-prod-20261011T043000Z.sql.gz $(wc -c < "$BACKUP_FILE")" ;;
    "latest shop prod") echo "$LATEST" ;;
    "info shop stage"|"info shop stage full") echo "setup-server-backup-pull: no stage backup of shop yet" >&2; exit 65 ;;
    "fresh shop prod"|"fresh shop prod full")
        [ -n "${OLD_SERVER:-}" ] && usage
        [ -n "${NO_FRESH:-}" ] && { echo "setup-server-backup-pull: this key may not ask for a fresh backup (bin/add-backup-key --allow-fresh)" >&2; exit 65; }
        echo "==> shop prod: backing up into /srv/x" >&2
        echo "progress backup 10 40" >&2
        echo "progress backup 40 40" >&2
        case "$request" in *full) echo "fresh-full-prod-20261013T150000Z.sql.gz $(wc -c < "$BACKUP_FILE")" ;;
                           *) echo "fresh-prod-20261013T150000Z.sql.gz $(wc -c < "$BACKUP_FILE")" ;; esac ;;
    "get shop prod "*) cat "$BACKUP_FILE" ;;
    "info "*|"latest "*|"fresh "*) set -- $request; echo "setup-server-backup-pull: no project $2 on this server" >&2; exit 65 ;;
    *) usage ;;
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
        self.backup.write_bytes(gzip.compress(b"CREATE TABLE `ps_x` (\n  `id` int\n);\n"))
        self.log = self.root / "ssh.log"
        environment = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "SSH_LOG": str(self.log),
                       "BACKUP_FILE": str(self.backup), "LATEST": "worker-prod-20261012T033000Z.sql.gz"}
        for key in ("OLD_SERVER", "NO_FRESH"):
            environment[key] = ""
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        storage = patch("project_storage.require_space")
        storage.start()
        self.addCleanup(storage.stop)
        self.shared = self.root / "setup.env.backup"
        shared = patch("database_pull.SHARED", self.shared)
        shared.start()
        self.addCleanup(shared.stop)
        (self.root / "app/env").mkdir(parents=True)
        self.project = SimpleNamespace(environment="local", directory=self.root / "app",
                                       settings={"BACKUP_SSH": "setup@10.8.0.14", "PROJECT_NAME": "shop",
                                                 "DATABASE_NAME": "shop"})

    def pull(self, source="prod", **options):
        options.setdefault("interactive", False)
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
            target = pull(self.project, source, **options)
        self.errors = errors.getvalue()
        return target, output.getvalue()

    def calls(self):
        return [line.rsplit(" setup@10.8.0.14 ", 1)[1] for line in self.log.read_text().splitlines()]

    def test_the_newest_backup_lands_private_checked_and_read_once(self):
        target, output = self.pull()
        self.assertEqual(target, self.root / "app/.generated/backups/worker-prod-20261012T033000Z.sql.gz")
        self.assertEqual(target.read_bytes(), self.backup.read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertIn("real customer data", output)
        self.assertEqual(self.calls(), ["info shop prod", "get shop prod worker-prod-20261012T033000Z.sql.gz"])
        self.assertIn("BatchMode=yes", self.log.read_text().splitlines()[0])
        self.assertIn("Download:", self.errors, "a log gets one line per step")
        # Read on the way: the import does not read the file again.
        scan = database_import.SCANS[database_import.file_key(target, None)]
        self.assertEqual((scan["size"], scan["tables"]), (len(gzip.decompress(self.backup.read_bytes())), {"ps_x": {"id"}}))
        _, again = self.pull()
        self.assertIn("Already downloaded", again)
        self.assertEqual(len(self.calls()), 3, "no second download")

    def test_full_downloads_the_newest_full_backup(self):
        target, _ = self.pull(full=True)
        self.assertEqual(target.name, "full-prod-20261011T043000Z.sql.gz")
        self.assertEqual(self.calls()[0], "info shop prod full")

    def test_fresh_asks_the_server_for_a_new_backup_and_follows_its_progress(self):
        target, _ = self.pull(fresh=True)
        self.assertEqual(target.name, "fresh-prod-20261013T150000Z.sql.gz")
        self.assertEqual(self.calls(), ["info shop prod", "fresh shop prod",
                                        "get shop prod fresh-prod-20261013T150000Z.sql.gz"])
        self.assertIn("==> [1/2] A new prod backup of shop on the server", self.errors)
        self.assertIn("==> shop prod: backing up into /srv/x", self.errors)
        self.assertIn("Backup on the server: 40 B", self.errors)
        self.assertIn("==> [2/2] Download fresh-prod-20261013T150000Z.sql.gz", self.errors)
        full, _ = self.pull(fresh=True, full=True)
        self.assertEqual(full.name, "fresh-full-prod-20261013T150000Z.sql.gz")
        self.assertIn("fresh shop prod full", self.calls())

    def test_fresh_refusals_say_why(self):
        os.environ["NO_FRESH"] = "1"
        with self.assertRaisesRegex(ValueError, "may not ask for a fresh backup"):
            self.pull(fresh=True)
        os.environ["NO_FRESH"] = ""
        os.environ["OLD_SERVER"] = "1"
        with self.assertRaisesRegex(ValueError, "cannot make fresh backups yet"):
            self.pull(fresh=True)
        with self.assertRaisesRegex(ValueError, "too old for full=1"):
            self.pull(full=True)

    def test_an_old_server_without_info_still_hands_out_the_newest(self):
        os.environ["OLD_SERVER"] = "1"
        target, _ = self.pull()
        self.assertEqual(target.name, "worker-prod-20261012T033000Z.sql.gz")
        self.assertEqual(self.calls()[:2], ["info shop prod", "latest shop prod"])

    def test_a_cut_or_short_download_is_not_kept(self):
        self.backup.write_bytes(gzip.compress(os.urandom(100000))[:5000])
        with self.assertRaisesRegex(ValueError, "not a complete .sql.gz"):
            self.pull()
        self.assertEqual(list((self.root / "app/.generated/backups").iterdir()), [])

    def test_the_server_project_is_the_setting_else_the_git_name_else_project_name(self):
        app = self.project.directory
        subprocess.run(["git", "init", "-q", str(app)], check=True)
        subprocess.run(["git", "-C", str(app), "remote", "add", "origin", "git@bitbucket.org:ws/Shop_Tracker.git"], check=True)
        self.pull()
        # The git name (lower case, as the server names build roots) first, then PROJECT_NAME.
        self.assertEqual(self.calls()[:2], ["info shop_tracker prod", "info shop prod"])
        self.log.unlink()
        self.project.settings.update(BACKUP_SERVER_PROJECT="other", BACKUP_SSH_KEY="~/.ssh/backup_ed25519")
        with self.assertRaisesRegex(ValueError, "no project named other"):
            self.pull()
        self.assertIn(f"-i {Path('~/.ssh/backup_ed25519').expanduser()} -o IdentitiesOnly=yes", self.log.read_text())
        self.assertEqual(self.calls(), ["info other prod"], "a configured name is the only one asked for")

    def test_an_unknown_project_is_asked_for_once_and_kept_in_local_env(self):
        self.project.settings["PROJECT_NAME"] = "nope"
        with self.assertRaisesRegex(ValueError, "no project named nope"):
            self.pull()
        self.log.unlink()
        target, _ = self.pull(interactive=True, ask=lambda prompt: "shop")
        self.assertEqual(target.name, "worker-prod-20261012T033000Z.sql.gz")
        self.assertIn("BACKUP_SERVER_PROJECT=shop", (self.root / "app/env/local.env").read_text())

    def test_the_server_is_asked_for_once_and_shared_by_every_project(self):
        self.project.settings.pop("BACKUP_SSH")
        with self.assertRaisesRegex(ValueError, "run make db-pull once in a terminal"):
            connection(self.project, interactive=False)
        answers = iter(["setup@10.8.0.14", ""])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(connection(self.project, interactive=True, ask=lambda prompt: next(answers)),
                             ("setup@10.8.0.14", ""))
        self.assertEqual(self.shared.stat().st_mode & 0o777, 0o600)
        self.assertEqual(read_shared(self.shared)["BACKUP_SSH"], "setup@10.8.0.14")
        # Another project on this machine asks nothing; its own setting wins.
        other = SimpleNamespace(environment="local", directory=self.root, settings={})
        self.assertEqual(connection(other, interactive=False), ("setup@10.8.0.14", ""))
        other.settings["BACKUP_SSH"] = "backup@10.9.0.1"
        self.assertEqual(connection(other, interactive=False)[0], "backup@10.9.0.1")
        # configure=1 asks again, offering the stored answer; a key that is not there saves nothing.
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "No key file"):
            answers = iter(["", "~/no-such-key"])
            connection(self.project, reconfigure=True, interactive=True, ask=lambda prompt: next(answers))
        self.assertEqual(read_shared(self.shared)["BACKUP_SSH"], "setup@10.8.0.14")
        with self.assertRaisesRegex(ValueError, "user@host"):
            connection(self.project, reconfigure=True, interactive=True, ask=lambda prompt: "setup@x; rm -rf ~")

    def test_refusals_and_settings_are_clear(self):
        with self.assertRaisesRegex(ValueError, "no stage backup of shop yet"):
            self.pull("stage")
        with self.assertRaisesRegex(ValueError, "stage or prod"):
            self.pull("local")
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
