"""Target /metrics: host (processes, I/O wait, server versions), containers (Docker collector) and cron groups;
B: real containers seen through the collector and a failed cron job recorded by cron-run.sh."""

import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_metrics as base
from test_metrics import TOKEN, docker_container, fetch, labelled, parse, tab_rows, write_docker_state

ROOT = base.ROOT
COLLECTOR = ROOT / "docker/runtime/metrics/docker-collector.php"
CRON_RUN = ROOT / "docker/runtime/cron/cron-run.sh"


def group_up(metrics, group):
    return metrics["setup_metrics_group_up"][f'{{group="{group}"}}']


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class HostGroupTest(base.EndpointMixin, unittest.TestCase):
    """The script's top CPU/RAM processes under the viewer's names, I/O wait counters and the server's versions."""

    def setUp(self):
        self.prepare()

    def scrape(self, **env):
        status, _, body = fetch(self.start(**env), token=TOKEN)
        self.assertEqual(status, 200, body)
        return body, parse(body)

    def test_processes_are_summed_per_command_as_ps_shows_them(self):
        body, metrics = self.scrape()
        # ps %cpu: CPU time over the process's lifetime; %mem: resident share of MemTotal; summed per command.
        self.assertEqual(labelled(metrics, "processes_consuming_cpu", "process"),
                         {"/usr/sbin/mysqld": 50, "php-fpm: pool www": 20, "/usr/bin/java": 1, "[kthreadd]": 0, "sh": 0})
        self.assertEqual(labelled(metrics, "processes_consuming_cpu_total", "process"),
                         {"/usr/sbin/mysqld": 1, "php-fpm: pool www": 2, "/usr/bin/java": 1, "[kthreadd]": 1, "sh": 1})
        self.assertEqual(labelled(metrics, "processes_consuming_memory", "process"),
                         {"/usr/bin/java": 20, "/usr/sbin/mysqld": 10, "php-fpm: pool www": 2, "sh": 0, "[kthreadd]": 0})
        self.assertEqual(labelled(metrics, "processes_consuming_memory_total", "process")["php-fpm: pool www"], 2)
        # Arguments are never shown: the runner's OAuth arguments, the shell's script.
        self.assertNotIn("oauth-secret-value", body)
        self.assertNotIn("sleep 1", body)
        self.assertEqual(group_up(metrics, "host"), 1)

    def test_ten_commands_each_and_vanished_processes_are_skipped(self):
        for number in range(12):
            base.write_process(self.proc, 1000 + number, f"w{number}", [f"/opt/worker-{number:02}"], 10 + number, 100, 100)
        # A process that exits between the listing and the read leaves a directory without files.
        (self.proc / "999").mkdir()
        _, metrics = self.scrape()
        cpu = labelled(metrics, "processes_consuming_cpu", "process")
        self.assertEqual(len(cpu), 10)
        self.assertEqual(list(cpu)[:3], ["/usr/sbin/mysqld", "/opt/worker-11", "/opt/worker-10"])
        self.assertEqual(cpu["/opt/worker-11"], 21)
        self.assertEqual(set(labelled(metrics, "processes_consuming_cpu_total", "process")), set(cpu))
        self.assertEqual(len(labelled(metrics, "processes_consuming_memory", "process")), 10)

    def test_io_wait_counters_and_server_versions(self):
        _, metrics = self.scrape()
        # /proc/stat: iowait 200 ticks; procs_blocked absent in the fixture.
        self.assertEqual(metrics["setup_cpu_iowait_seconds_total"][""], 200 / base.HERTZ)
        # Whole disks only: partitions, loop devices left out.
        self.assertEqual(labelled(metrics, "setup_disk_io_time_seconds_total", "device"), {"sda": 1.5, "nvme0n1": 2.5, "dm-0": 3})
        self.assertEqual(list(metrics["setup_server_info"].items()),
                         [('{os="Ubuntu 22.04.5 LTS",kernel="6.8.0-45-generic",docker="28.5.1",database="8.4.0"}', 1)])
        (self.proc / "stat").write_text((self.proc / "stat").read_text() + "procs_running 3\nprocs_blocked 2\n")
        (self.docker_state / "state.json").unlink()
        _, metrics = self.scrape()
        self.assertEqual(metrics["setup_processes_blocked"][""], 2)
        # Without the collector the versions it reads are empty; the containers group reports why.
        self.assertEqual(list(metrics["setup_server_info"]), ['{os="",kernel="6.8.0-45-generic",docker="",database="8.4.0"}'])
        self.assertEqual((group_up(metrics, "host"), group_up(metrics, "containers")), (1, 0))


class DockerApi(http.server.BaseHTTPRequestHandler):
    """A Docker Engine API on a Unix socket answering what the collector asks, recording every request."""

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        path, _, query = self.path.partition("?")
        answer = self.server.answers.get(path)
        if path == "/events":
            self.server.event_queries.append(urllib.parse.parse_qs(query))
            body = "".join(json.dumps(event) + "\n" for event in self.server.events).encode()
        elif path == "/containers/json":
            self.server.list_queries.append(urllib.parse.parse_qs(query))
            body = json.dumps(answer).encode()
        elif answer is None:
            self.send_response(404)
            self.end_headers()
            return
        else:
            body = json.dumps(answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.requests.append(("POST", self.path))
        self.send_response(500)
        self.end_headers()

    do_DELETE = do_PUT = do_POST

    def log_message(self, *args):
        pass


def inspected(identifier, name, service, status, health=None, restarts=0, exit_code=0, oom=False):
    """A container as GET /containers/{id}/json returns it (only the fields that matter here, plus noise)."""
    state = {"Status": status, "Running": status == "running", "Paused": False, "Restarting": status == "restarting",
             "OOMKilled": oom, "Dead": False, "Pid": 4242, "ExitCode": exit_code, "Error": "",
             "StartedAt": "2026-10-09T09:30:00.123456789Z", "FinishedAt": "0001-01-01T00:00:00Z"}
    if health:
        state["Health"] = {"Status": health, "FailingStreak": 3 if health == "unhealthy" else 0, "Log": []}
    return {"Id": identifier, "Name": "/" + name, "RestartCount": restarts, "State": state,
            "Config": {"Image": f"demo-{service}-stage-s1", "Env": ["SECRET=never-copied"],
                       "Labels": {"com.docker.compose.project": "demo-stage", "com.docker.compose.service": service}}}


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class ContainersGroupTest(base.EndpointMixin, unittest.TestCase):
    """The collector maps the Docker API to a snapshot; the metrics service serves it without Docker access."""

    def setUp(self):
        self.prepare()
        # A socket path holds at most 107 bytes; the CI checkout path alone takes ~70, so not under self.work.
        sockets = tempfile.TemporaryDirectory(prefix="setup-")
        self.addCleanup(sockets.cleanup)
        self.socket_path = Path(sockets.name) / "docker.sock"
        server = socketserver.ThreadingUnixStreamServer(str(self.socket_path), DockerApi)
        server.requests, server.list_queries, server.event_queries = [], [], []
        containers = [inspected("a1", "demo-stage-php-fpm", "php-fpm", "running", "unhealthy", restarts=3),
                      inspected("b2", "demo-stage-database", "database", "running", "healthy"),
                      inspected("c3", "demo-stage-cron", "cron", "exited", exit_code=137, oom=True),
                      inspected("d4", "demo-stage-webserver", "webserver", "restarting", restarts=7, exit_code=1)]
        server.answers = {"/info": {"OperatingSystem": "Ubuntu 22.04.5 LTS", "ServerVersion": "28.5.1", "NCPU": 16},
                          "/containers/json": [{"Id": c["Id"], "Names": [c["Name"]]} for c in containers]}
        server.answers.update({f"/containers/{c['Id']}/json": c for c in containers})
        server.events = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.api = server
        self.collected = self.work / "collected"
        self.collected.mkdir()

    def collect(self):
        result = subprocess.run(["php", str(COLLECTOR), "--once"], capture_output=True, text=True, timeout=60,
                                env={**os.environ, "METRICS_DOCKER_SOCKET": str(self.socket_path),
                                     "METRICS_DOCKER_PROJECT": "demo-stage", "METRICS_DOCKER_DIR": str(self.collected)})
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.collected / "state.json").read_text())

    def test_the_collector_maps_the_docker_api_and_the_scrape_shows_unhealthy_restarted_and_oom(self):
        snapshot = self.collect()
        # Only GET requests, and only this project's containers.
        self.assertEqual({method for method, _ in self.api.requests}, {"GET"})
        self.assertEqual(json.loads(self.api.list_queries[0]["filters"][0]), {"label": ["com.docker.compose.project=demo-stage"]})
        self.assertEqual(self.api.list_queries[0]["all"], ["1"])
        self.assertNotIn("never-copied", json.dumps(snapshot))
        self.assertEqual(oct((self.collected / "state.json").stat().st_mode & 0o777), "0o644")
        # The first snapshot starts the OOM event window; nothing before it is counted.
        self.assertEqual(self.api.event_queries, [])
        self.assertEqual(snapshot["oom_kills"], {})

        self.api.events = [{"Type": "container", "Action": "oom", "Actor": {"ID": "c3", "Attributes":
                            {"com.docker.compose.project": "demo-stage", "com.docker.compose.service": "cron"}}}] * 2
        until = snapshot["events_until"]
        time.sleep(1.1)
        snapshot = self.collect()
        query = self.api.event_queries[0]
        self.assertEqual(query["since"], [str(until)])
        self.assertEqual(json.loads(query["filters"][0]),
                         {"type": ["container"], "event": ["oom"], "label": ["com.docker.compose.project=demo-stage"]})
        self.assertEqual(snapshot["oom_kills"], {"cron": 2})
        self.api.events = self.api.events[:1]
        time.sleep(1.1)
        self.collect()

        base_url = self.start(METRICS_DOCKER_DIR=str(self.collected))
        metrics = parse(fetch(base_url, token=TOKEN)[2])
        # The Containers tab shows the same snapshot: one row per container.
        rows = {row[1]: row for row in tab_rows(fetch(base_url, "/containers")[2], "Containers")}
        self.assertEqual({name: (row[2], row[3], row[4], row[5], row[6], row[7], row[8]) for name, row in rows.items()}, {
            "demo-stage-cron": ("exited", "none", "no", "–", "0", "yes", "137"),
            "demo-stage-database": ("running", "healthy", "yes", "yes", "0", "no", "0"),
            "demo-stage-php-fpm": ("running", "unhealthy", "yes", "no", "3", "no", "0"),
            "demo-stage-webserver": ("restarting", "none", "no", "–", "7", "no", "1")})
        key = lambda service: f'{{project="demo-stage",service="{service}",container="demo-stage-{service}"}}'
        self.assertEqual(metrics["setup_container_running"],
                         {key("cron"): 0, key("database"): 1, key("php-fpm"): 1, key("webserver"): 0})
        self.assertEqual(metrics["setup_container_healthy"], {key("database"): 1, key("php-fpm"): 0})
        self.assertEqual(metrics["setup_container_restarts_total"],
                         {key("cron"): 0, key("database"): 0, key("php-fpm"): 3, key("webserver"): 7})
        self.assertEqual(metrics["setup_container_oom_killed"][key("cron")], 1)
        self.assertEqual(metrics["setup_container_exit_code"][key("cron")], 137)
        self.assertEqual(labelled(metrics, "setup_container_oom_kills_total", 'project="demo-stage",service'),
                         {"cron": 3, "database": 0, "php-fpm": 0, "webserver": 0})
        self.assertIn('{project="demo-stage",service="php-fpm",container="demo-stage-php-fpm",image="demo-php-fpm-stage-s1",'
                      'state="running",health="unhealthy"}', metrics["setup_container_info"])
        self.assertEqual(metrics["setup_container_started_timestamp_seconds"][key("php-fpm")], 1791538200)
        self.assertEqual(group_up(metrics, "containers"), 1)
        self.assertEqual(list(metrics["setup_server_info"]),
                         ['{os="Ubuntu 22.04.5 LTS",kernel="6.8.0-45-generic",docker="28.5.1",database="8.4.0"}'])

    def test_an_unreachable_api_keeps_the_last_snapshot_which_then_ages(self):
        self.collect()
        self.api.answers.pop("/info")
        result = subprocess.run(["php", str(COLLECTOR), "--once"], capture_output=True, text=True, timeout=60,
                                env={**os.environ, "METRICS_DOCKER_SOCKET": str(self.socket_path),
                                     "METRICS_DOCKER_PROJECT": "demo-stage", "METRICS_DOCKER_DIR": str(self.collected)})
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 404", result.stderr)
        self.assertTrue((self.collected / "state.json").is_file())

    def test_a_stale_or_missing_snapshot_clears_up_and_serves_no_containers(self):
        # Three intervals plus 30 s: with METRICS_DOCKER_INTERVAL=10, 61 s is stale.
        write_docker_state(self.docker_state, collected=int(time.time()) - 61,
                           containers=[docker_container("demo-stage-php-fpm", "php-fpm", health="unhealthy")])
        base_url = self.start(METRICS_DOCKER_INTERVAL="10")
        metrics = parse(fetch(base_url, token=TOKEN)[2])
        self.assertNotIn("setup_container_running", metrics)
        self.assertEqual(group_up(metrics, "containers"), 0)
        write_docker_state(self.docker_state, collected=int(time.time()) - 40,
                           containers=[docker_container("demo-stage-php-fpm", "php-fpm", health="starting")])
        metrics = parse(fetch(base_url, token=TOKEN)[2])
        self.assertEqual(list(metrics["setup_container_healthy"].values()), [0])
        self.assertEqual(group_up(metrics, "containers"), 1)
        (self.docker_state / "state.json").write_text("{")
        self.assertEqual(group_up(parse(fetch(base_url, token=TOKEN)[2]), "containers"), 0)


def base_samplers():
    """The test servers in start order (test_metrics.SAMPLERS)."""
    return base.SAMPLERS


def job_id(command, occurrence=1):
    """compose.php's job id: the command (and its occurrence in the same file)."""
    return hashlib.sha1((command if occurrence == 1 else f"{command}\0{occurrence}").encode()).hexdigest()[:12]


FAILING = 'echo "https://shop.example/cron?token=cron-secret-value" > /dev/null; exit 3'


@unittest.skipUnless(shutil.which("php") and shutil.which("sh"), "PHP CLI and sh are required")
class CronGroupTest(base.EndpointMixin, unittest.TestCase):
    """Each crontab job's last run, exit code and duration from the history cron-run.sh writes."""

    def setUp(self):
        self.prepare()
        self.config = self.work / "cron-config"
        self.history = self.work / "cron-history"
        self.config.mkdir()
        self.history.mkdir()
        (self.config / "crontab").write_text(f"SHELL=/bin/sh\n# nightly\n*/5 * * * * {FAILING}\n0 3 * * * true\n"
                                             "@hourly echo never\nbad line\n")
        (self.config / "crontab.stage").write_text("15 * * * * true\n")
        (self.config / "crontab.prod").write_text("30 * * * * echo prod-only\n")

    def run_job(self, command, trigger="scheduled", job=None):
        return subprocess.run(["sh", str(CRON_RUN), job or job_id(command), trigger, command], capture_output=True, text=True,
                              timeout=60, env={**os.environ, "CRON_HISTORY_DIR": str(self.history)}).returncode

    def scrape(self, **env):
        settings = {"METRICS_CRON": "1", "METRICS_CRON_CONFIG": str(self.config), "METRICS_CRON_HISTORY": str(self.history),
                    "ENV": "stage", **env}
        status, _, body = fetch(self.start(**settings), token=TOKEN)
        self.assertEqual(status, 200, body)
        series = [line.rsplit(" ", 1)[0] for line in body.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(series), len(set(series)), "a series is served twice")
        return body, parse(body)

    def test_the_cron_tab_shows_the_jobs_of_metrics_and_says_when_cron_is_off(self):
        self.assertEqual(self.run_job(FAILING), 3)
        _, metrics = self.scrape()
        base = list(base_samplers())[-1]
        page = fetch(base, "/cron")[2]
        # job, file, line, then schedule, last run, exit, duration, last success, next expected.
        jobs = {tuple(row[:3]): row for row in tab_rows(page, "Jobs")}
        failing = jobs[(job_id(FAILING), "crontab", "3")]
        self.assertEqual((failing[3], failing[5]), ("*/5 * * * *", "3"))
        # The nightly job is in two files: two rows with their own schedules and the job's one history.
        nightly, stage = jobs[(job_id("true"), "crontab", "4")], jobs[(job_id("true"), "crontab.stage", "1")]
        self.assertEqual((nightly[3], stage[3]), ("0 3 * * *", "15 * * * *"))
        self.assertEqual(nightly[4:8], stage[4:8])
        self.assertEqual(len(jobs), len(metrics["setup_cron_job_info"]))
        self.assertNotIn("cron-secret-value", page)
        _, metrics = self.scrape(METRICS_CRON="0")
        self.assertIn("No data: the cron service is not enabled", fetch(list(base_samplers())[-1], "/cron")[2])

    def test_a_failed_job_shows_its_exit_code_and_a_job_without_runs_its_expected_time(self):
        self.assertEqual(self.run_job("true"), 0)
        self.assertEqual(self.run_job(FAILING), 3)
        started = time.time()
        body, metrics = self.scrape()
        # true in crontab.stage is its file's first occurrence: the nightly job's id and history (as in the cron UI),
        # so its run figures are served once; crontab.prod is not this environment's.
        failing, nightly, hourly = job_id(FAILING), job_id("true"), job_id("echo never")
        info = metrics["setup_cron_job_info"]
        self.assertEqual(set(info), {f'{{job="{failing}",source="crontab",line="3",schedule="*/5 * * * *"}}',
                                     f'{{job="{nightly}",source="crontab",line="4",schedule="0 3 * * *"}}',
                                     f'{{job="{hourly}",source="crontab",line="5",schedule="@hourly"}}',
                                     f'{{job="{nightly}",source="crontab.stage",line="1",schedule="15 * * * *"}}'})
        self.assertEqual(labelled(metrics, "setup_cron_job_last_exit_code", "job"), {failing: 3, nightly: 0})
        last = labelled(metrics, "setup_cron_job_last_run_timestamp_seconds", "job")
        self.assertAlmostEqual(last[failing], started, delta=30)
        self.assertGreaterEqual(labelled(metrics, "setup_cron_job_last_duration_seconds", "job")[failing], 0)
        self.assertEqual(set(labelled(metrics, "setup_cron_job_last_success_timestamp_seconds", "job")), {nightly})
        # Next scheduled time after the last run (*/5: within 5 minutes); without runs, after now (@hourly).
        expected = labelled(metrics, "setup_cron_job_expected_run_timestamp_seconds", "job")
        self.assertTrue(last[failing] < expected[failing] <= last[failing] + 300)
        self.assertEqual(expected[hourly] % 3600, 0)
        self.assertTrue(started < expected[hourly] <= started + 3600 + 30)
        # The command (and the token in it) is never a label.
        self.assertNotIn("cron-secret-value", body)
        self.assertNotIn("echo", body)
        self.assertEqual(group_up(metrics, "cron"), 1)

        # The nightly id's earliest schedule (15 * * * * of crontab.stage) gives its expected time.
        self.assertEqual(time.gmtime(expected[nightly]).tm_min, 15)
        # A later successful manual run replaces the failure; the last success follows it.
        self.assertEqual(self.run_job("true", "manual", job=failing), 0)
        _, metrics = self.scrape()
        self.assertEqual(labelled(metrics, "setup_cron_job_last_exit_code", "job")[failing], 0)
        self.assertIn(failing, labelled(metrics, "setup_cron_job_last_success_timestamp_seconds", "job"))

    def test_with_the_schedule_off_no_run_is_expected(self):
        self.assertEqual(self.run_job(FAILING, "manual"), 3)
        _, metrics = self.scrape()
        self.assertEqual(metrics["setup_cron_schedule_enabled"], {"": 1})
        self.assertIn("setup_cron_job_expected_run_timestamp_seconds", metrics)
        _, metrics = self.scrape(METRICS_CRON_SCHEDULE="off")
        self.assertEqual(metrics["setup_cron_schedule_enabled"], {"": 0})
        # The jobs and their (manual) runs stay; no expected time, so no missing-run alert.
        self.assertEqual(len(metrics["setup_cron_job_info"]), 4)
        self.assertEqual(labelled(metrics, "setup_cron_job_last_exit_code", "job"), {job_id(FAILING): 3})
        self.assertNotIn("setup_cron_job_expected_run_timestamp_seconds", metrics)
        self.assertEqual(group_up(metrics, "cron"), 1)

    def test_without_cron_nothing_is_served_and_a_missing_history_clears_up(self):
        _, metrics = self.scrape(METRICS_CRON="0")
        self.assertFalse([name for name in metrics if name.startswith("setup_cron_")])
        self.assertEqual(group_up(metrics, "cron"), 1)
        # Enabled, but nothing ran yet: the jobs with their expected time, no run figures.
        _, metrics = self.scrape()
        self.assertEqual(len(metrics["setup_cron_job_info"]), 4)
        self.assertNotIn("setup_cron_job_last_exit_code", metrics)
        shutil.rmtree(self.history)
        _, metrics = self.scrape()
        self.assertEqual(group_up(metrics, "cron"), 0)


PHP = os.environ.get("PHP_TEST_IMAGE")


@unittest.skipUnless(PHP, "Set PHP_TEST_IMAGE to a built setup PHP image")
class ContainersIntegrationTest(unittest.TestCase):
    """Real containers: an unhealthy and a restarting one read by the collector over the real socket (no network),
    a failed job recorded by cron-run.sh in the PHP image, both visible in one scrape."""

    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=check).stdout.strip()

    def test_unhealthy_and_restarted_containers_and_a_failed_cron_job_in_one_scrape(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        project = f"setup-metrics-{uuid.uuid4().hex[:8]}"
        labels = lambda service: ["--label", f"com.docker.compose.project={project}", "--label", f"com.docker.compose.service={service}"]
        sick, crashing, state = project + "-sick", project + "-crashing", project + "-state"
        collector, endpoint = project + "-collector", project + "-metrics"
        self.addCleanup(self.docker, "volume", "rm", "-f", state, check=False)
        self.addCleanup(self.docker, "rm", "-f", sick, crashing, collector, endpoint, check=False)
        self.docker("run", "-d", "--name", sick, "--network", "none", "--memory", "32m", *labels("sick"),
                    "--health-cmd", "false", "--health-interval", "1s", "--health-retries", "1", "--entrypoint", "sleep", PHP, "300")
        self.docker("run", "-d", "--name", crashing, "--network", "none", "--memory", "32m", *labels("crashing"),
                    "--restart", "on-failure:3", "--entrypoint", "sh", PHP, "-c", "sleep 1; exit 4")
        # A failed job, recorded by the real cron-run.sh in the image (as the project account).
        (work / "config").mkdir()
        (work / "history").mkdir()
        command = "echo failing; exit 5"
        (work / "config/crontab").write_text(f"* * * * * {command}\n")
        self.docker("run", "--rm", "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-v", f"{work}/history:/var/lib/setup-cron",
                    "--entrypoint", "sh", PHP, "/opt/setup/runtime/cron/cron-run.sh", job_id(command), "scheduled", command,
                    check=False)
        time.sleep(8)  # health check failures and restarts
        self.docker("volume", "create", state)
        result = subprocess.run(["docker", "run", "--name", collector, "--network", "none", "--user", "0:0", "--read-only",
                                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                 "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-v", "/var/run/docker.sock:/var/run/docker.sock:ro",
                                 "-v", f"{state}:/var/lib/setup-docker", "-e", f"METRICS_DOCKER_PROJECT={project}",
                                 "-e", "METRICS_DOCKER_DIR=/var/lib/setup-docker", "--entrypoint", "php", PHP,
                                 "/opt/setup/runtime/metrics/docker-collector.php", "--once"], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.docker("run", "-d", "--name", endpoint, "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
                    "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-v", "/proc:/host/proc:ro",
                    "-v", f"{work}:/host/disk:ro", "-v", f"{state}:/host/docker:ro",
                    "-v", f"{work}/history:/host/cron-history:ro", "-v", f"{work}/config:/host/cron-config:ro",
                    "-e", f"METRICS_TOKEN={TOKEN}", "-e", "METRICS_CRON=1", "-e", "ENV=prod", "-e", "METRICS_DISK_WRITE_MB=0",
                    "-e", "TZ=UTC", "--tmpfs", "/var/lib/setup-metrics:mode=1777", "-e", "METRICS_SAMPLER_INTERVAL=2", "-e", "METRICS_PROBE_INTERVAL=5",
                    "--entrypoint", "sh", PHP, "/opt/setup/runtime/metrics/start.sh")
        scrape = ("$c = stream_context_create(['http' => ['header' => 'Authorization: Bearer " + TOKEN + "', 'timeout' => 60]]);"
                  " echo file_get_contents('http://127.0.0.1:9100/metrics', false, $c);")
        body = ""
        for _ in range(30):
            body = subprocess.run(["docker", "exec", endpoint, "php", "-r", scrape], capture_output=True, text=True, timeout=120).stdout
            # Until the sampler's first snapshot every group is down.
            if "setup_metrics_snapshot_timestamp_seconds" in body and 'setup_metrics_group_up{group="containers"} 1' in body:
                break
            time.sleep(0.5)
        metrics = parse(body)
        key = lambda service, name: f'{{project="{project}",service="{service}",container="{name}"}}'
        self.assertEqual(metrics["setup_container_healthy"], {key("sick", sick): 0})
        self.assertEqual(metrics["setup_container_running"][key("sick", sick)], 1)
        self.assertGreaterEqual(metrics["setup_container_restarts_total"][key("crashing", crashing)], 1)
        self.assertEqual(set(metrics["setup_container_running"]), {key("sick", sick), key("crashing", crashing)})
        self.assertEqual(labelled(metrics, "setup_cron_job_last_exit_code", "job"), {job_id(command): 5})
        self.assertNotIn("failing", body)
        info = list(metrics["setup_server_info"])[0]
        self.assertRegex(info, r'docker="\d+\.\d+')
        self.assertEqual((group_up(metrics, "containers"), group_up(metrics, "cron"), group_up(metrics, "host")), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
