<?php
// Target /metrics: Prometheus text exposition under the names the existing Grafana dashboards and
// alerts use (from the old monitoring script). Bearer token here; IP allowlist in nginx-proxy.
require __DIR__ . '/metrics.php';

$token = (string) getenv('METRICS_TOKEN');
if (strlen($token) < 24) metrics_respond(503, "METRICS_TOKEN (24+ characters) is not configured\n");
$authorization = isset($_SERVER['HTTP_AUTHORIZATION']) ? (string) $_SERVER['HTTP_AUTHORIZATION'] : '';
if (!hash_equals('Bearer ' . $token, $authorization)) {
    header('WWW-Authenticate: Bearer');
    metrics_respond(401, "unauthorized\n");
}
if (parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) !== '/metrics') metrics_respond(404, "not found\n");

// One file per group, runtime/metrics/<group>.php with metrics_<group>_parts(). Planned: database, host.
$metrics = new Metrics();
foreach (array('system', 'website', 'http', 'security') as $group) {
    metrics_collect($metrics, $group);
}
metrics_respond(200, $metrics->render());
