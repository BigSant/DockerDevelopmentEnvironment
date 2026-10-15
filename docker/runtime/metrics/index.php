<?php
// metrics.<domain>: Prometheus text exposition at /metrics under the names the existing Grafana dashboards and
// alerts use (from the old monitoring script). IP allowlist in nginx-proxy, which also asks the METRICS_HTPASSWD
// users for every page except /metrics; an optional bearer token here guards /metrics.
require __DIR__ . '/metrics.php';

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path === '/') {
    // The pages people open; the dashboard replaces this list.
    http_response_code(200);
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    echo "Monitoring of this project\n\n/metrics  Prometheus exposition (bearer token when METRICS_TOKEN is set)\n";
    exit;
}
if ($path !== '/metrics') metrics_respond(404, "not found\n");

$token = (string) getenv('METRICS_TOKEN');
if ($token !== '') {
    if (strlen($token) < 24) metrics_respond(503, "METRICS_TOKEN must have 24+ characters\n");
    $authorization = isset($_SERVER['HTTP_AUTHORIZATION']) ? (string) $_SERVER['HTTP_AUTHORIZATION'] : '';
    if (!hash_equals('Bearer ' . $token, $authorization)) {
        header('WWW-Authenticate: Bearer');
        metrics_respond(401, "unauthorized\n");
    }
}

// One file per group, runtime/metrics/<group>.php with metrics_<group>_parts(). host follows database
// (it reuses the database group's connection for the version).
$metrics = new Metrics();
foreach (array('system', 'website', 'http', 'security', 'database', 'host', 'containers', 'cron', 'registry') as $group) {
    metrics_collect($metrics, $group);
}
metrics_respond(200, $metrics->render());
