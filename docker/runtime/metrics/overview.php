<?php
// Overview tab: one card per area with the latest snapshot's value, a colour from setup's fixed limits and a
// 24-hour line from the sampler's history. Each card reads the flat "name{labels}" map (dashboard_flat), so the
// same function gives the current value and every point of the line.
require_once __DIR__ . '/dashboard.php';

const OVERVIEW_HOURS = 24;

// Level from fixed limits: over $bad is bad, over $warn a warning.
function overview_level($value, $warn, $bad)
{
    return $value > $bad ? 'bad' : ($value > $warn ? 'warn' : 'ok');
}

function overview_number($value, $decimals = 1)
{
    return number_format((float) $value, $decimals, '.', ' ');
}

function overview_value($values, $key)
{
    return isset($values[$key]) && $values[$key] !== null ? (float) $values[$key] : null;
}

// The cards: title, state(values) => [level, value text, note] or null (nothing to show), series(values) => number.
function overview_cards()
{
    $cores = function ($v) {
        return max(1, count(dashboard_family($v, 'cpu_usage_idle')) - 1);
    };
    $ram = function ($v) {
        $total = overview_value($v, 'ram_total');
        $available = overview_value($v, 'ram_available');
        return $total ? ($total - $available) / $total * 100 : null;
    };
    $errors = function ($v) {
        if (!isset($v['http_requests_per_minute'])) return null;
        $sum = 0;
        foreach (dashboard_family($v, 'http_response_codes') as $labels => $count) {
            if (preg_match('/code="5\d\d"/', $labels)) $sum += $count;
        }
        return $sum / max(1, (int) metrics_setting('METRICS_HTTP_WINDOW_MINUTES', '1'));
    };
    $slowest = function ($v) {
        $times = dashboard_family($v, 'website_response_time');
        return $times ? max($times) : null;
    };
    return array(
        'load' => array('Load', function ($v) use ($cores) {
            $load = overview_value($v, 'uptime_average_load_1min');
            if ($load === null) return null;
            $count = $cores($v);
            return array(overview_level($load, 0.7 * $count, $count), overview_number($load, 2),
                '5 min ' . overview_number(overview_value($v, 'uptime_average_load_10min'), 2) . ' · ' . $count . ' cores');
        }, function ($v) { return overview_value($v, 'uptime_average_load_1min'); }),
        'iowait' => array('I/O wait', function ($v) {
            $wait = overview_value($v, 'cpu_usage_iowait{cpu="all"}');
            if ($wait === null) return null;
            return array(overview_level($wait, 5, 15), overview_number($wait) . ' %', 'CPU time waiting for the disk');
        }, function ($v) { return overview_value($v, 'cpu_usage_iowait{cpu="all"}'); }),
        'ram' => array('Memory', function ($v) use ($ram) {
            $used = $ram($v);
            if ($used === null) return null;
            return array(overview_level($used, 80, 90), overview_number($used) . ' %',
                overview_number(overview_value($v, 'ram_available') / 1048576) . ' GB available of '
                . overview_number(overview_value($v, 'ram_total') / 1048576) . ' GB');
        }, $ram),
        'disk' => array('Disk', function ($v) {
            $used = overview_value($v, 'disk_available_percentage');
            if ($used === null) return null;
            return array(overview_level($used, 80, 90), overview_number($used, 0) . ' %',
                overview_number((overview_value($v, 'disk_size') - overview_value($v, 'disk_used')) / 1048576) . ' GB free');
        }, function ($v) { return overview_value($v, 'disk_available_percentage'); }),
        'errors' => array('5xx / min', function ($v) use ($errors) {
            $rate = $errors($v);
            if ($rate === null) return null;
            return array($rate >= 10 ? 'bad' : ($rate > 0 ? 'warn' : 'ok'), overview_number($rate),
                overview_number(overview_value($v, 'http_requests_per_minute')) . ' requests / min');
        }, $errors),
        'p95' => array('Response p95', function ($v) {
            $p95 = overview_value($v, 'setup_http_response_time_p95_seconds');
            if ($p95 === null) return null;
            return array(overview_level($p95, 1, 3), overview_number($p95, 2) . ' s',
                'median ' . overview_number(overview_value($v, 'setup_http_response_time_p50_seconds'), 2) . ' s');
        }, function ($v) { return overview_value($v, 'setup_http_response_time_p95_seconds'); }),
        'php' => array('PHP workers', function ($v) {
            $active = overview_value($v, 'setup_php_fpm_processes{pool="www",state="active"}');
            if ($active === null) return null;
            $total = $active + overview_value($v, 'setup_php_fpm_processes{pool="www",state="idle"}');
            $queue = overview_value($v, 'setup_php_fpm_listen_queue{pool="www"}');
            $reached = overview_value($v, 'setup_php_fpm_max_children_reached_total{pool="www"}');
            $busy = $total ? $active / $total * 100 : 0;
            return array($queue > 0 ? 'bad' : ($busy >= 80 || $reached > 0 ? 'warn' : 'ok'), overview_number($active, 0) . ' / ' . overview_number($total, 0),
                ($queue > 0 ? overview_number($queue, 0) . ' waiting · ' : '') . 'all busy ' . overview_number($reached, 0) . ' times since start'
                . (($rate = overview_value($v, 'setup_php_opcache_hit_rate')) !== null ? ' · OPcache ' . overview_number($rate) . ' %' : ''));
        }, function ($v) { return overview_value($v, 'setup_php_fpm_processes{pool="www",state="active"}'); }),
        'database' => array('Database', function ($v) {
            $connections = overview_value($v, 'database_connections');
            if (overview_value($v, 'setup_metrics_group_up{group="database"}') !== 1.0 && $connections === null) {
                return array('bad', 'down', 'no statistics from the DB monitor account');
            }
            $max = overview_value($v, 'database_connections_max');
            $share = $max ? $connections / $max * 100 : 0;
            return array(overview_level($share, 60, 80), overview_number($connections, 0) . ' conn.',
                overview_number(overview_value($v, 'database_connections_running'), 0) . ' running · limit ' . overview_number($max, 0));
        }, function ($v) { return overview_value($v, 'database_connections'); }),
        'containers' => array('Containers', function ($v) {
            $running = dashboard_family($v, 'setup_container_running');
            if (!$running) {
                return overview_value($v, 'setup_metrics_group_up{group="containers"}') === 1.0 ? null
                    : array('warn', '–', 'no snapshot from the Docker collector');
            }
            $down = count(array_filter($running, function ($state) { return (int) $state !== 1; }));
            $unhealthy = count(array_filter(dashboard_family($v, 'setup_container_healthy'), function ($state) { return (int) $state !== 1; }));
            $problems = array_filter(array($down ? $down . ' not running' : '', $unhealthy ? $unhealthy . ' not healthy' : ''));
            return array($problems ? 'bad' : 'ok', (count($running) - $down) . ' / ' . count($running),
                $problems ? implode(' · ', $problems) : 'running, healthy');
        }, function ($v) {
            $running = dashboard_family($v, 'setup_container_running');
            return $running ? array_sum($running) : null;
        }),
        'cron' => array('Cron', function ($v) {
            $exits = dashboard_family($v, 'setup_cron_job_last_exit_code');
            if (!dashboard_family($v, 'setup_cron_job_info')) return null;
            $failing = count(array_filter($exits, function ($code) { return (int) $code !== 0; }));
            $late = 0;
            if (overview_value($v, 'setup_cron_schedule_enabled') === 1.0) {
                foreach (dashboard_family($v, 'setup_cron_job_expected_run_timestamp_seconds') as $expected) {
                    if ($expected < overview_value($v, 'uptime_current_time') - 60) $late++;
                }
            }
            $jobs = count(dashboard_family($v, 'setup_cron_job_info'));
            return array($failing ? 'bad' : ($late ? 'warn' : 'ok'), $failing ? $failing . ' failing' : $jobs . ' jobs',
                ($late ? $late . ' overdue · ' : '') . count($exits) . ' with a recorded run');
        }, function ($v) {
            $exits = dashboard_family($v, 'setup_cron_job_last_exit_code');
            return $exits ? count(array_filter($exits, function ($code) { return (int) $code !== 0; })) : null;
        }),
        'pages' => array('Page checks', function ($v) use ($slowest) {
            $statuses = dashboard_family($v, 'website_response_status');
            if (!$statuses) return null;
            $failed = count(array_filter($statuses, function ($status) { return (int) $status === 0 || $status >= 500; }));
            $odd = count(array_filter($statuses, function ($status) { return $status >= 400 && $status < 500; }));
            $slow = $slowest($v);
            $level = $failed ? 'bad' : ($odd || $slow > 3 ? 'warn' : 'ok');
            return array($level, (count($statuses) - $failed - $odd) . ' / ' . count($statuses) . ' OK',
                'slowest ' . overview_number($slow, 2) . ' s');
        }, $slowest),
    );
}

// The tab's body: the cards of the latest snapshot (a stale one too, with a warning) and their 24-hour lines.
function overview_body($now)
{
    $snapshot = metrics_read_json(metrics_snapshot_directory() . '/snapshot.json');
    if ($snapshot === null || !isset($snapshot['families'], $snapshot['taken'])) {
        return '<div class="empty">No snapshot yet: the sampler writes the first one within a minute of the service starting.</div>';
    }
    $values = dashboard_flat($snapshot['families']);
    $cards = overview_cards();
    $points = array_fill_keys(array_keys($cards), array());
    $from = $now - OVERVIEW_HOURS * 3600;
    dashboard_history($from, function ($time, $line) use ($cards, &$points) {
        foreach ($cards as $key => $card) {
            $points[$key][$time] = call_user_func($card[2], $line);
        }
    });
    $age = $now - (int) $snapshot['taken'];
    $body = '';
    if ($age > 3 * max(1, (int) $snapshot['interval'])) {
        $body .= '<div class="callout error">The latest snapshot is ' . h(round($age / 60)) . ' minutes old: the sampler has stopped '
            . '(see the metrics service\'s docker logs). These are its last figures; /metrics reports every group as down.</div>';
    }
    $body .= '<div class="cards">';
    foreach ($cards as $key => $card) {
        $state = call_user_func($card[1], $values);
        if ($state === null) continue;
        list($level, $text, $note) = $state;
        $line = dashboard_sparkline($points[$key], $from, $now);
        $body .= '<section class="card ' . h($level) . '" data-card="' . h($key) . '"><h2>' . h($card[0]) . '</h2>'
            . '<div class="value">' . h($text) . '</div><div class="note">' . h($note) . '</div>'
            . ($line !== '' ? $line : '<div class="nohistory">24 h line once the history has two points</div>') . '</section>';
    }
    $body .= '</div><p class="stamp">Snapshot ' . h(date('Y-m-d H:i:s', (int) $snapshot['taken'])) . ' · refreshes every minute · '
        . 'lines: the last ' . OVERVIEW_HOURS . ' hours, the highest value of each 10 minutes</p>';
    return $body;
}
