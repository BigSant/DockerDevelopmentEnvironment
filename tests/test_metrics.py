"""Target /metrics: token and allowlist, system and website groups, Grafana name compatibility; B: real host scrape."""

import contextlib
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

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from create_project import scaffold
import grafana_names
from project import Project
from project_release import release_model

ENDPOINT = ROOT / "docker/runtime/metrics/index.php"
PROC = Path(__file__).resolve().parent / "fixtures/metrics-proc"
DATABASE_FIXTURE = Path(__file__).resolve().parent / "fixtures/metrics-database.json"
TOKEN = "m" * 32
# Groups the target serves so far; 07 adds http, 08 database, 09 host (and then every group is served).
IMPLEMENTED = ("system", "website", "database")
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


def fetch(base, path="/metrics", token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=60) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read().decode()


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Website(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
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

    def start(self, **env):
        port = free_port()
        settings = {"METRICS_TOKEN": TOKEN, "METRICS_PROC_ROOT": str(self.proc), "METRICS_DISK_PATH": str(self.work / "disk"),
                    "METRICS_STATE_DIR": str(self.work / "state"), "METRICS_SAMPLE_SECONDS": "0.1",
                    "METRICS_DISK_WRITE_MB": "1", "METRICS_URLS": "", "METRICS_SUPPORT_LEVEL": "2", **env}
        settings.setdefault("METRICS_DATABASE_FIXTURE", str(DATABASE_FIXTURE))
        server = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", str(ENDPOINT)], env={**os.environ, **settings},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

    def test_without_a_configured_token_it_refuses_to_serve(self):
        for token in ("", "short"):
            base = self.start(METRICS_TOKEN=token)
            self.assertEqual(fetch(base, token=token or None)[0], 503)

    def test_the_bearer_token_is_required_and_only_metrics_is_served(self):
        base = self.start()
        self.assertEqual(fetch(base)[0], 401)
        self.assertEqual(fetch(base, token="wrong" * 8)[0], 401)
        self.assertEqual(fetch(base, token=TOKEN.upper())[0], 401)
        self.assertEqual(fetch(base, "/", TOKEN)[0], 404)
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

    def test_without_urls_the_website_group_still_reports_the_support_level(self):
        body = fetch(self.start(METRICS_SUPPORT_LEVEL="0"), token=TOKEN)[2]
        self.assertIn("support_level 0\n", body)
        self.assertNotIn("website_response_status{", body)


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
        expected = set().union(*(grafana_names.GROUPS[group] for group in IMPLEMENTED))
        self.assertEqual(sorted(expected - served), [])
        if set(IMPLEMENTED) == set(grafana_names.GROUPS):
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
        base = self.start(METRICS_URLS=f'http://shop.invalid:{plain}/?q="a\\b" https://shop.invalid:{tls}/',
                          METRICS_PROBE_ADDRESS="127.0.0.1")
        body = fetch(base, token=TOKEN)[2]
        self.assertIn('url="http://shop.invalid:%d/?q=\\"a\\\\b\\""' % plain, body)
        command = ["promtool"] if shutil.which("promtool") else ["docker", "run", "--rm", "-i", "--entrypoint", "promtool", PROMTOOL]
        check = lambda *flags: subprocess.run(command + ["check", "metrics", *flags], input=body, capture_output=True,
                                              text=True, timeout=120)
        # Format: the exposition parses (cardinality report only, no naming lint).
        result = check("--extended", "--lint=none")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + body)
        # Naming lint: only the dashboards' own gauge names ending in _total may be reported.
        kept = {f'{name} non-counter metrics should not have "_total" suffix'
                for name in grafana_names.recorded() | grafana_names.VIEWER_ONLY if name.endswith("_total")}
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

    def test_stage_requires_an_allowlist_and_a_token(self):
        app = self.project("")
        with self.assertRaisesRegex(ValueError, "metrics on stage requires METRICS_ALLOWED_IPS or ALLOWED_IPS"):
            Project(app, "stage")
        with (app / "env/stage.env").open("a") as env:
            env.write("METRICS_ALLOWED_IPS=10.8.0.20\n")
        with self.assertRaisesRegex(ValueError, "METRICS_TOKEN"):
            Project(app, "stage")
        with (app / "env/stage.env").open("a") as env:
            env.write("METRICS_TOKEN=" + "s" * 20 + "\n")
        with self.assertRaisesRegex(ValueError, "METRICS_TOKEN"):
            Project(app, "stage")

    def test_compose_model_reads_the_host_read_only_and_stays_off_the_project_network(self):
        app = self.project(f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        project = Project(app, "stage")
        self.assertEqual(project.process_env["SETUP_ENABLE_METRICS"], "1")
        services = json.loads(project.capture(["config", "--format", "json"]))["services"]
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
        # The release keeps /proc (the same on every Linux target) and maps the data directory.
        released = release_model(project, "/srv/demo")["services"]["metrics"]["volumes"]
        self.assertEqual({(v["source"], v["target"]) for v in released if v["type"] == "bind"},
                         {("/proc", "/host/proc"), ("/srv/demo/data/stage", "/host/disk")})

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
    def docker(self, *args, check=True):
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60, check=check).stdout.strip()

    def test_proxy_path_token_allowlist_and_host_numbers(self):
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
        metrics, proxy = network + "-metrics", network + "-proxy"
        self.addCleanup(self.docker, "rm", "-f", metrics, proxy, check=False)
        self.docker("run", "-d", "--name", metrics, "--network", network, "--network-alias", "metrics",
                    "--network-alias", "webserver", "--memory", "128m",
                    "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro",
                    "-v", "/proc:/host/proc:ro", "-v", f"{data}:/host/disk:ro", "-e", f"METRICS_TOKEN={TOKEN}",
                    "-e", "METRICS_URLS=http://demo.local/", "-e", "METRICS_PROBE_ADDRESS=", "-e", "METRICS_SUPPORT_LEVEL=1",
                    "-e", "METRICS_DISK_WRITE_MB=8", "--entrypoint", "php", PHP, "-S", "0.0.0.0:9100",
                    "/opt/setup/runtime/metrics/index.php")
        # The image predates this change: start it with the working tree's proxy configuration.
        install = ("cp /conf/sites.conf /etc/nginx/conf.d/sites.conf && cp /conf/sites.metrics.conf /etc/nginx/templates/setup-metrics.conf"
                   " && cp /conf/init.sh /conf/access.sh /usr/local/bin/ && exec /usr/local/bin/init.sh demo.local 60")
        self.docker("run", "-d", "--name", proxy, "--network", network, "--memory", "128m", "-v", f"{CONF}:/conf:ro",
                    "-v", f"{certificates}:/etc/ssl/certs:ro",
                    "-e", "SETUP_ENABLE_METRICS=1", "-e", "SETUP_ENVIRONMENT=prod", "-e", "METRICS_ALLOWED_IPS=203.0.113.10",
                    "-e", "TRUSTED_PROXIES=127.0.0.1", "--entrypoint", "sh", NGINX, "-c", install)

        def scrape(client, token=None):
            args = ["exec", proxy, "curl", "-sS", "--max-time", "30", "-H", f"X-Forwarded-For: {client}",
                    "-o", "/dev/stderr", "-w", "%{http_code}"]
            args += ["-H", f"Authorization: Bearer {token}"] if token else []
            for _ in range(50):
                result = subprocess.run(["docker", *args, "http://127.0.0.1/metrics", "-H", "Host: demo.local"],
                                        capture_output=True, text=True, timeout=60)
                if result.stdout not in ("", "000", "502"):
                    return result.stdout, result.stderr
                time.sleep(0.2)
            self.fail(self.docker("logs", proxy) + self.docker("logs", metrics))

        self.assertEqual(scrape("198.51.100.1", TOKEN)[0], "403")
        self.assertEqual(scrape("203.0.113.10")[0], "401")
        self.assertEqual(scrape("203.0.113.10", "wrong" * 8)[0], "401")
        status, body = scrape("203.0.113.10", TOKEN)
        self.assertEqual(status, "200", body)
        values = parse(body)
        meminfo = dict(line.split(":", 1) for line in self.docker("run", "--rm", "--entrypoint", "cat", PHP, "/proc/meminfo").splitlines())
        self.assertEqual(values["ram_total"][""], int(meminfo["MemTotal"].split()[0]))
        busy = sum(values[f"cpu_usage_{name}"]['{cpu="all"}'] for name in ("usr", "nice", "sys", "iowait", "irq", "soft", "steal", "idle"))
        self.assertAlmostEqual(busy, 100, delta=1)
        self.assertGreater(values["uptime"][""], 0)
        self.assertGreater(values["disk_size"][""], 0)
        self.assertIn("network_rxkb", values)
        self.assertEqual(values["website_response_status"], {'{url="http://demo.local/"}': 0})
        self.assertEqual(values["setup_metrics_group_up"], {'{group="system"}': 1, '{group="website"}': 1,
                                                            '{group="database"}': 0})  # no database in this test


if __name__ == "__main__":
    unittest.main()
