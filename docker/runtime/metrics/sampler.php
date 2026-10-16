<?php
// Sampler of the metrics service: collects every group once a minute into a snapshot that /metrics (and the
// dashboard) serve, so the number of scrapes changes neither the load nor the windows, and keeps a 48-hour
// history of the small families for the dashboard's charts. The website probes run in a loop of their own
// (--probes, every METRICS_PROBE_INTERVAL seconds), so a page that takes 30 s never delays the minute.
// Files in METRICS_SNAPSHOT_DIR (data/<env>/metrics): snapshot.json, probes.json, history/<UTC hour>.jsonl.
// Each loop runs every round as its own process (--once): a round never sees what an earlier one cached.
// Usage: php sampler.php [--once]            the minute loop (one sample with --once)
//        php sampler.php --probes [--once]   the probe loop (one round with --once)
//        php sampler.php --traffic [--once]  the traffic loop: caches the closed hours' analysis (traffic.php)
require_once __DIR__ . '/metrics.php';

// The history keeps the families of up to this many samples, without lists of clients, queries, tables or
// processes (labelled by these): what changes from minute to minute in them is the membership, not a series.
const METRICS_HISTORY_SAMPLES = 16;
// Families the Overview's lines count whatever their size: one sample per cron job or container. A larger family with
// a cpu label keeps only its cpu="all" sample (the I/O wait line), not one per core.
const METRICS_HISTORY_WHOLE = array('setup_cron_job_last_exit_code', 'setup_container_running');
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
        $samples = $family['samples'];
        if (count($samples) > METRICS_HISTORY_SAMPLES && !in_array($name, METRICS_HISTORY_WHOLE, true)) {
            $samples = array_filter($samples, function ($sample) { return isset($sample[0]['cpu']) && (string) $sample[0]['cpu'] === 'all'; });
        }
        foreach ($samples as $sample) {
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

// One round in a process of its own (php sampler.php [--probes|--traffic] --once): nothing a round caches (a database
// connection or its failure, the Docker collector's snapshot) is carried into the next. A round that fails is
// logged by its process; the loop goes on.
function metrics_round($mode)
{
    $command = escapeshellarg(PHP_BINARY) . ' ' . escapeshellarg(__FILE__) . ($mode === '' ? '' : ' ' . $mode) . ' --once';
    passthru($command, $code);
    return $code;
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    umask(022);
    $once = in_array('--once', $argv, true);
    $probes = in_array('--probes', $argv, true);
    $traffic = in_array('--traffic', $argv, true);
    if ($once) {
        try {
            if ($traffic) metrics_traffic_round(time());
            elseif ($probes) metrics_probe_round(time());
            else metrics_sample(time());
        } catch (Throwable $error) {
            // The snapshot ages; /metrics then reports every group as down.
            fwrite(STDERR, 'metrics-sampler: ' . ($traffic ? 'traffic: ' : '') . $error->getMessage() . "\n");
            exit(1);
        }
        exit(0);
    }
    if ($probes) {
        require_once __DIR__ . '/website.php';
        // Nothing to probe: the loop has nothing to do until the settings change (a restart).
        if (!metrics_website_urls()) while (true) sleep(3600);
    }
    $mode = $traffic ? '--traffic' : ($probes ? '--probes' : '');
    // The traffic loop every 5 minutes (the hour that has closed is cached within minutes), the probes every
    // METRICS_PROBE_INTERVAL, the minute's sample a few seconds after the minute starts, once its last log lines
    // are written.
    $interval = $traffic ? 300 : ($probes ? metrics_website_probe_interval() : metrics_sampler_interval());
    while (true) {
        $now = time();
        metrics_round($mode);
        $next = $mode !== '' ? $now + $interval : $now - $now % $interval + $interval + min(5, intdiv($interval, 4));
        sleep(max(1, $next - time()));
    }
}
