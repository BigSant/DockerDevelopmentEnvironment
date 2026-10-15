"""Target /metrics database group: the script's DB names over the monitor account; B: real MySQL with performance_schema."""

import contextlib
import io
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker"), str(Path(__file__).resolve().parent)]
from create_project import scaffold
from database_grants import MONITOR
import grafana_names
from project import Project
from project_release import release_model
from test_metrics import REMOTE, TOKEN, EndpointMixin, fetch, parse, tab_rows

FIXTURE = Path(__file__).resolve().parent / "fixtures/metrics-database.json"
CUSTOMER = 'SELECT * FROM `customer` WHERE `email` = ?'
SLEEP = 'SELECT `SLEEP` (?)'
CART = 'UPDATE `cart` SET `qty` = ? WHERE `id` = ?'
QUOTED = 'SELECT `a"b\\c` FROM `t`'


def label(text):
    return '{query="%s"}' % text.replace("\\", "\\\\").replace('"', '\\"')


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class DatabaseGroupTest(EndpointMixin, unittest.TestCase):
    """The query-to-metric mapping, from fixture query results (METRICS_DATABASE_FIXTURE)."""

    def setUp(self):
        self.prepare()
        self.fixture = self.work / "database.json"
        self.data = json.loads(FIXTURE.read_text())
        self.save()
        self.base = self.start(METRICS_DATABASE_FIXTURE=str(self.fixture), METRICS_DISK_WRITE_MB="0")

    def save(self):
        self.fixture.write_text(json.dumps(self.data))

    def scrape(self):
        status, _, body = fetch(self.base, token=TOKEN)
        self.assertEqual(status, 200, body)
        return body, parse(body)

    def test_capacity_and_connections_use_the_script_units(self):
        body, metrics = self.scrape()
        # INNODB_BUFFER_POOL_STATS pages x innodb_page_size in MB, the used share in %, dirty pages in MB.
        self.assertEqual({name: metrics[name][""] for name in ("sql_buffer_pool_size", "sql_data_cached", "sql_ram_free",
                                                               "sql_buffer_used", "sql_dirty_pages")},
                         {"sql_buffer_pool_size": 128, "sql_data_cached": 64, "sql_ram_free": 62.5, "sql_buffer_used": 50,
                          "sql_dirty_pages": 1.56})
        self.assertEqual((metrics["database_connections"][""], metrics["database_connections_running"][""],
                          metrics["database_connections_max"][""]), (7, 2, 151))
        self.assertEqual(metrics["database_performance_schema"][""], 1)
        self.assertIn('setup_metrics_group_up{group="database"} 1', body)

    def test_statements_cover_the_window_since_the_previous_scrape(self):
        body, metrics = self.scrape()
        # First scrape: everything since the database started; one series per statement text across schemas.
        self.assertEqual(metrics["database_slow_query_total"],
                         {label(CUSTOMER): 12, label(SLEEP): 1, label(CART): 100, label(QUOTED): 4, label("NULL"): 5})
        self.assertEqual(metrics["database_slow_query_time_total"][label(CUSTOMER)], 6)
        self.assertEqual(metrics["database_slow_query_time_average"][label(CUSTOMER)], 0.5)
        self.assertEqual(metrics["database_slow_query_time_average"][label(SLEEP)], 3)
        self.assertEqual(metrics["database_slow_query_time_average"][label(CART)], 0.02)
        # Table scans: only shapes that examined rows, by rows examined.
        self.assertEqual(metrics["database_table_scan_query_total_row_examined"], {label(CUSTOMER): 1200, label(CART): 100})
        self.assertEqual(metrics["database_table_scan_query_total_row_sent"], {label(CUSTOMER): 12, label(CART): 0})
        self.assertEqual(metrics["database_table_scan_query_total"], {label(CUSTOMER): 12, label(CART): 100})
        self.assertEqual(metrics["database_total_time_query_time_total"][label(CUSTOMER)], 6)
        self.assertEqual(metrics["database_total_time_query_total"][label(CART)], 100)
        self.assertEqual(metrics["database_total_time_query_time_average"][label(SLEEP)], 3)
        # Order in the exposition follows the script: slowest average first.
        slow = [line for line in body.splitlines() if line.startswith("database_slow_query_time_average{")]
        self.assertTrue(slow[0].startswith("database_slow_query_time_average" + label(SLEEP)), slow)

        digests = self.data["digests"]
        digests[0].update(executions="12", wait="9000000000000", rows_examined="1100", rows_sent="11")
        digests[3].update(executions="3", wait="30000000000", rows_examined="3")  # reset: counted from zero
        self.save()
        _, metrics = self.scrape()
        # Second scrape: only what ran since the first one, as the script's truncate-after-each-run did.
        self.assertEqual(metrics["database_slow_query_total"], {label(CUSTOMER): 2, label(CART): 3})
        self.assertEqual(metrics["database_slow_query_time_total"], {label(CUSTOMER): 4, label(CART): 0.03})
        self.assertEqual(metrics["database_slow_query_time_average"], {label(CUSTOMER): 2, label(CART): 0.01})
        self.assertEqual(metrics["database_table_scan_query_total_row_examined"], {label(CUSTOMER): 100, label(CART): 3})
        _, metrics = self.scrape()
        self.assertNotIn("database_slow_query_total", metrics)

    def test_each_statement_list_keeps_the_top_ten(self):
        self.data["digests"] = [{"schema_name": "shop", "digest": f"x{index}", "query": f"SELECT `c{index}` FROM `t`",
                                 "executions": "1", "wait": str((index + 1) * 10 ** 11), "max_wait": str((index + 1) * 10 ** 11),
                                 "rows_examined": str(index), "rows_sent": "1"} for index in range(12)]
        self.save()
        _, metrics = self.scrape()
        expected = {label(f"SELECT `c{index}` FROM `t`") for index in range(2, 12)}
        for name in ("database_slow_query_time_average", "database_table_scan_query_total_row_examined",
                     "database_total_time_query_time_total"):
            self.assertEqual(set(metrics[name]), expected, name)

    def test_slowest_query_locks_table_io_and_running_statements(self):
        _, metrics = self.scrape()
        # The slowest single execution, not an average: SLEEP's 3 s beats the customer shape's 2 s maximum.
        self.assertEqual(metrics["database_slowest_query_time"], {label(SLEEP): 3})
        table = '{schema="shop",table="customer"}'
        self.assertEqual(metrics["database_table_lock_total"], {table: 40, '{schema="shop",table="cart"}': 5})
        self.assertEqual(metrics["database_table_lock_time_total"][table], 0.25)
        self.assertEqual((metrics["database_table_io_read_total"][table], metrics["database_table_io_write_total"][table],
                          metrics["database_table_io_read_time_total"][table], metrics["database_table_io_write_time_total"][table]),
                         (900, 10, 1.5, 0.2))
        self.assertEqual(metrics["database_running_statements"][""], 3)
        self.assertEqual(metrics["database_running_statement_time"], {label(SLEEP): 4, label("NULL"): 0.0005})

    def test_performance_schema_off_is_a_state_not_an_error(self):
        self.data["server"][0]["performance_schema"] = "0"
        for name in ("digests", "table_locks", "table_io", "running"):
            self.data[name] = {"error": "performance_schema is off and must not be queried"}
        self.save()
        body, metrics = self.scrape()
        self.assertEqual(metrics["database_performance_schema"][""], 0)
        self.assertIn('setup_metrics_group_up{group="database"} 1', body)
        self.assertEqual(metrics["sql_buffer_pool_size"][""], 128)
        self.assertEqual(metrics["database_connections"][""], 7)
        self.assertFalse([name for name in metrics if name.startswith(("database_slow", "database_table", "database_total",
                                                                        "database_running", "database_slowest"))])

    def test_a_failed_query_only_clears_the_group_flag(self):
        self.data["table_io"] = {"error": "SELECT command denied"}
        self.save()
        body, metrics = self.scrape()
        self.assertIn('setup_metrics_group_up{group="database"} 0', body)
        self.assertIn('setup_metrics_group_up{group="system"} 1', body)
        self.assertNotIn("database_table_io_read_total", metrics)
        self.assertIn("database_table_lock_total", metrics)
        self.assertIn("sql_buffer_pool_size", metrics)

    def test_active_connections_count_established_tcp_like_netstat(self):
        # write_connections (test_metrics): every ESTABLISHED (01) line counts, loopback and port 22 included.
        _, metrics = self.scrape()
        self.assertEqual(metrics["active_connections"][""], 13)
        (self.proc / "1/net/tcp6").unlink()
        _, metrics = self.scrape()
        self.assertEqual(metrics["active_connections"][""], 10)

    def test_the_viewers_other_database_names_are_served(self):
        _, metrics = self.scrape()
        self.assertEqual(sorted(grafana_names.VIEWER_ONLY - set(metrics)), [])


    def test_the_database_tab_shows_the_values_of_metrics(self):
        body, metrics = self.scrape()
        page = fetch(self.base, "/database")[2]
        # The sample /metrics served, unchanged: the tab reads the same snapshot.
        slow = {row[0]: row[1:] for row in tab_rows(page, "Slow statements (by average time)")}
        for labels, runs in metrics["database_slow_query_total"].items():
            query = re.search(r'query="(.*)"', labels)[1].replace('\\"', '"').replace("\\\\", "\\")
            self.assertEqual(slow[query][0], f"{int(runs):,}".replace(",", " "), query)
        capacity = tab_rows(page, "Capacity and connections")
        self.assertEqual(capacity[0][0], str(int(metrics["sql_buffer_pool_size"][""])))
        io = {tuple(row[:2]): row[2:] for row in tab_rows(page, "Table I/O")}
        self.assertTrue(io, page)
        self.assertIn('class="on">Database</a>', page)


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class DatabaseUnavailableTest(EndpointMixin, unittest.TestCase):
    def test_without_the_monitor_account_only_the_database_group_is_down(self):
        self.prepare()
        base = self.start(METRICS_DATABASE_FIXTURE="", DATABASE_MONITOR_PASSWORD="", METRICS_DISK_WRITE_MB="0")
        status, _, body = fetch(base, token=TOKEN)
        self.assertEqual(status, 200, body)
        self.assertIn('setup_metrics_group_up{group="database"} 0', body)
        self.assertIn('setup_metrics_group_up{group="system"} 1', body)
        self.assertNotIn("sql_buffer_pool_size", body)
        self.assertNotIn("database_performance_schema", body)
        # The dashboard says what is missing and what to run, on the tab and on the Overview.
        for path in ("/database", "/"):
            page = fetch(base, path)[2]
            self.assertIn("DATABASE_MONITOR_PASSWORD is not set", page, path)
            self.assertIn("make db-monitor-user ENV=", page, path)
        self.assertNotIn("DATABASE_MONITOR_PASSWORD", fetch(base, "/containers")[2])


class DatabaseComposeTest(unittest.TestCase):
    def test_the_database_joins_the_metrics_network_and_the_service_gets_the_monitor_account(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            app = scaffold("metrics-db", temporary.name)
        with (app / "env/common.env").open("a") as env:
            env.write("SERVICES=metrics\n")
        (app / "env/stage.env").write_text(REMOTE + f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n"
                                           "DATABASE_PERFORMANCE_SCHEMA=on\nDATABASE_MONITOR_PASSWORD=monitor-test-password\n")
        project = Project(app, "stage")
        services = json.loads(project.capture(["config", "--format", "json"]))["services"]
        metrics, database = services["metrics"], services["database"]
        # The metrics service stays off the project network; only the database joins its network.
        self.assertEqual(list(metrics["networks"]), ["network_metrics"])
        self.assertEqual(set(database["networks"]), {"network_app", "network_metrics"})
        self.assertNotIn("ports", database)
        self.assertNotIn("depends_on", metrics)
        self.assertEqual((metrics["environment"]["DATABASE_MONITOR_USER"], metrics["environment"]["DATABASE_MONITOR_PASSWORD"]),
                         ("demo_monitor", "monitor-test-password"))
        self.assertEqual(database["environment"]["DATABASE_PERFORMANCE_SCHEMA"], "on")
        released = release_model(project, "/srv/demo")["services"]
        self.assertIn("network_metrics", released["database"]["networks"])
        # Without the metrics service the database stays on the project network only.
        with (app / "env/common.env").open("a") as env:
            env.write("SERVICES=\n")
        services = json.loads(Project(app, "stage").capture(["config", "--format", "json"]))["services"]
        self.assertEqual(list(services["database"]["networks"]), ["network_app"])


MYSQL = os.environ.get("SETUP_MYSQL_IMAGE")
PHP = os.environ.get("PHP_TEST_IMAGE")


@unittest.skipUnless(MYSQL and PHP, "Set SETUP_MYSQL_IMAGE and PHP_TEST_IMAGE to built setup images")
class DatabaseMetricsIntegrationTest(unittest.TestCase):
    def start_database(self, performance_schema):
        network = f"setup-dbmetrics-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "network", "create", "--internal", network], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "network", "rm", network], capture_output=True)
        self.database = network + "-db"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.database, "--network", network, "--network-alias", "database",
                        "--memory", "1g", "--tmpfs", "/var/lib/mysql", "-e", "MYSQL_ROOT_PASSWORD=root-test-only",
                        "-e", "MYSQL_DATABASE=shop", "-e", "MYSQL_USER=shop", "-e", "MYSQL_PASSWORD=shop-test-only",
                        "-e", "DATABASE_BUFFER_POOL=128M", "-e", f"DATABASE_PERFORMANCE_SCHEMA={performance_schema}", MYSQL],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.database], capture_output=True)
        for _ in range(90):
            if self.sql("shop", "shop-test-only", "SELECT 1").returncode == 0:
                break
            time.sleep(2)
        created = subprocess.run(["docker", "exec", "-i", self.database, "sh", "-c", MONITOR, "monitor-user", "shop_monitor"],
                                 input="monitor-test-password\n", capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.endpoint = network + "-metrics"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", self.endpoint, "--network", network, "--memory", "128m",
                        "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro",
                        "-v", "/proc:/host/proc:ro", "-e", f"METRICS_TOKEN={TOKEN}", "-e", "METRICS_URLS=",
                        "-e", "METRICS_DISK_WRITE_MB=0", "-e", "DATABASE_MONITOR_USER=shop_monitor",
                        "-e", "DATABASE_MONITOR_PASSWORD=monitor-test-password",
                        # The service's loops (start.sh), sampling every 2 s.
                        "--tmpfs", "/var/lib/setup-metrics:mode=1777", "-e", "METRICS_SAMPLER_INTERVAL=2", "-e", "METRICS_PROBE_INTERVAL=5",
                        "--entrypoint", "sh", PHP, "/opt/setup/runtime/metrics/start.sh"],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", self.endpoint], capture_output=True)

    def sql(self, user, password, query):
        return subprocess.run(["docker", "exec", "-e", f"MYSQL_PWD={password}", self.database, "mysql", "-h127.0.0.1",
                               f"-u{user}", "-N", "-e", query], capture_output=True, text=True, timeout=60)

    def scrape(self):
        fetch_script = ("$o = array('http' => array('ignore_errors' => true, 'timeout' => 60, 'header' => 'Authorization: Bearer ' . $argv[1]));"
                        "echo file_get_contents('http://127.0.0.1:9100/metrics', false, stream_context_create($o));")
        # A snapshot the sampler took after this call: what the test did before it shows.
        after = int(time.time())
        for _ in range(60):
            body = subprocess.run(["docker", "exec", self.endpoint, "php", "-r", fetch_script, TOKEN],
                                  capture_output=True, text=True, timeout=90).stdout
            metrics = parse(body) if body.strip() else {}
            if metrics.get("setup_metrics_snapshot_timestamp_seconds", {}).get("", 0) > after:
                return body, metrics
            time.sleep(0.5)
        self.fail("no new snapshot from the metrics service")

    def test_a_wrong_or_missing_account_says_to_run_db_monitor_user(self):
        self.start_database("on")
        script = ("require '/opt/setup/runtime/metrics/metrics.php'; $m = new Metrics(); "
                  "echo json_encode(metrics_collect($m, 'database'));")
        answer = subprocess.run(["docker", "exec", "-e", "DATABASE_MONITOR_PASSWORD=not-the-monitor-password", "-e", "ENV=prod",
                                 self.endpoint, "php", "-r", script], capture_output=True, text=True, timeout=60)
        problems = json.loads(answer.stdout)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("shop_monitor cannot log in (access denied): make db-monitor-user ENV=prod", problems[0])

    def test_statistics_appear_and_the_monitor_reads_no_project_data(self):
        self.start_database("on")
        app = lambda query: self.sql("shop", "shop-test-only", "USE shop; " + query)
        self.assertEqual(app("CREATE TABLE customer (id int PRIMARY KEY, email varchar(64));"
                             "INSERT INTO customer VALUES (1, 'secret-customer@example.com'), (2, 'other@example.com');").returncode, 0)
        for _ in range(3):
            app("SELECT * FROM customer WHERE email = 'secret-customer@example.com';")
        app("LOCK TABLES customer WRITE; UPDATE customer SET email = 'secret-update@example.com' WHERE id = 2; UNLOCK TABLES;")
        app("SELECT SLEEP(1.2);")
        denied = self.sql("shop_monitor", "monitor-test-password", "SELECT * FROM shop.customer")
        self.assertNotEqual(denied.returncode, 0, "the monitor account cannot read project data")
        self.assertIn("denied", denied.stderr)

        body, metrics = self.scrape()
        self.assertIn('setup_metrics_group_up{group="database"} 1', body)
        self.assertEqual(metrics["database_performance_schema"][""], 1)
        self.assertEqual(sorted(grafana_names.GROUPS["database"] - set(metrics)), [])
        self.assertEqual(sorted(grafana_names.VIEWER_ONLY - set(metrics)), [])
        self.assertAlmostEqual(metrics["sql_buffer_pool_size"][""], 128, delta=1)
        self.assertGreater(metrics["sql_data_cached"][""], 0)
        sleep = [key for key in metrics["database_slow_query_time_average"] if "SLEEP" in key]
        self.assertEqual(len(sleep), 1, metrics["database_slow_query_time_average"])
        self.assertGreaterEqual(metrics["database_slow_query_time_average"][sleep[0]], 1.2)
        self.assertEqual(list(metrics["database_slowest_query_time"]), sleep)
        self.assertGreaterEqual(metrics["database_slowest_query_time"][sleep[0]], 1.2)
        scans = [key for key in metrics["database_table_scan_query_total_row_examined"] if "`customer`" in key]
        self.assertTrue(scans, metrics["database_table_scan_query_total_row_examined"])
        self.assertGreater(metrics["database_table_io_read_total"]['{schema="shop",table="customer"}'], 0)
        self.assertGreater(metrics["database_table_lock_total"]['{schema="shop",table="customer"}'], 0)
        self.assertGreaterEqual(metrics["database_connections"][""], 1)
        # Statement shapes only: literals are replaced by ?, rows are never read.
        self.assertNotIn("example.com", body)

        running = subprocess.Popen(["docker", "exec", "-e", "MYSQL_PWD=shop-test-only", self.database, "mysql", "-h127.0.0.1",
                                    "-ushop", "-e", "SELECT SLEEP(4)"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(running.wait)
        time.sleep(1.5)
        body, metrics = self.scrape()
        self.assertGreaterEqual(metrics["database_running_statements"][""], 1)
        longest = {key: value for key, value in metrics.get("database_running_statement_time", {}).items() if "SLEEP" in key}
        self.assertTrue(longest and max(longest.values()) >= 1, body)
        # The window: the customer lookups ran before the previous scrape, so they are not in this one.
        self.assertFalse([key for key in metrics.get("database_slow_query_total", {}) if "`customer`" in key])
        self.assertNotIn("example.com", body)

    def test_performance_schema_off_is_reported_as_a_state(self):
        self.start_database("off")
        body, metrics = self.scrape()
        self.assertIn('setup_metrics_group_up{group="database"} 1', body)
        self.assertEqual(metrics["database_performance_schema"][""], 0)
        self.assertGreater(metrics["sql_buffer_pool_size"][""], 0)
        self.assertIn("sql_dirty_pages", metrics)
        self.assertGreaterEqual(metrics["database_connections"][""], 1)
        self.assertNotIn("database_slow_query_total", metrics)


if __name__ == "__main__":
    unittest.main()
