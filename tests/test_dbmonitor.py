"""DB monitor: token gate, monitor account rules, guarded web access; B: real MySQL with the monitor account."""

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
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "docker")]
from create_project import scaffold
from database_grants import MONITOR, monitor_account
from project import Project
from project_policy import resolve_policy

ENDPOINT = ROOT / "docker/runtime/dbmonitor/index.php"
TOKEN = "t" * 32
REMOTE = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\nWEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\n'
          'PROXY_MEMORY=128m\nPROXY_CPUS=0.5\nDOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\n'
          'DATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test-only\n')


def request(base, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/", headers=headers), timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class EndpointTest(unittest.TestCase):
    def start(self, env):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", str(ENDPOINT)], env={**os.environ, **env},
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(server.wait)
        self.addCleanup(server.terminate)
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                request(base)
                return base
            except OSError:
                time.sleep(0.1)
        self.fail("endpoint did not start")

    def test_without_a_configured_token_it_refuses_to_serve(self):
        base = self.start({"DBMONITOR_TOKEN": "short"})
        self.assertEqual(request(base, "short")[0], 503)

    def test_the_bearer_token_is_required_before_any_database_work(self):
        base = self.start({"DBMONITOR_TOKEN": TOKEN, "DATABASE_MONITOR_HOST": "monitor.invalid"})
        self.assertEqual(request(base)[0], 401)
        self.assertEqual(request(base, "wrong" * 8)[0], 401)
        status, body = request(base, TOKEN)
        self.assertEqual((status, body["error"]), (503, "database unavailable for the monitor account"))


class SettingsTest(unittest.TestCase):
    def test_monitor_account_is_separate_and_has_a_strong_password(self):
        settings = {"DATABASE_NAME": "shop", "DATABASE_USER": "shop", "DATABASE_MONITOR_PASSWORD": "x" * 20}
        self.assertEqual(monitor_account(SimpleNamespace(settings=settings, environment="local")), ("shop_monitor", "x" * 20))
        for change, message in (({"DATABASE_MONITOR_PASSWORD": "short"}, "16\\+ characters"),
                                ({"DATABASE_MONITOR_USER": "shop"}, "its own account"),
                                ({"DATABASE_MONITOR_USER": "bad name"}, "letters, digits")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                monitor_account(SimpleNamespace(settings={**settings, **change}, environment="local"))

    def test_performance_schema_switch(self):
        self.assertEqual(resolve_policy({}, "local")["DATABASE_PERFORMANCE_SCHEMA"], "off")
        self.assertEqual(resolve_policy({"DATABASE_PERFORMANCE_SCHEMA": "on"}, "local")["DATABASE_PERFORMANCE_SCHEMA"], "on")
        with self.assertRaisesRegex(ValueError, "DATABASE_PERFORMANCE_SCHEMA"):
            resolve_policy({"DATABASE_PERFORMANCE_SCHEMA": "yes"}, "local")

    def test_stage_monitor_is_allowlisted_and_the_database_port_stays_closed(self):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            with contextlib.redirect_stdout(io.StringIO()):
                app = scaffold("monitor-demo", temporary)
            with (app / "env/common.env").open("a") as env:
                env.write("SERVICES=dbmonitor\n")
            (app / "env/stage.env").write_text(REMOTE)
            with self.assertRaisesRegex(ValueError, "dbmonitor on stage requires DBMONITOR_ALLOWED_IPS or ALLOWED_IPS"):
                Project(app, "stage")
            with (app / "env/stage.env").open("a") as env:
                env.write("DBMONITOR_ALLOWED_IPS=203.0.113.7\n")
            project = Project(app, "stage")
            self.assertEqual(project.process_env["SETUP_ENABLE_DBMONITOR"], "1")
            services = json.loads(project.capture(["config", "--format", "json"]))["services"]
            self.assertNotIn("ports", services["database"])
            self.assertNotIn("ports", services["dbmonitor"])
            self.assertEqual(services["nginx-proxy"]["environment"]["DBMONITOR_ALLOWED_IPS"], "203.0.113.7")


MYSQL = os.environ.get("SETUP_MYSQL_IMAGE")
PHP = os.environ.get("PHP_TEST_IMAGE")


@unittest.skipUnless(MYSQL and PHP, "Set SETUP_MYSQL_IMAGE and PHP_TEST_IMAGE to built setup images")
class MonitorIntegrationTest(unittest.TestCase):
    def test_monitor_reads_statistics_but_no_project_data(self):
        network = f"setup-monitor-{uuid.uuid4().hex[:8]}"
        subprocess.run(["docker", "network", "create", "--internal", network], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "network", "rm", network], capture_output=True)
        database = network + "-db"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", database, "--network", network, "--network-alias", "database",
                        "--tmpfs", "/var/lib/mysql", "-e", "MYSQL_ROOT_PASSWORD=root-test-only", "-e", "MYSQL_DATABASE=shop",
                        "-e", "MYSQL_USER=shop", "-e", "MYSQL_PASSWORD=shop-test-only", "-e", "DATABASE_BUFFER_POOL=128M",
                        "-e", "DATABASE_PERFORMANCE_SCHEMA=on", MYSQL], check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", database], capture_output=True)
        sql = lambda user, password, query: subprocess.run(
            ["docker", "exec", "-e", f"MYSQL_PWD={password}", database, "mysql", "-h127.0.0.1", f"-u{user}", "-N", "-e", query],
            capture_output=True, text=True)
        for _ in range(60):
            if sql("shop", "shop-test-only", "SELECT 1").returncode == 0:
                break
            time.sleep(2)
        self.assertEqual(sql("shop", "shop-test-only", "SELECT @@performance_schema").stdout.strip(), "1")
        sql("shop", "shop-test-only", "USE shop; CREATE TABLE customer (email varchar(64)); INSERT INTO customer VALUES ('a@b.c');"
                                      "SELECT COUNT(*) FROM customer;")
        created = subprocess.run(["docker", "exec", "-i", database, "sh", "-c", MONITOR, "monitor-user", "shop_monitor"],
                                 input="monitor-test-password\n", capture_output=True, text=True)
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertNotEqual(sql("shop_monitor", "monitor-test-password", "SELECT * FROM shop.customer").returncode, 0,
                            "the monitor account cannot read project data")
        digests = sql("shop_monitor", "monitor-test-password",
                      "SELECT COUNT(*) FROM performance_schema.events_statements_summary_by_digest")
        self.assertEqual(digests.returncode, 0, digests.stderr)
        endpoint = network + "-monitor"
        subprocess.run(["docker", "run", "-d", "--rm", "--name", endpoint, "--network", network,
                        "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro", "-e", f"DBMONITOR_TOKEN={TOKEN}",
                        "-e", "DATABASE_MONITOR_USER=shop_monitor", "-e", "DATABASE_MONITOR_PASSWORD=monitor-test-password",
                        "--entrypoint", "php", PHP, "-S", "0.0.0.0:8081", "/opt/setup/runtime/dbmonitor/index.php"],
                       check=True, capture_output=True)
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", endpoint], capture_output=True)
        fetch = ("$o = array('http' => array('ignore_errors' => true, 'header' => 'Authorization: Bearer ' . $argv[1]));"
                 "echo file_get_contents('http://127.0.0.1:8081/', false, stream_context_create($o));")
        for _ in range(30):
            body = subprocess.run(["docker", "exec", endpoint, "php", "-r", fetch, TOKEN], capture_output=True, text=True).stdout
            if body.strip():
                break
            time.sleep(0.5)
        report = json.loads(body)
        self.assertEqual(report["format"], "setup-dbmonitor/draft")
        self.assertTrue(report["performance_schema"])
        self.assertIn("Threads_connected", report["status"])
        self.assertTrue(any("customer" in (row["digest"] or "") for row in report["statements"]))


if __name__ == "__main__":
    unittest.main()
