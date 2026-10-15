<?php
// Run by php-fpm itself (the metrics service asks for this file over FastCGI): OPcache's shared memory belongs to
// the php-fpm master, so only a request inside it sees the statistics. Figures only, no script list.
header('Content-Type: application/json');
header('Cache-Control: no-store');
$status = function_exists('opcache_get_status') ? @opcache_get_status(false) : false;
$configuration = function_exists('opcache_get_configuration') ? @opcache_get_configuration() : false;
echo json_encode(array(
    'loaded' => function_exists('opcache_get_status'),
    'status' => is_array($status) ? $status : null,
    'max_accelerated_files' => is_array($configuration) && isset($configuration['directives']['opcache.max_accelerated_files'])
        ? (int) $configuration['directives']['opcache.max_accelerated_files'] : null,
));
