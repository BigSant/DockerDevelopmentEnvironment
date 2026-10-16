"""Traffic analysis (the dashboard's Traffic tab, the successor of scan_access_file.sh): the same bots, SQL injections
and IP counts as the script on a known log, the other attack types, the project's own bots, percentiles, the hourly
cache that a 7-day window reads, and the analysis within a small memory limit."""
import datetime
import gzip
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_metrics import (ENDPOINT, ROOT, SAMPLER, TOKEN, EndpointMixin, fetch, free_port, labelled, parse,
                          write_collector_state)

FIXTURES = Path(__file__).resolve().parent / "fixtures/traffic"
ZONE = "Europe/Vilnius"
FIREFOX = "Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0"


def line(ip, moment, request, status=200, agent=FIREFOX, rt="0.050", host="demo.local"):
    stamp = moment.astimezone(ZoneInfo(ZONE)).strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'{ip} - - [{stamp}] "{request}" {status} 100 "-" "{agent}" rt={rt} urt={rt} host={host}\n'


def script_result():
    """scan_access_file.sh's result.txt on the fixture log: its sections as dicts and lists (zero bots left out)."""
    sections, current = {}, None
    for text in (FIXTURES / "scan_access_file.result.txt").read_text().splitlines():
        if text.startswith("# "):
            current = sections.setdefault(text[2:], [])
        elif text and current is not None:
            current.append(text)
    counted = lambda rows: {name: int(count) for count, name in (row.split(" - ", 1) for row in rows) if int(count)}
    return {"bots": counted(sections["BOT requests"]), "sqli": sections["Potential SQL injections"],
            "suspicious": counted(sections["Suspicious IPS"]), "regular": counted(sections["Regular IPS"])}


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class TrafficBase(unittest.TestCase):
    """php -S over a work directory: the log collector's files, the sampler's and the project's traffic classes
    (absent until a test writes them)."""
    php = ()

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.logs = self.work / "logs"
        (self.logs / "nginx-proxy").mkdir(parents=True)
        write_collector_state(self.logs)
        self.snapshots = self.work / "snapshots"
        self.snapshots.mkdir()
        self.environment = {"PATH": "/usr/bin:/bin", "METRICS_SNAPSHOT_DIR": str(self.snapshots), "METRICS_LOGS_DIR": str(self.logs),
                            "TZ": ZONE, "DOMAIN": "demo.local", "METRICS_BOTS": "ShopCrawler/1.0, Inhouse Monitor",
                            "METRICS_TRAFFIC_CONFIG": str(self.work / "metrics-config/traffic.php")}
        port = free_port()
        server = subprocess.Popen(["php", *self.php, "-S", f"127.0.0.1:{port}", str(ENDPOINT)], env=self.environment,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        self.base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                fetch(self.base, "/")
                break
            except OSError:
                time.sleep(0.1)

    def report(self, query):
        status, kind, body = fetch(self.base, "/traffic.json?" + query)
        self.assertEqual((status, kind), (200, "application/json"), body)
        return json.loads(body)


class TrafficTest(TrafficBase):
    def test_a_known_log_gives_the_scripts_bots_injections_and_ips(self):
        shutil.copy(FIXTURES / "access-2026-10-14.log", self.logs / "nginx-proxy")
        expected = script_result()
        report = self.report("day=2026-10-14")
        self.assertEqual(report["bots"], expected["bots"])
        self.assertEqual(report["attack_lines"]["sqli"], expected["sqli"])
        self.assertEqual(report["attacks"], {"sqli": 3})
        self.assertEqual(report["suspicious_ips"], expected["suspicious"])
        self.assertEqual(report["regular_ips"], expected["regular"])
        # The python-requests line is an SQL injection.
        self.assertEqual(report["classes"], {"human": 41, "api": 0, "bot": 21, "asset": 35, "attack": 3})
        # 4xx and 5xx by URL (no query), the slowest URLs by average.
        self.assertEqual(report["errors4"], {"/lt/nera": 2, "/module/x/ajax.php": 1})
        self.assertEqual(report["errors5"], {"/lt/uzsakymas": 2})
        self.assertEqual(list(report["slowest"])[0], "/lt/uzsakymas")
        self.assertEqual(report["slowest"]["/lt/uzsakymas"], {"requests": 2, "average": 17.101, "max": 30.001})
        # Percentiles of the non-static requests' times (nearest rank).
        times = []
        for match in re.finditer(r'"(GET|POST) (\S+) [^"]*".* rt=([\d.]+) ', (FIXTURES / "access-2026-10-14.log").read_text()):
            if not (match[1] == "GET" and re.search(r"\.(jpg|css)$", match[2].split("?")[0])):
                times.append(float(match[3]))
        times.sort()
        rank = lambda share: times[max(1, math.ceil(share * len(times))) - 1]
        self.assertEqual(len(times), 65)
        self.assertEqual((report["p50"], report["p95"]), (rank(0.5), rank(0.95)))
        # The page shows the same.
        page = fetch(self.base, "/traffic?day=2026-10-14")[2]
        self.assertIn("Googlebot", page)
        self.assertIn("185.220.101.44", page)
        self.assertIn('class="on">Traffic</a>', page)

    def test_attack_types_bots_of_the_project_and_tool_sites(self):
        now = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        rows = [line("203.0.113.5", now, "GET /../../etc/passwd HTTP/1.1", 400),
                line("203.0.113.5", now, "GET /.env HTTP/1.1", 404),
                line("203.0.113.6", now, "GET /wp-login.php HTTP/1.1", 404),
                line("203.0.113.6", now, "GET /.git/config HTTP/1.1", 404),
                line("203.0.113.7", now, "GET /lt/paieska?s=%3Cscript%3Ealert(1)%3C/script%3E HTTP/1.1"),
                # A probe in a crawler's name is an attack.
                line("203.0.113.8", now, "GET /.env HTTP/1.1", 404, agent="Mozilla/5.0 (compatible; Googlebot/2.1)"),
                line("192.0.2.1", now, "GET /lt/ HTTP/1.1", agent="ShopCrawler/1.0 (+https://shop.example/bot)"),
                line("192.0.2.2", now, "GET /lt/ HTTP/1.1", agent="Inhouse Monitor 3"),
                line("192.0.2.3", now, "GET /lt/ HTTP/1.1"),
                # metrics.<domain> is not the site's traffic.
                line("10.8.0.20", now, "GET /metrics HTTP/1.1", agent="Prometheus/2.53", host="metrics.demo.local")]
        local = now.astimezone(ZoneInfo(ZONE))
        (self.logs / f"nginx-proxy/access-{local:%Y-%m-%d}.log").write_text("".join(rows) + "\n")
        report = self.report("window=1h")
        self.assertEqual(report["attacks"], {"probe": 4, "traversal": 1, "xss": 1})
        self.assertEqual(report["classes"], {"human": 1, "api": 0, "bot": 2, "asset": 0, "attack": 6})
        # Bots are counted by agent like the script did, an attack's agent too; the class is the attack.
        self.assertEqual(report["bots"], {"ShopCrawler/1.0": 1, "Inhouse Monitor": 1, "Googlebot": 1})
        self.assertEqual(set(report["suspicious_ips"]), {"203.0.113.5", "203.0.113.6", "203.0.113.7", "203.0.113.8"})
        self.assertNotIn("10.8.0.20", report["ips"])

    def test_closed_hours_are_read_once_and_a_7_day_window_merges_them(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for days in (5, 2):
            moment = now - datetime.timedelta(days=days)
            name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log.gz"
            with gzip.open(self.logs / "nginx-proxy" / name, "wt") as handle:
                for number in range(30):
                    handle.write(line(f"198.51.100.{number}", moment, f"GET /lt/{days}-{number} HTTP/1.1"))
        today = self.logs / f"nginx-proxy/access-{now.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log"
        current = now - datetime.timedelta(seconds=30)
        today.write_text(line("192.0.2.9", current, "GET /lt/now HTTP/1.1"))
        first = self.report("window=7d")
        self.assertEqual(first["requests"], 61)
        cached = sorted((self.snapshots / "traffic").iterdir())
        self.assertGreaterEqual(len(cached), 6 * 24)
        # The closed hours come from the cache: the older files may change or go, the window stays the same.
        for old in (self.logs / "nginx-proxy").glob("*.gz"):
            old.write_bytes(gzip.compress(b"not an access line\n"))
        today.write_text(today.read_text() + line("192.0.2.10", current, "GET /lt/again HTTP/1.1"))
        second = self.report("window=7d")
        self.assertEqual(second["requests"], 62, "the current hour is read again")
        self.assertEqual({k: v for k, v in second["ips"].items() if k.startswith("198.51.100.")},
                         {k: v for k, v in first["ips"].items() if k.startswith("198.51.100.")})

    def test_an_agent_with_escaped_bytes_is_cached_and_served(self):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
        name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log"
        # nginx writes bytes outside ASCII as \\xHH text.
        (self.logs / "nginx-proxy" / name).write_text(line("198.51.100.1", moment, "GET /lt/ HTTP/1.1", agent="bad\\xFF\\xFEagent"))
        subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, check=True, timeout=120)
        hour = int(moment.timestamp()) // 3600 * 3600
        cached = json.loads((self.snapshots / f"traffic/{hour}.json").read_text())
        self.assertEqual(cached["agents"], {"bad\\xFF\\xFEagent": 1})
        self.assertEqual(self.report("window=24h")["agents"], {"bad\\xFF\\xFEagent": 1})

    def test_hours_are_not_cached_while_the_log_collector_is_stopped(self):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
        name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log"
        (self.logs / "nginx-proxy" / name).write_text(line("198.51.100.1", moment, "GET /lt/ HTTP/1.1"))
        state = self.logs / ".collector-state.json"
        state.write_text("{}")
        stopped = time.time() - 4 * 3600
        os.utime(state, (stopped, stopped))
        subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, check=True, timeout=120)
        hour = int(moment.timestamp()) // 3600 * 3600
        # The hours that ended before the collector stopped are cached; the line's hour, after it stopped, is not.
        self.assertTrue((self.snapshots / f"traffic/{hour - 2 * 3600}.json").is_file())
        self.assertFalse((self.snapshots / f"traffic/{hour}.json").exists())
        # The window still counts the hour, read from the file.
        self.assertEqual(self.report("window=24h")["requests"], 1)
        self.assertFalse((self.snapshots / f"traffic/{hour}.json").exists())
        # Running again (its state saved now): every closed hour is cached.
        os.utime(state, None)
        subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, check=True, timeout=120)
        self.assertEqual(json.loads((self.snapshots / f"traffic/{hour}.json").read_text())["requests"], 1)
        self.assertEqual(len(list((self.snapshots / "traffic").glob("*.json"))), 7 * 24)

    def test_hours_wait_until_the_log_collector_has_caught_up(self):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
        name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log"
        (self.logs / "nginx-proxy" / name).write_text(line("198.51.100.1", moment, "GET /lt/ HTTP/1.1"))
        hour = int(moment.timestamp()) // 3600 * 3600
        state = self.logs / ".collector-state.json"
        prepare = lambda: subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, check=True, timeout=120)
        # A first deploy: the collector has saved nothing yet, then saves while it reads what Docker holds.
        state.unlink()
        prepare()
        self.assertEqual(list((self.snapshots / "traffic").glob("*.json")), [])
        state.write_text(json.dumps({"project": "demo-stage", "containers": {}, "caught_up": None}))
        prepare()
        self.assertEqual(list((self.snapshots / "traffic").glob("*.json")), [])
        # Back-filling after a stop, caught up until 4 hours ago: the hours before are cached, the line's is not.
        write_collector_state(self.logs, caught_up=int(time.time()) - 4 * 3600)
        prepare()
        self.assertTrue((self.snapshots / f"traffic/{hour - 3 * 3600}.json").is_file())
        self.assertFalse((self.snapshots / f"traffic/{hour}.json").exists())
        self.assertEqual(self.report("window=24h")["requests"], 1)
        # Caught up now: the line's hour is cached with its request.
        write_collector_state(self.logs)
        prepare()
        self.assertEqual(json.loads((self.snapshots / f"traffic/{hour}.json").read_text())["requests"], 1)

    def test_a_window_after_midnight_reads_no_gzipped_yesterday(self):
        script = ("require $argv[1]; require $argv[2]; require $argv[3]; $d = new DateTime('2026-10-15 00:10:00', new DateTimeZone('Europe/Vilnius'));"
                  " echo json_encode(traffic_files($d->getTimestamp() - 60, $d->getTimestamp()));")
        with gzip.open(self.logs / "nginx-proxy/access-2026-10-14.log.gz", "wt") as handle:
            handle.write("x\n")
        (self.logs / "nginx-proxy/access-2026-10-15.log").write_text("x\n")
        runtime = ROOT / "docker/runtime/metrics"
        result = subprocess.run(["php", "-r", script, str(runtime / "metrics.php"), str(runtime / "http.php"), str(runtime / "traffic.php")],
                                env=self.environment, capture_output=True, text=True, check=True)
        self.assertEqual([Path(entry[0]).name for entry in json.loads(result.stdout)], ["access-2026-10-15.log"])

    def test_the_sampler_prepares_the_closed_hours(self):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)
        name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log"
        (self.logs / "nginx-proxy" / name).write_text(line("198.51.100.1", moment, "GET /lt/ HTTP/1.1"))
        result = subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        hour = int(moment.timestamp()) // 3600 * 3600
        cached = json.loads((self.snapshots / f"traffic/{hour}.json").read_text())
        self.assertEqual(cached["ips"], {"198.51.100.1": 1})
        # Every closed hour of the last 7 days.
        self.assertEqual(len(list((self.snapshots / "traffic").iterdir())), 7 * 24)
        # Hours past 8 days are removed.
        old = self.snapshots / f"traffic/{hour - 9 * 86400}.json"
        old.write_text("{}")
        subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, check=True, timeout=120)
        self.assertFalse(old.exists())


class TrafficClassesTest(TrafficBase):
    """Scripts and API clients (api) apart from people, and the project's own classes from config/metrics/traffic.php:
    in file order after the attacks and before setup's bot, asset, api and human; an invalid file changes nothing."""

    def write(self, rows, config=None):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
        local = moment.astimezone(ZoneInfo(ZONE))
        (self.logs / f"nginx-proxy/access-{local:%Y-%m-%d}.log").write_text(
            "".join(line(ip, moment, request, status, agent, host=host) for ip, request, status, agent, host in rows))
        if config is not None:
            (self.work / "metrics-config").mkdir(exist_ok=True)
            (self.work / "metrics-config/traffic.php").write_text(config)

    def test_scripts_and_api_clients_are_not_people(self):
        self.write([("198.51.100.1", "GET /lt/ HTTP/1.1", 200, FIREFOX, "demo.local"),
                    ("198.51.100.2", "GET /module/x/cron.php?token=a HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("198.51.100.3", "POST /api/orders HTTP/1.1", 201, "python-requests/2.31.0", "demo.local"),
                    ("198.51.100.4", "GET / HTTP/1.1", 200, "-", "demo.local"),
                    ("198.51.100.5", "GET /lt/x HTTP/1.1", 200, "GuzzleHttp/7", "demo.local"),
                    ("198.51.100.6", "GET /lt/y HTTP/1.1", 200, "Java/17.0.2", "demo.local"),
                    ("198.51.100.7", "GET /lt/z HTTP/1.1", 200, "Go-http-client/1.1", "demo.local"),
                    # A static file is one whoever asks.
                    ("198.51.100.8", "GET /img/logo.png HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    # An agent setup does not know is a person.
                    ("198.51.100.9", "GET /lt/ HTTP/1.1", 200, "MyApp/1.0", "demo.local")])
        report = self.report("window=1h")
        self.assertEqual(report["classes"], {"human": 2, "api": 6, "bot": 0, "asset": 1, "attack": 0})
        self.assertEqual(list(report["labels"].items()), [("human", "People"), ("api", "Scripts & API"), ("bot", "Bots"),
                                                          ("asset", "Static files"), ("attack", "Attacks")])
        # Regular IPs are people's.
        self.assertEqual(report["regular_ips"], {"198.51.100.1": 1, "198.51.100.9": 1})
        page = fetch(self.base, "/traffic?window=1h")[2]
        self.assertIn('data-class="api"><h2>Scripts &amp; API</h2><div class="value">6</div>', page)
        self.assertNotIn("data-problems", page)

    def test_the_projects_classes_match_in_file_order_before_setups(self):
        config = """<?php
return array(
    'classes' => array(
        'payment' => array('label' => 'Payment callbacks', 'match' => array(array('path' => '#^/module/paysera/callback#'))),
        'integration' => array('match' => array(array('path' => '#^/api/#', 'method' => array('put', 'POST')),
            array('agent' => '#GuzzleHttp/#'))),
        'monitor' => array('match' => array(array('ip' => array('10.8.0.0/24', '2001:db8::/32'), 'path' => '#^/health$#'))),
        'admin' => array('match' => array(array('host' => 'Admin.Demo.Local'),
            array('url' => '#[?&]controller=AdminOrders#', 'status' => array(200, 302)))),
        'teapot' => array('match' => function (array $request) { return $request['status'] === 418; }),
        'api' => array('extend' => true, 'match' => array(array('agent' => '#^MyErpSync/#'))),
    ),
    'bots' => array('FileCrawler/2'),
);
"""
        googlebot = "Mozilla/5.0 (compatible; Googlebot/2.1)"
        self.write([("192.0.2.1", "GET /module/paysera/callback?order=1 HTTP/1.1", 200, googlebot, "demo.local"),
                    ("192.0.2.2", "POST /api/orders HTTP/1.1", 201, "curl/8.5.0", "demo.local"),
                    ("192.0.2.2", "GET /api/orders HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("192.0.2.3", "GET /lt/ HTTP/1.1", 200, "GuzzleHttp/7", "demo.local"),
                    ("10.8.0.5", "GET /health HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("2001:db8::5", "GET /health HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("10.9.0.5", "GET /health HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("192.0.2.4", "GET /x HTTP/1.1", 200, FIREFOX, "admin.demo.local"),
                    ("192.0.2.4", "GET /adm/index.php?controller=AdminOrders&token=x HTTP/1.1", 302, FIREFOX, "demo.local"),
                    ("192.0.2.5", "GET /tea HTTP/1.1", 418, FIREFOX, "demo.local"),
                    ("192.0.2.6", "GET /lt/ HTTP/1.1", 200, "MyErpSync/3.1", "demo.local"),
                    ("192.0.2.7", "GET /lt/ HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                    ("192.0.2.8", "GET /lt/ HTTP/1.1", 200, "FileCrawler/2.1", "demo.local"),
                    ("192.0.2.9", "GET /lt/ HTTP/1.1", 200, FIREFOX, "demo.local"),
                    # An attack on a project path is an attack.
                    ("203.0.113.1", "GET /module/paysera/callback/../../etc/passwd HTTP/1.1", 400, FIREFOX, "demo.local")],
                   config)
        report = self.report("window=1h")
        self.assertEqual(report["classes"], {"human": 1, "payment": 1, "integration": 2, "monitor": 2, "admin": 2, "teapot": 1,
                                             "api": 4, "bot": 1, "asset": 0, "attack": 1})
        self.assertEqual(list(report["labels"].items()),
                         [("human", "People"), ("payment", "Payment callbacks"), ("integration", "Integration"),
                          ("monitor", "Monitor"), ("admin", "Admin"), ("teapot", "Teapot"), ("api", "Scripts & API"),
                          ("bot", "Bots"), ("asset", "Static files"), ("attack", "Attacks")])
        # Bots are still counted by agent, the project file's too.
        self.assertEqual(report["bots"], {"Googlebot": 1, "FileCrawler/2": 1})
        self.assertEqual(report["regular_ips"], {"192.0.2.9": 1})
        self.assertEqual(set(report["suspicious_ips"]), {"203.0.113.1"})
        page = fetch(self.base, "/traffic?window=1h")[2]
        self.assertIn('data-class="payment"><h2>Payment callbacks</h2><div class="value">1</div>', page)
        self.assertNotIn("data-problems", page)

    def test_the_built_in_api_class_is_replaced_or_turned_off(self):
        rows = [("192.0.2.1", "GET /lt/ HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                ("192.0.2.2", "GET /lt/ HTTP/1.1", 200, "MyErpSync/3.1", "demo.local")]
        self.write(rows, "<?php return array('classes' => array('api' => array('match' => array(array('agent' => '#^MyErpSync/#')))));")
        self.assertEqual(self.report("window=1h")["classes"], {"human": 1, "api": 1, "bot": 0, "asset": 0, "attack": 0})
        self.write(rows, "<?php return array('classes' => array('api' => false));")
        report = self.report("window=1h")
        self.assertEqual(report["classes"], {"human": 2, "bot": 0, "asset": 0, "attack": 0})
        self.assertNotIn("api", report["labels"])

    def test_an_invalid_file_leaves_the_built_in_classes_and_says_why(self):
        rows = [("192.0.2.1", "GET /lt/ HTTP/1.1", 200, "curl/8.5.0", "demo.local"),
                ("192.0.2.2", "GET /api/x HTTP/1.1", 200, FIREFOX, "demo.local")]
        for config, reason in (
                ("<?php return array('classes' => array('own' => array('match' => array(array('path' => '#^/api/')))));",
                 "class 'own' rule 1: path is not a valid regular expression"),
                ("<?php return array('classes' => array('own' => array('match' => array(array('path' => '#^/api/#')))) ;",
                 "ParseError on line 1")):
            self.write(rows, config)
            report = self.report("window=1h")
            self.assertEqual(report["classes"], {"human": 1, "api": 1, "bot": 0, "asset": 0, "attack": 0})
            page = fetch(self.base, "/traffic?window=1h")[2]
            self.assertIn('<div class="callout error" data-problems>config/metrics/traffic.php: ', page)
            self.assertIn(reason, page.replace("&#039;", "'"))
            self.assertIn("The built-in classes apply until it is fixed.", page)

    def test_a_closure_that_warns_or_throws_only_does_not_match(self):
        self.write([("192.0.2.1", "GET /lt/ HTTP/1.1", 200, FIREFOX, "demo.local"),
                    ("192.0.2.2", "GET /tea HTTP/1.1", 418, FIREFOX, "demo.local")],
                   "<?php return array('classes' => array("
                   "'noisy' => array('match' => function (array $r) { return $r['referer'] === 'x' || $r['status'] === 418; }),"
                   "'failing' => array('match' => function (array $r) { throw new RuntimeException('no'); })));")
        # The warnings stay out of the JSON (report() parses it) and the page.
        self.assertEqual(self.report("window=1h")["classes"],
                         {"human": 1, "noisy": 1, "failing": 0, "api": 0, "bot": 0, "asset": 0, "attack": 0})
        self.assertNotIn("Warning", fetch(self.base, "/traffic?window=1h")[2])

    def test_cached_hours_are_built_again_when_the_classes_change(self):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=2)
        name = f"access-{moment.astimezone(ZoneInfo(ZONE)):%Y-%m-%d}.log.gz"
        with gzip.open(self.logs / "nginx-proxy" / name, "wt") as handle:
            for number in range(30):
                handle.write(line(f"198.51.100.{number}", moment, f"GET /lt/old-{number} HTTP/1.1"))
        self.assertEqual(self.report("window=7d")["classes"]["human"], 30)
        hour = int(moment.timestamp()) // 3600 * 3600
        before = json.loads((self.snapshots / f"traffic/{hour}.json").read_text())["rules"]
        (self.work / "metrics-config").mkdir()
        (self.work / "metrics-config/traffic.php").write_text("<?php return array('classes' => array('old' => array('match' => array(array('path' => '#^/lt/old-#')))));")
        # A page does not read the week's logs itself: until the next traffic round the hours keep the previous classes.
        report = self.report("window=7d")
        self.assertEqual((report["classes"]["old"], report["classes"]["human"]), (0, 30))
        self.assertEqual(json.loads((self.snapshots / f"traffic/{hour}.json").read_text())["rules"], before)
        result = subprocess.run(["php", str(SAMPLER), "--traffic", "--once"], env=self.environment, capture_output=True,
                                text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.report("window=7d")
        self.assertEqual((report["classes"]["old"], report["classes"]["human"]), (30, 0))
        self.assertNotEqual(json.loads((self.snapshots / f"traffic/{hour}.json").read_text())["rules"], before)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class TrafficConfigCheckTest(unittest.TestCase):
    """php traffic-config.php [file]: what make up runs in the metrics service; 3 and the errors for an invalid file."""
    CHECK = ROOT / "docker/runtime/metrics/traffic-config.php"

    def check(self, text):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "traffic.php"
            if text is not None:
                file.write_text(text)
            return subprocess.run(["php", str(self.CHECK), str(file)], capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"})

    def test_a_valid_file_lists_its_classes_and_none_means_the_built_in_ones(self):
        result = self.check("<?php return array('classes' => array('payment' => array('match' => array(array('path' => '#^/pay#')))),"
                            " 'bots' => array('X/1'));")
        self.assertEqual((result.returncode, result.stdout), (0, "Traffic classes: human, payment, api, bot, asset, attack\n"))
        # Class constants and anonymous classes are no declarations.
        result = self.check("<?php $a = Closure::class; $b = new class {};\n"
                            "return array('classes' => array('own' => array('match' => function (array $r) { return $r['agent'] === 'x'; })));")
        self.assertEqual((result.returncode, result.stdout), (0, "Traffic classes: human, own, api, bot, asset, attack\n"), result.stderr)
        result = self.check(None)
        self.assertEqual(result.returncode, 0)
        self.assertIn("the built-in classes apply (human, api, bot, asset, attack)", result.stdout)

    def test_every_mistake_is_named(self):
        result = self.check("""<?php return array('other' => 1, 'bots' => array(''), 'classes' => array(
            'Bad-Name' => array('match' => array(array('path' => '#x#'))),
            'bot' => array('match' => array(array('agent' => '#x#'))),
            'human' => false,
            'nomatch' => array('label' => 'x'),
            'empty' => array('match' => array()),
            'fields' => array('label' => str_repeat('x', 41), 'colour' => 1, 'match' => array(array('path' => '#(#', 'ip' => '10.0.0.0/33',
                'status' => '200', 'method' => array(), 'agent' => 5, 'size' => 1))),
            'api' => array('extend' => 'yes', 'match' => array(array('agent' => '#x#'))),
        ));""")
        self.assertEqual(result.returncode, 3, result.stderr)
        for reason in ("unknown key 'other' (classes, bots)", "'bots' must be a list of user-agent words",
                       "class 'Bad-Name': a name is a-z, 0-9 and _", "class 'bot' is built in (add crawlers under 'bots')",
                       "class 'human' is built in; only api can be changed", "class 'nomatch': no 'match'",
                       "class 'empty': 'match' is a list of rules", "class 'fields': unknown key 'colour'",
                       "class 'fields': the label is a text of 40 characters at most",
                       "class 'fields' rule 1: path is not a valid regular expression",
                       "class 'fields' rule 1: 10.0.0.0/33 is not an IP address or CIDR range",
                       "class 'fields' rule 1: status is a status code or a list of them", "class 'fields' rule 1: method is empty",
                       "class 'fields' rule 1: agent is not a valid regular expression",
                       "class 'fields' rule 1: unknown field 'size'", "class 'api': extend is true or false"):
            self.assertIn(reason, result.stderr)

    def test_what_would_break_the_service_is_refused_before_the_file_runs(self):
        # The dashboard defines h(): a named function could already exist where the file is read, and exit ends it.
        result = self.check("<?php\nfunction h($r) { return true; }\nclass Rules {}\nif (getenv('X')) exit(1);\n"
                            "return array('classes' => array());")
        self.assertEqual((result.returncode, result.stdout), (3, ""))
        for reason in ("line 2: function 'h': use a closure", "line 3: no class declarations", "line 4: exit would end the analysis"):
            self.assertIn(reason, result.stderr)

    def test_eleven_classes_a_syntax_error_and_no_array_are_refused(self):
        classes = ", ".join(f"'c{n}' => array('match' => array(array('path' => '#^/{n}#')))" for n in range(11))
        result = self.check(f"<?php return array('classes' => array({classes}));")
        self.assertEqual(result.returncode, 3)
        self.assertIn("11 classes; 10 at most", result.stderr)
        result = self.check("<?php return array(")
        self.assertEqual(result.returncode, 3)
        self.assertIn("ParseError on line", result.stderr)
        result = self.check("<?php echo 'noise'; return 'classes';")
        self.assertEqual((result.returncode, result.stdout), (3, ""))
        self.assertIn("it must return an array with 'classes'", result.stderr)


class TrafficMemoryTest(TrafficTest):
    """The analysis keeps its lists bounded: a busy day within 64 MB (the metrics service has 128 MB)."""
    php = ("-d", "memory_limit=64M")

    def test_a_busy_day_within_the_memory_limit(self):
        moment = datetime.datetime(2026, 10, 13, 8, tzinfo=ZoneInfo(ZONE))
        with gzip.open(self.logs / "nginx-proxy/access-2026-10-13.log.gz", "wt", compresslevel=1) as handle:
            for number in range(200000):
                at = moment + datetime.timedelta(seconds=number // 4)
                handle.write(line(f"10.{number % 200}.{number // 200 % 250}.{number % 7}", at, f"GET /lt/p-{number % 30000}?x={number} HTTP/1.1",
                                  agent=f"agent-{number % 5000}", rt=f"0.{number % 1000:03d}"))
        report = self.report("day=2026-10-13")
        self.assertEqual(report["requests"], 200000)
        self.assertEqual(len(report["ips"]), 20)
        self.assertIsNotNone(report["p95"])

    def test_preparing_busy_days_stays_within_the_memory_limit(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for days in (6, 4, 2):
            moment = (now - datetime.timedelta(days=days)).astimezone(ZoneInfo(ZONE)).replace(hour=1, minute=0, second=0)
            with gzip.open(self.logs / f"nginx-proxy/access-{moment:%Y-%m-%d}.log.gz", "wt", compresslevel=1) as handle:
                for number in range(60000):
                    at = moment + datetime.timedelta(seconds=number)
                    handle.write(line(f"10.{days}.{number // 250 % 250}.{number % 250}", at, f"GET /p/{number} HTTP/1.1", agent=f"a{number}"))
        result = subprocess.run(["php", "-d", "memory_limit=64M", str(SAMPLER), "--traffic", "--once"], env=self.environment,
                                capture_output=True, text=True, timeout=600)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(list((self.snapshots / "traffic").glob("*.json"))), 7 * 24)

    # The other tests need no second run under the limit.
    test_a_known_log_gives_the_scripts_bots_injections_and_ips = None
    test_attack_types_bots_of_the_project_and_tool_sites = None
    test_closed_hours_are_read_once_and_a_7_day_window_merges_them = None
    test_the_sampler_prepares_the_closed_hours = None
    test_an_agent_with_escaped_bytes_is_cached_and_served = None
    test_hours_are_not_cached_while_the_log_collector_is_stopped = None
    test_a_window_after_midnight_reads_no_gzipped_yesterday = None


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class TrafficMetricsTest(EndpointMixin, unittest.TestCase):
    """/metrics: the bounded traffic set of the HTTP window (the fixture logs' [12:29, 12:34) of 2026-10-09)."""

    def setUp(self):
        self.prepare()

    def scrape(self, **env):
        status, _, body = fetch(self.start(**env), token=TOKEN)
        self.assertEqual(status, 200, body)
        return parse(body)

    def test_classes_slow_requests_and_percentiles_of_the_window(self):
        metrics = self.scrape()
        # Nine site requests in the window (the scrape of metrics.demo.local is left out), none a bot or an attack: two
        # by curl and a TLS handshake without an agent are scripts.
        self.assertEqual(labelled(metrics, "setup_http_class_requests", "class"),
                         {"human": 6, "api": 3, "bot": 0, "asset": 0, "attack": 0})
        self.assertEqual(labelled(metrics, "setup_http_attack_requests", "type"), {"sqli": 0, "traversal": 0, "probe": 0, "xss": 0})
        self.assertNotIn("setup_http_bot_requests", metrics)
        # Timed lines in the window: 0, 12, 250 and 30001 ms; one over 2 s.
        self.assertEqual((metrics["setup_http_response_time_p50_seconds"][""], metrics["setup_http_response_time_p95_seconds"][""]),
                         (0.012, 30.001))
        self.assertEqual(metrics["setup_http_slow_requests"][""], 1)
        self.assertEqual(self.scrape(METRICS_SLOW_REQUEST_SECONDS="0.2")["setup_http_slow_requests"][""], 2)
        self.assertEqual(labelled(metrics, "setup_http_url_5xx_requests", "url"), {"/error": 1})
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 1)

    def test_the_projects_classes_are_labels_and_an_invalid_file_keeps_the_group_up(self):
        config = self.work / "traffic.php"
        config.write_text("<?php return array('classes' => array('failing' => array('match' => array(array('path' => '#^/error$#')))));")
        metrics = self.scrape(METRICS_TRAFFIC_CONFIG=str(config))
        # curl's /error is the project's class now.
        self.assertEqual(labelled(metrics, "setup_http_class_requests", "class"),
                         {"human": 6, "failing": 1, "api": 2, "bot": 0, "asset": 0, "attack": 0})
        config.write_text("<?php return array('classes' => array('bot' => array('match' => array(array('path' => '#^/error$#')))));")
        metrics = self.scrape(METRICS_TRAFFIC_CONFIG=str(config))
        self.assertEqual(labelled(metrics, "setup_http_class_requests", "class"),
                         {"human": 6, "api": 3, "bot": 0, "asset": 0, "attack": 0})
        # Every figure is there, so the group is up; the dashboard says what is wrong.
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="http"}'], 1)
        problems = json.loads((self.work / "snapshots/snapshot.json").read_text())["problems"]["http"]
        self.assertEqual(problems, ["config/metrics/traffic.php: class 'bot' is built in (add crawlers under 'bots'); only api "
                                    "can be changed. The built-in classes apply until it is fixed."])

    def test_label_sets_are_bounded_to_the_busiest(self):
        listed = json.loads(subprocess.run(["php", "-r", "require $argv[1]; echo json_encode(TRAFFIC_BOTS);",
                                            str(ROOT / "docker/runtime/metrics/traffic.php")], capture_output=True, text=True, check=True).stdout)
        # Thirty names none of which holds another (each agent names one bot).
        bots = [bot for bot in listed if not any(other != bot and (other in bot or bot in other) for other in listed)][:30]
        self.assertEqual(len(bots), 30)
        moment = datetime.datetime(2026, 10, 9, 12, 30, 15, tzinfo=ZoneInfo(ZONE))
        rows = []
        for rank, bot in enumerate(bots):
            rows += [line("192.0.2.1", moment, "GET /lt/ HTTP/1.1", agent=f"Mozilla/5.0 (compatible; {bot})")] * (rank + 1)
        for rank in range(15):
            rows += [line("198.51.100.9", moment, f"GET /lt/page-{rank}?q={rank} HTTP/1.1")] * (100 + rank)
            rows += [line("198.51.100.9", moment, f"POST /lt/fail-{rank} HTTP/1.1", status=502)] * (rank + 1)
        with (self.logs / "nginx-proxy/access-2026-10-09.log").open("a") as handle:
            handle.write("\n" + "".join(rows))
        metrics = self.scrape()
        served = labelled(metrics, "setup_http_bot_requests", "bot")
        self.assertEqual(len(served), 20)
        self.assertEqual(set(served), set(bots[10:]))
        # The bots' 465 requests of /lt/ make it the busiest URL; then the nine busiest pages.
        self.assertEqual(set(labelled(metrics, "setup_http_url_requests", "url")), {"/lt/"} | {f"/lt/page-{rank}" for rank in range(6, 15)})
        self.assertEqual(set(labelled(metrics, "setup_http_url_5xx_requests", "url")), {f"/lt/fail-{rank}" for rank in range(5, 15)})


if __name__ == "__main__":
    unittest.main()
