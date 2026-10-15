<?php
// PHP group: php-fpm's pool status (pm.status_path) and OPcache's statistics, both asked from php-fpm over FastCGI
// on the metrics network (METRICS_PHP_FPM_ADDRESS: addresses tried in order, the first that accepts a connection
// answers). PHP 8.0+ images listen for the status on a pool of their own (setup-status.conf, pm.status_listen 9001):
// a request there needs no worker of the www pool, so the figures come while every worker is busy, which is when
// they matter, and the request is not counted as an active worker. Older PHP, or an image built before that file,
// answer on the pool's 9000. The status path is served by php-fpm only; visitors cannot reach it, because Apache
// passes php-fpm only existing .php files.

function metrics_php_parts()
{
    return array('metrics_php_pool', 'metrics_php_opcache');
}

// One FastCGI request (role responder, no body): the response's stdout without its headers.
function metrics_php_fastcgi($params)
{
    $socket = false;
    foreach (explode(',', metrics_setting('METRICS_PHP_FPM_ADDRESS', 'php-fpm:9001,php-fpm:9000')) as $address) {
        $address = trim($address);
        $socket = @stream_socket_client('tcp://' . $address, $errno, $error, 5);
        if ($socket) break;
    }
    if (!$socket) throw new RuntimeException('cannot reach php-fpm at ' . $address . ': ' . $error);
    stream_set_timeout($socket, 10);
    $record = function ($type, $content) {
        return pack('CCnnCx', 1, $type, 1, strlen($content), 0) . $content;
    };
    $length = function ($text) {
        return strlen($text) < 128 ? chr(strlen($text)) : pack('N', strlen($text) | 0x80000000);
    };
    $encoded = '';
    foreach ($params as $name => $value) {
        $encoded .= $length($name) . $length($value) . $name . $value;
    }
    fwrite($socket, $record(1, pack('nCx5', 1, 0)) . $record(4, $encoded) . $record(4, '') . $record(5, ''));
    $stdout = '';
    $ended = false;
    while (!$ended && !feof($socket)) {
        $header = fread($socket, 8);
        if ($header === false || strlen($header) < 8) break;
        $fields = unpack('Cversion/Ctype/nid/nlength/Cpadding', $header);
        $content = '';
        while (strlen($content) < $fields['length'] + $fields['padding'] && !feof($socket)) {
            $chunk = fread($socket, $fields['length'] + $fields['padding'] - strlen($content));
            if ($chunk === false || $chunk === '') break;
            $content .= $chunk;
        }
        if ($fields['type'] === 6) $stdout .= substr($content, 0, $fields['length']);
        if ($fields['type'] === 3) $ended = true;
    }
    fclose($socket);
    if (!$ended) throw new RuntimeException('no complete FastCGI answer from php-fpm at ' . $address);
    $parts = preg_split('/\r?\n\r?\n/', $stdout, 2);
    if (count($parts) < 2) throw new RuntimeException('no FastCGI answer body from php-fpm');
    if (preg_match('/^Status: (\d{3})/mi', $parts[0], $status) && $status[1] !== '200') {
        throw new RuntimeException('php-fpm answered ' . $status[1] . ' for ' . $params['SCRIPT_NAME']);
    }
    return $parts[1];
}

function metrics_php_request($script, $file, $query = '')
{
    return metrics_php_fastcgi(array('GATEWAY_INTERFACE' => 'CGI/1.1', 'REQUEST_METHOD' => 'GET', 'SCRIPT_NAME' => $script,
        'SCRIPT_FILENAME' => $file, 'REQUEST_URI' => $script . ($query === '' ? '' : '?' . $query), 'QUERY_STRING' => $query,
        'SERVER_PROTOCOL' => 'HTTP/1.1', 'REMOTE_ADDR' => '127.0.0.1', 'SERVER_NAME' => 'setup-metrics'));
}

// pm.status_path's JSON: workers, the listen queue and the counters since php-fpm started.
function metrics_php_pool(Metrics $metrics)
{
    $status = json_decode(metrics_php_request('/setup-fpm-status', '/setup-fpm-status', 'json'), true);
    if (!is_array($status) || !isset($status['active processes'])) throw new RuntimeException('no php-fpm status (pm.status_path)');
    $labels = array('pool' => (string) $status['pool']);
    $metrics->add('setup_php_fpm_processes', 'php-fpm workers by state: active (serving a request) or idle',
        (int) $status['active processes'], $labels + array('state' => 'active'));
    $metrics->add('setup_php_fpm_processes', 'php-fpm workers by state: active (serving a request) or idle',
        (int) $status['idle processes'], $labels + array('state' => 'idle'));
    $metrics->add('setup_php_fpm_max_active_processes', 'Most workers active at once since php-fpm started', (int) $status['max active processes'], $labels);
    $metrics->add('setup_php_fpm_listen_queue', 'Requests waiting for a free worker now', (int) $status['listen queue'], $labels);
    $metrics->add('setup_php_fpm_listen_queue_max', 'Most requests waiting at once since php-fpm started', (int) $status['max listen queue'], $labels);
    $metrics->add('setup_php_fpm_max_children_reached_total', 'Times every worker (pm.max_children) was busy since php-fpm started',
        (int) $status['max children reached'], $labels, 'counter');
    $metrics->add('setup_php_fpm_slow_requests_total', 'Requests over request_slowlog_timeout since php-fpm started (0 while the slowlog is off)',
        (int) $status['slow requests'], $labels, 'counter');
    $metrics->add('setup_php_fpm_accepted_connections_total', 'Requests php-fpm accepted since it started', (int) $status['accepted conn'], $labels, 'counter');
    $metrics->add('setup_php_fpm_start_timestamp_seconds', 'When php-fpm started (a deploy reloads its workers, not this)', (int) $status['start time'], $labels);
}

// opcache_get_status() inside php-fpm (fpm-opcache.php): hit rate, memory, cached scripts and restarts.
function metrics_php_opcache(Metrics $metrics)
{
    // The path in the php-fpm container, which mounts setup's runtime where this service does.
    $script = metrics_setting('METRICS_PHP_OPCACHE_SCRIPT', '/opt/setup/runtime/metrics/fpm-opcache.php');
    $answer = json_decode(metrics_php_request('/setup-fpm-opcache', $script), true);
    if (!is_array($answer)) throw new RuntimeException('no OPcache answer from php-fpm');
    $status = $answer['status'];
    $metrics->add('setup_php_opcache_enabled', 'Whether OPcache caches php-fpm\'s scripts (PHP_OPCACHE)',
        is_array($status) && !empty($status['opcache_enabled']) ? 1 : 0);
    if (!is_array($status) || empty($status['opcache_enabled'])) return;
    $memory = $status['memory_usage'];
    $statistics = $status['opcache_statistics'];
    $metrics->add('setup_php_opcache_hit_rate', 'Share of script loads served from OPcache since its last restart (percent)',
        round((float) $statistics['opcache_hit_rate'], 2));
    $metrics->add('setup_php_opcache_hits_total', 'Script loads served from OPcache since its last restart', (int) $statistics['hits'], array(), 'counter');
    $metrics->add('setup_php_opcache_misses_total', 'Script loads compiled since OPcache\'s last restart', (int) $statistics['misses'], array(), 'counter');
    foreach (array('used' => 'used_memory', 'free' => 'free_memory', 'wasted' => 'wasted_memory') as $state => $key) {
        $metrics->add('setup_php_opcache_memory_bytes', 'OPcache shared memory by state: used, free, wasted (by changed scripts)',
            (int) $memory[$key], array('state' => $state));
    }
    $metrics->add('setup_php_opcache_cached_scripts', 'Scripts in OPcache', (int) $statistics['num_cached_scripts']);
    if ($answer['max_accelerated_files'] !== null) {
        $metrics->add('setup_php_opcache_max_cached_keys', 'Room for scripts in OPcache (its hash table, from opcache.max_accelerated_files)',
            (int) $statistics['max_cached_keys']);
    }
    foreach (array('oom' => 'oom_restarts', 'hash' => 'hash_restarts', 'manual' => 'manual_restarts') as $reason => $key) {
        $metrics->add('setup_php_opcache_restarts_total', 'OPcache restarts by reason: oom (memory full), hash (no room for scripts), manual',
            (int) $statistics[$key], array('reason' => $reason), 'counter');
    }
}
