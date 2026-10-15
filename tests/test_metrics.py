"""Target /metrics: token and allowlist, system, website, http and security groups, Grafana name compatibility;
B: real host scrape and real nginx-proxy log lines."""

import collections
import contextlib
import datetime
import gzip
import http.server
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from create_project import scaffold
import grafana_names
from project import Project
from project_release import release_model

ENDPOINT = ROOT / "docker/runtime/metrics/index.php"
PROC = Path(__file__).resolve().parent / "fixtures/metrics-proc"
DATABASE_FIXTURE = Path(__file__).resolve().parent / "fixtures/metrics-database.json"
# The log collector's day files the HTTP group reads (nginx-proxy access, webserver and php-fpm errors).
LOGS = Path(__file__).resolve().parent / "fixtures/metrics-logs"
# The fixture logs' clock: 2026-10-09 12:34:30 in Europe/Vilnius, so the 5-minute window is [12:29:00, 12:34:00).
HTTP_NOW = "1791538470"
TOKEN = "m" * 32
# Every group the target serves: the Grafana names' groups plus containers and cron (no dashboard yet).
IMPLEMENTED = ("system", "website", "http", "security", "database", "host", "containers", "cron")
REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n'
          'DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test-only\n')
SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})? (\S+)$')


def parse(text):
    """{name: {labels-string: value}} from an exposition; comments must describe the metric that follows."""
    samples = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = SAMPLE.match(line)
        if not match:
            raise AssertionError(f"Not an exposition line: {line!r}")
        samples.setdefault(match[1], {})[match[2] or ""] = float(match[3])
    return samples


METRICS_HOST = "metrics.demo.local"


SAMPLER = ROOT / "docker/runtime/metrics/sampler.php"
# The environment of each test server's sampler: fetch() samples before it scrapes, as the service's minute loop
# would have, so a fixture changed between two scrapes shows in the second.
SAMPLERS = {}


def sample(sampler):
    """One round of website probes and one sample, as the sampler's two loops write them."""
    php, environment = sampler
    for args in (["--probes", "--once"], ["--once"]):
        result = subprocess.run(["php", *php, str(SAMPLER), *args], env=environment, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(f"sampler {args}: {result.stderr}")


def fetch(base, path="/metrics", token=None, fresh=True):
    """One request; /metrics of a test server after a fresh sample unless fresh=False."""
    if fresh and path == "/metrics" and base in SAMPLERS:
        sample(SAMPLERS[base])
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=60) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read().decode()


def tab_rows(page, title):
    """The rows of the dashboard table titled title, each a list of its cells' text."""
    import html as markup
    section = re.search(r"<h2>" + re.escape(title) + r"</h2>(.*?)</section>", page, re.S)
    if not section or "<tbody>" not in section[1]:
        return []
    body = section[1].split("<tbody>", 1)[1]
    return [[markup.unescape(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            for row in re.findall(r"<tr>(.*?)</tr>", body, re.S)]


def tcp_table(rows, ipv6=False):
    """A /proc/net/tcp(6) table: rows of (local address, local port, remote address, remote port, state hex)."""
    def address(text):
        packed = socket.inet_pton(socket.AF_INET6 if ipv6 else socket.AF_INET, text)
        # The kernel prints each 32-bit word in host (little-endian) byte order.
        return b"".join(packed[i:i + 4][::-1] for i in range(0, len(packed), 4)).hex().upper()
    lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"]
    for number, (local, lport, remote, rport, state) in enumerate(rows):
        lines.append(f"{number:4}: {address(local)}:{lport:04X} {address(remote)}:{rport:04X} {state} "
                     "00000000:00000000 00:00000000 00000000   101        0 1234 1 0000000000000000 20 4 30 10 -1")
    return "\n".join(lines) + "\n"


def write_connections(proc, proxy):
    """Host and nginx-proxy TCP tables: listeners, loopback, the proxy's gateway and other ports are not counted."""
    host = [("0.0.0.0", 80, "0.0.0.0", 0, "0A")] + [("10.0.0.2", 443, "203.0.113.50", 40000 + i, "01") for i in range(3)]
    host += [("10.0.0.2", 80, "203.0.113.51", 41000, "06"), ("127.0.0.1", 80, "127.0.0.1", 42000, "01"),
             ("127.0.0.1", 80, "127.0.0.1", 42001, "01"), ("10.0.0.2", 45000, "93.184.216.34", 80, "01")]
    host += [("10.0.0.2", 22, "203.0.113.52", 43000 + i, "01") for i in range(4)]
    host6 = [("::", 443, "::", 0, "0A"), ("::ffff:10.0.0.2", 443, "::ffff:203.0.113.50", 44000, "01"),
             ("2001:db8::1", 80, "2001:db8::7", 44001, "01"), ("2001:db8::1", 80, "2001:db8::7", 44002, "08"),
             ("::1", 80, "::1", 44003, "01")]
    (proc / "1/net/tcp").write_text(tcp_table(host))
    (proc / "1/net/tcp6").write_text(tcp_table(host6, ipv6=True))
    published = [("172.20.0.5", 80, "198.51.100.60", 45000 + i, "01") for i in range(5)]
    published += [("172.20.0.5", 80, "172.20.0.1", 46000 + i, "01") for i in range(6)]
    published += [("172.20.0.5", 443, "198.51.100.61", 47000, "01"), ("172.20.0.5", 443, "198.51.100.62", 47001, "01")]
    proxy.mkdir(parents=True, exist_ok=True)
    (proxy / "tcp").write_text(tcp_table(published))
    (proxy / "tcp6").write_text(tcp_table([], ipv6=True))
    (proxy / "route").write_text("Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
                                 "eth0\t00000000\t010014AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
                                 "eth0\t000014AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n")


UPTIME = 123456.78  # fixtures/metrics-proc/uptime
HERTZ, PAGE = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGESIZE")


def write_process(proc, pid, name, argv, cpu_seconds, age, rss_kb):
    """/proc/<pid>/stat and cmdline: CPU time over the process's age and its resident memory, as ps reads them."""
    directory = proc / str(pid)
    directory.mkdir(parents=True, exist_ok=True)
    start = int((UPTIME - age) * HERTZ)
    utime = int(cpu_seconds * HERTZ * 0.8)
    stime = int(cpu_seconds * HERTZ) - utime
    (directory / "stat").write_text(f"{pid} ({name}) S 1 {pid} {pid} 0 -1 4194560 100 0 0 0 {utime} {stime} 0 0 20 0 1 0 "
                                    f"{start} 100000000 {rss_kb * 1024 // PAGE} 18446744073709551615 1 1 0 0 0 0 0 0 0 0 0 0 0 17 3 0 0\n")
    (directory / "cmdline").write_bytes(b"".join(arg.encode() + b"\0" for arg in argv))


def write_host(proc):
    """Processes (MemTotal 16000000 kB), disks and the kernel release of the fixture host."""
    write_process(proc, 2, "kthreadd", [], 0, UPTIME - 1, 0)
    write_process(proc, 100, "mysqld", ["/usr/sbin/mysqld", "--defaults-file=/etc/mysql/my.cnf"], 50000, 100000, 1600000)
    for pid in (200, 201):
        write_process(proc, pid, "php-fpm8.4", ["php-fpm: pool www"], 100, 1000, 160000)
    # The runner gets its OAuth secret as arguments (Atlassian's design): only argv[0] may be shown.
    write_process(proc, 300, "java", ["/usr/bin/java", "-jar", "runner.jar", "--secret", "oauth-secret-value"], 100, 10000, 3200000)
    write_process(proc, 400, "odd) (name", ["sh", "-c", "sleep 1"], 0, 50, 4000)
    (proc / "sys/kernel").mkdir(parents=True, exist_ok=True)
    (proc / "sys/kernel/osrelease").write_text("6.8.0-45-generic\n")
    rows = [("8 0 sda", 1500), ("8 1 sda1", 1400), ("7 0 loop0", 900), ("43 0 nbd0", 800), ("259 0 nvme0n1", 2500), ("259 1 nvme0n1p1", 2400),
            ("253 0 dm-0", 3000)]
    (proc / "diskstats").write_text("".join(f"   {device} 100 0 2000 50 200 0 4000 100 0 {ticks} 150 0 0 0 0 0 0\n"
                                            for device, ticks in rows))


def docker_container(name, service, state="running", health="healthy", restarts=0, exit_code=0, oom_killed=False):
    return {"name": name, "service": service, "image": f"demo-{service}", "state": state, "health": health,
            "restarts": restarts, "exit_code": exit_code, "oom_killed": oom_killed, "started": 1791530000, "finished": 0}


def write_docker_state(directory, collected=None, **state):
    """The Docker collector's snapshot as the metrics service reads it."""
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = {"collected": int(time.time()) if collected is None else collected, "project": "demo-stage",
                "server": {"os": "Ubuntu 22.04.5 LTS", "docker": "28.5.1"},
                "containers": [docker_container("demo-stage-php-fpm", "php-fpm")], "oom_kills": {},
                "events_until": int(time.time()), **state}
    (directory / "state.json").write_text(json.dumps(snapshot))


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Website(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/slow":
            time.sleep(3)
        self.send_response(204 if self.path == "/empty" else 200)
        self.end_headers()

    def log_message(self, *args):
        pass


def serve(test, certificate=None):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Website)
    if certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate / "domain.crt", certificate / "domain.key")
        server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server.server_address[1]


class EndpointMixin:
    """The endpoint over PHP's built-in server, reading a copy of the fixture /proc."""

    def prepare(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.proc = self.work / "proc"
        shutil.copytree(PROC, self.proc)
        (self.work / "disk").mkdir()
        (self.work / "state").mkdir()
        (self.work / "snapshots").mkdir()
        # The HTTP log volume holds the proxy's TCP snapshot; the log collector's volume the day files.
        self.http = self.work / "http"
        self.http.mkdir()
        self.logs = self.work / "logs"
        shutil.copytree(LOGS, self.logs, copy_function=shutil.copy)
        write_connections(self.proc, self.http / "proxy-net")
        write_host(self.proc)
        self.docker_state = self.work / "docker"
        write_docker_state(self.docker_state)

    def start(self, php=(), **env):
        port = free_port()
        settings = {"METRICS_TOKEN": TOKEN, "METRICS_PROC_ROOT": str(self.proc), "METRICS_DISK_PATH": str(self.work / "disk"),
                    "METRICS_STATE_DIR": str(self.work / "state"), "METRICS_SAMPLE_SECONDS": "0.1",
                    "METRICS_DISK_WRITE_MB": "1", "METRICS_URLS": "", "METRICS_SUPPORT_LEVEL": "2",
                    "METRICS_HTTP_LOG_DIR": str(self.http), "METRICS_LOGS_DIR": str(self.logs), "DOMAIN": "demo.local",
                    "METRICS_HTTP_NOW": HTTP_NOW, "TZ": "Europe/Vilnius",
                    "METRICS_DOCKER_DIR": str(self.docker_state), "METRICS_SNAPSHOT_DIR": str(self.work / "snapshots"),
                    # The fixture logs' 5-minute window.
                    "METRICS_HTTP_WINDOW_MINUTES": "5", **env}
        settings.setdefault("METRICS_DATABASE_FIXTURE", str(DATABASE_FIXTURE))
        server = subprocess.Popen(["php", *php, "-S", f"127.0.0.1:{port}", str(ENDPOINT)], env={**os.environ, **settings},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # The sampler collects now, with the same PHP options (php=("-d", "memory_limit=16M") limits it).
        SAMPLERS[f"http://127.0.0.1:{port}"] = (tuple(php), {**os.environ, **settings})
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                fetch(base, "/")
                return base
            except OSError:
                time.sleep(0.1)
        self.fail("endpoint did not start")


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class EndpointTest(EndpointMixin, unittest.TestCase):
    def setUp(self):
        self.prepare()

    def test_a_scrape_serves_the_snapshot_and_collects_nothing(self):
        base = self.start(METRICS_SAMPLE_SECONDS="1")
        first = fetch(base, token=TOKEN)[2]
        # The host changes and two more scrapes follow before the next sample: both serve the same snapshot, at once.
        (self.proc / "loadavg").write_text("9.50 9.40 9.30 1/200 4242\n")
        started = time.monotonic()
        second, third = (fetch(base, token=TOKEN, fresh=False)[2] for _ in range(2))
        self.assertLess(time.monotonic() - started, 0.9, "a scrape does not sample (METRICS_SAMPLE_SECONDS is 1 s)")
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertIn("uptime_average_load_1min 0.5\n", second)
        snapshot = json.loads((self.work / "snapshots/snapshot.json").read_text())
        self.assertIn(f"setup_metrics_snapshot_timestamp_seconds {snapshot['taken']}\n", second)
        # The next sample takes the new figure.
        self.assertIn("uptime_average_load_1min 9.5\n", fetch(base, token=TOKEN)[2])

    def test_a_stopped_sampler_turns_every_group_down_after_three_intervals(self):
        base = self.start(METRICS_SAMPLER_INTERVAL="1")
        self.assertEqual(parse(fetch(base, token=TOKEN)[2])["setup_metrics_group_up"]['{group="system"}'], 1)
        # Whole seconds on both sides: 4.5 s is past three intervals however the second boundaries fall.
        time.sleep(4.5)
        metrics = parse(fetch(base, token=TOKEN, fresh=False)[2])
        self.assertEqual(set(metrics["setup_metrics_group_up"].values()), {0})
        self.assertEqual(len(metrics["setup_metrics_group_up"]), len(IMPLEMENTED) + 1)
        self.assertNotIn("ram_total", metrics)
        self.assertIn("setup_metrics_snapshot_timestamp_seconds", metrics)
        # Never sampled: the same, without a snapshot time.
        (self.work / "snapshots/snapshot.json").unlink()
        metrics = parse(fetch(base, token=TOKEN, fresh=False)[2])
        self.assertEqual(set(metrics["setup_metrics_group_up"].values()), {0})
        self.assertNotIn("setup_metrics_snapshot_timestamp_seconds", metrics)

    def test_the_history_keeps_small_families_for_48_hours(self):
        history = self.work / "snapshots/history"
        history.mkdir()
        now = time.time()
        hour = lambda hours_ago: time.strftime("%Y-%m-%d-%H", time.gmtime(now - hours_ago * 3600))
        (history / f"{hour(50)}.jsonl").write_text("{}\n")
        (history / f"{hour(47)}.jsonl").write_text("{}\n")
        fetch(self.start(), token=TOKEN)
        self.assertEqual(sorted(p.name for p in history.iterdir()), sorted([f"{hour(47)}.jsonl", f"{hour(0)}.jsonl"]))
        line = json.loads((history / f"{hour(0)}.jsonl").read_text().splitlines()[-1])
        self.assertAlmostEqual(line["t"], now, delta=30)
        self.assertEqual(line["v"]["uptime_average_load_1min"], 0.5)
        self.assertEqual(line["v"]['setup_metrics_group_up{group="system"}'], 1)
        # Per-client lists stay out: http_request_ips has more samples than the history keeps.
        self.assertFalse([key for key in line["v"] if key.startswith("http_request_ips")])

    def test_a_short_token_refuses_to_serve(self):
        base = self.start(METRICS_TOKEN="short")
        self.assertEqual(fetch(base, token="short")[0], 503)

    def test_without_a_token_the_ip_allowlist_alone_guards_it(self):
        base = self.start(METRICS_TOKEN="")
        self.assertEqual(fetch(base, "/other")[0], 404)
        status, _, body = fetch(base)
        self.assertEqual(status, 200, body)

    def test_the_bearer_token_guards_metrics_and_not_the_pages_people_open(self):
        base = self.start()
        self.assertEqual(fetch(base)[0], 401)
        self.assertEqual(fetch(base, token="wrong" * 8)[0], 401)
        self.assertEqual(fetch(base, token=TOKEN.upper())[0], 401)
        self.assertEqual(fetch(base, "/other", TOKEN)[0], 404)
        # nginx-proxy guards the pages with METRICS_HTPASSWD, whose Basic-auth takes the Authorization header.
        status, kind, body = fetch(base, "/")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"), body)
        self.assertIn('href="/metrics"', body)
        status, kind, body = fetch(base, token=TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(kind, "text/plain; version=0.0.4; charset=utf-8")

    def test_system_group_uses_the_monitoring_script_names_and_units(self):
        base = self.start(METRICS_SAMPLE_SECONDS="1")
        result = {}
        request = threading.Thread(target=lambda: result.update(response=fetch(base, token=TOKEN)))
        request.start()
        time.sleep(0.4)
        # The service samples CPU and network over the interval, as mpstat 1 1 / sar -n DEV 1 1 do.
        (self.proc / "stat").write_text(
            "cpu  1060 100 520 8100 210 0 110 50 0 0\ncpu0 560 50 270 4100 110 0 60 25 0 0\n"
            "cpu1 600 50 250 4100 100 0 50 25 0 0\nbtime 1700000000\n")
        dev = (self.proc / "1/net/dev").read_text().replace(
            "eth0: 2048000    1000", "eth0: 2150400    1100").replace("1024000     500", "1075200     550")
        (self.proc / "1/net/dev").write_text(dev)
        request.join(30)
        status, _, body = result["response"]
        self.assertEqual(status, 200, body)
        metrics = parse(body)
        cpu = {name: metrics[f"cpu_usage_{name}"] for name in ("usr", "sys", "idle", "iowait", "soft", "nice")}
        self.assertEqual(cpu["usr"], {'{cpu="all"}': 30.0, '{cpu="0"}': 30.0, '{cpu="1"}': 50.0})
        self.assertEqual((cpu["sys"]['{cpu="all"}'], cpu["idle"]['{cpu="all"}'], cpu["iowait"]['{cpu="all"}'],
                          cpu["soft"]['{cpu="all"}'], cpu["nice"]['{cpu="all"}']), (10.0, 50.0, 5.0, 5.0, 0.0))
        # free(1) in KiB: used = total - free - buffers - cache (Cached + SReclaimable).
        self.assertEqual({name: metrics[f"ram_{name}"][""] for name in ("total", "used", "free", "shared", "buff_cache", "available")},
                         {"total": 16000000, "used": 8100000, "free": 1000000, "shared": 300000, "buff_cache": 6900000,
                          "available": 9000000})
        # iostat -x avg-cpu: since boot, under the dashboard's own names.
        self.assertEqual({name: metrics[name][""] for name in ("iousage_usr", "ip_usage_nice", "io_usage_sys", "io_usage_iowait",
                                                               "io_usage_steal", "io_usage_idle")},
                         {"iousage_usr": 10.44, "ip_usage_nice": 0.99, "io_usage_sys": 6.21, "io_usage_iowait": 2.07,
                          "io_usage_steal": 0.49, "io_usage_idle": 79.8})
        # sar -n DEV of the default-route interface: packets and KiB per second.
        for name, expected in (("rxpck", 100), ("txpck", 50), ("rxkb", 100), ("txkb", 50)):
            self.assertAlmostEqual(metrics[f"network_{name}"][""], expected, delta=expected * 0.4)
        self.assertEqual(metrics["uptime"][""], 123456)
        self.assertEqual((metrics["uptime_average_load_1min"][""], metrics["uptime_average_load_10min"][""],
                          metrics["uptime_average_load_15min"][""]), (0.5, 0.4, 0.3))
        self.assertAlmostEqual(metrics["uptime_current_time"][""], time.time(), delta=60)
        # df -k / df -i of the project's data filesystem; *_available_percentage is df's Use% column.
        df = subprocess.run(["df", "-P", "-k", str(self.work / "disk")], capture_output=True, text=True).stdout.splitlines()[1].split()
        self.assertAlmostEqual(metrics["disk_size"][""], int(df[1]), delta=int(df[1]) * 0.001)
        self.assertAlmostEqual(metrics["disk_used"][""], int(df[2]), delta=int(df[1]) * 0.01)
        self.assertAlmostEqual(metrics["disk_available_percentage"][""], int(df[4].rstrip("%")), delta=1)
        inodes = subprocess.run(["df", "-P", "-i", str(self.work / "disk")], capture_output=True, text=True).stdout.splitlines()[1].split()
        if inodes[1] != "0":
            self.assertEqual(metrics["disk_inodes_total"][""], int(inodes[1]))
        self.assertGreater(metrics["disk_write_speed"][""], 0)
        self.assertEqual(metrics["support_level"][""], 2)

    def test_network_interface_setting_and_missing_host_files(self):
        base = self.start(METRICS_NETWORK_INTERFACE="eth1")
        self.assertIn("network_rxkb 0\n", fetch(base, token=TOKEN)[2])
        (self.proc / "meminfo").unlink()
        body = fetch(base, token=TOKEN)[2]
        self.assertIn('setup_metrics_group_up{group="system"} 0', body)
        self.assertIn('setup_metrics_group_up{group="website"} 1', body)
        self.assertNotIn("ram_total", body)

    def test_website_group_probes_each_url_through_the_probe_address(self):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj", "/CN=shop.invalid",
                        "-keyout", str(self.work / "domain.key"), "-out", str(self.work / "domain.crt")],
                       capture_output=True, check=True, timeout=30)
        plain, tls = serve(self), serve(self, self.work)
        closed = free_port()
        urls = f"http://shop.invalid:{plain}/ http://shop.invalid:{plain}/empty https://shop.invalid:{tls}/ http://shop.invalid:{closed}/"
        base = self.start(METRICS_URLS=urls, METRICS_PROBE_ADDRESS="127.0.0.1")
        metrics = parse(fetch(base, token=TOKEN)[2])
        status = metrics["website_response_status"]
        self.assertEqual(status, {f'{{url="http://shop.invalid:{plain}/"}}': 200, f'{{url="http://shop.invalid:{plain}/empty"}}': 204,
                                  f'{{url="https://shop.invalid:{tls}/"}}': 0, f'{{url="http://shop.invalid:{closed}/"}}': 0})
        self.assertEqual(set(metrics["website_response_time"]), set(status))
        # The certificate date is read even when it does not verify (self-signed here).
        expiry = metrics["website_ssl_expiry_timestamp_seconds"]
        self.assertEqual(list(expiry), [f'{{domain="shop.invalid:{tls}"}}'])
        self.assertAlmostEqual(list(expiry.values())[0], time.time() + 30 * 86400, delta=86400)

    def test_a_page_over_the_probe_timeout_counts_as_the_timeout_and_never_delays_the_sample(self):
        slow = serve(self)
        url = f"http://127.0.0.1:{slow}/slow"
        base = self.start(METRICS_URLS=url, METRICS_PROBE_TIMEOUT="1")
        php, environment = SAMPLERS[base]
        started = time.monotonic()
        subprocess.run(["php", str(SAMPLER), "--once"], env=environment, check=True, timeout=60, capture_output=True)
        self.assertLess(time.monotonic() - started, 1.5, "the minute's sample does not probe the pages")
        metrics = parse(fetch(base, token=TOKEN)[2])
        self.assertEqual((metrics["website_response_time"][f'{{url="{url}"}}'], metrics["website_response_status"][f'{{url="{url}"}}']),
                         (1, 0))
        self.assertAlmostEqual(metrics["setup_website_probe_timestamp_seconds"][""], time.time(), delta=30)
        # Probes older than three intervals: the website group is down and its pages are not served.
        probes = self.work / "snapshots/probes.json"
        data = json.loads(probes.read_text())
        data["probed"] -= 901
        probes.write_text(json.dumps(data))
        subprocess.run(["php", str(SAMPLER), "--once"], env=environment, check=True, timeout=60, capture_output=True)
        metrics = parse(fetch(base, token=TOKEN, fresh=False)[2])
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="website"}'], 0)
        self.assertNotIn("website_response_status", metrics)

    def test_without_urls_the_website_group_still_reports_the_support_level(self):
        body = fetch(self.start(METRICS_SUPPORT_LEVEL="0"), token=TOKEN)[2]
        self.assertIn("support_level 0\n", body)
        self.assertNotIn("website_response_status{", body)


# The old monitoring script's results.txt headers in its order (monitoring.sh's write_stats_file calls); /feed keeps them.
MONITORING_HEADERS = [
    "Uptime", "Disk Space Usage", "Inode Usage", "RAM Usage", "I/O Statistics", "CPU Usage", "Network Usage",
    "Website Response Time", "SSL Expiry Date", "Active Connections", "HTTP response codes", "HTTP requests per minute",
    "HTTP bytes transferred", "HTTP top IP addresses", "HTTP top User agents", "HTTP error type counts", "SQL capacity monitoring",
    "Top Slow SQL Statements (by Average Execution Time)", "Top Table scan Statements", "Queries by Total Time Consumption",
    "Most Accessed Tables (I/O)", "Table Lock Contention", "File I/O Hotspots", "Current Running Statements", "CPU-Heavy Users",
    "Top IPs with Most Connections (DDoS Check)", "Disk Write Speed", "Network Latency (Ping to 8.8.8.8)",
    "Top CPU-consuming Processes", "Top RAM-consuming Processes"]


def feed_sections(text):
    """[(header, body)] of results.txt-style text: "Header:", the body, a line of dashes."""
    sections = []
    for block in text.split("---------------------------------\n"):
        if block.strip():
            header, _, body = block.partition(":\n")
            sections.append((header, body.rstrip("\n")))
    return sections


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class FeedTest(EndpointMixin, unittest.TestCase):
    """/feed: the snapshot and the last 24 hours' traffic as results.txt sections, guarded like /metrics."""

    def setUp(self):
        self.prepare()

    def test_the_scripts_sections_then_the_traffic(self):
        plain = serve(self)
        url = f"http://127.0.0.1:{plain}/"
        # A request of the last hour in today's file, for the traffic sections.
        moment = datetime.datetime.now(ZoneInfo("Europe/Vilnius")) - datetime.timedelta(minutes=10)
        today = self.logs / f"nginx-proxy/access-{moment:%Y-%m-%d}.log"
        today.write_text(f'203.0.113.9 - - [{moment:%d/%b/%Y:%H:%M:%S %z}] "GET /.env HTTP/1.1" 404 10 "-" "curl/8" rt=0.010 '
                         'urt=0.010 host=demo.local\n')
        base = self.start(METRICS_URLS=url)
        fetch(base, token=TOKEN)
        status, kind, text = fetch(base, "/feed", TOKEN)
        self.assertEqual((status, kind), (200, "text/plain; charset=utf-8"), text)
        sections = feed_sections(text)
        headers = [header for header, _ in sections]
        self.assertEqual(headers[0], "Snapshot")
        script = [header for header in headers if not header.startswith("Traffic") and header != "Snapshot"]
        self.assertEqual(script, MONITORING_HEADERS)
        body = dict(sections)
        self.assertEqual(body["Website Response Time"], f"Url: {url}\nResponse Time: {body['Website Response Time'].split('Response Time: ')[1].split('s')[0]}s\nStatus Code: 200")
        self.assertIn("uptime_average_load_1min 0.5", body["Uptime"])
        self.assertIn('http_response_codes{code="200"} 5', body["HTTP response codes"])
        self.assertEqual(body["File I/O Hotspots"], "not collected by setup")
        # The traffic of the last 24 hours after the script's sections.
        self.assertEqual([h for h in headers if h.startswith("Traffic")][:3], ["Traffic window", "Traffic classes", "Traffic bot requests"])
        self.assertIn("probe 1", body["Traffic attacks"])
        self.assertIn("203.0.113.9", body["Traffic suspicious IPs"])

    def test_the_token_guards_it_like_metrics(self):
        base = self.start()
        self.assertEqual(fetch(base, "/feed")[0], 401)
        self.assertEqual(fetch(base, "/feed", "wrong" * 8)[0], 401)
        self.assertEqual(fetch(base, "/feed", TOKEN)[0], 200)
        # Before the first sample it says so.
        (self.work / "snapshots/snapshot.json").unlink(missing_ok=True)
        self.assertIn("none yet", fetch(base, "/feed", TOKEN)[2])


MOZILLA = "Mozilla/5.0 (X11; Linux x86_64)"


def labelled(metrics, name, label):
    """{label value: sample value} of one family with a single label."""
    pattern = re.compile(r'^\{' + label + r'="((?:[^"\\]|\\.)*)"\}$')
    return {pattern.match(key)[1]: value for key, value in metrics.get(name, {}).items()}


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class HttpGroupTest(EndpointMixin, unittest.TestCase):
    """The old script's access and error log figures over the last full minutes, read from the end of the logs."""

    def setUp(self):
        self.prepare()

    def scrape(self, **env):
        status, _, body = fetch(self.start(**env), token=TOKEN)
        self.assertEqual(status, 200, body)
        return parse(body)

    def test_access_log_counts_over_the_last_full_minutes(self):
        # In the window: 12:29:00 (start), a late-written 12:29:01 behind a 12:28:59 line, a +0000 line, lines with
        # and without the timings and 12:33:59; outside: 12:28:59, 12:34:00 (current minute), the unfinished last
        # line and a scrape of metrics.demo.local (a tool site of the same proxy, not site traffic).
        metrics = self.scrape()
        self.assertEqual(labelled(metrics, "http_response_codes", "code"), {"200": 5, "304": 1, "400": 1, "404": 1, "500": 1})
        self.assertEqual(metrics["http_bytes_transferred_total"], {"": 3968})
        self.assertEqual(labelled(metrics, "http_request_ips", "ip"),
                         {"198.51.100.1": 4, "198.51.100.2": 2, "198.51.100.3": 1, "198.51.100.4": 1, "2001:db8::5": 1})
        self.assertEqual(labelled(metrics, "http_request_user_agents", "user_agent"),
                         {MOZILLA: 5, "curl/8.5.0": 2, "-": 1, "Bot \\\\x22quoted\\\\x22 / 1.0": 1})
        # Requests per minute: 3, 2, 2, 1, 1 in 12:29 … 12:33.
        self.assertEqual((metrics["http_requests_per_minute"][""], metrics["http_requests_per_minute_max"][""]), (1.8, 3))
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 1)

    def test_error_types_count_each_entry_once_within_the_window(self):
        # The webserver's and php-fpm's error logs. Continuation lines belong to the entry above them: the one
        # before the window is not counted, the in-window fatal error (its time in UTC) with its stack trace
        # counts once; an older php-fpm's decorated line counts by the master's time.
        metrics = self.scrape()
        self.assertEqual(labelled(metrics, "http_error_type", "type"),
                         {"Access to context": 0, "Abort request processing": 0, "PHP Warning": 2, "PHP Fatal error": 1,
                          "Segmentation fault": 1, "Connection reset": 1})

    def test_window_setting_and_missing_logs(self):
        metrics = self.scrape(METRICS_HTTP_WINDOW_MINUTES="1")
        self.assertEqual(labelled(metrics, "http_response_codes", "code"), {"500": 1})
        self.assertEqual(metrics["http_requests_per_minute"][""], 1)
        self.assertEqual(labelled(metrics, "http_error_type", "type")["PHP Warning"], 1)
        # A day without errors has no error files: no errors.
        for log in self.logs.glob("*/error-*"):
            log.unlink()
        metrics = self.scrape()
        self.assertEqual(set(labelled(metrics, "http_error_type", "type").values()), {0})
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 1)
        # Nor requests without an access file.
        (self.logs / "nginx-proxy/access-2026-10-09.log").unlink()
        metrics = self.scrape()
        self.assertEqual(metrics["http_bytes_transferred_total"], {"": 0})
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 1)
        # Without the collector's volume (not mounted) the group is down and serves no request figures.
        shutil.rmtree(self.logs)
        metrics = self.scrape()
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 0)
        self.assertNotIn("http_response_codes", metrics)
        self.assertNotIn("http_bytes_transferred_total", metrics)

    def test_window_across_midnight_reads_both_days(self):
        day = lambda name: self.logs / "nginx-proxy" / name
        day("access-2026-10-09.log").write_text(
            '198.51.100.1 - - [09/Oct/2026:23:59:30 +0300] "GET /evening HTTP/1.1" 200 10 "-" "a" rt=0.010 urt=0.009 host=demo.local\n')
        day("access-2026-10-10.log").write_text(
            '198.51.100.2 - - [10/Oct/2026:00:00:30 +0300] "GET /night HTTP/1.1" 200 20 "-" "a" rt=0.010 urt=0.009 host=demo.local\n')
        # Gzipped days are past every window: not read.
        with gzip.open(self.logs / "nginx-proxy/access-2026-10-08.log.gz", "wt") as old:
            old.write('198.51.100.3 - - [09/Oct/2026:23:59:40 +0300] "GET /x HTTP/1.1" 200 40 "-" "a"\n')
        # 2026-10-10 00:01:10 in Europe/Vilnius: the window is [23:59:00, 00:01:00).
        metrics = self.scrape(METRICS_HTTP_NOW="1791579670", METRICS_HTTP_WINDOW_MINUTES="2")
        self.assertEqual(labelled(metrics, "http_request_ips", "ip"), {"198.51.100.1": 1, "198.51.100.2": 1})
        self.assertEqual(metrics["http_bytes_transferred_total"], {"": 30})

    def test_a_large_log_is_read_from_its_end_within_a_small_memory_limit(self):
        old = ('203.0.113.9 - - [09/Oct/2026:11:{:02d}:00 +0300] "GET /old/{:06d} HTTP/1.1" 200 5000 "-" "' + MOZILLA + '"\n')
        with (self.logs / "nginx-proxy/access-2026-10-09.log").open("w") as log:
            for number in range(250000):
                log.write(old.format(number * 60 // 250000, number))
            # One 3 MiB line without a timestamp, then about 20 MiB in the window: 12 clients with 600 … 7200
            # requests per minute, more than the memory limit would hold as lines.
            log.write("x" * (3 << 20) + "\n")
            for minute in range(29, 34):
                for client in range(1, 13):
                    for _ in range(client * 600):
                        log.write(f'198.51.100.{client} - - [09/Oct/2026:12:{minute}:30 +0300] "GET / HTTP/1.1" 200 10 "-" "agent-{client}"\n')
        self.assertGreater((self.logs / "nginx-proxy/access-2026-10-09.log").stat().st_size, 40 << 20)
        metrics = self.scrape(php=("-d", "memory_limit=16M"))
        total = sum(client * 600 * 5 for client in range(1, 13))
        self.assertEqual(labelled(metrics, "http_response_codes", "code"), {"200": total})
        self.assertEqual(metrics["http_bytes_transferred_total"], {"": total * 10})
        # The script's head -10: the ten busiest clients and user agents.
        self.assertEqual(labelled(metrics, "http_request_ips", "ip"), {f"198.51.100.{c}": c * 3000 for c in range(3, 13)})
        self.assertEqual(labelled(metrics, "http_request_user_agents", "user_agent"), {f"agent-{c}": c * 3000 for c in range(3, 13)})
        self.assertEqual(metrics["http_requests_per_minute_max"][""], total / 5)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class SecurityGroupTest(EndpointMixin, unittest.TestCase):
    """The script's netstat DDoS check: the five peers with the most connections to the web ports."""

    def setUp(self):
        self.prepare()

    def test_most_connected_peers_from_the_host_and_the_proxy(self):
        # Host: 203.0.113.50 ×3 on 443 and once over IPv4-mapped IPv6, 2001:db8::7 ×2, 203.0.113.51 ×1 (TIME_WAIT);
        # listeners, loopback, SSH and outgoing :80 are not counted. Proxy: 198.51.100.60 ×5, its gateway (the
        # host relaying local requests) is not counted, .61 and .62 ×1. Ties go by address, five are kept.
        metrics = parse(fetch(self.start(), token=TOKEN)[2])
        self.assertEqual(labelled(metrics, "ddos_most_connections_ip_count", "ip"),
                         {"198.51.100.60": 5, "203.0.113.50": 4, "2001:db8::7": 2, "198.51.100.61": 1, "198.51.100.62": 1})
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="security"}'], 1)

    def test_a_stale_or_missing_proxy_snapshot_keeps_the_host_counts_and_clears_up(self):
        stale = time.time() - 600
        for table in (self.http / "proxy-net").iterdir():
            os.utime(table, (stale, stale))
        base = self.start()
        metrics = parse(fetch(base, token=TOKEN)[2])
        self.assertEqual(labelled(metrics, "ddos_most_connections_ip_count", "ip"),
                         {"203.0.113.50": 4, "2001:db8::7": 2, "203.0.113.51": 1})
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="security"}'], 0)
        shutil.rmtree(self.http / "proxy-net")
        metrics = parse(fetch(base, token=TOKEN)[2])
        self.assertEqual(len(metrics["ddos_most_connections_ip_count"]), 3)
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="security"}'], 0)


HTTP_LOGS_SCRIPT = ROOT / "docker/docker/nginx-proxy/conf/http-logs.sh"


@unittest.skipUnless(shutil.which("bash"), "bash is required")
class HttpLogsScriptTest(unittest.TestCase):
    """nginx-proxy's helper snapshots the proxy's TCP table and removes the log copies an older setup kept."""

    def test_connection_snapshot_and_old_copies_removed(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        work = Path(temporary.name)
        logs, net = work / "logs", work / "net"
        for directory in (logs, net, work / "1/net"):
            directory.mkdir(parents=True)
        write_connections(work, net)
        for name in ("access.log", "access.log.1", "error.log", "error.log.1"):
            (logs / name).write_text("old copy\n")
        env = {**os.environ, "SETUP_HTTP_LOGS": str(logs), "SETUP_HTTP_PROC_NET": str(net)}
        result = subprocess.run(["bash", str(HTTP_LOGS_SCRIPT), "once"], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({p.name for p in logs.iterdir()}, {"proxy-net"})
        self.assertEqual({p.name for p in (logs / "proxy-net").iterdir()}, {"tcp", "tcp6", "route"})
        self.assertEqual((logs / "proxy-net/tcp").read_text(), (net / "tcp").read_text())


class GrafanaCompatibilityTest(EndpointMixin, unittest.TestCase):
    """Every name the existing dashboards and alerts query is served once its group is implemented."""

    def test_every_dashboard_name_belongs_to_a_group(self):
        unknown = sorted(name for name in grafana_names.recorded() if grafana_names.group_of(name) is None)
        self.assertEqual(unknown, [], "classify new dashboard names in tests/grafana_names.py GROUPS")
        stale = sorted(set().union(*grafana_names.GROUPS.values()) - grafana_names.recorded())
        self.assertEqual(stale, [], "GROUPS lists names no dashboard or alert uses")

    @unittest.skipUnless(os.environ.get("SETUP_GRAFANA_SOURCE"), "Set SETUP_GRAFANA_SOURCE to a server_stats_viewer checkout")
    def test_recorded_names_match_the_current_dashboards_and_alerts(self):
        self.assertEqual(set(grafana_names.extract(os.environ["SETUP_GRAFANA_SOURCE"])), grafana_names.recorded())

    def test_name_extraction_skips_functions_labels_ranges_and_variables(self):
        expression = ('100 * (sum_over_time(orders_new{job="${job}_x", code!="5"}[1h] offset 7d) or on(job) avg by(job) '
                      '(cpu_usage{cpu="all"})) / clamp_max(ram_used > bool 0, 1) + up{job=~".+"}[$__range:5m]')
        self.assertEqual(grafana_names.names_in(expression), {"orders_new", "cpu_usage", "ram_used", "up"})

    @unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
    def test_implemented_groups_serve_every_dashboard_name(self):
        self.prepare()
        port = serve(self)
        base = self.start(METRICS_URLS=f"http://shop.invalid:{port}/", METRICS_PROBE_ADDRESS="127.0.0.1")
        served = set(parse(fetch(base, token=TOKEN)[2]))
        expected = set().union(*(grafana_names.GROUPS[group] for group in IMPLEMENTED if group in grafana_names.GROUPS))
        self.assertEqual(sorted(expected - served), [])
        # Every group is served: the full set of names the dashboards and alerts use (except shop, PageSpeed, up).
        self.assertLessEqual(set(grafana_names.GROUPS), set(IMPLEMENTED))
        self.assertEqual(sorted({n for n in grafana_names.recorded() if grafana_names.group_of(n) != "elsewhere"} - served), [])


PROMTOOL = os.environ.get("SETUP_PROMTOOL_IMAGE")


@unittest.skipUnless(shutil.which("php") and (shutil.which("promtool") or PROMTOOL),
                     "Install promtool or set SETUP_PROMTOOL_IMAGE (e.g. prom/prometheus:latest)")
class ExpositionFormatTest(EndpointMixin, unittest.TestCase):
    def test_promtool_accepts_the_exposition(self):
        self.prepare()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30", "-subj", "/CN=shop.invalid",
                        "-keyout", str(self.work / "domain.key"), "-out", str(self.work / "domain.crt")],
                       capture_output=True, check=True, timeout=30)
        plain, tls = serve(self), serve(self, self.work)
        # Every group, cron with a recorded run.
        (self.work / "cron").mkdir()
        (self.work / "cron/crontab").write_text("*/5 * * * * true\n")
        (self.work / "cron/history.jsonl").write_text('{"job":"5ffe533b830f","trigger":"scheduled","started":1791538470000,'
                                                      '"duration_ms":12,"exit":2,"log":"x.log"}\n')
        base = self.start(METRICS_URLS=f'http://shop.invalid:{plain}/?q="a\\b" https://shop.invalid:{tls}/',
                          METRICS_PROBE_ADDRESS="127.0.0.1", METRICS_CRON="1", METRICS_CRON_CONFIG=str(self.work / "cron"),
                          METRICS_CRON_HISTORY=str(self.work / "cron"))
        body = fetch(base, token=TOKEN)[2]
        self.assertIn('url="http://shop.invalid:%d/?q=\\"a\\\\b\\""' % plain, body)
        self.assertIn('setup_cron_job_last_exit_code{job="5ffe533b830f"} 2', body)
        command = ["promtool"] if shutil.which("promtool") else ["docker", "run", "--rm", "-i", "--entrypoint", "promtool", PROMTOOL]
        check = lambda *flags: subprocess.run(command + ["check", "metrics", *flags], input=body, capture_output=True,
                                              text=True, timeout=120)
        # Format: the exposition parses (cardinality report only, no naming lint).
        result = check("--extended", "--lint=none")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + body)
        # Naming lint: only the dashboards' own gauge names ending in _total or _count may be reported.
        kept = {f'{name} non-counter metrics should not have "_total" suffix'
                for name in grafana_names.recorded() | grafana_names.VIEWER_ONLY if name.endswith("_total")}
        kept |= {f'{name} non-histogram and non-summary metrics should not have "_count" suffix'
                 for name in grafana_names.recorded() if name.endswith("_count")}
        result = check()
        problems = set(filter(None, (result.stdout + result.stderr).splitlines()))
        self.assertEqual(problems - kept, set())
        self.assertEqual(result.returncode, 3 if problems else 0)


class SettingsTest(unittest.TestCase):
    def project(self, stage_env, services="metrics"):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            app = scaffold("metrics-demo", temporary.name)
        with (app / "env/common.env").open("a") as env:
            env.write(f"SERVICES={services}\n")
        (app / "env/stage.env").write_text(REMOTE + stage_env)
        return app

    def test_stage_requires_an_allowlist_and_a_token_is_optional(self):
        app = self.project("")
        with self.assertRaisesRegex(ValueError, "metrics on stage requires METRICS_ALLOWED_IPS or ALLOWED_IPS"):
            Project(app, "stage")
        with (app / "env/stage.env").open("a") as env:
            env.write("METRICS_ALLOWED_IPS=10.8.0.20\n")
        Project(app, "stage")
        with (app / "env/stage.env").open("a") as env:
            env.write("METRICS_TOKEN=" + "s" * 20 + "\n")
        with self.assertRaisesRegex(ValueError, "METRICS_TOKEN on stage must have 24\\+ characters"):
            Project(app, "stage")

    def test_compose_model_reads_the_host_read_only_and_stays_off_the_project_network(self):
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        project = Project(app, "stage")
        self.assertEqual(project.process_env["SETUP_ENABLE_METRICS"], "1")
        model = json.loads(project.capture(["config", "--format", "json"]))
        services = model["services"]
        metrics, proxy = services["metrics"], services["nginx-proxy"]
        self.assertNotIn("ports", metrics)
        self.assertEqual(list(metrics["networks"]), ["network_metrics"])
        self.assertIn("network_metrics", proxy["networks"])
        mounts = {volume["target"]: volume for volume in metrics["volumes"]}
        self.assertEqual((mounts["/host/proc"]["source"], mounts["/host/proc"]["read_only"]), ("/proc", True))
        self.assertEqual((mounts["/host/disk"]["source"], mounts["/host/disk"]["read_only"]), (str(project.data_directory), True))
        self.assertEqual(proxy["environment"]["METRICS_ALLOWED_IPS"], "10.8.0.20")
        self.assertEqual(proxy["environment"]["SETUP_ENABLE_METRICS"], "1")
        environment = metrics["environment"]
        self.assertEqual((environment["METRICS_URLS"], environment["METRICS_SUPPORT_LEVEL"], environment["METRICS_DISK_WRITE_MB"]),
                         ("https://demo.example.com/", "0", "100"))
        self.assertEqual(metrics["user"], f"{os.getuid()}:{os.getgid()}")
        self.assertEqual(environment["PHP_CLI_SERVER_WORKERS"], "4", "slow probes must not block scrapes")
        # The HTTP log volume: nginx-proxy writes its TCP snapshot there; the collector's volume holds the logs.
        # The metrics service only reads both, and nothing else writes them any more (no copies).
        logs = mounts["/host/http-logs"]
        self.assertEqual((logs["type"], logs["read_only"]), ("volume", True))
        shared = {volume["target"]: volume for volume in proxy["volumes"]}["/var/log/setup-http"]
        self.assertEqual((shared["type"], shared["source"], shared.get("read_only", False)), ("volume", logs["source"], False))
        collected = mounts["/host/logs"]
        self.assertEqual((collected["type"], collected["read_only"]), ("volume", True))
        self.assertEqual(model["volumes"][collected["source"]]["name"], f"{project.settings['PROJECT_NAME']}-stage-logs")
        for service in ("webserver", "php-fpm"):
            self.assertNotIn("/var/log/setup-http", {v["target"] for v in services[service].get("volumes", [])}, service)
            self.assertNotIn("SETUP_ENABLE_METRICS", services[service]["environment"], service)
        self.assertEqual((environment["DOMAIN"], environment["PROXY_TOOLS"]), ("demo.example.com", ""))
        self.assertEqual(model["volumes"][logs["source"]]["name"], f"{project.settings['PROJECT_NAME']}-stage-http-logs")
        # One minute, as often as the sampler samples and Prometheus scrapes.
        self.assertEqual(environment["METRICS_HTTP_WINDOW_MINUTES"], "1")
        # The sampler's directory, writable and kept with the project data; the entry point starts its loops.
        sampler = mounts["/var/lib/setup-metrics"]
        self.assertEqual((sampler["type"], sampler["source"], sampler.get("read_only", False)),
                         ("bind", str(project.data_directory / "metrics"), False))
        self.assertEqual(metrics["entrypoint"], ["sh", "/opt/setup/runtime/metrics/start.sh"])
        # The release keeps /proc (the same on every Linux target), maps the data directory and keeps the volume.
        release = release_model(project, "/srv/demo")
        released = release["services"]["metrics"]["volumes"]
        self.assertEqual({(v["source"], v["target"]) for v in released if v["type"] == "bind"},
                         {("/proc", "/host/proc"), ("/srv/demo/data/stage", "/host/disk"),
                          ("/srv/demo/data/stage/metrics", "/var/lib/setup-metrics"),
                          # What the last backup found about the table registry (database as code is on by default).
                          ("/srv/demo/data/stage/table-registry", "/host/table-registry")})
        self.assertIn((logs["source"], "/host/http-logs"), {(v["source"], v["target"]) for v in released if v["type"] == "volume"})
        self.assertEqual(release["volumes"], model["volumes"])

    def test_the_log_collector_has_the_collector_guards_and_its_own_volume(self):
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\nLOGS_RETENTION_DAYS=30\n")
        project = Project(app, "stage")
        model = json.loads(project.capture(["config", "--format", "json"]))
        logs = model["services"]["metrics-logs"]
        self.assertEqual((logs["network_mode"], logs["read_only"], logs["cap_drop"], logs["user"]), ("none", True, ["ALL"], "0:0"))
        self.assertIn("no-new-privileges:true", logs["security_opt"])
        self.assertNotIn("ports", logs)
        mounts = {volume["target"]: volume for volume in logs["volumes"]}
        socket = mounts["/var/run/docker.sock"]
        self.assertEqual((socket["source"], socket["read_only"]), ("/var/run/docker.sock", True))
        volume = mounts["/var/lib/setup-logs"]
        self.assertEqual((volume["type"], volume.get("read_only", False)), ("volume", False))
        self.assertEqual(model["volumes"][volume["source"]]["name"], f"{project.settings['PROJECT_NAME']}-stage-logs")
        environment = logs["environment"]
        self.assertEqual((environment["METRICS_DOCKER_PROJECT"], environment["LOGS_RETENTION_DAYS"], environment["LOGS_MAX_MB"]),
                         (project.name, "30", "2048"))
        release = release_model(project, "/srv/demo")
        self.assertIn(("/var/run/docker.sock", "/var/run/docker.sock"),
                      {(v["source"], v["target"]) for v in release["services"]["metrics-logs"]["volumes"] if v["type"] == "bind"})
        for setting, value, message in (("LOGS_RETENTION_DAYS", "0", "1 to 366"), ("LOGS_MAX_MB", "10", "64 to 1048576")):
            with (app / "env/stage.env").open("a") as env:
                env.write(f"{setting}={value}\n")
            with self.assertRaisesRegex(ValueError, message):
                Project(app, "stage")
            text = (app / "env/stage.env").read_text().replace(f"{setting}={value}\n", "")
            (app / "env/stage.env").write_text(text)

    def test_make_logs_day_reads_the_collector_files(self):
        from project_commands import logs
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        project = Project(app, "stage")
        calls = []
        project.run = lambda args, profiles=None: calls.append(args)
        logs(project, "php-fpm", day="2026-10-14")
        logs(project, day="today")
        logs(project, "php-fpm")
        self.assertEqual(calls, [
            ["exec", "-T", "metrics-logs", "php", "/opt/setup/runtime/metrics/log-collector.php", "--print", "2026-10-14", "php-fpm"],
            ["exec", "-T", "metrics-logs", "php", "/opt/setup/runtime/metrics/log-collector.php", "--print", "today"],
            ["logs", "--tail", "100", "php-fpm"]])
        with self.assertRaisesRegex(ValueError, "follow=1 is for docker logs"):
            logs(project, day="today", follow=True)
        plain = Project(self.project("CRON_ALLOWED_IPS=10.8.0.20\n", services="cron"), "stage")
        with self.assertRaisesRegex(ValueError, "runs with SERVICES=metrics"):
            logs(plain, day="today")

    def test_only_the_networkless_collector_reads_docker_and_cron_history_is_mounted_with_cron(self):
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        project = Project(app, "stage")
        services = json.loads(project.capture(["config", "--format", "json"]))["services"]
        metrics, collector = services["metrics"], services["metrics-docker"]
        # The web-facing service never gets the socket; the collectors have no network, ports or capabilities.
        for name, service in services.items():
            if name not in ("metrics-docker", "metrics-logs"):
                self.assertNotIn("/var/run/docker.sock", {v.get("source") for v in service.get("volumes", [])}, name)
        self.assertEqual((collector["network_mode"], collector["read_only"], collector["cap_drop"]), ("none", True, ["ALL"]))
        self.assertIn("no-new-privileges:true", collector["security_opt"])
        self.assertNotIn("ports", collector)
        self.assertNotIn("networks", collector)
        mounts = {volume["target"]: volume for volume in collector["volumes"]}
        socket = mounts["/var/run/docker.sock"]
        self.assertEqual((socket["type"], socket["source"], socket["read_only"]), ("bind", "/var/run/docker.sock", True))
        state = {volume["target"]: volume for volume in metrics["volumes"]}["/host/docker"]
        self.assertEqual((state["type"], state["source"], state["read_only"]), ("volume", mounts["/var/lib/setup-docker"]["source"], True))
        self.assertFalse(mounts["/var/lib/setup-docker"].get("read_only", False))
        self.assertEqual(collector["environment"]["METRICS_DOCKER_PROJECT"], project.name)
        self.assertEqual((collector["environment"]["METRICS_DOCKER_INTERVAL"], metrics["environment"]["METRICS_DOCKER_INTERVAL"]), ("15", "15"))
        self.assertNotIn("METRICS_CRON", metrics["environment"])
        self.assertNotIn("/host/cron-history", {v["target"] for v in metrics["volumes"]})
        # The release keeps the read-only socket of the collector, like /proc.
        release = release_model(project, "/srv/demo")
        self.assertIn(("/var/run/docker.sock", "/var/run/docker.sock"),
                      {(v["source"], v["target"]) for v in release["services"]["metrics-docker"]["volumes"] if v["type"] == "bind"})
        # With cron: the history (data/cron) and the crontab (app/config/cron, when present) read-only.
        cron = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\nCRON_ALLOWED_IPS=10.8.0.20\n", services="cron metrics")
        project = Project(cron, "stage")
        metrics = json.loads(project.capture(["config", "--format", "json"]))["services"]["metrics"]
        mounts = {volume["target"]: volume for volume in metrics["volumes"]}
        self.assertEqual((mounts["/host/cron-history"]["source"], mounts["/host/cron-history"]["read_only"]),
                         (str(project.data_directory / "cron"), True))
        self.assertNotIn("/host/cron-config", mounts)  # no app/config/cron in this project: not created by Docker
        self.assertEqual((metrics["environment"]["METRICS_CRON"], metrics["environment"]["ENV"]), ("1", "stage"))
        self.assertEqual(metrics["environment"]["METRICS_CRON_SCHEDULE"], "on")
        (project.root / "app/config/cron").mkdir(parents=True)
        metrics = json.loads(Project(cron, "stage").capture(["config", "--format", "json"]))["services"]["metrics"]
        mounts = {volume["target"]: volume for volume in metrics["volumes"]}
        self.assertEqual((mounts["/host/cron-config"]["source"], mounts["/host/cron-config"]["read_only"]),
                         (str(project.root / "app/config/cron"), True))

    def test_a_writable_or_other_host_mount_is_still_refused_by_the_release(self):
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        (app / "compose").mkdir(exist_ok=True)
        (app / "compose/stage.yaml").write_text("services:\n  metrics:\n    volumes:\n      - /proc:/host/proc-rw\n")
        with self.assertRaisesRegex(ValueError, "outside the project"):
            release_model(Project(app, "stage"), "/srv/demo")

    def test_disabled_by_default_and_settings_are_validated(self):
        project = Project(self.project("", services=""), "stage")
        self.assertEqual(project.process_env["SETUP_ENABLE_METRICS"], "0")
        for setting, message in (("METRICS_SUPPORT_LEVEL=high", "METRICS_SUPPORT_LEVEL"),
                                 ("METRICS_PROBE_ADDRESS=shop.example.com", "METRICS_PROBE_ADDRESS"),
                                 ("METRICS_URLS=ftp://demo.example.com/", "METRICS_URLS"),
                                 ("METRICS_URLS='https://a.example.com/ x'", "METRICS_URLS"),
                                 ("METRICS_DISK_WRITE_MB=-1", "METRICS_DISK_WRITE_MB"),
                                 ("METRICS_HTTP_WINDOW_MINUTES=0", "METRICS_HTTP_WINDOW_MINUTES"),
                                 ("METRICS_HTTP_WINDOW_MINUTES=61", "METRICS_HTTP_WINDOW_MINUTES"),
                                 ("METRICS_DOCKER_INTERVAL=4", "METRICS_DOCKER_INTERVAL"),
                                 ("METRICS_DOCKER_INTERVAL=1m", "METRICS_DOCKER_INTERVAL"),
                                 ("METRICS_NETWORK_INTERFACE='eth0;'", "METRICS_NETWORK_INTERFACE")):
            with self.subTest(setting=setting), self.assertRaisesRegex(ValueError, message):
                Project(self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n{setting}\n"), "stage")

    def test_local_runs_without_a_list_or_token_and_probes_the_vpn_address(self):
        app = self.project("")
        with (app / "env/local.env").open("a") as env:
            env.write("METRICS_URLS=http://asana.local/\nMETRICS_PROBE_ADDRESS=10.8.0.14\n")
        project = Project(app, "local")
        metrics = json.loads(project.capture(["config", "--format", "json"]))["services"]["metrics"]["environment"]
        self.assertEqual((metrics["METRICS_URLS"], metrics["METRICS_PROBE_ADDRESS"]), ("http://asana.local/", "10.8.0.14"))


PHP = os.environ.get("PHP_TEST_IMAGE")
NGINX = os.environ.get("SETUP_NGINX_TEST_IMAGE")
CONF = ROOT / "docker/docker/nginx-proxy/conf"


@unittest.skipUnless(PHP and NGINX, "Set PHP_TEST_IMAGE and SETUP_NGINX_TEST_IMAGE to built setup images")
class MetricsIntegrationTest(unittest.TestCase):
    maxDiff = None
    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60, check=check).stdout.strip()

    def stack(self, proxy_env=(), **metrics_env):
        """nginx-proxy (working tree configuration), the metrics service sharing the HTTP log volume (the proxy's TCP
        snapshot) and the log collector's volume, which collect() fills from the proxy's docker logs."""
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        data, certificates = Path(temporary.name) / "data", Path(temporary.name) / "ssl"
        data.mkdir()
        certificates.mkdir()
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=demo.local",
                        "-keyout", str(certificates / "domain.key"), "-out", str(certificates / "domain.crt")],
                       capture_output=True, check=True, timeout=30)
        network = f"setup-metrics-{uuid.uuid4().hex[:8]}"
        self.docker("network", "create", network)
        self.addCleanup(self.docker, "network", "rm", network, check=False)
        logs, collected = network + "-http-logs", network + "-logs"
        for volume in (logs, collected):
            self.docker("volume", "create", volume)
            self.addCleanup(self.docker, "volume", "rm", "-f", volume, check=False)
        self.project, self.collected = network, collected
        metrics, proxy = network + "-metrics", network + "-proxy"
        self.addCleanup(self.docker, "rm", "-f", metrics, proxy, check=False)
        settings = {"METRICS_TOKEN": TOKEN, "METRICS_URLS": "http://demo.local/", "METRICS_PROBE_ADDRESS": "",
                    "METRICS_SUPPORT_LEVEL": "1", "METRICS_DISK_WRITE_MB": "8", "TZ": "UTC", "DOMAIN": "demo.local", **metrics_env}
        self.docker("run", "-d", "--name", metrics, "--network", network, "--network-alias", "metrics",
                    "--network-alias", "webserver", "--memory", "128m",
                    "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro",
                    "-v", "/proc:/host/proc:ro", "-v", f"{data}:/host/disk:ro", "-v", f"{logs}:/host/http-logs:ro",
                    "-v", f"{collected}:/host/logs:ro",
                    *[arg for key, value in settings.items() for arg in ("-e", f"{key}={value}")],
                    # The service's loops (start.sh), sampling every 2 s.
                    "--tmpfs", "/var/lib/setup-metrics:mode=1777", "-e", "METRICS_SAMPLER_INTERVAL=2", "-e", "METRICS_PROBE_INTERVAL=5",
                    "--entrypoint", "sh", PHP, "/opt/setup/runtime/metrics/start.sh")
        # The image predates this change: start it with the working tree's proxy configuration.
        install = ("cp /conf/nginx.conf /etc/nginx/nginx.conf && cp /conf/sites.conf /etc/nginx/conf.d/sites.conf"
                   " && cp /conf/sites.metrics.conf /etc/nginx/templates/setup-metrics.conf"
                   " && cp /conf/init.sh /conf/access.sh /conf/http-logs.sh /usr/local/bin/ && exec /usr/local/bin/init.sh demo.local 60")
        self.docker("run", "-d", "--name", proxy, "--network", network, "--memory", "128m", "-v", f"{CONF}:/conf:ro",
                    "--label", f"com.docker.compose.project={network}", "--label", "com.docker.compose.service=nginx-proxy",
                    "-v", f"{certificates}:/etc/ssl/certs:ro", "-v", f"{logs}:/var/log/setup-http", "-e", "TZ=UTC",
                    "-e", "SETUP_ENABLE_METRICS=1", "-e", "SETUP_ENVIRONMENT=prod", "-e", "METRICS_ALLOWED_IPS=203.0.113.10",
                    "-e", "TRUSTED_PROXIES=127.0.0.1", "-e", "SETUP_HTTP_SNAPSHOT_SECONDS=1", *proxy_env,
                    "--entrypoint", "sh", NGINX, "-c", install)
        return network, proxy, metrics

    def collect(self):
        """One pass of the log collector over the stack's containers (the proxy's docker logs)."""
        self.docker("run", "--rm", "--network", "none", "--user", "0:0", "--read-only", "--cap-drop", "ALL",
                    "-v", "/var/run/docker.sock:/var/run/docker.sock:ro", "-v", f"{self.collected}:/var/lib/setup-logs",
                    "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-e", f"METRICS_DOCKER_PROJECT={self.project}",
                    "-e", "TZ=UTC", "--entrypoint", "php", PHP, "/opt/setup/runtime/metrics/log-collector.php", "--once")

    def request(self, proxy, path, client, token=None, agent="setup-test", host=METRICS_HOST, user=None):
        """A request through the proxy from inside it (peer 127.0.0.1, a trusted proxy) for a forwarded client;
        metrics.<domain> by default, the site with host="demo.local" (its webserver does not answer: 502)."""
        args = ["exec", proxy, "curl", "-sS", "--max-time", "30", "-H", f"X-Forwarded-For: {client}", "-A", agent,
                "-o", "/dev/stderr", "-w", "%{http_code}"]
        args += ["-H", f"Authorization: Bearer {token}"] if token else []
        args += ["-u", user] if user else []
        for _ in range(50):
            result = subprocess.run(["docker", *args, f"http://127.0.0.1{path}", "-H", f"Host: {host}"],
                                    capture_output=True, text=True, timeout=60)
            if result.stdout not in ("", "000") and (host != METRICS_HOST or result.stdout != "502"):
                return result.stdout, result.stderr
            time.sleep(0.2)
        self.fail(self.docker("logs", proxy) + self.docker("logs", proxy.replace("-proxy", "-metrics")))

    def test_proxy_path_token_allowlist_and_host_numbers(self):
        _, proxy, metrics = self.stack()
        self.assertEqual(self.request(proxy, "/metrics", "198.51.100.1", TOKEN)[0], "403")
        self.assertEqual(self.request(proxy, "/metrics", "203.0.113.10")[0], "401")
        self.assertEqual(self.request(proxy, "/metrics", "203.0.113.10", "wrong" * 8)[0], "401")
        # A snapshot taken once the first round of probes and the proxy's first TCP snapshot are written.
        for _ in range(60):
            status, body = self.request(proxy, "/metrics", "203.0.113.10", TOKEN)
            self.assertEqual(status, "200", body)
            values = parse(body)
            up = values["setup_metrics_group_up"]
            if up.get('{group="website"}') == 1 and up.get('{group="security"}') == 1:
                break
            time.sleep(0.5)
        meminfo = dict(line.split(":", 1) for line in self.docker("run", "--rm", "--entrypoint", "cat", PHP, "/proc/meminfo").splitlines())
        self.assertEqual(values["ram_total"][""], int(meminfo["MemTotal"].split()[0]))
        busy = sum(values[f"cpu_usage_{name}"]['{cpu="all"}'] for name in ("usr", "nice", "sys", "iowait", "irq", "soft", "steal", "idle"))
        self.assertAlmostEqual(busy, 100, delta=1)
        self.assertGreater(values["uptime"][""], 0)
        self.assertGreater(values["disk_size"][""], 0)
        self.assertIn("network_rxkb", values)
        self.assertEqual(values["website_response_status"], {'{url="http://demo.local/"}': 0})
        self.assertEqual(values["setup_metrics_group_up"],
                         {'{group="system"}': 1, '{group="website"}': 1, '{group="http"}': 1, '{group="security"}': 1,
                          '{group="database"}': 0, '{group="host"}': 1, '{group="containers"}': 0, '{group="cron"}': 1,
                          '{group="registry"}': 1})
        # No database and no Docker collector in this test; cron is not enabled (nothing to read).

    def test_metrics_lives_on_its_own_host_and_its_pages_ask_for_the_password(self):
        entry = "ops:" + subprocess.run(["openssl", "passwd", "-apr1", "secret-ops"], capture_output=True, text=True,
                                        check=True).stdout.strip()
        _, proxy, _ = self.stack(proxy_env=("-e", f"METRICS_HTPASSWD={entry}"))
        # The page people open: allowlist and Basic-auth.
        self.assertEqual(self.request(proxy, "/", "198.51.100.1", user="ops:secret-ops")[0], "403")
        self.assertEqual(self.request(proxy, "/", "203.0.113.10")[0], "401")
        self.assertEqual(self.request(proxy, "/", "203.0.113.10", user="ops:wrong")[0], "401")
        status, body = self.request(proxy, "/", "203.0.113.10", user="ops:secret-ops")
        self.assertEqual(status, "200", body)
        self.assertIn("/metrics", body)
        # /metrics and /feed: allowlist and their own bearer token, no Basic-auth.
        for path in ("/metrics", "/feed"):
            self.assertEqual(self.request(proxy, path, "198.51.100.1", TOKEN)[0], "403")
            self.assertEqual(self.request(proxy, path, "203.0.113.10")[0], "401")
            self.assertEqual(self.request(proxy, path, "203.0.113.10", TOKEN)[0], "200")
        # The site no longer has a /metrics of its own: the request goes to the webserver like any page.
        status, body = self.request(proxy, "/metrics", "203.0.113.10", TOKEN, host="demo.local")
        self.assertEqual(status, "502", body)
        self.assertNotIn("# TYPE", body)

    def test_real_proxy_log_lines_and_connections(self):
        # The window ends after the current minute, so the requests made now are inside it.
        network, proxy, metrics = self.stack(METRICS_HTTP_NOW=str(int(time.time()) + 120), METRICS_HTTP_WINDOW_MINUTES="5")
        sent = [("/", "203.0.113.21", "agent-a")] * 3 + [("/missing", "203.0.113.22", "agent-b")]
        codes = {self.request(proxy, path, client, agent=agent, host="demo.local")[0] for path, client, agent in sent}
        # Scrapes are not site traffic: metrics.<domain> is in the proxy's log, the HTTP group leaves it out.
        self.request(proxy, "/metrics", "203.0.113.10", TOKEN)
        # Three connections held open from another container on the network (not the proxy's gateway).
        client = network + "-client"
        self.addCleanup(self.docker, "rm", "-f", client, check=False)
        self.docker("run", "-d", "--name", client, "--network", network, "--entrypoint", "bash", NGINX, "-c",
                    f"exec 3<>/dev/tcp/{proxy}/80 4<>/dev/tcp/{proxy}/80 5<>/dev/tcp/{proxy}/80; sleep 120")
        client_ip = self.docker("inspect", "-f", f"{{{{(index .NetworkSettings.Networks \"{network}\").IPAddress}}}}", client)
        values = {}
        for _ in range(60):
            self.collect()
            values = parse(self.request(proxy, "/metrics", "203.0.113.10", TOKEN)[1])
            if (labelled(values, "http_request_ips", "ip").get("203.0.113.22")
                    and labelled(values, "ddos_most_connections_ip_count", "ip").get(client_ip) == 3):
                break
            time.sleep(1)
        self.assertEqual(labelled(values, "http_request_ips", "ip"), {"203.0.113.21": 3, "203.0.113.22": 1})
        self.assertEqual(labelled(values, "http_request_user_agents", "user_agent"), {"agent-a": 3, "agent-b": 1})
        self.assertEqual(labelled(values, "ddos_most_connections_ip_count", "ip"), {client_ip: 3})
        # docker logs still carry every request; the metrics figures match their site lines.
        lines = [line for line in subprocess.run(["docker", "logs", proxy], capture_output=True, text=True).stdout.splitlines()
                 if '"GET ' in line]
        site = [line for line in lines if "/metrics" not in line]
        self.assertEqual(len(site), 4)
        # combined, then the request's and the upstream's time and the host (the webserver does not answer: 502).
        for line in site:
            self.assertRegex(line, r'" rt=\d+\.\d{3} urt=(\d+\.\d{3}|-)(, \S+)* host=demo\.local$')
        self.assertGreater(len(lines), 4)
        fields = [line.split('" ')[1].split() for line in site]
        self.assertEqual(labelled(values, "http_response_codes", "code"), dict(collections.Counter(f[0] for f in fields)))
        self.assertEqual(set(labelled(values, "http_response_codes", "code")), codes)
        self.assertEqual(values["http_bytes_transferred_total"][""], sum(int(f[1]) for f in fields))
        self.assertEqual(values["setup_metrics_group_up"]['{group="http"}'], 1)
        self.assertEqual(values["setup_metrics_group_up"]['{group="security"}'], 1)




if __name__ == "__main__":
    unittest.main()
