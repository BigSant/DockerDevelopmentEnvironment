"""PHP group: php-fpm's pool status and OPcache asked over FastCGI. A: a FastCGI server answering what php-fpm
would, the compose model (php-fpm on the metrics network, SYS_PTRACE only with the slowlog). B (PHP_TEST_IMAGE, a
php-fpm image; SETUP_APACHE_TEST_IMAGE): a saturated pool, real OPcache figures, the slowlog, the status path out of
the visitors' reach."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))
from test_metrics import REMOTE, ROOT, SAMPLER, TOKEN, EndpointMixin, fetch, labelled, parse
from create_project import scaffold
from project import Project

PHP = os.environ.get("PHP_TEST_IMAGE")
APACHE = os.environ.get("SETUP_APACHE_TEST_IMAGE")
POOL = {"pool": "www", "process manager": "dynamic", "start time": 1791530000, "start since": 600, "accepted conn": 4321,
        "listen queue": 2, "max listen queue": 7, "listen queue len": 511, "idle processes": 1, "active processes": 4,
        "total processes": 5, "max active processes": 5, "max children reached": 3, "slow requests": 0}
OPCACHE = {"loaded": True, "max_accelerated_files": 10000, "status": {
    "opcache_enabled": True, "memory_usage": {"used_memory": 60000000, "free_memory": 70000000, "wasted_memory": 4000000},
    "opcache_statistics": {"num_cached_scripts": 1500, "max_cached_keys": 16229, "hits": 99000, "misses": 1000,
                           "opcache_hit_rate": 99.0, "oom_restarts": 1, "hash_restarts": 0, "manual_restarts": 2}}}


class FastCgi(socketserver.BaseRequestHandler):
    """php-fpm as the metrics service sees it: the status path's JSON, the OPcache script's JSON, else 404."""

    def handle(self):
        params, data = {}, b""
        while True:
            header = self.request.recv(8, socket_flags())
            if len(header) < 8:
                return
            _, kind, _, length, padding = struct.unpack(">BBHHBx", header)
            content = b""
            while len(content) < length + padding:
                content += self.request.recv(length + padding - len(content))
            content = content[:length]
            if kind == 4:
                data += content
            if kind == 5 and not content:
                break
        position = 0
        while position < len(data):
            sizes = []
            for _ in range(2):
                size = data[position]
                if size >> 7:
                    size = struct.unpack(">I", data[position:position + 4])[0] & 0x7FFFFFFF
                    position += 4
                else:
                    position += 1
                sizes.append(size)
            name = data[position:position + sizes[0]].decode()
            params[name] = data[position + sizes[0]:position + sizes[0] + sizes[1]].decode()
            position += sizes[0] + sizes[1]
        self.server.asked.append(params)
        if params["SCRIPT_NAME"] == "/setup-fpm-status" and params["QUERY_STRING"] == "json":
            body = "Content-Type: application/json\r\n\r\n" + json.dumps(POOL)
        elif params["SCRIPT_FILENAME"] == "/opt/setup/runtime/metrics/fpm-opcache.php":
            body = "Content-Type: application/json\r\n\r\n" + json.dumps(OPCACHE)
        else:
            body = "Status: 404 Not Found\r\nContent-Type: text/plain\r\n\r\nFile not found."
        out = body.encode()
        self.request.sendall(struct.pack(">BBHHBx", 1, 6, 1, len(out), 0) + out + struct.pack(">BBHHBx", 1, 6, 1, 0, 0)
                             + struct.pack(">BBHHBx", 1, 3, 1, 8, 0) + b"\0" * 8)


def socket_flags():
    import socket
    return socket.MSG_WAITALL


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class PhpGroupTest(EndpointMixin, unittest.TestCase):
    def setUp(self):
        self.prepare()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FastCgi)
        server.asked = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.fpm = server

    def test_pool_and_opcache_figures_from_php_fpm(self):
        address = f"127.0.0.1:{self.fpm.server_address[1]}"
        base = self.start(METRICS_PHP_FPM_ADDRESS=address)
        metrics = parse(fetch(base, token=TOKEN)[2])
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="php"}'], 1)
        self.assertEqual(metrics["setup_php_fpm_processes"], {'{pool="www",state="active"}': 4, '{pool="www",state="idle"}': 1})
        self.assertEqual((metrics["setup_php_fpm_listen_queue"]['{pool="www"}'], metrics["setup_php_fpm_listen_queue_max"]['{pool="www"}'],
                          metrics["setup_php_fpm_max_children_reached_total"]['{pool="www"}']), (2, 7, 3))
        self.assertEqual(metrics["setup_php_opcache_hit_rate"][""], 99)
        self.assertEqual(labelled(metrics, "setup_php_opcache_memory_bytes", "state"), {"used": 60000000, "free": 70000000, "wasted": 4000000})
        self.assertEqual(labelled(metrics, "setup_php_opcache_restarts_total", "reason"), {"oom": 1, "hash": 0, "manual": 2})
        self.assertEqual(metrics["setup_php_opcache_cached_scripts"][""], 1500)
        # Asked as php-fpm's own path and setup's script, nothing of the application.
        self.assertEqual({(asked["SCRIPT_NAME"], asked["SCRIPT_FILENAME"]) for asked in self.fpm.asked},
                         {("/setup-fpm-status", "/setup-fpm-status"), ("/setup-fpm-opcache", "/opt/setup/runtime/metrics/fpm-opcache.php")})
        # The Overview's card: 4 of 5 busy, 2 waiting.
        page = fetch(base, "/")[2]
        self.assertIn('data-card="php"><h2>PHP workers</h2><div class="value">4 / 5</div>', page)
        self.assertIn('<section class="card bad" data-card="php">', page)

    def test_without_php_fpm_only_the_php_group_is_down(self):
        metrics = parse(fetch(self.start(METRICS_PHP_FPM_ADDRESS="127.0.0.1:1"), token=TOKEN)[2])
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="php"}'], 0)
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="system"}'], 1)
        self.assertNotIn("setup_php_fpm_processes", metrics)


class PhpComposeTest(unittest.TestCase):
    def project(self, stage_env):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            app = scaffold("php-demo", temporary.name)
        with (app / "env/common.env").open("a") as env:
            env.write("SERVICES=metrics\n")
        (app / "env/stage.env").write_text(REMOTE + f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n" + stage_env)
        return Project(app, "stage")

    def test_php_fpm_joins_the_metrics_network_and_gets_ptrace_only_for_the_slowlog(self):
        services = json.loads(self.project("").capture(["config", "--format", "json"]))["services"]
        fpm = services["php-fpm"]
        self.assertIn("network_metrics", fpm["networks"])
        self.assertNotIn("cap_add", fpm)
        self.assertNotIn("SETUP_FPM_SLOWLOG_TIMEOUT", fpm.get("environment", {}))
        services = json.loads(self.project("PHP_FPM_SLOWLOG_SECONDS=5\n").capture(["config", "--format", "json"]))["services"]
        self.assertEqual(services["php-fpm"]["cap_add"], ["SYS_PTRACE"])
        self.assertEqual(services["php-fpm"]["environment"]["SETUP_FPM_SLOWLOG_TIMEOUT"], "5")
        with self.assertRaisesRegex(ValueError, "PHP_FPM_SLOWLOG_SECONDS must be 1 to 600"):
            self.project("PHP_FPM_SLOWLOG_SECONDS=5s\n")


def docker(*args, check=True, timeout=120):
    return subprocess.run(["docker", *args], capture_output=True, text=True, errors="replace", timeout=timeout, check=check)


SLOW_SCRIPT = "<?php usleep((int) (isset($_GET['ms']) ? $_GET['ms'] : 3000) * 1000); echo 'ok';"
# A FastCGI GET of /tmp/slow.php from inside the container, in the background.
REQUEST = r'''
$record = function ($t, $c) { return pack("CCnnCx", 1, $t, 1, strlen($c), 0) . $c; };
$len = function ($s) { return strlen($s) < 128 ? chr(strlen($s)) : pack("N", strlen($s) | 0x80000000); };
$p = ""; foreach (array("SCRIPT_FILENAME" => "/tmp/slow.php", "SCRIPT_NAME" => "/slow.php", "REQUEST_METHOD" => "GET", "QUERY_STRING" => $argv[1],
  "REQUEST_URI" => "/slow.php", "REMOTE_ADDR" => "127.0.0.1", "SERVER_PROTOCOL" => "HTTP/1.1") as $k => $v) $p .= $len($k) . $len($v) . $k . $v;
$s = fsockopen("127.0.0.1", 9000); fwrite($s, $record(1, pack("nCx5", 1, 0)) . $record(4, $p) . $record(4, "") . $record(5, ""));
echo stream_get_contents($s);
'''


@unittest.skipUnless(PHP, "Set PHP_TEST_IMAGE to a php-fpm image")
class PhpFpmIntegrationTest(unittest.TestCase):
    def fpm(self, *extra, environment=(), network="none"):
        name = "setup-fpm-metrics-" + uuid.uuid4().hex[:8]
        self.addCleanup(docker, "rm", "-f", name, check=False)
        conf = ROOT / "docker/docker/php-fpm/setup-global.conf"
        # OPcache as setup's images run it: loaded (official images before 8.5 do not load it) and on.
        loaded = "Zend OPcache" in docker("run", "--rm", "--entrypoint", "php", PHP, "-m").stdout
        # The status listener as the Dockerfile adds it: PHP 8.0+ only.
        status = ROOT / "docker/docker/php-fpm/setup-status.conf"
        self.status_listener = docker("run", "--rm", "--entrypoint", "php", PHP, "-r", "echo PHP_VERSION_ID >= 80000 ? 1 : 0;").stdout.strip() == "1"
        docker("run", "-d", "--name", name, "--network", network, "--memory", "256m", *extra,
               "-v", f"{conf}:/usr/local/etc/php-fpm.d/zz-setup-global.conf:ro", "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro",
               *(["-v", f"{status}:/usr/local/etc/php-fpm.d/zz-setup-status.conf:ro"] if self.status_listener else []),
               "-e", "SETUP_FPM_ACCESS_LOG=/proc/self/fd/2", "-e", "SETUP_FPM_SLOWLOG_TIMEOUT=0", *environment,
               "--entrypoint", "php-fpm", PHP, "-F", "-R", *([] if loaded else ["-d", "zend_extension=opcache.so"]), "-d", "opcache.enable=1")
        subprocess.run(["docker", "exec", "-i", name, "sh", "-c", "cat > /tmp/slow.php"], input=SLOW_SCRIPT, text=True, check=True)
        for _ in range(50):
            if docker("exec", name, "php", "-r", "exit(@fsockopen('127.0.0.1',9000)?0:1);", check=False).returncode == 0:
                return name
            time.sleep(0.2)
        self.fail(docker("logs", name).stderr)

    def sample(self, name):
        docker("exec", name, "sh", "-c", "mkdir -p /tmp/snap /tmp/disk")
        # The service's default order: the status listener, then the pool's own port.
        docker("exec", "-e", "METRICS_SNAPSHOT_DIR=/tmp/snap", "-e", "METRICS_PHP_FPM_ADDRESS=127.0.0.1:9001,127.0.0.1:9000",
               "-e", "METRICS_DISK_PATH=/tmp/disk",
               "-e", "METRICS_PROC_ROOT=/proc", "-e", "METRICS_DISK_WRITE_MB=0", "-e", "METRICS_URLS=", name,
               "php", "/opt/setup/runtime/metrics/sampler.php", "--once", check=False)
        families = json.loads(docker("exec", name, "cat", "/tmp/snap/snapshot.json").stdout)["families"]
        # PHP writes empty labels as [].
        return {key: {json.dumps(sample[0] or {}, sort_keys=True): sample[1] for sample in family["samples"]} for key, family in families.items()}

    def test_a_saturated_pool_counts_max_children_and_its_queue(self):
        # The metrics service runs on PHP 7.0+; its sampler runs in this container here.
        if docker("run", "--rm", "--entrypoint", "php", PHP, "-r", "echo PHP_MAJOR_VERSION;").stdout.strip() < "7":
            self.skipTest("the metrics service needs PHP 7.0+")
        limits = ROOT / ".test-work" / f"pool-{uuid.uuid4().hex[:6]}.conf"
        (ROOT / ".test-work").mkdir(exist_ok=True)
        # php-fpm counts "max children reached" when its dynamic manager wants another worker and may not.
        limits.write_text("[www]\npm = dynamic\npm.max_children = 2\npm.start_servers = 1\npm.min_spare_servers = 1\n"
                          "pm.max_spare_servers = 1\n")
        self.addCleanup(limits.unlink)
        name = self.fpm("-v", f"{limits}:/usr/local/etc/php-fpm.d/zzz-limits.conf:ro")
        before = self.sample(name)
        self.assertEqual(before["setup_php_fpm_max_children_reached_total"], {'{"pool": "www"}': 0})
        # Six 3-second requests at once against two workers: they queue.
        for _ in range(6):
            docker("exec", "-d", name, "php", "-r", REQUEST, "ms=3000")
        time.sleep(1.5)
        during = self.sample(name)
        self.assertGreater(during["setup_php_fpm_listen_queue"]['{"pool": "www"}'], 0)
        self.assertEqual(during["setup_php_fpm_processes"]['{"pool": "www", "state": "active"}'], 2)
        self.assertGreaterEqual(during["setup_php_fpm_max_children_reached_total"]['{"pool": "www"}'], 1)
        self.assertEqual(during["setup_metrics_group_up"]['{"group": "php"}'], 1)
        # OPcache of the php-fpm master: scripts cached by the requests.
        self.assertEqual(during["setup_php_opcache_enabled"], {"{}": 1})
        self.assertGreaterEqual(during["setup_php_opcache_cached_scripts"]["{}"], 1)

    def test_a_pool_with_every_worker_busy_still_answers_on_the_status_listener(self):
        limits = ROOT / ".test-work" / f"pool-{uuid.uuid4().hex[:6]}.conf"
        (ROOT / ".test-work").mkdir(exist_ok=True)
        limits.write_text("[www]\npm = static\npm.max_children = 1\n")
        self.addCleanup(limits.unlink)
        name = self.fpm("-v", f"{limits}:/usr/local/etc/php-fpm.d/zzz-limits.conf:ro")
        if not self.status_listener:
            self.skipTest("pm.status_listen needs PHP 8.0+")
        # Idle: the status request runs outside www, so no worker is counted active.
        idle = self.sample(name)
        self.assertEqual(idle["setup_php_fpm_processes"]['{"pool": "www", "state": "active"}'], 0)
        # The only worker busy for 30 s: the status and OPcache still answer at once.
        docker("exec", "-d", name, "php", "-r", REQUEST, "ms=30000")
        time.sleep(1)
        started = time.monotonic()
        busy = self.sample(name)
        self.assertLess(time.monotonic() - started, 8)
        self.assertEqual(busy["setup_metrics_group_up"]['{"group": "php"}'], 1)
        self.assertEqual(busy["setup_php_fpm_processes"]['{"pool": "www", "state": "active"}'], 1)
        self.assertEqual(busy["setup_php_opcache_enabled"], {"{}": 1})

    def test_the_slowlog_writes_a_stack_trace_with_ptrace(self):
        name = self.fpm("--cap-add", "SYS_PTRACE", environment=("-e", "SETUP_FPM_SLOWLOG_TIMEOUT=1"))
        docker("exec", name, "php", "-r", REQUEST, "ms=2500")
        logs = ""
        for _ in range(20):
            logs = docker("logs", name).stderr
            if "slow.php" in logs:
                break
            time.sleep(0.25)
        self.assertIn("executing too slow", logs)
        self.assertRegex(logs, r"script_filename = /tmp/slow\.php")
        self.assertIn("usleep()", logs)

    @unittest.skipUnless(APACHE, "Set SETUP_APACHE_TEST_IMAGE to a built setup Apache image")
    def test_visitors_never_reach_the_status_path_through_apache(self):
        network = "setup-fpm-status-" + uuid.uuid4().hex[:6]
        docker("network", "create", network)
        self.addCleanup(docker, "network", "rm", network, check=False)
        work = ROOT / ".test-work" / network
        (work / "html").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, work)
        (work / "html/index.php").write_text("<?php echo 'site';")
        for path in [work, *work.rglob("*")]:
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
        name = self.fpm("--network-alias", "php-fpm", "-v", f"{work}/html:/var/www/html:ro", network=network)
        apache = network + "-apache"
        self.addCleanup(docker, "rm", "-f", apache, check=False)
        docker("run", "-d", "--name", apache, "--network", network, "-v", f"{work}/html:/var/www/html:ro",
               "-v", f"{ROOT}/docker/docker/apache/conf/init.sh:/setup-init.sh:ro", "-e", "DOMAIN=demo.local",
               "--entrypoint", "bash", APACHE, "/setup-init.sh", "demo.local", "/", "prod")
        get = lambda path: docker("exec", apache, "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/80 && printf 'GET " + path
                                  + " HTTP/1.0\\r\\nHost: demo.local\\r\\n\\r\\n' >&3 && cat <&3", check=False).stdout
        answer = ""
        for _ in range(30):
            answer = get("/index.php")
            if "site" in answer:
                break
            time.sleep(0.5)
        self.assertIn("site", answer)
        for path in ("/setup-fpm-status", "/setup-fpm-status?json", "/setup-fpm-status.php", "/index.php/setup-fpm-status"):
            answer = get(path)
            self.assertNotIn("accepted conn", answer, path)
            self.assertNotIn("max children reached", answer, path)


if __name__ == "__main__":
    unittest.main()
