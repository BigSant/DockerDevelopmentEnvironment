"""Dashboard of metrics.<domain>: the Overview's cards read only the sampler's snapshot and history (no /proc, no
logs, no database), colour by setup's fixed limits and draw 24-hour lines; the page refreshes its body. The Logs
tab lists the log collector's files, shows a file's end filtered (gzipped too) and serves it whole, never a path
outside the collector's directory."""
import datetime
import gzip
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_metrics import ENDPOINT, ROOT, fetch, free_port


def family(samples, help_text="x"):
    return {"help": help_text, "type": "gauge", "samples": [[labels, value] for labels, value in samples]}


def snapshot(**overrides):
    """A snapshot as the sampler writes it: 4 CPUs, load 2, 3 % I/O wait, 50 % RAM, a 70 % disk."""
    families = {
        "cpu_usage_idle": family([({"cpu": "all"}, 90), ({"cpu": "0"}, 90), ({"cpu": "1"}, 90), ({"cpu": "2"}, 90), ({"cpu": "3"}, 90)]),
        "cpu_usage_iowait": family([({"cpu": "all"}, 3)]),
        "uptime_average_load_1min": family([({}, 2)]),
        "uptime_average_load_10min": family([({}, 1.5)]),
        "uptime_current_time": family([({}, int(time.time()))]),
        "ram_total": family([({}, 16 * 1048576)]),
        "ram_available": family([({}, 8 * 1048576)]),
        "disk_available_percentage": family([({}, 70)]),
        "disk_size": family([({}, 100 * 1048576)]),
        "disk_used": family([({}, 70 * 1048576)]),
        "http_requests_per_minute": family([({}, 120)]),
        "http_response_codes": family([({"code": "200"}, 110), ({"code": "502"}, 3)]),
        "database_connections": family([({}, 10)]),
        "database_connections_running": family([({}, 1)]),
        "database_connections_max": family([({}, 151)]),
        "setup_container_running": family([({"project": "demo", "service": "php-fpm", "container": "demo-php-fpm"}, 1),
                                           ({"project": "demo", "service": "cron", "container": "demo-cron"}, 0)]),
        "website_response_status": family([({"url": "https://demo.example/"}, 200)]),
        "website_response_time": family([({"url": "https://demo.example/"}, 0.4)]),
        "setup_metrics_group_up": family([({"group": "database"}, 1), ({"group": "containers"}, 1)]),
    }
    families.update(overrides)
    return {"taken": int(time.time()), "interval": 60, "families": families}


class DashboardServer:
    php = ()
    zone = "UTC"

    def setUp(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.snapshots = self.work / "snapshots"
        self.snapshots.mkdir()
        port = free_port()
        # Nothing to collect from: a page that read /proc, the logs or a database would show nothing.
        environment = {"PATH": "/usr/bin:/bin", "METRICS_SNAPSHOT_DIR": str(self.snapshots), "METRICS_PROC_ROOT": str(self.work / "none"),
                       "METRICS_LOGS_DIR": str(self.work / "none"), "METRICS_DATABASE_FIXTURE": str(self.work / "none.json"),
                       "ENV": "prod", "PROJECT_NAME": "demo", "TZ": self.zone}
        self.logs = self.work / "logs"
        environment["METRICS_LOGS_DIR"] = str(self.logs)
        server = subprocess.Popen(["php", *self.php, "-S", f"127.0.0.1:{port}", str(ENDPOINT)], env=environment,
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

    def write(self, data):
        (self.snapshots / "snapshot.json").write_text(json.dumps(data))


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class TabRefreshTest(DashboardServer, unittest.TestCase):
    # Not UTC (and not the PHP CLI's default zone): the stamps must follow TZ.
    zone = "Pacific/Chatham"

    def test_every_tab_refreshes_its_body_alone_and_shows_times_in_tz(self):
        data = snapshot()
        self.write(data)
        stamp = "Snapshot " + datetime.datetime.fromtimestamp(data["taken"], ZoneInfo(self.zone)).strftime("%Y-%m-%d %H:%M:%S")
        for path in ("/", "/database", "/containers", "/cron"):
            status, _, page = fetch(self.base, path)
            self.assertEqual(status, 200, path)
            self.assertIn("<!doctype html>", page, path)
            self.assertIn(stamp, page, path)
            status, _, fragment = fetch(self.base, path + "?fragment=1")
            self.assertEqual(status, 200, path)
            # The refresh puts this into <main>: no second page, top bar or nav inside it.
            self.assertNotIn("<!doctype", fragment, path)
            self.assertNotIn('class="top"', fragment, path)
            self.assertNotIn("<main>", fragment, path)
            self.assertIn(stamp, fragment, path)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class ProblemsTest(DashboardServer, unittest.TestCase):
    def test_the_sampler_s_reasons_say_what_is_missing(self):
        data = snapshot(setup_metrics_group_up=family([({"group": "database"}, 0), ({"group": "php"}, 0), ({"group": "containers"}, 1)]))
        for name in [name for name in data["families"] if name.startswith("database_")]:
            del data["families"][name]
        data["problems"] = {"database": ["database: DATABASE_MONITOR_PASSWORD is not set: run make db-monitor-user ENV=prod"],
                            "php": ["cannot reach php-fpm at php-fpm:9000: <refused & gone>"]}
        self.write(data)
        overview = fetch(self.base, "/")[2]
        self.assertIn("<li><b>database</b>: database: DATABASE_MONITOR_PASSWORD is not set: run make db-monitor-user ENV=prod</li>", overview)
        # Escaped like every other value.
        self.assertIn("&lt;refused &amp; gone&gt;", overview)
        self.assertNotIn("<refused", overview)
        # A tab shows its own group's reasons instead of the general hint.
        tab = fetch(self.base, "/database")[2]
        self.assertIn("DATABASE_MONITOR_PASSWORD is not set", tab)
        self.assertNotIn("php-fpm", tab)
        self.assertNotIn("No data: the DB monitor account", tab)
        # A snapshot without reasons (taken before the sampler kept them): the general hint, no callout.
        del data["problems"]
        self.write(data)
        self.assertNotIn("data-problems", fetch(self.base, "/")[2])
        self.assertIn("No data: the DB monitor account gives no statistics", fetch(self.base, "/database")[2])


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class OverviewTest(DashboardServer, unittest.TestCase):

    def cards(self, path="/"):
        status, kind, body = fetch(self.base, path)
        self.assertEqual(status, 200, body)
        cards = {}
        for match in re.finditer(r'<section class="card (\w+)" data-card="(\w+)"><h2>[^<]*</h2><div class="value">([^<]*)</div>'
                                 r'<div class="note">([^<]*)</div>(<svg|<div class="nohistory")', body):
            cards[match[2]] = (match[1], match[3], match[4], match[5] == "<svg")
        return body, cards

    def test_cards_show_the_snapshot_and_colour_by_the_fixed_limits(self):
        self.write(snapshot())
        body, cards = self.cards()
        self.assertIn("<title>Overview · demo</title>", body)
        self.assertEqual(cards["load"][:3], ("ok", "2.00", "5 min 1.50 · 4 cores"))
        self.assertEqual(cards["iowait"][:2], ("ok", "3.0 %"))
        self.assertEqual(cards["ram"][:3], ("ok", "50.0 %", "8.0 GB available of 16.0 GB"))
        self.assertEqual(cards["disk"][:3], ("ok", "70 %", "30.0 GB free"))
        self.assertEqual(cards["errors"][:3], ("warn", "3.0", "120.0 requests / min"))
        self.assertEqual(cards["database"][:2], ("ok", "10 conn."))
        self.assertEqual(cards["containers"][:3], ("bad", "1 / 2", "1 not running"))
        self.assertEqual(cards["pages"][:3], ("ok", "1 / 1 OK", "slowest 0.40 s"))
        # Without its figures a card is not shown: no p95 before the traffic analysis, no cron without the service.
        self.assertNotIn("p95", cards)
        self.assertNotIn("cron", cards)
        # Over the limits: load above the cores, I/O wait over 15 %, memory and disk over 90 %.
        self.write(snapshot(uptime_average_load_1min=family([({}, 4.5)]), cpu_usage_iowait=family([({"cpu": "all"}, 16)]),
                            ram_available=family([({}, 1048576)]), disk_available_percentage=family([({}, 85)]),
                            setup_http_response_time_p50_seconds=family([({}, 0.3)]),
                            setup_http_response_time_p95_seconds=family([({}, 3.2)])))
        _, cards = self.cards()
        self.assertEqual((cards["load"][0], cards["iowait"][0], cards["ram"][0], cards["disk"][0]), ("bad", "bad", "bad", "warn"))
        self.assertEqual(cards["p95"][:3], ("bad", "3.20 s", "median 0.30 s"))
        # Load between 70 % of the cores and the core count, I/O wait between 5 and 15 %: warnings.
        self.write(snapshot(uptime_average_load_1min=family([({}, 3)]), cpu_usage_iowait=family([({"cpu": "all"}, 6)])))
        _, cards = self.cards()
        self.assertEqual((cards["load"][0], cards["iowait"][0]), ("warn", "warn"))

    def test_lines_come_from_the_last_24_hours_of_history(self):
        self.write(snapshot())
        history = self.snapshots / "history"
        history.mkdir()
        now = int(time.time())
        for minutes_ago, load in ((26 * 60, 50), (120, 1), (60, 3), (1, 2)):
            moment = now - minutes_ago * 60
            with (history / time.strftime("%Y-%m-%d-%H.jsonl", time.gmtime(moment))).open("a") as handle:
                handle.write(json.dumps({"t": moment, "v": {"uptime_average_load_1min": load}}) + "\n")
        body, cards = self.cards()
        self.assertTrue(cards["load"][3], "the load card draws its line")
        self.assertFalse(cards["iowait"][3], "no I/O wait in the history: no line")
        line = re.search(r'data-card="load">.*?<polyline points="([^"]+)"', body)[1]
        heights = [float(point.split(",")[1]) for point in line.split()]
        # Three points within 24 hours (the 26-hour-old 50 is not among them): the highest, 3, at the top.
        self.assertEqual(len(heights), 3)
        self.assertEqual(min(heights), 4.0)

    def test_without_a_snapshot_or_with_an_old_one_the_page_says_so(self):
        body = fetch(self.base, "/")[2]
        self.assertIn("No snapshot yet", body)
        old = snapshot()
        old["taken"] -= 600
        self.write(old)
        body, cards = self.cards()
        self.assertIn("The latest snapshot is 10 minutes old", body)
        self.assertIn("load", cards)

    def test_the_body_refreshes_itself_from_the_fragment(self):
        self.write(snapshot())
        page = fetch(self.base, "/")[2]
        self.assertIn('fragment=1', page)
        self.assertIn("setInterval", page)
        status, kind, fragment = fetch(self.base, "/?fragment=1")
        self.assertEqual((status, kind), (200, "text/html; charset=utf-8"))
        self.assertTrue(fragment.startswith('<div class="cards">'), fragment[:80])
        self.assertNotIn("<html", fragment)
        # The page loads nothing from elsewhere.
        self.assertNotRegex(page, r'(src|href)="(https?:)?//')


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class LogsTabTest(DashboardServer, unittest.TestCase):
    # The tab works within a small memory limit, whatever the files' size.
    php = ("-d", "memory_limit=16M")

    def setUp(self):
        super().setUp()
        (self.logs / "php-fpm").mkdir(parents=True)
        (self.logs / "nginx-proxy").mkdir()
        (self.logs / "php-fpm/error-2026-10-15.log").write_text(
            "[15-Oct-2026 10:00:00 UTC] PHP Warning:  first\n[15-Oct-2026 10:00:01 UTC] PHP Fatal error:  boom\n"
            "[15-Oct-2026 10:00:02 UTC] PHP Notice:  quiet\n")
        with gzip.open(self.logs / "nginx-proxy/access-2026-10-14.log.gz", "wt") as handle:
            for number in range(1000):
                status = 502 if number % 100 == 0 else 200
                handle.write(f'203.0.113.{number % 50} - - [14/Oct/2026:10:00:00 +0300] "GET /p/{number} HTTP/1.1" {status} 10 "-" "a"\n')

    def lines(self, body):
        found = re.search(r'<pre class="log">(.*?)</pre>', body, re.S)
        return html.unescape(found[1]).splitlines() if found else []

    def test_the_list_shows_every_service_and_day_with_sizes(self):
        status, _, body = fetch(self.base, "/logs/")
        self.assertEqual(status, 200)
        self.assertIn('href="/logs/view?file=php-fpm%2Ferror-2026-10-15.log"', body)
        self.assertIn('href="/logs/download?file=nginx-proxy%2Faccess-2026-10-14.log.gz"', body)
        self.assertRegex(body, r"error-2026-10-15\.log</a></td><td class=\"num\">\d+ B<")
        self.assertIn('class="on">Logs</a>', body)

    def test_the_end_of_a_file_filtered_by_level_and_text_gzipped_too(self):
        body = fetch(self.base, "/logs/view?file=php-fpm%2Ferror-2026-10-15.log")[2]
        self.assertEqual(len(self.lines(body)), 3)
        body = fetch(self.base, "/logs/view?file=php-fpm%2Ferror-2026-10-15.log&level=error")[2]
        self.assertEqual(self.lines(body), ["[15-Oct-2026 10:00:01 UTC] PHP Fatal error:  boom"])
        body = fetch(self.base, "/logs/view?file=php-fpm%2Ferror-2026-10-15.log&q=FIRST")[2]
        self.assertEqual(len(self.lines(body)), 1)
        body = fetch(self.base, "/logs/view?file=nginx-proxy%2Faccess-2026-10-14.log.gz&level=5xx")[2]
        self.assertEqual([line.split('"GET ')[1].split()[0] for line in self.lines(body)], [f"/p/{n}" for n in range(0, 1000, 100)])
        body = fetch(self.base, "/logs/view?file=nginx-proxy%2Faccess-2026-10-14.log.gz&lines=100")[2]
        lines = self.lines(body)
        self.assertEqual((len(lines), lines[-1].split('"GET ')[1].split()[0]), (100, "/p/999"))

    def test_a_large_file_is_read_within_the_memory_limit(self):
        line = '198.51.100.1 - - [15/Oct/2026:10:00:00 +0300] "GET / HTTP/1.1" 200 10 "-" "' + "x" * 200 + '"\n'
        with (self.logs / "nginx-proxy/access-2026-10-15.log").open("w") as handle:
            for _ in range(150000):
                handle.write(line)
            handle.write(line.replace(" 200 ", " 503 "))
        with gzip.open(self.logs / "nginx-proxy/access-2026-10-13.log.gz", "wt", compresslevel=1) as handle:
            for _ in range(150000):
                handle.write(line)
        self.assertGreater((self.logs / "nginx-proxy/access-2026-10-15.log").stat().st_size, 30 << 20)
        for name in ("access-2026-10-15.log", "access-2026-10-13.log.gz"):
            status, _, body = fetch(self.base, f"/logs/view?file=nginx-proxy%2F{name}&lines=2000")
            self.assertEqual(status, 200, body[-500:])
            self.assertEqual(len(self.lines(body)), 2000)
        body = fetch(self.base, "/logs/view?file=nginx-proxy%2Faccess-2026-10-15.log&level=5xx")[2]
        self.assertEqual(len(self.lines(body)), 1)

    def test_paths_outside_the_collector_files_are_refused(self):
        (self.work / "secret.log").write_text("secret\n")
        for asked in ("../secret.log", "php-fpm/../../secret.log", "/etc/passwd", "php-fpm/error-2026-10-15.log/../x",
                      "php-fpm%2Ferror-2026-10-16.log", "PHP-FPM/error-2026-10-15.log", "php-fpm/other-2026-10-15.log", ""):
            for page in ("view", "download"):
                status, _, body = fetch(self.base, f"/logs/{page}?file={asked.replace('/', '%2F')}")
                self.assertEqual(status, 404, (page, asked))
                self.assertNotIn("secret", body)
        # A symlink in the collector's directory is not followed out of it either: it is not a listed day file.
        (self.logs / "php-fpm/error-2026-10-01.log").symlink_to(self.work / "secret.log")
        (self.logs / "elsewhere").symlink_to(self.work, target_is_directory=True)
        (self.work / "access-2026-10-01.log").write_text("secret\n")
        for asked in ("php-fpm%2Ferror-2026-10-01.log", "elsewhere%2Faccess-2026-10-01.log"):
            for page in ("view", "download"):
                status, _, body = fetch(self.base, f"/logs/{page}?file={asked}")
                self.assertEqual(status, 404, (page, asked))
                self.assertNotIn("secret", body)
        self.assertNotIn("2026-10-01", fetch(self.base, "/logs/")[2])

    def test_the_download_is_the_file_as_it_is(self):
        request = urllib.request.Request(self.base + "/logs/download?file=nginx-proxy%2Faccess-2026-10-14.log.gz")
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
            self.assertEqual(response.headers["Content-Type"], "application/gzip")
            self.assertIn('filename="access-2026-10-14.log.gz"', response.headers["Content-Disposition"])
        self.assertEqual(data, (self.logs / "nginx-proxy/access-2026-10-14.log.gz").read_bytes())


if __name__ == "__main__":
    unittest.main()
