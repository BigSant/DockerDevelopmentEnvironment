<?php
// Registry group: tables of the project database that the table registry does not classify, as the last
// backup found them (make db-backup and the pre-deploy backup write data/<env>/table-registry/status.json).
// Table names only; no backup yet means no figures.

function metrics_registry_parts()
{
    return array('metrics_registry_status');
}

function metrics_registry_status(Metrics $metrics)
{
    if (metrics_setting('METRICS_REGISTRY', '0') !== '1') return;
    $file = metrics_setting('METRICS_REGISTRY_STATUS', '/host/table-registry/status.json');
    if (!is_file($file)) return;
    $status = json_decode((string) file_get_contents($file), true);
    if (!is_array($status) || !isset($status['checked'], $status['unregistered']) || !is_array($status['unregistered'])) {
        throw new RuntimeException('unreadable ' . $file);
    }
    $metrics->add('setup_database_registry_check_timestamp_seconds', 'When the last backup checked the table registry',
        (int) $status['checked']);
    $metrics->add('setup_database_unregistered_tables', 'Tables of the project database without a table registry line',
        count($status['unregistered']));
    // The names, bounded: an alert says which tables to classify.
    foreach (array_slice($status['unregistered'], 0, 100) as $table) {
        $metrics->add('setup_database_unregistered_table_info', 'A table without a table registry line', 1, array('table' => (string) $table));
    }
}
