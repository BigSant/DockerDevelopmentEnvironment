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
    return array('metrics_http_access', 'metrics_http_errors');
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

// [start, end): METRICS_HTTP_WINDOW_MINUTES full minutes before the current one.
function metrics_http_window()
{
    $now = (int) metrics_setting('METRICS_HTTP_NOW', (string) time());
    $end = $now - $now % 60;
    return array($end - 60 * max(1, (int) metrics_setting('METRICS_HTTP_WINDOW_MINUTES', '5')), $end);
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
    // A day without requests has no file; no directory means the collector's volume is not mounted.
    if (!is_dir(metrics_logs_directory())) throw new RuntimeException('no log collector files in ' . metrics_logs_directory());
    $codes = $ips = $agents = $minutes = array();
    $bytes = 0;
    $fields = null;
    $tools = metrics_http_tool_hosts();
    // The counter gets the line the clock has just parsed.
    metrics_http_scan(metrics_http_files('nginx-proxy', 'access'), function ($line) use (&$fields) {
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
    $counts = array_fill_keys(array_keys(METRICS_HTTP_ERROR_TYPES), 0);
    $files = array_merge(metrics_http_files('webserver', 'error'), metrics_http_files('php-fpm', 'error'));
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
