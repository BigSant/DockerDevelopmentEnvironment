<?php
// HTTP group: the old script's access and error log figures over the last full minutes (its "5 minutes ago"
// up to the current minute), from the log collector's day files (read-only volume): nginx-proxy's access log
// (client IPs after the real-IP handling; the tool sites <tool>.DOMAIN left out) and the error logs of the
// webserver and php-fpm. Each file is read backwards from its end in chunks and only until the window has
// passed, so a large log is neither loaded nor read whole.

const METRICS_HTTP_CHUNK = 65536;
// Longer lines are skipped rather than buffered.
const METRICS_HTTP_LINE_LIMIT = 1048576;
// Lines are written when a request ends, so a slightly older line may follow a newer one.
const METRICS_HTTP_SLACK = 60;
// Error types with the viewer's labels; each matches the script's awk pattern, counted once per entry.
const METRICS_HTTP_ERROR_TYPES = array(
    'Access to context' => '/Access to context/',
    'Abort request processing' => '/Abort request processing/',
    'PHP Warning' => '/PHP Warning/',
    'PHP Fatal error' => '/PHP Fatal error/',
    'Segmentation fault' => '/Segmentation fault/',
    'Connection reset' => '/connection reset/i',
);

function metrics_http_parts()
{
    return array('metrics_http_access', 'metrics_http_errors', 'metrics_http_traffic');
}

// nginx-proxy's TCP table snapshot (security group).
function metrics_http_directory()
{
    return rtrim(metrics_setting('METRICS_HTTP_LOG_DIR', '/host/http-logs'), '/');
}

function metrics_logs_directory()
{
    return rtrim(metrics_setting('METRICS_LOGS_DIR', '/host/logs'), '/');
}

// The log collector saves its state at least every 5 seconds while its loop runs. Without a recent one it has
// stopped or hangs (a Docker stream that never answers), its files miss the latest lines, and a window of zero
// requests and zero 5xx would read as a quiet site: the HTTP group is down instead.
const METRICS_LOGS_STALE = 120;
// The end of the note the collector writes when a day's file reaches its bound (LOGS_FULL_END in log-collector.php).
const METRICS_LOGS_FULL_END = ' are only in docker logs';

// A file the collector stopped writing (its day's bound, LOGS_MAX_MB) misses the rest of the day's lines: during a
// flood the request figures would drop to zero exactly when they matter, so the part reading it is down instead.
function metrics_logs_complete($files)
{
    foreach ($files as $file) {
        $size = (int) @filesize($file);
        $end = $size > 0 ? (string) @file_get_contents($file, false, null, max(0, $size - 256)) : '';
        if (substr($end, -strlen(METRICS_LOGS_FULL_END) - 1) === METRICS_LOGS_FULL_END . "\n") {
            throw new RuntimeException(basename(dirname($file)) . '/' . basename($file) . ' reached its LOGS_MAX_MB bound; later lines are only in docker logs');
        }
    }
    return $files;
}

function metrics_logs_check()
{
    // A day without requests has no file; no directory means the collector's volume is not mounted.
    if (!is_dir(metrics_logs_directory())) throw new RuntimeException('no log collector files in ' . metrics_logs_directory());
    $state = metrics_logs_directory() . '/.collector-state.json';
    clearstatcache(true, $state);
    if (!is_file($state)) throw new RuntimeException('the log collector (metrics-logs) has saved no state yet');
    $age = time() - (int) @filemtime($state);
    if ($age > METRICS_LOGS_STALE) {
        throw new RuntimeException('the log collector (metrics-logs) last saved its state ' . $age . ' s ago: stopped or hung');
    }
}

// [start, end): METRICS_HTTP_WINDOW_MINUTES full minutes before the current one.
function metrics_http_window()
{
    $now = (int) metrics_setting('METRICS_HTTP_NOW', (string) time());
    $end = $now - $now % 60;
    return array($end - 60 * max(1, (int) metrics_setting('METRICS_HTTP_WINDOW_MINUTES', '1')), $end);
}

// The collector's files of one service and kind that the window reaches: today's and, near midnight, the day
// before (the collector compresses a day two hours after its last line, longer than the longest window).
function metrics_http_files($service, $kind)
{
    list($start, $end) = metrics_http_window();
    $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
    $files = array();
    foreach (array($end - 1, $start - METRICS_HTTP_SLACK) as $moment) {
        $day = (new DateTime('@' . $moment))->setTimezone($zone)->format('Y-m-d');
        $file = metrics_logs_directory() . '/' . $service . '/' . $kind . '-' . $day . '.log';
        if (is_file($file) && !in_array($file, $files, true)) $files[] = $file;
    }
    return $files;
}

// <tool>.DOMAIN and www.<tool>.DOMAIN of setup's tool sites and the project's own (PROXY_TOOLS): not site traffic.
function metrics_http_tool_hosts()
{
    $domain = strtolower(metrics_setting('DOMAIN', ''));
    if ($domain === '') return array();
    $tools = array_merge(array('pma', 'mailpit', 'cron', 'dbmonitor', 'backup', 'metrics'),
        preg_split('/[\s,]+/', metrics_setting('PROXY_TOOLS', ''), -1, PREG_SPLIT_NO_EMPTY));
    $hosts = array();
    foreach ($tools as $tool) {
        $hosts[$tool . '.' . $domain] = true;
        $hosts['www.' . $tool . '.' . $domain] = true;
    }
    return $hosts;
}

// Calls $visit for each complete line from the end of the file towards its start until it returns false.
// Holds one chunk and one line at most; the unfinished last line (being written) is not visited.
function metrics_http_backwards($file, $visit)
{
    $handle = @fopen($file, 'rb');
    if ($handle === false) throw new RuntimeException('cannot read ' . $file);
    fseek($handle, 0, SEEK_END);
    $position = ftell($handle);
    $carry = '';
    $skipping = true;
    try {
        while ($position > 0) {
            $size = (int) min(METRICS_HTTP_CHUNK, $position);
            $position -= $size;
            fseek($handle, $position);
            $chunk = (string) fread($handle, $size);
            $lines = explode("\n", $chunk . $carry);
            $carry = array_shift($lines);
            if ($lines && $skipping) {
                array_pop($lines);
                $skipping = false;
            }
            if (strlen($carry) > METRICS_HTTP_LINE_LIMIT) {
                $carry = '';
                $skipping = true;
            }
            for ($index = count($lines) - 1; $index >= 0; $index--) {
                if ($visit($lines[$index]) === false) return;
            }
        }
        if (!$skipping && $carry !== '') $visit($carry);
    } finally {
        fclose($handle);
    }
}

// Visits the lines of every file; $time(line) is a Unix time or null (no timestamp: a continuation of the entry
// above it). $count(entry lines, time) gets each entry inside the window.
function metrics_http_scan($files, $time, $count)
{
    list($start, $end) = metrics_http_window();
    foreach ($files as $file) {
        $continuation = array();
        metrics_http_backwards($file, function ($line) use ($time, $count, $start, $end, &$continuation) {
            $at = $time($line);
            if ($at === null) {
                if (count($continuation) < 100) $continuation[] = $line;
                return true;
            }
            $entry = $continuation;
            $continuation = array();
            if ($at < $start - METRICS_HTTP_SLACK) return false;
            if ($at >= $start && $at < $end) $count($line, $entry, $at);
            return true;
        });
    }
}

// Unix time of a log timestamp, cached: consecutive lines share their second. $zone: the zone the line names.
function metrics_http_time($format, $text, $zone = null)
{
    static $cache = array();
    $key = $format . '|' . $text . '|' . $zone;
    if (!isset($cache[$key])) {
        if (count($cache) > 1000) $cache = array();
        try {
            $zone = new DateTimeZone($zone !== null ? $zone : metrics_setting('TZ', date_default_timezone_get()));
        } catch (Exception $error) {
            $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
        }
        $date = DateTime::createFromFormat($format, $text, $zone);
        $cache[$key] = $date === false ? null : $date->getTimestamp();
    }
    return $cache[$key];
}

// An error log's entry time: the webserver's (Apache) "[Fri Oct 09 12:30:00.500000 2026] ..." in the container time
// zone (TZ); PHP's "[09-Oct-2026 12:30:00 Europe/Vilnius] PHP Warning: ..." in the zone it names; php-fpm's own
// "[09-Oct-2026 12:30:00] NOTICE: ..." in TZ.
function metrics_http_error_time($line)
{
    if (preg_match('/^\[\w{3} (\w{3} \d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)? (\d{4})\]/', $line, $match)) {
        return metrics_http_time('!M d H:i:s Y', $match[1] . ' ' . $match[2]);
    }
    if (preg_match('/^\[(\d{2}-\w{3}-\d{4} \d{2}:\d{2}:\d{2})(?: ([A-Za-z][A-Za-z0-9_\/+-]*))?\]/', $line, $match)) {
        return metrics_http_time('!d-M-Y H:i:s', $match[1], isset($match[2]) ? $match[2] : null);
    }
    return null;
}

// The combined format of nginx-proxy's site log: client, time, request, status, bytes, referrer, user agent.
function metrics_http_access_fields($line)
{
    $quoted = '"((?:[^"\\\\]|\\\\.)*)"';
    if (!preg_match('/^(\S+) \S+ .*?\[(\d{2}\/\w{3}\/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4})\] ' . $quoted . ' (\d{3}) (\d+|-) '
        . $quoted . ' ' . $quoted . '/', $line, $match)) return null;
    return $match;
}

function metrics_http_top($counts, $limit)
{
    // Busiest first, ties by name, so the same log always gives the same series.
    uksort($counts, function ($a, $b) use ($counts) {
        return $counts[$b] === $counts[$a] ? strcmp((string) $a, (string) $b) : $counts[$b] - $counts[$a];
    });
    return array_slice($counts, 0, $limit, true);
}

function metrics_http_access(Metrics $metrics)
{
    metrics_logs_check();
    $codes = $ips = $agents = $minutes = array();
    $bytes = 0;
    $fields = null;
    $tools = metrics_http_tool_hosts();
    // The counter gets the line the clock has just parsed.
    metrics_http_scan(metrics_logs_complete(metrics_http_files('nginx-proxy', 'access')), function ($line) use (&$fields) {
        $fields = metrics_http_access_fields($line);
        return $fields === null ? null : metrics_http_time('!d/M/Y:H:i:s O', $fields[2]);
    }, function ($line, $continuation, $at) use (&$fields, &$codes, &$ips, &$agents, &$minutes, &$bytes, $tools) {
        if ($tools && preg_match('/ host=(\S+)$/', $line, $host) && isset($tools[strtolower($host[1])])) return;
        $codes[$fields[4]] = (isset($codes[$fields[4]]) ? $codes[$fields[4]] : 0) + 1;
        $ips[$fields[1]] = (isset($ips[$fields[1]]) ? $ips[$fields[1]] : 0) + 1;
        // Printable ASCII only (nginx already escapes the rest), bounded: label values must stay valid UTF-8.
        $agent = substr(preg_replace('/[^\x20-\x7e]/', '?', $fields[7]), 0, 256);
        $agents[$agent] = (isset($agents[$agent]) ? $agents[$agent] : 0) + 1;
        $minute = $at - $at % 60;
        $minutes[$minute] = (isset($minutes[$minute]) ? $minutes[$minute] : 0) + 1;
        $bytes += (int) $fields[5];
    });
    ksort($codes);
    foreach ($codes as $code => $count) {
        $metrics->add('http_response_codes', 'Response codes requests returned', $count, array('code' => (string) $code));
    }
    $metrics->add('http_bytes_transferred_total', 'Total bytes transferred', $bytes);
    // The script's head -10 of the client addresses and user agents.
    foreach (metrics_http_top($ips, 10) as $ip => $count) {
        $metrics->add('http_request_ips', 'Response Ips', $count, array('ip' => (string) $ip));
    }
    foreach (metrics_http_top($agents, 10) as $agent => $count) {
        $metrics->add('http_request_user_agents', 'Requests per user agent (top 10)', $count, array('user_agent' => (string) $agent));
    }
    list($start, $end) = metrics_http_window();
    $metrics->add('http_requests_per_minute', 'Average requests per minute over the window', array_sum($minutes) / (($end - $start) / 60));
    $metrics->add('http_requests_per_minute_max', 'Requests in the busiest minute of the window', $minutes ? max($minutes) : 0);
}

// The webserver's and php-fpm's error logs (PHP's messages are php-fpm's); a day without errors has no file.
function metrics_http_errors(Metrics $metrics)
{
    metrics_logs_check();
    $counts = array_fill_keys(array_keys(METRICS_HTTP_ERROR_TYPES), 0);
    $files = metrics_logs_complete(array_merge(metrics_http_files('webserver', 'error'), metrics_http_files('php-fpm', 'error')));
    metrics_http_scan($files, 'metrics_http_error_time', function ($line, $continuation) use (&$counts) {
        $text = implode("\n", array_merge(array($line), $continuation));
        foreach (METRICS_HTTP_ERROR_TYPES as $type => $pattern) {
            if (preg_match($pattern, $text)) $counts[$type]++;
        }
    });
    foreach ($counts as $type => $count) {
        $metrics->add('http_error_type', 'HTTP error types', $count, array('type' => $type));
    }
}

// The traffic analysis (traffic.php) of the same window, as a bounded set: requests per class, the 20 busiest bots,
// attacks per type, the 10 busiest URLs and those with the most 5xx, requests slower than
// METRICS_SLOW_REQUEST_SECONDS (2 s) and the median and 95th percentile response time.
function metrics_http_traffic(Metrics $metrics)
{
    require_once __DIR__ . '/traffic.php';
    metrics_logs_check();
    metrics_logs_complete(metrics_http_files('nginx-proxy', 'access'));
    list($start, $end) = metrics_http_window();
    $collected = traffic_collect(array(array($start, $end)));
    $aggregate = $collected[0];
    foreach ($aggregate['classes'] as $class => $count) {
        $metrics->add('setup_http_class_requests', 'Requests of the window per class: human, api (scripts and API clients), bot, asset (static file), attack and the project\'s own (config/metrics/traffic.php)',
            $count, array('class' => (string) $class));
    }
    foreach (metrics_http_top($aggregate['bots'], 20) as $bot => $count) {
        $metrics->add('setup_http_bot_requests', 'Requests of the window per known bot (the 20 busiest)', $count, array('bot' => (string) $bot));
    }
    foreach (array_keys(TRAFFIC_ATTACKS) as $type) {
        $metrics->add('setup_http_attack_requests', 'Requests of the window matching an attack pattern, per type',
            isset($aggregate['attacks'][$type]) ? $aggregate['attacks'][$type] : 0, array('type' => $type));
    }
    $label = function ($url) { return substr((string) $url, 0, 200); };
    foreach (metrics_http_top($aggregate['urls'], 10) as $url => $count) {
        $metrics->add('setup_http_url_requests', 'Requests of the window per URL without its query (the 10 busiest, static files excluded)',
            $count, array('url' => $label($url)));
    }
    foreach (metrics_http_top($aggregate['errors5'], 10) as $url => $count) {
        $metrics->add('setup_http_url_5xx_requests', '5xx responses of the window per URL (the 10 with the most)', $count, array('url' => $label($url)));
    }
    $limit = (float) metrics_setting('METRICS_SLOW_REQUEST_SECONDS', '2');
    $slow = 0;
    foreach ($aggregate['durations'] as $milliseconds => $count) {
        if ($milliseconds > $limit * 1000) $slow += $count;
    }
    $metrics->add('setup_http_slow_requests', 'Requests of the window that took longer than METRICS_SLOW_REQUEST_SECONDS (static files excluded)', $slow);
    // Gauges of the window, not a summary: a summary's quantiles are over its own lifetime.
    foreach (array('p50' => 0.5, 'p95' => 0.95) as $name => $share) {
        $value = traffic_percentile($aggregate['durations'], $share);
        if ($value !== null) {
            $metrics->add('setup_http_response_time_' . $name . '_seconds',
                ($name === 'p50' ? 'Median' : '95th percentile') . ' response time of the window\'s requests (nginx rt, static files excluded)', $value);
        }
    }
    // Every figure is in (with the built-in classes); the dashboard says what is wrong with the project's file.
    $error = traffic_rules()['error'];
    if ($error !== null) throw new MetricsWarning($error);
}
