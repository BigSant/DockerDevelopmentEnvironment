<?php
// DB monitor endpoint: one JSON document of database performance data for an external system.
// The response format is a DRAFT until the consumer's format is supplied ("format" says so).
header('Content-Type: application/json');
header('Cache-Control: no-store');

function respond($status, $body)
{
    http_response_code($status);
    echo json_encode($body, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), "\n";
    exit;
}

$token = (string) getenv('DBMONITOR_TOKEN');
if (strlen($token) < 24) respond(503, array('error' => 'DBMONITOR_TOKEN (24+ characters) is not configured'));
$authorization = isset($_SERVER['HTTP_AUTHORIZATION']) ? (string) $_SERVER['HTTP_AUTHORIZATION'] : '';
if (!hash_equals('Bearer ' . $token, $authorization)) {
    header('WWW-Authenticate: Bearer');
    respond(401, array('error' => 'unauthorized'));
}
if (parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH) !== '/') respond(404, array('error' => 'not found'));

try {
    $db = new PDO('mysql:host=' . (getenv('DATABASE_MONITOR_HOST') ?: 'database') . ';port=3306',
        getenv('DATABASE_MONITOR_USER'), getenv('DATABASE_MONITOR_PASSWORD'),
        array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_TIMEOUT => 3));
} catch (PDOException $error) {
    respond(503, array('error' => 'database unavailable for the monitor account'));
}

$status = array();
foreach ($db->query("SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime', 'Threads_connected', 'Threads_running',
    'Questions', 'Slow_queries', 'Aborted_connects', 'Innodb_buffer_pool_reads', 'Innodb_buffer_pool_read_requests',
    'Innodb_row_lock_waits', 'Created_tmp_disk_tables')") as $row) {
    $status[$row[0]] = is_numeric($row[1]) ? $row[1] + 0 : $row[1];
}
$performanceSchema = (int) $db->query('SELECT @@performance_schema')->fetchColumn() === 1;
$statements = array();
if ($performanceSchema) {
    // Heaviest statement shapes by total time, literals already replaced by the server.
    $query = $db->query('SELECT SCHEMA_NAME, DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT / 1000000000 AS total_ms,
        AVG_TIMER_WAIT / 1000000000 AS avg_ms, SUM_ROWS_EXAMINED, SUM_NO_INDEX_USED
        FROM performance_schema.events_statements_summary_by_digest
        WHERE SCHEMA_NAME IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 20');
    foreach ($query->fetchAll(PDO::FETCH_ASSOC) as $row) {
        $statements[] = array('schema' => $row['SCHEMA_NAME'], 'digest' => $row['DIGEST_TEXT'], 'count' => (int) $row['COUNT_STAR'],
            'total_ms' => round((float) $row['total_ms'], 3), 'avg_ms' => round((float) $row['avg_ms'], 3),
            'rows_examined' => (int) $row['SUM_ROWS_EXAMINED'], 'no_index_used' => (int) $row['SUM_NO_INDEX_USED']);
    }
}
respond(200, array(
    'format' => 'setup-dbmonitor/draft',
    'generated_at' => gmdate('c'),
    'server' => $db->query('SELECT VERSION()')->fetchColumn(),
    'performance_schema' => $performanceSchema,
    'status' => $status,
    'statements' => $statements,
));
