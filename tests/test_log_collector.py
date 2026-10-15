"""Log collector (service metrics-logs): container logs kept per service, stream and day past the containers' life,
gzipped when the day is over, deleted past the retention or the size limit. A: a fake Docker API on a Unix socket;
B (PHP_TEST_IMAGE): real containers recreated between two reads."""
import datetime
import gzip
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.parse
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "docker/runtime/metrics/log-collector.php"
ZONE = "Europe/Vilnius"
PHP = os.environ.get("PHP_TEST_IMAGE")


def docker_time(moment):
    """Docker's RFC 3339 UTC time with nanoseconds, trailing zeros trimmed as the daemon prints them."""
    utc = moment.astimezone(datetime.timezone.utc)
    fraction = f"{utc.microsecond:06d}123".rstrip("0")
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + (f".{fraction}" if fraction else "") + "Z"


def since_value(text):
    """The API's since= (seconds.nanoseconds) as a comparable pair."""
    seconds, _, nanos = text.partition(".")
    return int(seconds), int((nanos or "0").ljust(9, "0"))


def time_value(text):
    base, _, rest = text.partition(".")
    nanos = rest.rstrip("Z") if rest else "0"
    seconds = int(datetime.datetime.strptime(base.rstrip("Z"), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp())
    return seconds, int(nanos.ljust(9, "0"))


class DockerApi(http.server.BaseHTTPRequestHandler):
    """Lists the containers, inspects them and serves their logs as multiplexed frames (HTTP/1.0, then close)."""

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        path, _, query = self.path.partition("?")
        parameters = urllib.parse.parse_qs(query)
        containers = self.server.containers
        if path == "/containers/json":
            body = json.dumps([{"Id": c["id"], "State": c["state"], "Labels": {
                "com.docker.compose.project": c.get("project", "demo-stage"), "com.docker.compose.service": c["service"]}}
                for c in containers]).encode()
        elif path.startswith("/containers/") and path.endswith("/json"):
            container = next((c for c in containers if c["id"] == path.split("/")[2]), None)
            if container is None:
                return self.answer(404, b"")
            body = json.dumps({"Id": container["id"], "Config": {"Tty": False}}).encode()
        elif path.startswith("/containers/") and path.endswith("/logs"):
            container = next((c for c in containers if c["id"] == path.split("/")[2]), None)
            if container is None:
                return self.answer(404, b"")
            self.server.log_queries.append(parameters)
            since = since_value(parameters["since"][0]) if "since" in parameters else (0, 0)
            body = b""
            for stream, moment, text, *split in container["lines"]:
                stamp = docker_time(moment)
                if time_value(stamp) < since:
                    continue
                if split:
                    # A line docker split in two parts, each with its time; only the last ends the line.
                    first, second = text[:split[0]], text[split[0]:]
                    for payload in (f"{stamp} {first}", f"{stamp} {second}\n"):
                        body += struct.pack(">BxxxI", stream, len(payload.encode())) + payload.encode()
                else:
                    payload = f"{stamp} {text}\n".encode()
                    body += struct.pack(">BxxxI", stream, len(payload)) + payload
        else:
            return self.answer(404, b"")
        self.answer(200, body)

    def answer(self, status, body):
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.server.requests.append(("POST", self.path))
        self.answer(500, b"")

    do_DELETE = do_PUT = do_POST

    def log_message(self, *args):
        pass


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class LogCollectorTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.logs = Path(temporary.name) / "logs"
        self.logs.mkdir()
        # A socket path holds at most 107 bytes: not under the checkout.
        sockets = tempfile.TemporaryDirectory(prefix="setup-")
        self.addCleanup(sockets.cleanup)
        self.socket = Path(sockets.name) / "docker.sock"
        server = socketserver.ThreadingUnixStreamServer(str(self.socket), DockerApi)
        server.requests, server.log_queries, server.containers = [], [], []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.api = server
        zone = ZoneInfo(ZONE)
        self.today = datetime.datetime.now(zone).date()
        self.yesterday = self.today - datetime.timedelta(days=1)
        self.midnight = datetime.datetime.combine(self.today, datetime.time(), zone)

    def run_collector(self, *args, **env):
        settings = {"METRICS_DOCKER_SOCKET": str(self.socket), "METRICS_DOCKER_PROJECT": "demo-stage",
                    "LOGS_DIR": str(self.logs), "TZ": ZONE, **env}
        return subprocess.run(["php", str(COLLECTOR), *args], capture_output=True, text=True, timeout=60,
                              env={**os.environ, **settings})

    def collect(self, **env):
        result = self.run_collector("--once", **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def read(self, relative):
        path = self.logs / relative
        if path.suffix == ".gz":
            return gzip.decompress(path.read_bytes()).decode()
        return path.read_text()

    def files(self):
        return sorted(str(path.relative_to(self.logs)) for path in self.logs.rglob("*") if path.is_file()
                      and not path.name.startswith("."))

    def test_lines_go_to_one_file_per_service_stream_and_local_day(self):
        before, after = self.midnight - datetime.timedelta(seconds=1), self.midnight + datetime.timedelta(seconds=1)
        self.api.containers = [
            {"id": "p1", "service": "nginx-proxy", "state": "running",
             "lines": [(1, before, '203.0.113.1 - - "GET / HTTP/1.1" 200 5 rt=0.010'),
                       (1, after, '203.0.113.2 - - "GET /a HTTP/1.1" 200 5 rt=0.020'),
                       (2, after, "nginx warning")]},
            {"id": "f1", "service": "php-fpm", "state": "running",
             "lines": [(1, after, 'GET /page 200 ms=12.5'), (2, after, "PHP Warning: " + "x" * 40, 20)]},
            {"id": "d1", "service": "database", "state": "exited", "lines": [(1, before, "mysqld ready")]},
            # The collector's own container and another project's are not read.
            {"id": "s1", "service": "metrics-logs", "state": "running", "lines": [(2, after, "own error")]},
        ]
        self.collect()
        y, t = self.yesterday.isoformat(), self.today.isoformat()
        self.assertEqual(self.files(), sorted([f"database/output-{y}.log", f"nginx-proxy/access-{t}.log", f"nginx-proxy/access-{y}.log",
                                               f"nginx-proxy/error-{t}.log", f"php-fpm/access-{t}.log", f"php-fpm/error-{t}.log"]))
        # The lines as the containers wrote them: no Docker time, the split line whole again.
        self.assertEqual(self.read(f"nginx-proxy/access-{y}.log"), '203.0.113.1 - - "GET / HTTP/1.1" 200 5 rt=0.010\n')
        self.assertEqual(self.read(f"php-fpm/error-{t}.log"), "PHP Warning: " + "x" * 40 + "\n")
        self.assertEqual(self.read(f"database/output-{y}.log"), "mysqld ready\n")
        self.assertNotIn("/containers/s1/logs", " ".join(path for _, path in self.api.requests))
        self.assertEqual({method for method, _ in self.api.requests}, {"GET"})
        # Read again, nothing new: since= repeats the last line, which is not written twice.
        self.collect()
        self.assertEqual(self.read(f"nginx-proxy/access-{t}.log"), '203.0.113.2 - - "GET /a HTTP/1.1" 200 5 rt=0.020\n')
        # The running ones are asked from their last line on; the stopped database, read to its end, is not asked again.
        again = self.api.log_queries[3:]
        self.assertEqual(len(again), 2, self.api.log_queries)
        self.assertTrue(all("since" in query for query in again), again)
        state = json.loads((self.logs / ".collector-state.json").read_text())
        self.assertEqual(set(state["containers"]), {"p1", "f1", "d1"})

    def test_a_line_that_follows_a_later_one_is_kept(self):
        # stdout and stderr reach the daemon's log file side by side: an earlier stderr line may come second.
        at = self.midnight + datetime.timedelta(hours=2)
        self.api.containers = [{"id": "f1", "service": "php-fpm", "state": "running",
                                "lines": [(1, at + datetime.timedelta(microseconds=5), "out"), (2, at, "err")]}]
        self.collect()
        t = self.today.isoformat()
        self.assertEqual((self.read(f"php-fpm/access-{t}.log"), self.read(f"php-fpm/error-{t}.log")), ("out\n", "err\n"))

    def test_a_recreated_container_continues_the_same_files(self):
        before = self.midnight + datetime.timedelta(hours=1)
        self.api.containers = [{"id": "old", "service": "php-fpm", "state": "running", "lines": [(1, before, "first")]}]
        self.collect()
        # deploy-images: the old container is gone with its docker logs, a new one has its own.
        self.api.containers = [{"id": "new", "service": "php-fpm", "state": "running",
                                "lines": [(1, before + datetime.timedelta(minutes=5), "second")]}]
        self.collect()
        self.assertEqual(self.read(f"php-fpm/access-{self.today.isoformat()}.log"), "first\nsecond\n")
        self.assertEqual(set(json.loads((self.logs / ".collector-state.json").read_text())["containers"]), {"new"})

    def test_a_finished_day_is_gzipped_and_a_late_line_is_appended_to_it(self):
        y = self.yesterday.isoformat()
        evening = self.midnight - datetime.timedelta(hours=2)
        self.api.containers = [{"id": "w1", "service": "webserver", "state": "running",
                                "lines": [(2, evening, "[proxy:error] first")]}]
        self.collect()
        plain = self.logs / f"webserver/error-{y}.log"
        # Written just now: a day's last lines may still come, so it waits.
        self.assertTrue(plain.is_file())
        old = time.time() - 3600
        os.utime(plain, (old, old))
        self.collect()
        self.assertEqual(self.files(), [f"webserver/error-{y}.log.gz"])
        self.assertEqual(self.read(f"webserver/error-{y}.log.gz"), "[proxy:error] first\n")
        self.assertEqual(oct((self.logs / f"webserver/error-{y}.log.gz").stat().st_mode & 0o777), "0o644")
        self.api.containers[0]["lines"].append((2, evening + datetime.timedelta(minutes=1), "[proxy:error] late"))
        self.collect()
        self.assertEqual(self.files(), [f"webserver/error-{y}.log.gz"])
        self.assertEqual(self.read(f"webserver/error-{y}.log.gz"), "[proxy:error] first\n[proxy:error] late\n")
        # zcat reads the appended member as part of the file.
        self.assertEqual(subprocess.run(["zcat", str(self.logs / f"webserver/error-{y}.log.gz")], capture_output=True,
                                        text=True).stdout, "[proxy:error] first\n[proxy:error] late\n")

    def test_retention_and_the_size_limit_never_take_today(self):
        def day(offset):
            return (self.today - datetime.timedelta(days=offset)).isoformat()
        (self.logs / "php-fpm").mkdir()
        for offset, size in ((20, 10), (14, 10), (13, 3 << 20), (2, 1 << 20), (0, 2 << 20)):
            (self.logs / f"php-fpm/access-{day(offset)}.log.gz").write_bytes(b"x" * size)
        self.collect(LOGS_RETENTION_DAYS="14")
        self.assertEqual(self.files(), sorted(f"php-fpm/access-{day(o)}.log.gz" for o in (14, 13, 2, 0)))
        # 6 MB over a 3 MB limit: the oldest go first, today's stays even when it alone is over.
        result = self.collect(LOGS_RETENTION_DAYS="14", LOGS_MAX_MB="3")
        self.assertEqual(self.files(), sorted(f"php-fpm/access-{day(o)}.log.gz" for o in (2, 0)))
        self.assertIn("over LOGS_MAX_MB", result.stderr)
        self.collect(LOGS_MAX_MB="1")
        self.assertEqual(self.files(), [f"php-fpm/access-{day(0)}.log.gz"])

    def test_make_logs_prints_one_day_of_a_service(self):
        after = self.midnight + datetime.timedelta(hours=3)
        self.api.containers = [
            {"id": "f1", "service": "php-fpm", "state": "running", "lines": [(1, after, "request"), (2, after, "warning")]},
            {"id": "p1", "service": "nginx-proxy", "state": "running", "lines": [(1, after, "proxy line")]}]
        self.collect()
        result = self.run_collector("--print", "today", "php-fpm")
        t = self.today.isoformat()
        self.assertEqual(result.stdout, f"==> php-fpm/access-{t}.log <==\nrequest\n==> php-fpm/error-{t}.log <==\nwarning\n")
        self.assertIn("proxy line", self.run_collector("--print", t).stdout)
        missing = self.run_collector("--print", "2001-01-01", "php-fpm")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("No logs of php-fpm on 2001-01-01", missing.stderr)
        for day, service in (("../etc", "php-fpm"), (t, "../x"), (t, "PHP")):
            self.assertEqual(self.run_collector("--print", day, service).returncode, 64, (day, service))


@unittest.skipUnless(PHP, "Set PHP_TEST_IMAGE to a built setup PHP image")
class LogCollectorIntegrationTest(unittest.TestCase):
    """Real containers on the real socket: a container recreated between two reads loses none of its lines."""

    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=120, check=check)

    def test_lines_of_a_removed_container_stay_after_it_is_gone(self):
        project = "setup-logs-" + uuid.uuid4().hex[:8]
        volume = project + "-logs"
        self.docker("volume", "create", volume)
        self.addCleanup(self.docker, "volume", "rm", "-f", volume, check=False)
        first, second, collector = project + "-a", project + "-b", project + "-collector"
        self.addCleanup(self.docker, "rm", "-f", first, second, collector, check=False)
        labels = ["--label", f"com.docker.compose.project={project}", "--label", "com.docker.compose.service=php-fpm"]
        self.docker("run", "--name", first, *labels, "--network", "none", "--entrypoint", "sh", PHP, "-c",
                    "echo request-one; echo warning-one >&2")
        def read():
            return self.docker("run", "--rm", "--name", collector, "--network", "none", "--user", "0:0", "--read-only",
                               "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                               "-v", "/var/run/docker.sock:/var/run/docker.sock:ro", "-v", f"{volume}:/var/lib/setup-logs",
                               "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-e", f"METRICS_DOCKER_PROJECT={project}",
                               "-e", "TZ=UTC", "--entrypoint", "php", PHP, "/opt/setup/runtime/metrics/log-collector.php", "--once")
        read()
        self.docker("rm", "-f", first)
        self.docker("run", "--name", second, *labels, "--network", "none", "--entrypoint", "sh", PHP, "-c", "echo request-two")
        read()
        printed = self.docker("run", "--rm", "--network", "none", "-v", f"{volume}:/var/lib/setup-logs:ro",
                              "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-e", "TZ=UTC", "--entrypoint", "php",
                              PHP, "/opt/setup/runtime/metrics/log-collector.php", "--print", "today", "php-fpm").stdout
        self.assertIn("access-", printed)
        self.assertEqual([line for line in printed.splitlines() if not line.startswith("==>")],
                         ["request-one", "request-two", "warning-one"])


if __name__ == "__main__":
    unittest.main()
