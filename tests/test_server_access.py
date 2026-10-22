"""make ssh: a terminal or command on a server through the setup-server worker (a stub ssh plays its access gate)."""

import contextlib
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "docker")]
from server_access import connect, request  # noqa: E402

# The stub ssh answers `access` with $GRANTS (lines "PROJECT ENV LEVEL") and exits $STATUS for any other request,
# logging each call; OLD_SERVER=1 plays a server whose key runs only the backup command.
SSH = r'''#!/bin/sh
for request; do :; done
echo "$*" >> "$SSH_LOG"
if [ -n "${OLD_SERVER:-}" ]; then
    echo "setup-server-backup-pull: expected: latest|info PROJECT ENV [full] | get PROJECT ENV FILE | fresh PROJECT ENV [full]" >&2
    exit 65
fi
case "$request" in
    access) printf '%b' "$GRANTS" ;;
    *) exit "${STATUS:-0}" ;;
esac
'''


class ServerAccessTest(unittest.TestCase):
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
        self.log = self.root / "ssh.log"
        environment = {"PATH": f"{bin_directory}:{os.environ['PATH']}", "SSH_LOG": str(self.log),
                       "GRANTS": "shop prod phpfpm\\nshop stage docker\\n", "STATUS": "0", "OLD_SERVER": ""}
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)
        shared = patch("database_pull.SHARED", self.root / "setup.env.backup")
        shared.start()
        self.addCleanup(shared.stop)
        (self.root / "app").mkdir()
        self.project = SimpleNamespace(environment="local", directory=self.root / "app",
                                       settings={"BACKUP_SSH": "setup@10.8.0.14", "PROJECT_NAME": "shop"})

    def connect(self, source="prod", tty=False, **options):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status = connect(self.project, source, interactive=False, tty=tty, **options)
        return status, output.getvalue()

    def calls(self):
        return [line.split(" setup@10.8.0.14 ", 1) for line in self.log.read_text().splitlines()]

    def test_each_option_is_one_request_of_the_gate(self):
        for options, words in (({}, ["shell", "shop", "prod"]),
                               ({"root": True}, ["shell", "shop", "prod", "root"]),
                               ({"readonly": True}, ["shell", "shop", "prod", "readonly"]),
                               ({"command": "php bin/console cache:clear"},
                                ["exec", "shop", "prod", "--", "php", "bin/console", "cache:clear"]),
                               ({"command": "sh -c 'ls | wc -l'", "readonly": True},
                                ["exec", "shop", "prod", "readonly", "--", "sh", "-c", "ls | wc -l"]),
                               ({"host": True}, ["host", "shop", "prod"]),
                               ({"host": True, "command": "df -h"}, ["host", "shop", "prod", "--", "df", "-h"]),
                               ({"docker": "logs --tail 50 php-fpm"}, ["docker", "shop", "prod", "logs", "--tail", "50", "php-fpm"])):
            with self.subTest(options=options):
                self.assertEqual(request("shop", "prod", **options), words)
        self.assertEqual(self.connect(command="php -v"), (0, ""))
        (probe, _), (_, sent) = self.calls()
        self.assertEqual(sent, "exec shop prod -- php -v", "one shell-quoted string, which the gate splits again")
        self.assertNotIn("-t", probe.split())

    def test_wrong_combinations_stop_before_connecting(self):
        for options, message in (({"root": True, "readonly": True}, "exclude each other"),
                                 ({"host": True, "root": True}, "server itself"),
                                 ({"docker": "ps", "command": "ls"}, "request of its own"),
                                 ({"command": "  "}, "cmd= is empty"),
                                 ({"command": "echo 'open"}, "quotation")):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                self.connect(**options)
        with self.assertRaisesRegex(ValueError, "stage or prod"):
            self.connect("local")
        self.assertFalse(self.log.exists())

    def test_a_terminal_gets_a_tty_and_a_pipe_does_not(self):
        self.connect(tty=True)
        self.connect(tty=False)
        (_, _), (with_tty, _), (_, _), (without, _) = self.calls()
        self.assertIn("-t", with_tty.split())
        self.assertNotIn("-t", without.split())

    def test_the_exit_status_is_the_remote_one(self):
        os.environ["STATUS"] = "3"
        self.assertEqual(self.connect(command="false")[0], 3)

    def test_the_project_is_the_first_name_the_key_may_reach(self):
        # The git name comes first, but the key reaches only PROJECT_NAME's; stage picks the name granted for stage.
        os.environ["GRANTS"] = "asana prod phpfpm\\nshop stage phpfpm-readonly\\n"
        with patch("database_pull.git_name", return_value="asana_tracker"):
            self.project.settings["PROJECT_NAME"] = "shop"
            self.connect("stage")
            self.assertEqual(self.calls()[-1][1], "shell shop stage")
            os.environ["GRANTS"] = "shop prod phpfpm\\nasana_tracker prod full\\n"
            self.connect()
            self.assertEqual(self.calls()[-1][1], "shell asana_tracker prod")
            os.environ["GRANTS"] = "other prod full\\n"
            with self.assertRaisesRegex(ValueError, "no project named asana_tracker or shop.*BACKUP_SERVER_PROJECT"):
                self.connect()

    def test_access_lists_what_the_key_may_reach(self):
        self.assertEqual(self.connect(access=True), (0, "shop prod phpfpm\nshop stage docker\n"))
        os.environ["GRANTS"] = ""
        self.assertIn("may reach no server yet", self.connect(access=True)[1])

    def test_an_old_server_or_no_route_says_what_to_do(self):
        os.environ["OLD_SERVER"] = "1"
        with self.assertRaisesRegex(ValueError, "no access gate yet.*--grant PROJECT:ENV=LEVEL"):
            self.connect()
        (self.root / "bin/ssh").write_text("#!/bin/sh\necho 'setup@10.8.0.14: Permission denied (publickey).' >&2\nexit 255\n")
        with self.assertRaisesRegex(ValueError, "Check the VPN, BACKUP_SSH"):
            self.connect()

    def test_make_passes_the_options_to_the_runner(self):
        output = subprocess.run(["make", "-n", "-f", str(ROOT / "docker/project.mk"), "ssh", "from=stage", "root=1",
                                 'cmd=php -r "echo 1;"', "SETUP_DIRECTORY=" + str(ROOT), "PROJECT_DOCKER_DIRECTORY=/x/app"],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(output.returncode, 0, output.stderr)
        words = shlex.split(output.stdout.strip().splitlines()[-1])
        self.assertEqual(words[words.index("ssh"):], ["ssh", "--from", "stage", "--root", "--command", 'php -r "echo 1;"'])


if __name__ == "__main__":
    unittest.main()
