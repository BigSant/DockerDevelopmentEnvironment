<?php
// The Database, Containers and Cron tabs: the latest snapshot's families of one group as tables, the same values
// /metrics serves. A table's rows are the label sets (a query, a table, a container, a job), its columns families
// or the labels of an info family. A group without figures says why instead of showing empty tables.
require_once __DIR__ . '/dashboard.php';

function tabs_list()
{
    return array(
        '/database' => array('Database', 'database', 'tabs_database'),
        '/containers' => array('Containers', 'containers', 'tabs_containers'),
        '/cron' => array('Cron', 'cron', 'tabs_cron'),
    );
}

function tabs_number($value, $decimals = 3)
{
    $value = (float) $value;
    return floor($value) == $value ? number_format($value, 0, '.', ' ') : rtrim(rtrim(number_format($value, $decimals, '.', ' '), '0'), '.');
}

function tabs_time($value)
{
    return $value ? date('Y-m-d H:i:s', (int) $value) : '–';
}

// $columns: [[family or "family:label", title, format(value) or null], ...]; rows keyed by the $keys labels. A sample
// with only some of them (a cron job's last run has its id, its info also the file and line) fills every row that
// agrees on those it has.
function tabs_table($families, $title, $keys, $columns, $note = '')
{
    $rows = array();
    $partial = array();
    foreach ($columns as $index => $column) {
        list($name, $label) = array_pad(explode(':', $column[0], 2), 2, null);
        if (!isset($families[$name])) continue;
        foreach ($families[$name]['samples'] as $sample) {
            $value = $label === null ? $sample[1] : (isset($sample[0][$label]) ? $sample[0][$label] : '');
            if (count(array_intersect($keys, array_keys($sample[0]))) < count($keys)) {
                $partial[] = array($index, $sample[0], $value);
                continue;
            }
            $key = array();
            foreach ($keys as $wanted) $key[] = (string) $sample[0][$wanted];
            $id = implode("\0", $key);
            if (!isset($rows[$id])) $rows[$id] = array('key' => $key, 'cells' => array());
            $rows[$id]['cells'][$index] = $value;
        }
    }
    foreach ($partial as $entry) {
        list($index, $labels, $value) = $entry;
        $matched = false;
        foreach ($rows as &$row) {
            $agrees = true;
            foreach ($keys as $position => $wanted) {
                if (isset($labels[$wanted]) && (string) $labels[$wanted] !== $row['key'][$position]) $agrees = false;
            }
            if ($agrees) {
                $row['cells'][$index] = $value;
                $matched = true;
            }
        }
        unset($row);
        if (!$matched) {
            $key = array();
            foreach ($keys as $wanted) $key[] = isset($labels[$wanted]) ? (string) $labels[$wanted] : '';
            $rows[implode("\0", $key)] = array('key' => $key, 'cells' => array($index => $value));
        }
    }
    $body = '<section class="panel wide"><h2>' . h($title) . '</h2>';
    if (!$rows) return $body . '<p class="stamp">No data.</p></section>';
    $body .= ($note !== '' ? '<p class="stamp">' . h($note) . '</p>' : '') . '<table class="list"><thead><tr>';
    foreach ($keys as $key) $body .= '<th>' . h($key) . '</th>';
    foreach ($columns as $column) $body .= '<th class="num">' . h($column[1]) . '</th>';
    $body .= '</tr></thead><tbody>';
    foreach ($rows as $row) {
        $body .= '<tr>';
        foreach ($row['key'] as $value) $body .= '<td class="key">' . h($value) . '</td>';
        foreach ($columns as $index => $column) {
            $value = isset($row['cells'][$index]) ? $row['cells'][$index] : null;
            $shown = $value === null ? '–' : (isset($column[2]) ? call_user_func($column[2], $value) : (is_numeric($value) ? tabs_number($value) : $value));
            $body .= '<td class="num">' . h($shown) . '</td>';
        }
        $body .= '</tr>';
    }
    return $body . '</tbody></table></section>';
}

function tabs_database($families)
{
    $seconds = function ($value) { return tabs_number($value) . ' s'; };
    return tabs_table($families, 'Capacity and connections', array(), array(
            array('sql_buffer_pool_size', 'buffer pool MB'), array('sql_data_cached', 'cached MB'), array('sql_ram_free', 'free MB'),
            array('sql_buffer_used', 'used %'), array('sql_dirty_pages', 'dirty MB'), array('database_connections', 'connections'),
            array('database_connections_running', 'running'), array('database_connections_max', 'limit'),
            array('database_running_statements', 'statements now')))
        . tabs_table($families, 'Performance schema', array('state'), array(array('database_performance_schema', 'value')))
        . tabs_table($families, 'Slow statements (by average time)', array('query'), array(array('database_slow_query_total', 'runs'),
            array('database_slow_query_time_average', 'average', $seconds), array('database_slow_query_time_total', 'total', $seconds)))
        . tabs_table($families, 'Table-scan statements', array('query'), array(array('database_table_scan_query_total', 'runs'),
            array('database_table_scan_query_total_row_examined', 'rows examined'), array('database_table_scan_query_total_row_sent', 'rows sent')))
        . tabs_table($families, 'Statements by total time', array('query'), array(array('database_total_time_query_total', 'runs'),
            array('database_total_time_query_time_total', 'total', $seconds), array('database_total_time_query_time_average', 'average', $seconds)))
        . tabs_table($families, 'Slowest single execution', array('query'), array(array('database_slowest_query_time', 'time', $seconds)))
        . tabs_table($families, 'Running now', array('query'), array(array('database_running_statement_time', 'elapsed', $seconds)))
        . tabs_table($families, 'Table locks', array('schema', 'table'), array(array('database_table_lock_total', 'locks'),
            array('database_table_lock_time_total', 'time', $seconds)), 'Since the server started.')
        . tabs_table($families, 'Table I/O', array('schema', 'table'), array(array('database_table_io_read_total', 'reads'),
            array('database_table_io_write_total', 'writes'), array('database_table_io_read_time_total', 'read time', $seconds),
            array('database_table_io_write_time_total', 'write time', $seconds)), 'Since the server started.');
}

function tabs_containers($families)
{
    $yes = function ($value) { return (int) $value === 1 ? 'yes' : 'no'; };
    return tabs_table($families, 'Containers', array('service', 'container'), array(
            array('setup_container_info:state', 'state'), array('setup_container_info:health', 'health'),
            array('setup_container_running', 'running', $yes), array('setup_container_healthy', 'healthy', $yes),
            array('setup_container_restarts_total', 'restarts'), array('setup_container_oom_killed', 'OOM killed', $yes),
            array('setup_container_exit_code', 'last exit'), array('setup_container_started_timestamp_seconds', 'started', 'tabs_time'),
            array('setup_container_info:image', 'image')))
        . tabs_table($families, 'OOM kills per service', array('service'), array(array('setup_container_oom_kills_total', 'kills')),
            'Since the Docker collector first ran.')
        . tabs_table($families, 'Docker collector', array(), array(array('setup_containers_collected_timestamp_seconds', 'snapshot', 'tabs_time')));
}

function tabs_cron($families)
{
    return tabs_table($families, 'Schedule', array(), array(array('setup_cron_schedule_enabled', 'runs on schedule',
            function ($value) { return (int) $value === 1 ? 'yes' : 'no (CRON_SCHEDULE=off)'; })))
        . tabs_table($families, 'Jobs', array('job', 'source', 'line'), array(array('setup_cron_job_info:schedule', 'schedule'),
            array('setup_cron_job_last_run_timestamp_seconds', 'last run', 'tabs_time'), array('setup_cron_job_last_exit_code', 'exit'),
            array('setup_cron_job_last_duration_seconds', 'duration', function ($value) { return tabs_number($value) . ' s'; }),
            array('setup_cron_job_last_success_timestamp_seconds', 'last success', 'tabs_time'),
            array('setup_cron_job_expected_run_timestamp_seconds', 'next expected', 'tabs_time')),
            'Jobs by id as the cron UI shows them; the command is not shown (it can hold tokens).');
}

// Why a group has nothing to show.
function tabs_missing($group)
{
    if ($group === 'cron' && metrics_setting('METRICS_CRON', '0') !== '1') {
        return 'No data: the cron service is not enabled (SERVICES="cron metrics").';
    }
    if ($group === 'containers') return 'No data: no fresh snapshot from the Docker collector (service metrics-docker).';
    if ($group === 'database') return 'No data: the DB monitor account gives no statistics (make db-monitor-user).';
    return 'No data.';
}

function tabs_route($path)
{
    $tabs = tabs_list();
    list($title, $group, $render) = $tabs[$path];
    $snapshot = metrics_read_json(metrics_snapshot_directory() . '/snapshot.json');
    if ($snapshot === null || !isset($snapshot['families'])) {
        dashboard_page($path, $title, '<div class="empty">No snapshot yet: the sampler writes the first one within a minute.</div>');
    }
    $families = $snapshot['families'];
    $up = 0;
    foreach (isset($families['setup_metrics_group_up']) ? $families['setup_metrics_group_up']['samples'] : array() as $sample) {
        if (isset($sample[0]['group']) && $sample[0]['group'] === $group) $up = (int) $sample[1];
    }
    $body = call_user_func($render, $families);
    $empty = strpos($body, '<table') === false;
    if ($empty) {
        $body = '<div class="empty">' . h(tabs_missing($group)) . '</div>';
    } elseif (!$up) {
        $body = '<div class="callout wait">Part of the ' . h($group) . ' group was not collected (see the metrics service\'s docker logs).</div>' . $body;
    }
    dashboard_page($path, $title, $body . '<p class="stamp">Snapshot ' . h(date('Y-m-d H:i:s', (int) $snapshot['taken'])) . '</p>');
}
