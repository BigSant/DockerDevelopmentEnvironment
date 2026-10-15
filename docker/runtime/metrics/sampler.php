<?php
// Sampler of the metrics service: collects every group once a minute into a snapshot that /metrics (and the
// dashboard) serve, so the number of scrapes changes neither the load nor the windows, and keeps a 48-hour
// history of the small families for the dashboard's charts. The website probes run in a loop of their own
// (--probes, every METRICS_PROBE_INTERVAL seconds), so a page that takes 30 s never delays the minute.
// Files in METRICS_SNAPSHOT_DIR (data/<env>/metrics): snapshot.json, probes.json, history/<UTC hour>.jsonl.
// Usage: php sampler.php [--once]            the minute loop (one sample with --once)
//        php sampler.php --probes [--once]   the probe loop (one round with --once)
//        php sampler.php --traffic [--once]  the traffic loop: caches the closed hours' analysis (traffic.php)
require_once __DIR__ . '/metrics.php';

// The history keeps the families of up to this many samples, without lists of clients, queries, tables or
// processes (labelled by these): what changes from minute to minute in them is the membership, not a series.
const METRICS_HISTORY_SAMPLES = 16;
const METRICS_HISTORY_SKIPPED_LABELS = array('ip', 'user_agent', 'query', 'schema', 'table', 'process', 'usr', 'label');
const METRICS_HISTORY_HOURS = 48;

function metrics_sampler_interval()
{
    return max(1, (int) metrics_setting('METRICS_SAMPLER_INTERVAL', '60'));
}

function metrics_sample($now)
{
    $started = microtime(true);
    $metrics = new Metrics();
    $problems = array();
    foreach (metrics_groups() as $group) {
        $found = metrics_collect($metrics, $group);
        if ($found) $problems[$group] = $found;
    }
    $metrics->add('setup_metrics_sample_duration_seconds', 'How long the sampler took to collect every group',
        round(microtime(true) - $started, 3));
    $directory = metrics_snapshot_directory();
    metrics_write_json($directory . '/snapshot.json',
        array('taken' => $now, 'interval' => metrics_sampler_interval(), 'families' => $metrics->families(), 'problems' => $problems));
    metrics_history($directory . '/history', $now, $metrics);
}

// One line per sample in the file of its UTC hour: {"t": time, "v": {"name{labels}": value, ...}}. Whole hours
// older than METRICS_HISTORY_HOURS go.
function metrics_history($directory, $now, Metrics $metrics)
{
    if (!is_dir($directory) && !@mkdir($directory, 0755) && !is_dir($directory)) throw new RuntimeException('cannot create ' . $directory);
    $values = array();
    foreach ($metrics->families() as $name => $family) {
        if (count($family['samples']) > METRICS_HISTORY_SAMPLES) continue;
        foreach ($family['samples'] as $sample) {
            if (array_intersect(array_keys($sample[0]), METRICS_HISTORY_SKIPPED_LABELS)) continue 2;
            $values[$name . metrics_labels($sample[0])] = is_float($sample[1]) && !is_finite($sample[1]) ? null : $sample[1];
        }
    }
    $line = json_encode(array('t' => $now, 'v' => (object) $values)) . "\n";
    if (@file_put_contents($directory . '/' . gmdate('Y-m-d-H', $now) . '.jsonl', $line, FILE_APPEND | LOCK_EX) === false) {
        throw new RuntimeException('cannot append to the history in ' . $directory);
    }
    foreach (glob($directory . '/*.jsonl') ?: array() as $file) {
        $hour = DateTime::createFromFormat('!Y-m-d-H', basename($file, '.jsonl'), new DateTimeZone('UTC'));
        if ($hour !== false && $hour->getTimestamp() + 3600 <= $now - METRICS_HISTORY_HOURS * 3600) @unlink($file);
    }
}

function metrics_traffic_round($now)
{
    require_once __DIR__ . '/http.php';
    require_once __DIR__ . '/traffic.php';
    traffic_prepare($now);
}

function metrics_probe_round($now)
{
    require_once __DIR__ . '/website.php';
    metrics_write_json(metrics_snapshot_directory() . '/probes.json', metrics_website_probe($now));
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    umask(022);
    $once = in_array('--once', $argv, true);
    $probes = in_array('--probes', $argv, true);
    if (in_array('--traffic', $argv, true)) {
        // Every 5 minutes: the hour that has closed is cached within minutes.
        do {
            try {
                metrics_traffic_round(time());
            } catch (Throwable $error) {
                fwrite(STDERR, 'metrics-sampler: traffic: ' . $error->getMessage() . "\n");
                if ($once) exit(1);
            }
            if (!$once) sleep(300);
        } while (!$once);
        exit(0);
    }
    if ($probes) {
        require_once __DIR__ . '/website.php';
        if (!metrics_website_urls() && !$once) {
            // Nothing to probe: the loop has nothing to do until the settings change (a restart).
            while (true) sleep(3600);
        }
    }
    $interval = $probes ? metrics_website_probe_interval() : metrics_sampler_interval();
    do {
        $now = time();
        try {
            $probes ? metrics_probe_round($now) : metrics_sample($now);
        } catch (Throwable $error) {
            // The snapshot ages; /metrics then reports every group as down.
            fwrite(STDERR, 'metrics-sampler: ' . $error->getMessage() . "\n");
            if ($once) exit(1);
        }
        if ($once) break;
        // The minute's sample a few seconds after the minute starts, once its last log lines are written.
        $next = $probes ? $now + $interval : $now - $now % $interval + $interval + min(5, intdiv($interval, 4));
        sleep(max(1, $next - time()));
    } while (true);
}
