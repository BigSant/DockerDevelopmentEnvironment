<?php
// Docker collector of the metrics service (service metrics-docker): reads this project's containers from the
// Docker API and writes a small snapshot the web-facing metrics service reads from a shared volume.
// Only this process touches the Docker socket: it sends GET requests only, has no network (network_mode: none),
// a read-only root filesystem and no capabilities; the metrics service never sees the socket. The socket's
// read-only mount does not limit the Docker API (any request still works through it): the protection is this
// GET-only code in a container nothing can reach.
// Usage: php docker-collector.php [--once]

function collector_setting($name, $default)
{
    $value = getenv($name);
    return $value === false || $value === '' ? $default : (string) $value;
}

// One GET to the Docker API over its Unix socket (HTTP/1.0: the daemon answers without chunking and closes).
function collector_get($path)
{
    $socket = collector_setting('METRICS_DOCKER_SOCKET', '/var/run/docker.sock');
    $connection = @stream_socket_client('unix://' . $socket, $errno, $error, 10);
    if (!$connection) throw new RuntimeException('cannot connect to ' . $socket . ': ' . $error);
    stream_set_timeout($connection, 30);
    fwrite($connection, 'GET ' . $path . " HTTP/1.0\r\nHost: docker\r\nAccept: application/json\r\n\r\n");
    $response = stream_get_contents($connection);
    fclose($connection);
    $parts = explode("\r\n\r\n", (string) $response, 2);
    if (count($parts) !== 2 || !preg_match('#^HTTP/\d\.\d (\d{3})#', $parts[0], $match)) {
        throw new RuntimeException('no HTTP answer to GET ' . $path);
    }
    if ($match[1] !== '200') throw new RuntimeException('GET ' . $path . ': HTTP ' . $match[1]);
    return $parts[1];
}

function collector_json($path)
{
    $data = json_decode(collector_get($path), true);
    if (!is_array($data)) throw new RuntimeException('GET ' . $path . ': not JSON');
    return $data;
}

function collector_filters($filters)
{
    return rawurlencode(json_encode($filters));
}

// Docker's RFC 3339 times with nanoseconds; the zero time (never started or finished) is 0.
function collector_time($text)
{
    if (!is_string($text) || strpos($text, '0001-01-01') === 0) return 0;
    $time = strtotime(preg_replace('/\.\d+/', '', $text));
    return $time === false ? 0 : $time;
}

// The snapshot: server versions, each container of the project and the OOM kills counted from the events since
// the previous snapshot (a counter that survives collector restarts through the snapshot itself).
function collector_snapshot($project, $previous, $now)
{
    $label = 'com.docker.compose.project=' . $project;
    $info = collector_json('/info');
    $containers = array();
    foreach (collector_json('/containers/json?all=1&filters=' . collector_filters(array('label' => array($label)))) as $listed) {
        $inspect = collector_json('/containers/' . rawurlencode($listed['Id']) . '/json');
        $state = isset($inspect['State']) ? $inspect['State'] : array();
        $labels = isset($inspect['Config']['Labels']) ? $inspect['Config']['Labels'] : array();
        $containers[] = array(
            'name' => ltrim((string) $inspect['Name'], '/'),
            'service' => isset($labels['com.docker.compose.service']) ? (string) $labels['com.docker.compose.service'] : '',
            'image' => isset($inspect['Config']['Image']) ? (string) $inspect['Config']['Image'] : '',
            'state' => isset($state['Status']) ? (string) $state['Status'] : 'unknown',
            'health' => isset($state['Health']['Status']) ? (string) $state['Health']['Status'] : 'none',
            'restarts' => isset($inspect['RestartCount']) ? (int) $inspect['RestartCount'] : 0,
            'exit_code' => isset($state['ExitCode']) ? (int) $state['ExitCode'] : 0,
            'oom_killed' => !empty($state['OOMKilled']),
            'started' => collector_time(isset($state['StartedAt']) ? $state['StartedAt'] : null),
            'finished' => collector_time(isset($state['FinishedAt']) ? $state['FinishedAt'] : null),
        );
    }
    usort($containers, function ($a, $b) { return strcmp($a['name'], $b['name']); });
    $oom = isset($previous['oom_kills']) && is_array($previous['oom_kills']) ? $previous['oom_kills'] : array();
    $since = isset($previous['events_until']) ? (int) $previous['events_until'] : $now;
    if ($since < $now) {
        $filters = collector_filters(array('type' => array('container'), 'event' => array('oom'), 'label' => array($label)));
        foreach (preg_split('/\r?\n/', collector_get('/events?since=' . $since . '&until=' . $now . '&filters=' . $filters)) as $line) {
            $event = json_decode($line, true);
            if (!is_array($event)) continue;
            $attributes = isset($event['Actor']['Attributes']) ? $event['Actor']['Attributes'] : array();
            $service = isset($attributes['com.docker.compose.service']) ? (string) $attributes['com.docker.compose.service'] : '';
            $oom[$service] = (isset($oom[$service]) ? (int) $oom[$service] : 0) + 1;
        }
    }
    return array(
        'collected' => $now,
        'project' => $project,
        'server' => array('os' => isset($info['OperatingSystem']) ? (string) $info['OperatingSystem'] : '',
                          'docker' => isset($info['ServerVersion']) ? (string) $info['ServerVersion'] : ''),
        'containers' => $containers,
        'oom_kills' => (object) $oom,
        'events_until' => max($since, $now),
    );
}

function collector_run()
{
    $project = collector_setting('METRICS_DOCKER_PROJECT', '');
    if (!preg_match('/^[a-z0-9][a-z0-9_-]*$/', $project)) throw new RuntimeException('METRICS_DOCKER_PROJECT is not a project name');
    $directory = collector_setting('METRICS_DOCKER_DIR', '/var/lib/setup-docker');
    $file = $directory . '/state.json';
    $previous = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
    if (!is_array($previous) || (isset($previous['project']) && $previous['project'] !== $project)) $previous = array();
    $snapshot = collector_snapshot($project, $previous, time());
    $temporary = $file . '.' . getmypid();
    if (@file_put_contents($temporary, json_encode($snapshot)) === false) throw new RuntimeException('cannot write ' . $temporary);
    // Readable by the metrics service, which runs as the project account.
    chmod($temporary, 0644);
    if (!@rename($temporary, $file)) throw new RuntimeException('cannot replace ' . $file);
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    $once = in_array('--once', $argv, true);
    $interval = max(5, (int) collector_setting('METRICS_DOCKER_INTERVAL', '15'));
    umask(022);
    do {
        try {
            collector_run();
        } catch (Throwable $error) {
            // The snapshot ages; the metrics service reports it as stale.
            fwrite(STDERR, 'metrics-docker: ' . $error->getMessage() . "\n");
            if ($once) exit(1);
        }
        if (!$once) sleep($interval);
    } while (!$once);
}
