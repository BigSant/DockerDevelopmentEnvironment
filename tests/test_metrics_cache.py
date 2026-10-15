"""Cache group: Redis's INFO and memcached's stats when the project runs them, nothing (and no error) when it does
not. A: servers answering both protocols, the compose model; B (SETUP_CACHE_TESTS=1): the real redis and memcached
images, with hits, misses and an eviction."""
import contextlib
import io
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
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker"))
from test_metrics import PROMTOOL, REMOTE, ROOT, SAMPLER, TOKEN, EndpointMixin, docker_container, fetch, parse, write_docker_state
from create_project import scaffold
from project import Project

REDIS_INFO = ("# Server\r\nredis_version:7.4.1\r\n# Clients\r\nconnected_clients:3\r\n# Memory\r\nused_memory:2097152\r\n"
              "maxmemory:67108864\r\n# Stats\r\nkeyspace_hits:900\r\nkeyspace_misses:100\r\nevicted_keys:5\r\nexpired_keys:7\r\n"
              "# Keyspace\r\ndb0:keys=40,expires=2,avg_ttl=0\r\ndb1:keys=2,expires=0,avg_ttl=0\r\n")
MEMCACHED_STATS = ("STAT pid 1\r\nSTAT curr_connections 4\r\nSTAT get_hits 300\r\nSTAT get_misses 100\r\nSTAT bytes 1048576\r\n"
                   "STAT curr_items 25\r\nSTAT limit_maxbytes 67108864\r\nSTAT evictions 2\r\nEND\r\n")


class Cache(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline()
        if line.startswith(b"*1"):
            self.rfile.readline()
            self.rfile.readline()
            self.wfile.write(f"${len(REDIS_INFO)}\r\n{REDIS_INFO}\r\n".encode())
        elif line == b"stats\r\n":
            self.wfile.write(MEMCACHED_STATS.encode())


@unittest.skipUnless(shutil.which("php"), "PHP CLI is required")
class CacheGroupTest(EndpointMixin, unittest.TestCase):
    def setUp(self):
        self.prepare()
        server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Cache)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.address = f"127.0.0.1:{server.server_address[1]}"

    def test_redis_and_memcached_figures(self):
        metrics = parse(fetch(self.start(METRICS_REDIS=self.address, METRICS_MEMCACHED=self.address), token=TOKEN)[2])
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="cache"}'], 1)
        self.assertEqual({name: metrics[name][""] for name in ("setup_redis_memory_used_bytes", "setup_redis_memory_max_bytes",
                                                              "setup_redis_hit_rate", "setup_redis_evicted_keys_total", "setup_redis_keys",
                                                              "setup_redis_connected_clients")},
                         {"setup_redis_memory_used_bytes": 2097152, "setup_redis_memory_max_bytes": 67108864, "setup_redis_hit_rate": 90,
                          "setup_redis_evicted_keys_total": 5, "setup_redis_keys": 42, "setup_redis_connected_clients": 3})
        self.assertEqual({name: metrics[name][""] for name in ("setup_memcached_memory_used_bytes", "setup_memcached_hit_rate",
                                                              "setup_memcached_evictions_total", "setup_memcached_items")},
                         {"setup_memcached_memory_used_bytes": 1048576, "setup_memcached_hit_rate": 75,
                          "setup_memcached_evictions_total": 2, "setup_memcached_items": 25})

    def test_without_the_services_there_is_no_group_and_no_error(self):
        body = fetch(self.start(), token=TOKEN)[2]
        self.assertNotIn('group="cache"', body)
        self.assertNotIn("setup_redis_", body)
        self.assertNotIn("setup_memcached_", body)
        # Only Redis: no memcached figures; a Redis that stopped turns the group down, the rest stays up.
        metrics = parse(fetch(self.start(METRICS_REDIS="127.0.0.1:1"), token=TOKEN)[2])
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="cache"}'], 0)
        self.assertEqual(metrics["setup_metrics_group_up"]['{group="system"}'], 1)
        self.assertNotIn("setup_memcached_items", metrics)


@unittest.skipUnless(shutil.which("php") and (shutil.which("promtool") or PROMTOOL),
                     "Install promtool or set SETUP_PROMTOOL_IMAGE (e.g. prom/prometheus:latest)")
class NewGroupsPromtoolTest(CacheGroupTest):
    """The groups after the dashboards' names: cache, php and the containers' resources pass promtool's lint."""

    def test_promtool_accepts_the_cache_php_and_resource_figures(self):
        from test_metrics_php import FastCgi
        fpm = socketserver.ThreadingTCPServer(("127.0.0.1", 0), FastCgi)
        fpm.asked = []
        threading.Thread(target=fpm.serve_forever, daemon=True).start()
        self.addCleanup(fpm.server_close)
        self.addCleanup(fpm.shutdown)
        container = docker_container("demo-stage-php-fpm", "php-fpm")
        container.update(memory_limit=512 << 20, resources={"cpu_seconds": 12.5, "cpu_percent": 3.2, "memory_bytes": 100 << 20,
                                                            "io_read_bytes": 4096, "io_write_bytes": 8192})
        write_docker_state(self.docker_state, containers=[container])
        body = fetch(self.start(METRICS_REDIS=self.address, METRICS_MEMCACHED=self.address,
                                METRICS_PHP_FPM_ADDRESS=f"127.0.0.1:{fpm.server_address[1]}"), token=TOKEN)[2]
        for name in ("setup_redis_hit_rate", "setup_memcached_items", "setup_php_fpm_processes", "setup_php_opcache_hit_rate",
                     "setup_container_memory_limit_ratio", "setup_container_cpu_seconds_total"):
            self.assertIn(name, body)
        command = ["promtool"] if shutil.which("promtool") else ["docker", "run", "--rm", "-i", "--entrypoint", "promtool", PROMTOOL]
        result = subprocess.run(command + ["check", "metrics"], input="\n".join(
            line for line in body.splitlines() if any(part in line for part in ("setup_redis", "setup_memcached", "setup_php_", "setup_container_")))
            + "\n", capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    # The parent's tests run once, in CacheGroupTest.
    test_redis_and_memcached_figures = None
    test_without_the_services_there_is_no_group_and_no_error = None


class CacheComposeTest(unittest.TestCase):
    def model(self, services):
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            app = scaffold("cache-demo", temporary.name)
        with (app / "env/common.env").open("a") as env:
            env.write(f"SERVICES={services}\n")
        (app / "env/stage.env").write_text(REMOTE + f"METRICS_ALLOWED_IPS=10.8.0.20\nMETRICS_TOKEN={TOKEN}\n")
        return json.loads(Project(app, "stage").capture(["config", "--format", "json"]))["services"]

    def test_the_caches_join_the_metrics_network_only_with_both_services(self):
        services = self.model('"redis memcached metrics"')
        for cache, address in (("redis", "redis:6379"), ("memcached", "memcached:11211")):
            self.assertIn("network_metrics", services[cache]["networks"])
            self.assertEqual(services["metrics"]["environment"][f"METRICS_{cache.upper()}"], address)
        services = self.model("metrics")
        self.assertNotIn("METRICS_REDIS", services["metrics"]["environment"])
        services = self.model("redis")
        self.assertEqual(list(services["redis"]["networks"]), ["network_app"])


@unittest.skipUnless(os.environ.get("SETUP_CACHE_TESTS") and shutil.which("php"), "Set SETUP_CACHE_TESTS=1 (pulls redis and memcached)")
class CacheIntegrationTest(unittest.TestCase):
    def test_real_redis_and_memcached(self):
        name = "setup-cache-" + uuid.uuid4().hex[:6]
        redis, memcached = name + "-redis", name + "-memcached"
        self.addCleanup(subprocess.run, ["docker", "rm", "-f", redis, memcached], capture_output=True)
        run = lambda *args: subprocess.run(["docker", *args], capture_output=True, text=True, check=True, timeout=300)
        run("run", "-d", "--name", redis, "-p", "127.0.0.1::6379", "redis:7-alpine", "redis-server", "--maxmemory", "8mb",
            "--maxmemory-policy", "allkeys-lru")
        run("run", "-d", "--name", memcached, "-p", "127.0.0.1::11211", "memcached:1.6-alpine", "memcached", "-m", "8")
        port = lambda container, inner: run("port", container, str(inner)).stdout.strip().rsplit(":", 1)[1]
        redis_port, memcached_port = port(redis, 6379), port(memcached, 11211)
        for _ in range(50):
            if run("exec", redis, "redis-cli", "ping").stdout.strip() == "PONG":
                break
            time.sleep(0.2)
        # Redis: one hit, one miss, and more than 8 MB of values so that keys are evicted.
        run("exec", redis, "redis-cli", "set", "a", "1")
        run("exec", redis, "redis-cli", "get", "a")
        run("exec", redis, "redis-cli", "get", "missing")
        run("exec", redis, "sh", "-c", "for i in $(seq 1 40); do head -c 400000 /dev/zero | tr '\\0' x | redis-cli -x set big$i >/dev/null; done")
        # memcached: one hit, two misses.
        script = (f"$s = fsockopen('127.0.0.1', {memcached_port}); fwrite($s, \"set k 0 0 1\\r\\nv\\r\\nget k\\r\\nget x\\r\\nget y\\r\\nquit\\r\\n\");"
                  " stream_get_contents($s);")
        subprocess.run(["php", "-r", script], check=True, timeout=30)
        scratch = ROOT / ".test-work"
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as work:
            environment = {**os.environ, "METRICS_SNAPSHOT_DIR": work, "METRICS_REDIS": f"127.0.0.1:{redis_port}",
                           "METRICS_MEMCACHED": f"127.0.0.1:{memcached_port}", "METRICS_DISK_WRITE_MB": "0", "METRICS_URLS": "",
                           "METRICS_PROC_ROOT": "/proc", "METRICS_DISK_PATH": work}
            subprocess.run(["php", str(SAMPLER), "--once"], env=environment, capture_output=True, timeout=120, check=True)
            families = json.loads((Path(work) / "snapshot.json").read_text())["families"]
        value = lambda family: families[family]["samples"][0][1]
        up = {sample[0]["group"]: sample[1] for sample in families["setup_metrics_group_up"]["samples"]}
        self.assertEqual(up["cache"], 1)
        self.assertEqual((value("setup_redis_keyspace_hits_total"), value("setup_redis_keyspace_misses_total")), (1, 1))
        self.assertGreater(value("setup_redis_evicted_keys_total"), 0)
        self.assertEqual(value("setup_redis_memory_max_bytes"), 8 << 20)
        self.assertEqual((value("setup_memcached_get_hits_total"), value("setup_memcached_get_misses_total")), (1, 2))
        self.assertEqual(value("setup_memcached_memory_max_bytes"), 8 << 20)
        self.assertEqual(value("setup_memcached_items"), 1)


if __name__ == "__main__":
    unittest.main()
