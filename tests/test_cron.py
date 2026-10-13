"""Cron: crontab parsing and next runs, the run wrapper's history, and the UI over PHP's built-in server."""

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CRON = ROOT / "docker/runtime/cron"
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from create_project import scaffold
from project import Project


def php(code, env=None):
    result = subprocess.run(["php", "-r", f"require '{CRON}/compose.php'; {code}"], capture_output=True, text=True,
                            env={**os.environ, "TZ": "Europe/Vilnius", **(env or {})})
    if result.returncode:
        raise AssertionError(result.stderr + result.stdout)
    return json.loads(result.stdout)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class CrontabTest(unittest.TestCase):
    def test_jobs_skip_comments_and_variables_and_keep_ids_stable(self):
        text = "# nightly\nMAILTO=\"\"\n\n*/15 * * * * php bin/console a:b\n@daily php cron.php --x 'y z'\n"
        jobs = php(f"echo json_encode(cronJobs({json.dumps(text)}, 'crontab'));")
        self.assertEqual([(j["schedule"], j["command"], j["line"]) for j in jobs],
                         [("*/15 * * * *", "php bin/console a:b", 4), ("@daily", "php cron.php --x 'y z'", 5)])
        again = php(f"echo json_encode(cronJobs({json.dumps('0 3 * * * php bin/console a:b')}, 'crontab'));")
        self.assertEqual(again[0]["id"], jobs[0]["id"], "the id follows the command, not the schedule")
        twice = php(f"echo json_encode(cronJobs({json.dumps('0 3 * * * php a.php' + chr(10) + '0 15 * * * php a.php')}, 'crontab'));")
        self.assertNotEqual(twice[0]["id"], twice[1]["id"], "one command on two schedules keeps two histories")

    def test_problems_name_the_line(self):
        text = "61 * * * * php a.php\n* * * * *\n@sometimes php b.php\n0 1 * * MON-FRI php c.php\n"
        problems = php(f"echo json_encode(cronProblems({json.dumps(text)}, 'crontab'));")
        self.assertEqual(len(problems), 3)
        self.assertIn('crontab:1: invalid schedule "61 * * * *"', problems[0])
        self.assertIn("crontab:2: no command", problems[1])
        self.assertIn("crontab:3", problems[2])

    def test_wrapped_crontab_records_every_job(self):
        text = "# keep\nTZ=Europe/Vilnius\n5 4 * * * php it's.php\n"
        wrapped = php(f"echo json_encode(cronWrapped({json.dumps(text)}, 'crontab'));", {"CRON_RUN": "/run.sh"})
        lines = wrapped.splitlines()
        self.assertEqual(lines[:2], ["# keep", "TZ=Europe/Vilnius"])
        self.assertRegex(lines[2], r"^5 4 \* \* \* /run\.sh [0-9a-f]{12} scheduled 'php it'\\''s\.php'$")

    def test_next_run(self):
        start = "mktime(10, 7, 30, 10, 8, 2026)"  # Thursday 2026-10-08 10:07:30
        cases = {"*/15 * * * *": "2026-10-08 10:15", "0 3 * * 1": "2026-10-12 03:00", "@daily": "2026-10-09 00:00",
                 "30 9 1 * 0": "2026-10-11 09:30", "0 0 29 2 *": "2027-03-01 00:00", "0 12 * JAN-MAR *": "2027-01-01 12:00"}
        for schedule, expected in cases.items():
            with self.subTest(schedule=schedule):
                result = php(f"$n = cronNextRun({json.dumps(schedule)}, {start}); echo json_encode($n ? date('Y-m-d H:i', $n) : null);")
                if schedule == "0 0 29 2 *":
                    self.assertIsNone(result, "no 29 February within a year of 2026-10")
                else:
                    self.assertEqual(result, expected)
        self.assertIsNone(php("echo json_encode(cronNextRun('0 0 31 4 *', time()));"), "31 April never comes")
        self.assertIsNone(php("echo json_encode(cronNextRun('nonsense', time()));"))


class RunWrapperTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cron-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.history = Path(temporary.name)

    def run_job(self, command, trigger="scheduled", keep="100"):
        return subprocess.run(["sh", str(CRON / "cron-run.sh"), "abc123def456", trigger, command], capture_output=True,
                              text=True, env={**os.environ, "CRON_HISTORY_DIR": str(self.history), "CRON_HISTORY_KEEP": keep})

    def test_each_run_records_output_exit_code_duration_and_trigger(self):
        ok = self.run_job("echo hello; echo oops >&2")
        failed = self.run_job("sleep 0.2; exit 3", "manual")
        self.assertEqual((ok.returncode, failed.returncode), (0, 3))
        self.assertIn("hello", ok.stdout)
        runs = [json.loads(line) for line in (self.history / "history.jsonl").read_text().splitlines()]
        self.assertEqual([(r["trigger"], r["exit"]) for r in runs], [("scheduled", 0), ("manual", 3)])
        self.assertGreaterEqual(runs[1]["duration_ms"], 150)
        self.assertEqual((self.history / "runs/abc123def456" / runs[0]["log"]).read_text(), "hello\noops\n")

    def test_a_running_job_leaves_a_marker_until_it_is_recorded(self):
        env = {**os.environ, "CRON_HISTORY_DIR": str(self.history)}
        job = subprocess.Popen(["sh", str(CRON / "cron-run.sh"), "abc123def456", "manual", "echo first; sleep 1.5"],
                               env=env, stdout=subprocess.DEVNULL)
        self.addCleanup(job.wait)
        marker = None
        for _ in range(50):
            markers = list((self.history / "running").glob("*.json")) if (self.history / "running").exists() else []
            if markers:
                marker = json.loads(markers[0].read_text())
                break
            time.sleep(0.05)
        self.assertIsNotNone(marker, "the marker appears when the job starts")
        self.assertEqual((marker["job"], marker["trigger"], marker["pid"]), ("abc123def456", "manual", job.pid))
        self.assertTrue((self.history / "runs/abc123def456" / marker["log"]).exists(), "the log exists with the marker")
        self.assertFalse((self.history / "history.jsonl").exists(), "nothing is recorded while it runs")
        self.assertEqual(job.wait(timeout=10), 0)
        self.assertEqual(list((self.history / "running").iterdir()), [], "the marker goes when the run is recorded")
        run = json.loads((self.history / "history.jsonl").read_text())
        self.assertEqual((run["log"], run["exit"]), (marker["log"], 0))

    def test_old_logs_are_pruned_and_bad_arguments_refused(self):
        for _ in range(3):
            self.run_job("true", keep="2")
            time.sleep(1.05)
        self.assertEqual(len(list((self.history / "runs/abc123def456").iterdir())), 2)
        (self.history / "history.jsonl").write_text("{}\n" * 401)
        self.run_job("true", keep="2")
        self.assertEqual(len((self.history / "history.jsonl").read_text().splitlines()), 200, "the run index stays bounded")
        bad = subprocess.run(["sh", str(CRON / "cron-run.sh"), "../x", "scheduled", "true"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 64)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class InterruptedRunTest(unittest.TestCase):
    def test_markers_left_by_a_stopped_container_become_interrupted_runs(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cron-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        history = Path(temporary.name)
        (history / "running").mkdir()
        (history / "runs/abc123def456").mkdir(parents=True)
        gone = subprocess.Popen(["true"])
        gone.wait()  # a PID that no longer runs cron-run.sh
        log = history / "runs/abc123def456/20261013T100000-1.log"
        log.write_text("half way\n")
        os.utime(log, (1791900030, 1791900030))
        (history / "running/abc123def456-1.json").write_text(json.dumps(
            {"job": "abc123def456", "trigger": "scheduled", "started": 1791900000000, "log": log.name, "pid": gone.pid}))
        # Recorded already, only its marker survived: removed, not recorded twice.
        (history / "history.jsonl").write_text(json.dumps(
            {"job": "abc123def456", "trigger": "manual", "started": 1, "duration_ms": 1, "exit": 0, "log": "20261013T090000-2.log"}) + "\n")
        (history / "running/abc123def456-2.json").write_text(json.dumps(
            {"job": "abc123def456", "trigger": "manual", "started": 1, "log": "20261013T090000-2.log", "pid": gone.pid}))
        closed = php(f"echo json_encode(cronCloseInterrupted({json.dumps(str(history))}));")
        self.assertEqual(closed, 1)
        self.assertEqual(list((history / "running").iterdir()), [])
        runs = [json.loads(line) for line in (history / "history.jsonl").read_text().splitlines()]
        self.assertEqual(len(runs), 2)
        self.assertEqual((runs[1]["exit"], runs[1]["interrupted"], runs[1]["log"], runs[1]["duration_ms"]),
                         (None, True, log.name, 30000), "the duration runs up to the log's last write")
        metrics = subprocess.run(["php", "-r", f"require '{ROOT}/docker/runtime/metrics/cron.php';"
                                  f"echo json_encode(metrics_cron_history({json.dumps(str(history / 'history.jsonl'))}));"],
                                 capture_output=True, text=True, check=True)
        last = json.loads(metrics.stdout)["abc123def456"]
        self.assertEqual((last["last"]["exit"], last["success"]["log"]), (-1, "20261013T090000-2.log"),
                         "metrics count an interrupted run as failed (-1), not as no run")


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class CronUiTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="cron-ui-", dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "cron.d").mkdir()
        (self.root / "cron.d/crontab").write_text("*/5 * * * * echo from-cron\n")
        (self.root / "bin").mkdir()
        # supercronic validates in the container; here a stand-in accepts what the UI already checked.
        (self.root / "bin/supercronic").write_text("#!/bin/sh\nexit 0\n")
        (self.root / "bin/supercronic").chmod(0o755)

    def start(self, environment, host="127.0.0.1", **settings):
        with socket.socket() as probe:
            probe.bind((host, 0))
            port = probe.getsockname()[1]
        env = {**os.environ, "ENV": environment, "TZ": "Europe/Vilnius", "CRON_DIRECTORY": str(self.root / "cron.d"),
               "CRON_HISTORY_DIR": str(self.root / "history"), "CRON_RUN": str(CRON / "cron-run.sh"),
               "CRON_CRONTAB": str(self.root / "crontab.wrapped"), "CRON_UI_TOKEN_FILE": str(self.root / "token"),
               "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", **settings}
        server = subprocess.Popen(["php", "-S", f"{host}:{port}", str(CRON / "ui/index.php")], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        self.base = f"http://{host}:{port}"
        for _ in range(50):
            try:
                self.get("/")
                return
            except urllib.error.HTTPError:
                return
            except OSError:
                time.sleep(0.1)
        self.fail("UI did not start")

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.read().decode()

    def post(self, path, fields):
        data = urllib.parse.urlencode({"token": (self.root / "token").read_text().strip(), **fields}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data), timeout=5) as response:
                self.last_url = response.geturl()
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def test_local_ui_validates_saves_and_runs_now(self):
        self.start("local")
        page = self.get("/")
        self.assertIn("echo from-cron", page)
        self.assertIn("Run now", page)
        status, body = self.post("/edit", {"file": "crontab", "crontab": "99 * * * * echo bad\n"})
        self.assertIn("Not saved", body)
        self.assertIn("invalid schedule", body)
        self.assertEqual((self.root / "cron.d/crontab").read_text(), "*/5 * * * * echo from-cron\n")
        status, body = self.post("/edit", {"file": "crontab", "crontab": "0 2 * * * echo nightly\n"})
        self.assertIn("Saved; restart cron to apply it", body)
        self.assertEqual((self.root / "cron.d/crontab").read_text(), "0 2 * * * echo nightly\n")
        self.assertIn("scheduled 'echo nightly'", (self.root / "crontab.wrapped").read_text())
        job = php("echo json_encode(cronJobs('0 2 * * * echo nightly', 'crontab')[0]['id']);")
        self.post("/run", {"job": job})
        for _ in range(50):
            history = self.root / "history/history.jsonl"
            if history.exists() and history.read_text().strip():
                break
            time.sleep(0.1)
        run = json.loads(history.read_text().splitlines()[-1])
        self.assertEqual((run["job"], run["trigger"], run["exit"]), (job, "manual", 0))
        self.assertIn("view", self.get(f"/history?job={job}"))
        self.assertIn("nightly", self.get(f"/log?job={job}&file={run['log']}"))
        self.assertEqual(self.post("/run", {"job": job, "token": "wrong"})[0], 403)

    def test_a_running_job_shows_on_the_board_and_its_output_follows_live(self):
        (self.root / "cron.d/crontab").write_text("0 2 * * * echo first; sleep 2; echo second\n")
        self.start("prod")
        job = php("echo json_encode(cronJobs('0 2 * * * echo first; sleep 2; echo second', 'crontab')[0]['id']);")
        status, body = self.post("/run", {"job": job})
        self.assertEqual(status, 200)
        # Run now goes to the history page, which opens the live output as soon as the run has started.
        live, page = (self.last_url[len(self.base):], body) if "/log?" in self.last_url else (None, "")
        started = urllib.parse.parse_qs(urllib.parse.urlparse(self.last_url).query).get("started", ["0"])[0]
        for _ in range(0 if live else 50):
            with urllib.request.urlopen(f"{self.base}/history?job={job}&started={started}", timeout=5) as response:
                if "/log?" in response.geturl():
                    live, page = response.geturl()[len(self.base):], response.read().decode()
                    break
            time.sleep(0.1)
        self.assertIsNotNone(live, "run now opens the live output once the run starts")
        self.assertIn('<span class="chip run">running</span>', page)
        self.assertIn("data-tail=", page)
        board = self.get("/")
        self.assertIn('class="running"', board)
        self.assertIn('class="lamp run"', board)
        self.assertIn("watch", self.get(f"/history?job={job}"))
        file = urllib.parse.parse_qs(urllib.parse.urlparse(live).query)["file"][0]
        tail = json.loads(self.get(f"/tail?job={job}&file={file}&from=0"))
        self.assertTrue(tail["running"])
        for _ in range(60):
            tail = json.loads(self.get(f"/tail?job={job}&file={file}&from=0"))
            if not tail["running"]:
                break
            time.sleep(0.1)
        self.assertFalse(tail["running"], "the run ends")
        self.assertEqual(tail["text"], "first\nsecond\n")
        self.assertEqual(tail["from"], len("first\nsecond\n"))
        self.assertEqual(json.loads(self.get(f"/tail?job={job}&file={file}&from={tail['from']}"))["text"], "")
        done = self.get(live)
        self.assertNotIn("data-tail=", done)
        self.assertIn('<span class="chip ok">exit 0</span>', done)
        self.assertNotIn('class="running"', self.get("/"))

    def test_schedule_off_lists_the_jobs_marks_them_off_and_still_runs_them_by_hand(self):
        # A process standing in for supercronic: an edit must not signal it (SIGUSR2 would end sleep).
        sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        (self.root / "supercronic.pid").write_text(str(sleeper.pid))
        self.start("local", CRON_SCHEDULE="off", CRON_PID_FILE=str(self.root / "supercronic.pid"))
        page = self.get("/")
        self.assertIn("echo from-cron", page)
        self.assertIn("Schedule off", page)
        self.assertIn("Next run (schedule off)", page)
        self.assertRegex(page, r'<span class="off">off</span><br><small>would be \d{4}-\d\d-\d\d \d\d:\d\d</small>')
        self.assertIn("Run now", page)
        status, body = self.post("/edit", {"file": "crontab", "crontab": "0 2 * * * echo nightly\n"})
        self.assertIn("Saved; the schedule is off", body)
        self.assertIsNone(sleeper.poll(), "nothing to reload with the schedule off")
        self.assertIn("echo nightly", self.get("/"))
        job = php("echo json_encode(cronJobs('0 2 * * * echo nightly', 'crontab')[0]['id']);")
        self.post("/run", {"job": job})
        history = self.root / "history/history.jsonl"
        for _ in range(50):
            if history.exists() and history.read_text().strip():
                break
            time.sleep(0.1)
        run = json.loads(history.read_text().splitlines()[-1])
        self.assertEqual((run["job"], run["trigger"], run["exit"]), (job, "manual", 0))
        self.assertIn("manual", self.get("/"), "the last run shows next to the job")

    def test_schedule_on_shows_the_next_run(self):
        self.start("prod", CRON_SCHEDULE="on")
        page = self.get("/")
        self.assertNotIn("Schedule off", page)
        self.assertRegex(page, r'<time class="next" datetime="[^"]+">\d{4}-\d\d-\d\d \d\d:\d\d</time>')

    def test_only_nginx_proxy_or_loopback_reaches_the_ui(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # no traffic: only picks the outgoing interface
            address = probe.getsockname()[0]
        if address.startswith("127."):
            self.skipTest("no non-loopback interface")
        self.start("local", host=address)
        with self.assertRaises(urllib.error.HTTPError) as refused:
            self.get("/")
        self.assertEqual(refused.exception.code, 403)

    def test_server_ui_only_shows(self):
        self.start("prod")
        self.assertIn("changes only through git", self.get("/"))
        status, body = self.post("/edit", {"file": "crontab", "crontab": "0 2 * * * echo nightly\n"})
        self.assertEqual(status, 403)
        self.assertEqual((self.root / "cron.d/crontab").read_text(), "*/5 * * * * echo from-cron\n")


class CrontabMountTest(unittest.TestCase):
    def test_ui_may_write_the_crontab_only_on_local_and_test(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold("cron-mount", temporary)
            with (app / "env/common.env").open("a") as env:
                env.write("SERVICES=cron\n")
            (app / "config/cron").mkdir(parents=True, exist_ok=True)
            (app / "config/cron/crontab").write_text("*/5 * * * * echo from-cron\n")
            # Absent optional drop-ins (config/php here) make the runner add a mount overlay.
            self.assertFalse((app / "config/php").exists())
            for environment, read_only in (("local", False), ("prod", True)):
                with self.subTest(environment=environment):
                    if environment == "prod":
                        (app / "env/prod.env").write_text(
                            "PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\n"
                            "WEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\n"
                            "DATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_ROOT_PASSWORD=root-test-only\n"
                            "DATABASE_PASSWORD=test-only\nCRON_ALLOWED_IPS=203.0.113.7\n")
                    services = json.loads(Project(app, environment).capture(["config", "--format", "json"]))["services"]
                    mount = next(v for v in services["cron"]["volumes"] if v["target"] == "/etc/cron.d")
                    self.assertEqual(mount.get("read_only", False), read_only)


class CronScheduleSettingTest(unittest.TestCase):
    def test_schedule_is_on_by_default_may_be_off_on_every_environment_and_is_validated(self):
        from project_policy import resolve_policy
        limits = {"PHP_MEMORY": "1g", "PHP_CPUS": "1", "DATABASE_MEMORY": "2g", "DATABASE_CPUS": "1",
                  "WEBSERVER_MEMORY": "256m", "WEBSERVER_CPUS": "1", "PROXY_MEMORY": "128m", "PROXY_CPUS": "0.5"}
        for environment in ("local", "test", "stage", "prod"):
            with self.subTest(environment=environment):
                self.assertEqual(resolve_policy(dict(limits), environment)["CRON_SCHEDULE"], "on")
                self.assertEqual(resolve_policy({**limits, "CRON_SCHEDULE": "off"}, environment)["CRON_SCHEDULE"], "off")
        for value in ("no", "OFF", "0"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "CRON_SCHEDULE must be one of: on, off"):
                resolve_policy({"CRON_SCHEDULE": value}, "local")

    def test_the_setting_reaches_the_cron_service(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold("cron-schedule", temporary)
            with (app / "env/common.env").open("a") as env:
                env.write("SERVICES=cron\n")
            cron = lambda: json.loads(Project(app, "local").capture(["config", "--format", "json"]))["services"]["cron"]
            self.assertEqual(cron()["environment"]["CRON_SCHEDULE"], "on")
            with (app / "env/local.env").open("a") as env:
                env.write("CRON_SCHEDULE=off\n")
            self.assertEqual(cron()["environment"]["CRON_SCHEDULE"], "off")


if __name__ == "__main__":
    unittest.main()
