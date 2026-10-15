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
from test_metrics import ENDPOINT, ROOT, SAMPLER, fetch, free_port

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
class TrafficTest(unittest.TestCase):
    php = ()

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.logs = self.work / "logs"
        (self.logs / "nginx-proxy").mkdir(parents=True)
        self.snapshots = self.work / "snapshots"
        self.snapshots.mkdir()
        self.environment = {"PATH": "/usr/bin:/bin", "METRICS_SNAPSHOT_DIR": str(self.snapshots), "METRICS_LOGS_DIR": str(self.logs),
                            "TZ": ZONE, "DOMAIN": "demo.local", "METRICS_BOTS": "ShopCrawler/1.0, Inhouse Monitor"}
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

    def test_a_known_log_gives_the_scripts_bots_injections_and_ips(self):
        shutil.copy(FIXTURES / "access-2026-10-14.log", self.logs / "nginx-proxy")
        expected = script_result()
        report = self.report("day=2026-10-14")
        self.assertEqual(report["bots"], expected["bots"])
        self.assertEqual(report["attack_lines"]["sqli"], expected["sqli"])
        self.assertEqual(report["attacks"], {"sqli": 3})
        self.assertEqual(report["suspicious_ips"], expected["suspicious"])
        self.assertEqual(report["regular_ips"], expected["regular"])
        self.assertEqual(report["classes"], {"human": 41, "bot": 21, "asset": 35, "attack": 3})
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
        self.assertEqual(report["classes"], {"human": 1, "bot": 2, "asset": 0, "attack": 6})
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

    # The other tests need no second run under the limit.
    test_a_known_log_gives_the_scripts_bots_injections_and_ips = None
    test_attack_types_bots_of_the_project_and_tool_sites = None
    test_closed_hours_are_read_once_and_a_7_day_window_merges_them = None
    test_the_sampler_prepares_the_closed_hours = None


if __name__ == "__main__":
    unittest.main()
