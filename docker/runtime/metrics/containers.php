<?php
// Containers group: state, health, restarts, OOM kills and resources of each container of this project, per service.
// The metrics service has no Docker access: the collector (docker-collector.php, service metrics-docker) writes
// a snapshot into a volume this service mounts read-only.

function metrics_containers_parts()
{
    return array('metrics_containers_states');
}

// The collector's snapshot; older than three of its intervals (plus a margin) means it stopped.
function metrics_containers_state()
{
    static $state = null;
    if ($state !== null) return $state;
    $file = metrics_setting('METRICS_DOCKER_DIR', '/host/docker') . '/state.json';
    $text = @file_get_contents($file);
    if ($text === false) throw new RuntimeException('no Docker snapshot ' . $file . ' (is metrics-docker running?)');
    $data = json_decode($text, true);
    if (!is_array($data) || !isset($data['collected'], $data['containers']) || !is_array($data['containers'])) {
        throw new RuntimeException('unreadable Docker snapshot ' . $file);
    }
    $limit = 3 * max(5, (int) metrics_setting('METRICS_DOCKER_INTERVAL', '15')) + 30;
    $age = time() - (int) $data['collected'];
    if ($age > $limit) throw new RuntimeException('the Docker snapshot is ' . $age . ' s old');
    $state = $data;
    return $state;
}

function metrics_containers_states(Metrics $metrics)
{
    $state = metrics_containers_state();
    $project = isset($state['project']) ? (string) $state['project'] : '';
    $metrics->add('setup_containers_collected_timestamp_seconds', 'When the Docker collector read the containers',
        (int) $state['collected']);
    $oom = isset($state['oom_kills']) && is_array($state['oom_kills']) ? $state['oom_kills'] : array();
    $services = array();
    foreach ($state['containers'] as $container) {
        $labels = array('project' => $project, 'service' => (string) $container['service'], 'container' => (string) $container['name']);
        $services[(string) $container['service']] = true;
        $metrics->add('setup_container_info', 'Container state (created, running, restarting, exited, …) and health (healthy, unhealthy, starting, none)', 1,
            $labels + array('image' => (string) $container['image'], 'state' => (string) $container['state'],
                            'health' => (string) $container['health']));
        $metrics->add('setup_container_running', 'Whether the container is running (1) or not (0)',
            $container['state'] === 'running' ? 1 : 0, $labels);
        if ($container['health'] !== 'none') {
            $metrics->add('setup_container_healthy', 'Whether the container health check passes (1); starting or unhealthy is 0',
                $container['health'] === 'healthy' ? 1 : 0, $labels);
        }
        $metrics->add('setup_container_restarts_total', 'Restarts by the restart policy since the container was created (RestartCount)',
            (int) $container['restarts'], $labels, 'counter');
        $metrics->add('setup_container_oom_killed', 'Whether the last exit was an out-of-memory kill (State.OOMKilled)',
            $container['oom_killed'] ? 1 : 0, $labels);
        $metrics->add('setup_container_exit_code', 'Exit code of the last exit (0 while never exited)', (int) $container['exit_code'], $labels);
        if ($container['started'] > 0) {
            $metrics->add('setup_container_started_timestamp_seconds', 'When the container last started', (int) $container['started'], $labels);
        }
        $limit = isset($container['memory_limit']) ? (int) $container['memory_limit'] : 0;
        if ($limit > 0) {
            $metrics->add('setup_container_memory_limit_bytes', 'The container\'s memory limit (mem_limit)', $limit, $labels);
        }
        if (empty($container['resources'])) continue;
        $resources = $container['resources'];
        $metrics->add('setup_container_cpu_seconds_total', 'CPU time the running container used since it started',
            (float) $resources['cpu_seconds'], $labels, 'counter');
        if ($resources['cpu_percent'] !== null) {
            $metrics->add('setup_container_cpu_percent', 'CPU use between the Docker collector\'s last two reads (percent of one CPU)',
                (float) $resources['cpu_percent'], $labels);
        }
        $metrics->add('setup_container_memory_bytes', 'Memory the running container uses, without the inactive page cache (as docker stats)',
            (int) $resources['memory_bytes'], $labels);
        if ($limit > 0) {
            $metrics->add('setup_container_memory_limit_ratio', 'Memory use as a share of mem_limit (1 = at the limit)',
                round($resources['memory_bytes'] / $limit, 4), $labels);
        }
        $metrics->add('setup_container_io_read_bytes_total', 'Bytes the running container read from block devices', (int) $resources['io_read_bytes'], $labels, 'counter');
        $metrics->add('setup_container_io_write_bytes_total', 'Bytes the running container wrote to block devices', (int) $resources['io_write_bytes'], $labels, 'counter');
    }
    foreach ($oom as $service => $count) $services[(string) $service] = true;
    ksort($services);
    foreach (array_keys($services) as $service) {
        $metrics->add('setup_container_oom_kills_total', 'Out-of-memory kills in the service\'s containers (Docker oom events) since the collector started',
            isset($oom[$service]) ? (int) $oom[$service] : 0, array('project' => $project, 'service' => (string) $service), 'counter');
    }
}
