"""Where Apache, PHP-FPM and nginx-proxy write their logs: requests on stdout, errors on stderr (docker logs keep
the two streams apart), nothing in files inside the containers, the request timings on nginx's and FPM's lines,
and PHP's messages with their time.
The B tests run with PHP_TEST_IMAGE (a php-fpm image) and SETUP_APACHE_TEST_IMAGE."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "docker/docker"
PHP = os.environ.get("PHP_TEST_IMAGE")
APACHE = os.environ.get("SETUP_APACHE_TEST_IMAGE")

# A FastCGI request as Apache's proxy_fcgi sends one: a pretty URL routed to a front controller.
FASTCGI_REQUEST = r'''
$record = function ($type, $content) { return pack("CCnnCx", 1, $type, 1, strlen($content), 0) . $content; };
$length = function ($text) { return strlen($text) < 128 ? chr(strlen($text)) : pack("N", strlen($text) | 0x80000000); };
$params = "";
foreach (array("SCRIPT_FILENAME" => "/tmp/front.php", "REQUEST_METHOD" => "GET", "REQUEST_URI" => "/pretty/url?page=2",
               "REMOTE_ADDR" => "203.0.113.7", "SERVER_PROTOCOL" => "HTTP/1.1", "GATEWAY_INTERFACE" => "CGI/1.1") as $k => $v) {
    $params .= $length($k) . $length($v) . $k . $v;
}
$socket = fsockopen("127.0.0.1", 9000);
fwrite($socket, $record(1, pack("nCx5", 1, 0)) . $record(4, $params) . $record(4, "") . $record(5, ""));
echo stream_get_contents($socket);
'''


class ConfigurationTest(unittest.TestCase):
    def test_nginx_writes_every_access_line_with_the_timings(self):
        conf = DOCKER / "nginx-proxy/conf"
        nginx = (conf / "nginx.conf").read_text()
        layout = re.search(r"log_format setup ((?:'[^']*'\s*)+);", nginx)
        self.assertTrue(layout, "nginx.conf defines the setup format")
        text = "".join(re.findall(r"'([^']*)'", layout[1]))
        # Combined first, so parsers of that format keep reading the lines; the timings and host after it.
        self.assertTrue(text.startswith('$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent '
                                        '"$http_referer" "$http_user_agent" '), text)
        self.assertTrue(text.endswith("rt=$request_time urt=$upstream_response_time host=$host"), text)
        lines = [line.strip() for name in ("nginx.conf", "sites.conf", "init.sh")
                 for line in (conf / name).read_text().splitlines() if re.match(r"\s*(echo ')?access_log ", line)]
        self.assertEqual(len(lines), 3, lines)
        for line in lines:
            self.assertRegex(line, r"access_log \S+ setup[ ;]", "every access log uses the setup format")

    def test_apache_keeps_no_log_file_and_sends_errors_to_stderr(self):
        sites = (DOCKER / "apache/conf/sites.conf").read_text()
        self.assertIn("ErrorLog  /proc/self/fd/2\n", sites)
        self.assertIn("CustomLog /dev/null common\n", sites)
        self.assertNotIn("/var/log/apache2", sites + (DOCKER / "apache/Dockerfile").read_text())

    def test_php_messages_go_to_stderr_with_their_time(self):
        # Without error_log php-fpm passes a worker's message as "NOTICE: PHP message: ..." without a time; with it PHP
        # writes "[15-Oct-2026 10:28:02 Europe/Vilnius] PHP Warning: ..." and the master hands that line on.
        policy = (ROOT / "docker/runtime/php-policy/70-errors.ini").read_text()
        self.assertIn("\nerror_log = /proc/self/fd/2\n", policy)
        self.assertNotIn("fastcgi.logging", (ROOT / "docker/runtime/start.sh").read_text())
        self.assertNotIn("rotatelogs", (DOCKER / "apache/conf/init.sh").read_text())

    def test_php_fpm_writes_requests_to_stdout_with_time_and_memory(self):
        conf = (DOCKER / "php-fpm/setup-global.conf").read_text()
        pool = conf[conf.index("[www]"):]
        self.assertIn("access.log = ${SETUP_FPM_ACCESS_LOG}\n", pool)
        self.assertIn(r'access.format = "%{REMOTE_ADDR}e - %u %t \"%m %{REQUEST_URI}e\" %s ms=%{mili}d kb=%{kilo}M cpu=%C%%"', pool)
        # php-fpm sends its own stdout to /dev/null: start.sh keeps the container's stdout as fd 3 for it, and
        # started any other way php-fpm needs the image's default, without which it would refuse to start.
        start = (ROOT / "docker/runtime/start.sh").read_text()
        self.assertIn("exec 3>&1\n    export SETUP_FPM_ACCESS_LOG=/proc/self/fd/3\n", start)
        self.assertIn("ENV SETUP_FPM_ACCESS_LOG=/proc/self/fd/2\n", (ROOT / "docker/Dockerfile").read_text())


def docker(*args, check=True):
    # The FastCGI answer carries binary record headers: decoded leniently.
    return subprocess.run(["docker", *args], capture_output=True, text=True, errors="replace", timeout=60, check=check)


@unittest.skipUnless(PHP, "Set PHP_TEST_IMAGE to a php-fpm image")
class PhpFpmStreamsTest(unittest.TestCase):
    def test_a_request_line_goes_to_stdout_and_php_messages_to_stderr(self):
        name = "setup-fpm-logs-" + uuid.uuid4().hex[:8]
        self.addCleanup(docker, "rm", "-f", name, check=False)
        # Started as the php-fpm service is: through the working tree's start.sh.
        docker("run", "-d", "--name", name, "--network", "none", "--memory", "128m", "--user", "1000:1000",
               "-v", f"{DOCKER}/php-fpm/setup-global.conf:/usr/local/etc/php-fpm.d/zz-setup-global.conf:ro",
               "-v", f"{ROOT}/docker/runtime:/opt/setup/runtime:ro",
               # setup's PHP policy as the php-fpm service reads it (its files expect these variables).
               "-e", "PHP_INI_SCAN_DIR=:/opt/setup/runtime/php-policy", "-e", "TZ=Europe/Vilnius", "-e", "PHP_OPCACHE=0",
               "-e", "PHP_APCU=0", "-e", "PHP_OPCACHE_VALIDATE_TIMESTAMPS=1", "-e", "PHP_OPCACHE_REVALIDATE_FREQ=0",
               # The CORE image creates these; a plain php-fpm image does not.
               "--tmpfs", "/usr/local/etc/php/project.r:mode=1777", "--tmpfs", "/usr/local/etc/php/project.r.env:mode=1777",
               # log_errors as setup's php.ini sets it (a plain image leaves it off).
               "--entrypoint", "/bin/sh", PHP, "/opt/setup/runtime/start.sh", "php-fpm", "-F", "-R", "-d", "log_errors=1")
        docker("exec", name, "sh", "-c", "printf '<?php error_log(\"setup-test message\"); trigger_error(\"setup-test warning\", "
                                         "E_USER_WARNING); echo \"ok\";' > /tmp/front.php")
        answer = ""
        for _ in range(50):
            answer = docker("exec", name, "php", "-r", FASTCGI_REQUEST, check=False).stdout
            if "ok" in answer:
                break
            time.sleep(0.2)
        self.assertIn("ok", answer)
        stdout = stderr = ""
        for _ in range(50):
            logs = docker("logs", name)
            stdout, stderr = logs.stdout, logs.stderr
            if "/pretty/url" in stdout and "setup-test message" in stderr:
                break
            time.sleep(0.2)
        request = [line for line in stdout.splitlines() if "/pretty/url" in line]
        self.assertEqual(len(request), 1, stdout)
        self.assertRegex(request[0], r'^203\.0\.113\.7 - +\S* \S+ "GET /pretty/url\?page=2" 200 ms=\d+\.\d+ kb=\d+ cpu=\d+\.\d+%$')
        self.assertIn("setup-test message", stderr)
        self.assertRegex(stderr, r"(?m)\[\d{2}-\w{3}-\d{4} \d{2}:\d{2}:\d{2} Europe/Vilnius\] PHP Warning:  setup-test warning")
        self.assertNotIn("/pretty/url", stderr)


@unittest.skipUnless(APACHE, "Set SETUP_APACHE_TEST_IMAGE to a built setup Apache image")
class ApacheStreamsTest(unittest.TestCase):
    def test_without_metrics_errors_go_to_stderr_and_no_log_file_grows(self):
        work = ROOT / ".test-work"
        work.mkdir(exist_ok=True)
        html = Path(tempfile.mkdtemp(dir=work))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(html)])
        (html / "index.php").write_text("<?php echo 1;")
        os.chmod(html, 0o755)
        os.chmod(html / "index.php", 0o644)
        name = "setup-apache-logs-" + uuid.uuid4().hex[:8]
        self.addCleanup(docker, "rm", "-f", name, check=False)
        # The image may predate this change: start it with the working tree's configuration.
        docker("run", "-d", "--name", name, "-v", f"{html}:/var/www/html:ro",
               "-v", f"{DOCKER}/apache/conf/init.sh:/setup-init.sh:ro",
               "-v", f"{DOCKER}/apache/conf/sites.conf:/usr/local/apache2/conf/sites.conf.source:ro",
               "-e", "DOMAIN=demo.local", "--entrypoint", "bash", APACHE, "-c",
               "cp /usr/local/apache2/conf/sites.conf.source /usr/local/apache2/conf/sites.conf && "
               "exec bash /setup-init.sh demo.local / prod")
        status = ""
        for _ in range(30):
            status = docker("exec", name, "bash", "-c", "exec 3<>/dev/tcp/127.0.0.1/80 && printf 'GET /index.php HTTP/1.0\\r\\n"
                            "Host: demo.local\\r\\n\\r\\n' >&3 && head -1 <&3", check=False).stdout
            if status:
                break
            time.sleep(0.5)
        # No php-fpm on this network: Apache logs the failed FastCGI request as an error.
        self.assertIn(" 50", status)
        logs = docker("logs", name)
        for _ in range(20):
            if "[proxy" in logs.stderr:
                break
            time.sleep(0.5)
            logs = docker("logs", name)
        self.assertRegex(logs.stderr, r"(?m)^\[\w{3} \w{3} \d{2} [\d:.]+ \d{4}\] \[proxy")
        self.assertNotIn("GET /index.php", logs.stdout + logs.stderr, "Apache keeps no access log")
        self.assertEqual(docker("exec", name, "sh", "-c", "ls /var/log/apache2 2>/dev/null | wc -l").stdout.strip(), "0")


if __name__ == "__main__":
    unittest.main()
