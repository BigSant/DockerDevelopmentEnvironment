<?php
// metrics.<domain>: Prometheus text exposition at /metrics under the names the existing Grafana dashboards and
// alerts use (from the old monitoring script), the text report /feed (feed.php) and the dashboard people open
// (dashboard.php). IP allowlist in nginx-proxy, which also asks the METRICS_HTPASSWD users for every page except
// /metrics and /feed; an optional bearer token here guards those two.
require __DIR__ . '/metrics.php';

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
if ($path === '/') {
    // The dashboard's Overview; ?fragment=1 is its body alone, which the page fetches every minute.
    require __DIR__ . '/overview.php';
    $body = overview_body(time());
    if (isset($_GET['fragment'])) {
        header('Content-Type: text/html; charset=utf-8');
        header('Cache-Control: no-store');
        exit($body);
    }
    dashboard_page('/', 'Overview', $body);
}
if ($path === '/traffic' || $path === '/traffic.json') {
    require __DIR__ . '/http.php';
    require __DIR__ . '/traffic-tab.php';
    traffic_tab_route($path);
}
if (strpos($path, '/logs') === 0) {
    require __DIR__ . '/http.php';
    require __DIR__ . '/logs.php';
    logs_tab_route($path);
}
if ($path !== '/metrics' && $path !== '/feed') metrics_respond(404, "not found\n");

$token = (string) getenv('METRICS_TOKEN');
if ($token !== '') {
    if (strlen($token) < 24) metrics_respond(503, "METRICS_TOKEN must have 24+ characters\n");
    $authorization = isset($_SERVER['HTTP_AUTHORIZATION']) ? (string) $_SERVER['HTTP_AUTHORIZATION'] : '';
    if (!hash_equals('Bearer ' . $token, $authorization)) {
        header('WWW-Authenticate: Bearer');
        metrics_respond(401, "unauthorized\n");
    }
}

if ($path === '/feed') {
    require __DIR__ . '/http.php';
    require __DIR__ . '/feed.php';
    http_response_code(200);
    header('Content-Type: text/plain; charset=utf-8');
    header('Cache-Control: no-store');
    exit(feed_text(time()));
}

// The sampler's latest snapshot (sampler.php): a scrape collects nothing. Without a fresh one (the sampler has
// stopped or not sampled yet) every group is down and no other figure is served.
$now = time();
$snapshot = metrics_snapshot($now);
if ($snapshot !== null) {
    $metrics = Metrics::fromFamilies($snapshot['families']);
} else {
    $metrics = new Metrics();
    foreach (metrics_groups() as $group) {
        $metrics->add('setup_metrics_group_up', 'Whether every part of the metrics group was collected', 0, array('group' => $group));
    }
}
$taken = metrics_read_json(metrics_snapshot_directory() . '/snapshot.json');
if ($taken !== null && isset($taken['taken'])) {
    $metrics->add('setup_metrics_snapshot_timestamp_seconds', 'When the sampler took its latest snapshot', (int) $taken['taken']);
}
metrics_respond(200, $metrics->render());
