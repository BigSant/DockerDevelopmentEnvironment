<?php
// Database group: the old monitoring script's "Database Monitoring" numbers under the names its viewer emitted
// (sql_*, database_slow_query_*, database_table_scan_query_*, database_total_time_query_*), plus table locks,
// table I/O, running statements, connections and the slowest query. Read with the DB monitor account only
// (PROCESS, performance_schema, sys): counters and normalised statement shapes (literals replaced by ?), never rows.

function metrics_database_parts()
{
    return array('metrics_database_server', 'metrics_database_capacity', 'metrics_database_statements',
        'metrics_database_tables', 'metrics_database_running');
}

// Rows of one named statistics query. METRICS_DATABASE_FIXTURE (tests) answers them from a JSON file instead.
function metrics_database_rows($name)
{
    $queries = array(
        'server' => 'SELECT @@performance_schema AS performance_schema, @@max_connections AS max_connections,'
            . ' @@innodb_page_size AS page_size',
        'status' => "SHOW GLOBAL STATUS WHERE Variable_name IN ('Threads_connected', 'Threads_running',"
            . " 'Innodb_buffer_pool_pages_dirty')",
        'buffer_pool' => 'SELECT SUM(POOL_SIZE) AS pool_size, SUM(DATABASE_PAGES) AS database_pages,'
            . ' SUM(FREE_BUFFERS) AS free_buffers FROM information_schema.INNODB_BUFFER_POOL_STATS',
        // Every statement shape: the window is computed here, the server keeps its totals since start.
        'digests' => 'SELECT SCHEMA_NAME AS schema_name, DIGEST AS digest, DIGEST_TEXT AS query, COUNT_STAR AS executions,'
            . ' SUM_TIMER_WAIT AS wait, MAX_TIMER_WAIT AS max_wait, SUM_ROWS_EXAMINED AS rows_examined,'
            . ' SUM_ROWS_SENT AS rows_sent FROM performance_schema.events_statements_summary_by_digest',
        'table_locks' => 'SELECT OBJECT_SCHEMA AS schema_name, OBJECT_NAME AS table_name, COUNT_STAR AS executions,'
            . ' SUM_TIMER_WAIT AS wait FROM performance_schema.table_lock_waits_summary_by_table WHERE COUNT_STAR > 0'
            . ' ORDER BY SUM_TIMER_WAIT DESC, COUNT_STAR DESC LIMIT 10',
        'table_io' => 'SELECT OBJECT_SCHEMA AS schema_name, OBJECT_NAME AS table_name, COUNT_READ AS read_count,'
            . ' COUNT_WRITE AS write_count, SUM_TIMER_READ AS read_wait, SUM_TIMER_WRITE AS write_wait'
            . ' FROM performance_schema.table_io_waits_summary_by_table WHERE COUNT_STAR > 0'
            . ' ORDER BY COUNT_READ + COUNT_WRITE DESC LIMIT 10',
        // Statements executing now, except this scrape's own; the digest, never SQL_TEXT (it carries the values).
        'running' => 'SELECT DIGEST_TEXT AS query, TIMER_WAIT AS wait FROM performance_schema.events_statements_current'
            . ' WHERE END_EVENT_ID IS NULL AND THREAD_ID NOT IN'
            . ' (SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID = CONNECTION_ID())',
    );
    $fixture = metrics_setting('METRICS_DATABASE_FIXTURE', '');
    if ($fixture !== '') {
        $data = json_decode((string) @file_get_contents($fixture), true);
        if (!is_array($data) || !array_key_exists($name, $data)) throw new RuntimeException('no ' . $name . ' in ' . $fixture);
        if (isset($data[$name]['error'])) throw new RuntimeException($data[$name]['error']);
        return $data[$name];
    }
    return metrics_database_connection()->query($queries[$name])->fetchAll(PDO::FETCH_ASSOC);
}

// One connection per scrape as DATABASE_MONITOR_USER; a failure is remembered so every part reports it at once.
function metrics_database_connection()
{
    static $connection = null, $failure = null;
    if ($failure !== null) throw $failure;
    if ($connection !== null) return $connection;
    try {
        $password = metrics_setting('DATABASE_MONITOR_PASSWORD', '');
        if ($password === '') throw new RuntimeException('DATABASE_MONITOR_PASSWORD is not set (see make db-monitor-user)');
        if (!class_exists('PDO') || !in_array('mysql', PDO::getAvailableDrivers(), true)) {
            throw new RuntimeException('the PHP image has no pdo_mysql');
        }
        $connection = new PDO('mysql:host=' . metrics_setting('DATABASE_MONITOR_HOST', 'database') . ';port=3306',
            metrics_setting('DATABASE_MONITOR_USER', ''), $password,
            array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 3));
        try {
            $connection->exec('SET SESSION MAX_EXECUTION_TIME = 10000');
        } catch (PDOException $error) {
            // MariaDB has no MAX_EXECUTION_TIME; the scrape timeout still bounds the request.
        }
    } catch (Exception $error) {
        $connection = null;
        $failure = new RuntimeException('database: ' . $error->getMessage());
        throw $failure;
    }
    return $connection;
}

function metrics_database_server_row()
{
    static $server = null;
    if ($server === null) {
        $rows = metrics_database_rows('server');
        if (!$rows) throw new RuntimeException('no server variables');
        $server = $rows[0];
    }
    return $server;
}

function metrics_database_status()
{
    static $status = null;
    if ($status === null) {
        $status = array();
        foreach (metrics_database_rows('status') as $row) {
            $row = array_values($row);
            $status[$row[0]] = (float) $row[1];
        }
    }
    return $status;
}

function metrics_database_performance_schema()
{
    $server = metrics_database_server_row();
    return (int) $server['performance_schema'] === 1;
}

function metrics_database_seconds($picoseconds)
{
    return (float) $picoseconds / 1e12;
}

// performance_schema on or off (a state, not an error) and the connections.
function metrics_database_server(Metrics $metrics)
{
    $server = metrics_database_server_row();
    $metrics->add('database_performance_schema',
        'Whether performance_schema collects statement and table statistics (DATABASE_PERFORMANCE_SCHEMA); 0 = those metrics are absent',
        metrics_database_performance_schema() ? 1 : 0);
    $status = metrics_database_status();
    $get = function ($key) use ($status) { return isset($status[$key]) ? (int) $status[$key] : 0; };
    $metrics->add('database_connections', 'Open client connections (Threads_connected)', $get('Threads_connected'));
    $metrics->add('database_connections_running', 'Connections executing a statement now (Threads_running)', $get('Threads_running'));
    $metrics->add('database_connections_max', 'Connection limit (max_connections)', (int) $server['max_connections']);
}

// The script's "SQL capacity monitoring" (INNODB_BUFFER_POOL_STATS): MB, the used share in %.
function metrics_database_capacity(Metrics $metrics)
{
    $server = metrics_database_server_row();
    $rows = metrics_database_rows('buffer_pool');
    $pool = $rows ? $rows[0] : array();
    if (empty($pool['pool_size'])) throw new RuntimeException('no InnoDB buffer pool statistics');
    $megabytes = function ($pages) use ($server) { return round((float) $pages * (float) $server['page_size'] / 1048576, 2); };
    $status = metrics_database_status();
    $metrics->add('sql_buffer_pool_size', 'Total InnoDB buffer pool size (in MB)', $megabytes($pool['pool_size']));
    $metrics->add('sql_data_cached', 'Actual table + index pages in RAM', $megabytes($pool['database_pages']));
    $metrics->add('sql_ram_free', 'Still empty RAM inside buffer pool', $megabytes($pool['free_buffers']));
    $metrics->add('sql_buffer_used', 'How full the buffer pool is (percentage)',
        round((float) $pool['database_pages'] * 100 / (float) $pool['pool_size'], 2));
    $metrics->add('sql_dirty_pages', 'Pages modified but not yet flushed to disk',
        $megabytes(isset($status['Innodb_buffer_pool_pages_dirty']) ? $status['Innodb_buffer_pool_pages_dirty'] : 0));
}

// The script truncated the digest table after every run, so its numbers covered the time since the previous
// run (5 minutes). The monitor account cannot truncate: the window is the difference to the previous scrape's
// snapshot in METRICS_STATE_DIR; the first scrape after the service starts covers the time since the database
// started. Returns statement text => array(executions, wait ps, rows examined, rows sent) for shapes run in it.
function metrics_database_window($rows)
{
    $state = metrics_setting('METRICS_STATE_DIR', sys_get_temp_dir() . '/setup-metrics');
    if (!is_dir($state) && !@mkdir($state, 0700, true)) throw new RuntimeException('cannot create ' . $state);
    $handle = @fopen($state . '/database-digests.json', 'c+');
    if (!$handle || !flock($handle, LOCK_EX)) throw new RuntimeException('cannot lock the digest snapshot in ' . $state);
    $previous = json_decode((string) stream_get_contents($handle), true);
    if (!is_array($previous)) $previous = array();
    $current = array();
    $window = array();
    foreach ($rows as $row) {
        $key = $row['schema_name'] . "\t" . $row['digest'];
        $now = array((float) $row['executions'], (float) $row['wait'], (float) $row['rows_examined'], (float) $row['rows_sent']);
        $current[$key] = $now;
        // Fewer executions than before: the statistics were reset, everything counted is new.
        $before = isset($previous[$key]) && $previous[$key][0] <= $now[0] ? $previous[$key] : array(0, 0, 0, 0);
        if ($now[0] <= $before[0]) continue;
        // One series per statement text: the same shape in several schemas is added up.
        $text = $row['query'] === null ? 'NULL' : (string) $row['query'];
        if (!isset($window[$text])) $window[$text] = array(0, 0, 0, 0);
        foreach ($now as $index => $value) $window[$text][$index] += max(0, $value - $before[$index]);
    }
    ftruncate($handle, 0);
    rewind($handle);
    fwrite($handle, json_encode($current));
    fflush($handle);
    flock($handle, LOCK_UN);
    fclose($handle);
    return $window;
}

// The first ten statement texts by $score (descending), ties by text.
function metrics_database_top($window, $score)
{
    $ranked = array();
    foreach ($window as $text => $values) {
        $value = call_user_func($score, $values);
        if ($value !== null) $ranked[] = array($value, $text, $values);
    }
    usort($ranked, function ($a, $b) { return $a[0] == $b[0] ? strcmp($a[1], $b[1]) : ($a[0] < $b[0] ? 1 : -1); });
    return array_slice($ranked, 0, 10);
}

// Slow statements (by average), table scans (by rows examined) and total time, each the script's top 10 of
// the window; the slowest single execution since the server's statistics began.
function metrics_database_statements(Metrics $metrics)
{
    if (!metrics_database_performance_schema()) return;
    $rows = metrics_database_rows('digests');
    $window = metrics_database_window($rows);

    $average = function ($v) { return $v[1] / $v[0]; };
    foreach (metrics_database_top($window, $average) as $entry) {
        list(, $text, $v) = $entry;
        $labels = array('query' => $text);
        $metrics->add('database_slow_query_total', 'Slow query total count', (int) $v[0], $labels);
        $metrics->add('database_slow_query_time_total', 'Slow query total time', metrics_database_seconds($v[1]), $labels);
        $metrics->add('database_slow_query_time_average', 'Slow query average time', metrics_database_seconds($v[1] / $v[0]), $labels);
    }
    $examined = function ($v) { return $v[2] > 0 ? $v[2] : null; };
    foreach (metrics_database_top($window, $examined) as $entry) {
        list(, $text, $v) = $entry;
        $labels = array('query' => $text);
        $metrics->add('database_table_scan_query_total', 'Table scan query total count', (int) $v[0], $labels);
        $metrics->add('database_table_scan_query_total_row_examined', 'Table scan query row examined', (int) $v[2], $labels);
        $metrics->add('database_table_scan_query_total_row_sent', 'Table scan query row sent', (int) $v[3], $labels);
    }
    $total = function ($v) { return $v[1]; };
    foreach (metrics_database_top($window, $total) as $entry) {
        list(, $text, $v) = $entry;
        $labels = array('query' => $text);
        $metrics->add('database_total_time_query_total', 'Total consumed query total count', (int) $v[0], $labels);
        $metrics->add('database_total_time_query_time_total', 'Total consumed query total time', metrics_database_seconds($v[1]), $labels);
        $metrics->add('database_total_time_query_time_average', 'Total consumed query average time',
            metrics_database_seconds($v[1] / $v[0]), $labels);
    }

    $slowest = null;
    foreach ($rows as $row) {
        if ((float) $row['executions'] > 0 && ($slowest === null || (float) $row['max_wait'] > (float) $slowest['max_wait'])) {
            $slowest = $row;
        }
    }
    if ($slowest !== null) {
        $metrics->add('database_slowest_query_time', 'Longest single execution of any statement since the server statistics began (seconds)',
            metrics_database_seconds($slowest['max_wait']), array('query' => $slowest['query'] === null ? 'NULL' : $slowest['query']));
    }
}

// Table lock waits and table I/O, the script's top 10, cumulative since the server started (seconds for times).
function metrics_database_tables(Metrics $metrics)
{
    if (!metrics_database_performance_schema()) return;
    foreach (metrics_database_rows('table_locks') as $row) {
        $labels = array('schema' => (string) $row['schema_name'], 'table' => (string) $row['table_name']);
        $metrics->add('database_table_lock_total', 'Table lock acquisitions since the server started', (int) $row['executions'],
            $labels, 'counter');
        $metrics->add('database_table_lock_time_total', 'Time spent on table locks since the server started (seconds)',
            metrics_database_seconds($row['wait']), $labels, 'counter');
    }
    foreach (metrics_database_rows('table_io') as $row) {
        $labels = array('schema' => (string) $row['schema_name'], 'table' => (string) $row['table_name']);
        $metrics->add('database_table_io_read_total', 'Table row reads since the server started', (int) $row['read_count'], $labels, 'counter');
        $metrics->add('database_table_io_write_total', 'Table row writes since the server started', (int) $row['write_count'], $labels, 'counter');
        $metrics->add('database_table_io_read_time_total', 'Time spent reading table rows since the server started (seconds)',
            metrics_database_seconds($row['read_wait']), $labels, 'counter');
        $metrics->add('database_table_io_write_time_total', 'Time spent writing table rows since the server started (seconds)',
            metrics_database_seconds($row['write_wait']), $labels, 'counter');
    }
}

// Statements executing now: their number and, per statement text, the longest elapsed time (top 10).
function metrics_database_running(Metrics $metrics)
{
    if (!metrics_database_performance_schema()) return;
    $rows = metrics_database_rows('running');
    $longest = array();
    foreach ($rows as $row) {
        $text = $row['query'] === null ? 'NULL' : (string) $row['query'];
        $longest[$text] = max(isset($longest[$text]) ? $longest[$text] : 0, (float) $row['wait']);
    }
    $metrics->add('database_running_statements', 'Statements executing now (excluding this scrape)', count($rows));
    $ranked = metrics_database_top(array_map(function ($wait) { return array($wait); }, $longest),
        function ($v) { return $v[0]; });
    foreach ($ranked as $entry) {
        $metrics->add('database_running_statement_time', 'Elapsed time of the longest running execution of the statement (seconds)',
            metrics_database_seconds($entry[0]), array('query' => $entry[1]));
    }
}
