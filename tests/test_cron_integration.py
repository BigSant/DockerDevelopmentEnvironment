"""Cron in the setup PHP image: supercronic's overlap behaviour, the UI edit/reload and run-now flow, the schedule off."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("PHP_TEST_IMAGE")


@unittest.skipUnless(IMAGE, "Set PHP_TEST_IMAGE to a built setup PHP image (it carries supercronic)")
class CronIntegrationTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cron-b-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("cron.d", "history"):
            (self.root / name).mkdir()
            os.chmod(self.root / name, 0o777)

    def container(self, *arguments, entrypoint="/bin/sh", environment=()):
        name = f"setup-cron-{uuid.uuid4().hex[:8]}"
        settings = [option for setting in environment for option in ("-e", setting)]
        subprocess.run(["docker", "run", "-d", "--rm", "--name", name, "--network", "none", "--memory", "256m", "--user",
                        f"{os.getuid()}:{os.getgid()}", "-e", "ENV=local", "-e", "TZ=Europe/Vilnius", *settings,
                        "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-v", f"{self.root / 'cron.d'}:/etc/cron.d",
                        "-v", f"{self.root / 'history'}:/var/lib/setup-cron", "--entrypoint", entrypoint, IMAGE, *arguments],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        return name

    def starts(self, overlapping):
        crontab = self.root / "cron.d/seconds"
        # Every second, a job that takes three seconds.
        crontab.write_text("* * * * * * * sh -c 'date +%s%N >> /var/lib/setup-cron/starts; sleep 3'\n")
        starts = self.root / "history/starts"
        starts.unlink(missing_ok=True)
        options = "-overlapping " if overlapping else ""
        name = self.container("-c", f"exec supercronic {options}/etc/cron.d/seconds")
        time.sleep(7.5)
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return len(starts.read_text().split()) if starts.exists() else 0

    def test_supercronic_skips_a_run_while_the_previous_one_is_still_running(self):
        without, with_overlap = self.starts(False), self.starts(True)
        self.assertLessEqual(without, 3, "default: the next start waits for the running job")
        self.assertGreaterEqual(with_overlap, 5, "-overlapping starts every second")

    def request(self, name, path, fields=None):
        script = ("$t = trim(file_get_contents('/tmp/cron-ui-token'));"
                  "$f = json_decode($argv[2], true); $o = array('http' => array('ignore_errors' => true));"
                  "if ($f !== null) { $f['token'] = $t; $o['http'] += array('method' => 'POST',"
                  " 'header' => 'Content-Type: application/x-www-form-urlencoded', 'content' => http_build_query($f)); }"
                  "echo file_get_contents('http://127.0.0.1:8080' . $argv[1], false, stream_context_create($o));")
        result = subprocess.run(["docker", "exec", name, "php", "-r", script, path, json.dumps(fields)],
                                capture_output=True, text=True)
        return result.stdout

    def test_ui_edit_reloads_supercronic_and_run_now_is_recorded(self):
        (self.root / "cron.d/crontab").write_text("0 3 * * * echo nightly\n")
        name = self.container("/opt/setup/runtime/cron/cron-start.sh")
        for _ in range(50):
            if "nightly" in self.request(name, "/"):
                break
            time.sleep(0.2)
        else:
            self.fail(subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr)
        body = self.request(name, "/edit", {"file": "crontab", "crontab": "0 3 * * * echo nightly\n0 4 * * 7 echo weekly\n"})
        self.assertIn("Saved and reloaded", body)
        time.sleep(1)
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        self.assertIn("reloading crontab", logs.stdout + logs.stderr)
        wrapped = subprocess.run(["docker", "exec", name, "cat", "/tmp/crontab"], capture_output=True, text=True).stdout
        self.assertIn("scheduled 'echo weekly'", wrapped, "the reloaded crontab carries the edit")
        rejected = self.request(name, "/edit", {"file": "crontab", "crontab": "61 3 * * * echo broken\n"})
        self.assertIn("Not saved", rejected)
        job = json.loads(subprocess.run(["docker", "exec", name, "php", "-r",
                                         "require '/opt/setup/runtime/cron/compose.php';"
                                         "echo json_encode(cronJobs('0 4 * * 7 echo weekly', 'crontab')[0]['id']);"],
                                        capture_output=True, text=True).stdout)
        self.request(name, "/run", {"job": job})
        history = self.root / "history/history.jsonl"
        for _ in range(50):
            if history.exists():
                break
            time.sleep(0.2)
        run = json.loads(history.read_text().splitlines()[-1])
        self.assertEqual((run["trigger"], run["exit"]), ("manual", 0))
        self.assertEqual((self.root / f"history/runs/{job}/{run['log']}").read_text(), "weekly\n")

    def test_schedule_off_starts_no_supercronic_but_the_ui_runs_jobs_and_stops_cleanly(self):
        (self.root / "cron.d/crontab").write_text("* * * * * echo every-minute\n")
        name = self.container("/opt/setup/runtime/cron/cron-start.sh", environment=["CRON_SCHEDULE=off"])
        for _ in range(50):
            if "every-minute" in self.request(name, "/"):
                break
            time.sleep(0.2)
        else:
            self.fail(subprocess.run(["docker", "logs", name], capture_output=True, text=True).stderr)
        self.assertIn("Schedule off", self.request(name, "/"))
        processes = subprocess.run(["docker", "exec", name, "sh", "-c", "cat /proc/[0-9]*/cmdline | tr '\\0' ' '"],
                                   capture_output=True, text=True).stdout
        self.assertIn("php -S", processes)
        self.assertNotIn("supercronic /tmp/crontab", processes)
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        self.assertIn("schedule off", logs.stdout)
        job = json.loads(subprocess.run(["docker", "exec", name, "php", "-r",
                                         "require '/opt/setup/runtime/cron/compose.php';"
                                         "echo json_encode(cronJobs('* * * * * echo every-minute', 'crontab')[0]['id']);"],
                                        capture_output=True, text=True).stdout)
        self.request(name, "/run", {"job": job})
        history = self.root / "history/history.jsonl"
        for _ in range(50):
            if history.exists():
                break
            time.sleep(0.2)
        runs = [json.loads(line) for line in history.read_text().splitlines()]
        self.assertEqual([(run["trigger"], run["exit"]) for run in runs], [("manual", 0)], "no scheduled run")
        started = time.monotonic()
        stopped = subprocess.run(["docker", "stop", "-t", "10", name], capture_output=True)
        self.assertEqual(stopped.returncode, 0)
        self.assertLess(time.monotonic() - started, 5, "SIGTERM ends the container without the kill timeout")

    def test_schedule_off_still_refuses_a_crontab_supercronic_rejects(self):
        # compose.php keeps lines it does not parse; supercronic rejects them as it would with the schedule on.
        (self.root / "cron.d/crontab").write_text("MAILTO\n* * * * * echo ok\n")
        name = f"setup-cron-{uuid.uuid4().hex[:8]}"
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", name], capture_output=True)
        result = subprocess.run(["docker", "run", "--name", name, "--network", "none", "--memory", "256m", "--user",
                                 f"{os.getuid()}:{os.getgid()}", "-e", "ENV=local", "-e", "CRON_SCHEDULE=off",
                                 "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-v", f"{self.root / 'cron.d'}:/etc/cron.d",
                                 "-v", f"{self.root / 'history'}:/var/lib/setup-cron", "--entrypoint", "/bin/sh", IMAGE,
                                 "/opt/setup/runtime/cron/cron-start.sh"], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("schedule off", result.stdout)


if __name__ == "__main__":
    unittest.main()
