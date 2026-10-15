<?php
// /feed: the latest snapshot and the last 24 hours' traffic analysis as text for people and LLMs, in the sections
// of the old monitoring script's results.txt ("Header:", the body, a line of dashes) under its own headers, then
// the traffic sections. The figures are the snapshot's, not the old commands' output; server_stats_viewer does not
// parse this text. Guarded like /metrics: the allowlist in nginx-proxy and the optional bearer token here.
require_once __DIR__ . '/dashboard.php';

const FEED_RULE = '---------------------------------';

// The script's headers in its order, each with the snapshot families shown under it (a name ending in "_" is a
// prefix); null for what setup does not collect.
function feed_sections()
{
    return array(
        'Uptime' => array('uptime', 'uptime_current_time', 'uptime_average_load_1min', 'uptime_average_load_10min', 'uptime_average_load_15min'),
        'Disk Space Usage' => array('disk_size', 'disk_used', 'disk_available', 'disk_available_percentage'),
        'Inode Usage' => array('disk_inodes_total', 'disk_inodes_used', 'disk_inodes_available', 'disk_inodes_available_percentage'),
        'RAM Usage' => array('ram_total', 'ram_used', 'ram_free', 'ram_shared', 'ram_buff_cache', 'ram_available'),
        'I/O Statistics' => array('iousage_usr', 'ip_usage_nice', 'io_usage_sys', 'io_usage_iowait', 'io_usage_steal', 'io_usage_idle',
            'setup_cpu_iowait_seconds_total', 'setup_processes_blocked', 'setup_disk_io_time_seconds_total'),
        'CPU Usage' => array('cpu_usage_'),
        'Network Usage' => array('network_rxpck', 'network_txpck', 'network_rxkb', 'network_txkb'),
        'Website Response Time' => 'feed_website',
        'SSL Expiry Date' => array('website_ssl_expiry_timestamp_seconds'),
        'Active Connections' => array('active_connections'),
        'HTTP response codes' => array('http_response_codes'),
        'HTTP requests per minute' => array('http_requests_per_minute', 'http_requests_per_minute_max'),
        'HTTP bytes transferred' => array('http_bytes_transferred_total'),
        'HTTP top IP addresses' => array('http_request_ips'),
        'HTTP top User agents' => array('http_request_user_agents'),
        'HTTP error type counts' => array('http_error_type'),
        'SQL capacity monitoring' => array('sql_', 'database_connections', 'database_connections_running', 'database_connections_max',
            'database_performance_schema'),
        'Top Slow SQL Statements (by Average Execution Time)' => array('database_slow_query_', 'database_slowest_query_time'),
        'Top Table scan Statements' => array('database_table_scan_query_'),
        'Queries by Total Time Consumption' => array('database_total_time_query_'),
        'Most Accessed Tables (I/O)' => array('database_table_io_'),
        'Table Lock Contention' => array('database_table_lock_'),
        'File I/O Hotspots' => null,
        'Current Running Statements' => array('database_running_statements', 'database_running_statement_time'),
        'CPU-Heavy Users' => null,
        'Top IPs with Most Connections (DDoS Check)' => array('ddos_most_connections_ip_count'),
        'Disk Write Speed' => array('disk_write_speed'),
        'Network Latency (Ping to 8.8.8.8)' => null,
        'Top CPU-consuming Processes' => array('processes_consuming_cpu', 'processes_consuming_cpu_total'),
        'Top RAM-consuming Processes' => array('processes_consuming_memory', 'processes_consuming_memory_total'),
    );
}

function feed_section($header, $body)
{
    return $header . ":\n" . rtrim($body, "\n") . "\n" . FEED_RULE . "\n";
}

// "name value" for a family without labels, "name{labels} value" for each labelled sample.
function feed_families($families, $names)
{
    $lines = array();
    foreach ($families as $name => $family) {
        foreach ($names as $wanted) {
            $prefix = substr($wanted, -1) === '_';
            if ($prefix ? strpos($name, $wanted) !== 0 : $name !== $wanted) continue;
            foreach ($family['samples'] as $sample) {
                $lines[] = $name . metrics_labels($sample[0]) . ' ' . metrics_number($sample[1]);
            }
            break;
        }
    }
    return $lines;
}

// One section per page as the script wrote it: Url, Response Time, Status Code.
function feed_website($families)
{
    $text = '';
    if (!isset($families['website_response_time'])) return feed_section('Website Response Time', 'no pages to check (METRICS_URLS)');
    $statuses = array();
    foreach (isset($families['website_response_status']) ? $families['website_response_status']['samples'] : array() as $sample) {
        $statuses[$sample[0]['url']] = $sample[1];
    }
    foreach ($families['website_response_time']['samples'] as $sample) {
        $url = $sample[0]['url'];
        $text .= feed_section('Website Response Time', 'Url: ' . $url . "\nResponse Time: " . metrics_number($sample[1]) . "s\nStatus Code: "
            . (isset($statuses[$url]) ? metrics_number($statuses[$url]) : '0'));
    }
    return $text;
}

// The traffic analysis's sections ("Traffic …"): its window first, then classes, bots, attacks and the lists.
function feed_traffic($report, $start, $end)
{
    $list = function ($rows, $format = null) {
        if (!$rows) return 'none';
        $lines = array();
        foreach ($rows as $key => $value) $lines[] = sprintf('%7s %s', $format ? $format($value) : $value, $key);
        return implode("\n", $lines);
    };
    $attacks = '';
    foreach ($report['attacks'] as $type => $count) {
        $attacks .= $type . ' ' . $count . "\n" . implode("\n", $report['attack_lines'][$type]) . "\n";
    }
    $slowest = function ($value) { return number_format($value['average'], 3, '.', '') . 's avg ' . $value['requests'] . ' req'; };
    return feed_section('Traffic window', date('Y-m-d H:i', $start) . ' - ' . date('Y-m-d H:i', $end) . ', ' . $report['requests'] . ' requests')
        . feed_section('Traffic classes', $list($report['classes']) . "\np50 " . ($report['p50'] === null ? '-' : $report['p50'] . 's')
            . "\np95 " . ($report['p95'] === null ? '-' : $report['p95'] . 's'))
        . feed_section('Traffic bot requests', $list($report['bots']))
        . feed_section('Traffic attacks', $attacks === '' ? 'none' : $attacks)
        . feed_section('Traffic suspicious IPs', $list($report['suspicious_ips']))
        . feed_section('Traffic regular IPs', $list($report['regular_ips']))
        . feed_section('Traffic top IP addresses', $list($report['ips']))
        . feed_section('Traffic top User agents', $list($report['agents']))
        . feed_section('Traffic top URLs', $list($report['urls']))
        . feed_section('Traffic 4xx by URL', $list($report['errors4']))
        . feed_section('Traffic 5xx by URL', $list($report['errors5']))
        . feed_section('Traffic slowest URLs', $list($report['slowest'], $slowest));
}

function feed_text($now)
{
    $snapshot = metrics_read_json(metrics_snapshot_directory() . '/snapshot.json');
    if ($snapshot === null || !isset($snapshot['families'], $snapshot['taken'])) {
        return feed_section('Snapshot', 'none yet: the sampler writes the first one within a minute of the service starting');
    }
    $families = $snapshot['families'];
    $text = feed_section('Snapshot', date('Y-m-d H:i:s', (int) $snapshot['taken'])
        . ($now - (int) $snapshot['taken'] > 3 * max(1, (int) $snapshot['interval']) ? ' (stale: the sampler has stopped)' : ''));
    foreach (feed_sections() as $header => $names) {
        if ($names === null) {
            $text .= feed_section($header, 'not collected by setup');
        } elseif (is_string($names)) {
            $text .= call_user_func($names, $families);
        } else {
            $lines = feed_families($families, $names);
            $text .= feed_section($header, $lines ? implode("\n", $lines) : 'no data');
        }
    }
    if (is_dir(metrics_logs_directory())) {
        require_once __DIR__ . '/traffic.php';
        // The last 24 hours from the whole hour before: closed hours come from the sampler's cache.
        $start = $now - 86400;
        $start -= $start % 3600;
        $text .= feed_traffic(traffic_report(traffic_window($start, $now, $now)), $start, $now);
    }
    return $text;
}
