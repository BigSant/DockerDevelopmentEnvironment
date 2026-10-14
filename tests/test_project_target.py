"""The Deploy target: real file operations on this machine, and the command lines an ssh target runs."""

from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker"))
from project_target import SSH_OPTIONS, LocalTarget, SshTarget, deploy_target

LISTENING = "LISTEN 0 511 0.0.0.0:80 0.0.0.0:*\nLISTEN 0 4096 [::]:443 [::]:*\n"


class DeployTargetTest(unittest.TestCase):
    def test_deploy_ssh_chooses_the_target_and_must_look_like_user_at_host(self):
        self.assertIsInstance(deploy_target(SimpleNamespace(settings={"DEPLOY_SSH": ""})), LocalTarget)
        target = deploy_target(SimpleNamespace(settings={"DEPLOY_SSH": "shop@host.example"}))
        self.assertEqual((target.local, target.label), (False, "shop@host.example:"))
        for host in ("host", "host; rm -rf /", "shop@host -oProxyCommand=x"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "user@host"):
                deploy_target(SimpleNamespace(settings={"DEPLOY_SSH": host}))


class LocalTargetTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.target = LocalTarget()

    def test_files_are_copied_listed_and_removed_on_this_machine(self):
        source = self.root / "release"
        (source / "modules/shop").mkdir(parents=True)
        (source / "modules/index.php").write_text("<?php\n")
        (source / "old.php").write_text("old\n")
        destination = self.root / "srv/public"
        self.target.mkdir([str(destination.parent)])
        self.target.push(f"{source}/", f"{destination}/", "-a")
        self.assertEqual(self.target.directories(f"{destination}/modules"), {"shop"}, "files are not modules")
        self.assertEqual(self.target.directories(f"{destination}/missing"), set())
        self.target.remove(str(destination), ["old.php", "never-there.php"])
        self.assertFalse((destination / "old.php").exists())
        pulled = self.root / "checkout/shop"
        pulled.parent.mkdir()
        self.target.pull(f"{destination}/modules/shop/", f"{pulled}/", "-a")
        self.assertTrue(pulled.is_dir())

    def test_listening_addresses_or_none_when_they_cannot_be_read(self):
        target = LocalTarget(run=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, LISTENING, ""))
        self.assertEqual(target.listening(), {("0.0.0.0", "80"), ("::", "443")})
        self.assertIsNone(LocalTarget(run=lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "")).listening())

        def missing(command, **kwargs):
            raise FileNotFoundError("ss")
        self.assertIsNone(LocalTarget(run=missing).listening())


class SshTargetTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.output = ""
        self.target = SshTarget("shop@host.example", run=self.record)

    def record(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, self.output, "")

    def test_every_ssh_call_is_batch_mode_with_a_connect_timeout(self):
        self.target.mkdir(["/srv/shop/data/mysql", "/srv/a dir"])
        self.target.push("/release/", "/srv/shop/public/", "-a", "--checksum")
        self.target.pull("/srv/shop/public/modules/x/", "/checkout/modules/x/", "-a")
        command, kwargs = self.calls[0]
        self.assertEqual(command, ["ssh", *SSH_OPTIONS, "shop@host.example", "mkdir -p -- /srv/shop/data/mysql '/srv/a dir'"])
        self.assertTrue(kwargs["check"])
        self.assertEqual(self.calls[1][0], ["rsync", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", "-a", "--checksum",
                                            "/release/", "shop@host.example:/srv/shop/public/"])
        self.assertEqual(self.calls[2][0][-2:], ["shop@host.example:/srv/shop/public/modules/x/", "/checkout/modules/x/"])
        self.assertIn("BatchMode=yes", SSH_OPTIONS)
        self.assertNotIn("StrictHostKeyChecking=accept-new", SSH_OPTIONS, "host keys stay checked")

    def test_directories_are_directories_only(self):
        self.output = "shop\nblog\n"
        self.assertEqual(self.target.directories("/srv/shop/public/modules"), {"shop", "blog"})
        remote = self.calls[0][0][-1]
        self.assertEqual(remote, "find /srv/shop/public/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\\n'")

    def test_removed_names_travel_on_stdin_never_on_the_command_line(self):
        self.target.remove("/srv/shop/public", ["a b.php", "c.php"])
        self.target.remove("/srv/shop/public", [])
        self.assertEqual(len(self.calls), 1, "nothing to remove: no connection")
        command, kwargs = self.calls[0]
        self.assertEqual(command[-1], "cd /srv/shop/public && xargs -0 rm -f --")
        self.assertEqual(kwargs["input"], b"a b.php\0c.php\0")

    def test_listening_reads_ss_on_the_server(self):
        self.output = LISTENING
        self.assertEqual(self.target.listening(), {("0.0.0.0", "80"), ("::", "443")})
        self.assertEqual(self.calls[0][0][-1], "ss -Hltn")


if __name__ == "__main__":
    unittest.main()
