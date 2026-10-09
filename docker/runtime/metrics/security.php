<?php
// Security group: the old script's DDoS check (netstat -an | grep :80 … | head -5), the five client addresses
// holding the most TCP connections to the web ports 80 and 443, counted where clients connect:
// - the host's network namespace (/proc/1/net), where a front web server such as DirectAdmin accepts them;
// - nginx-proxy's, from its snapshot in the HTTP log volume (published ports, e.g. a VPN target).
// Listening sockets, loopback peers and the proxy's gateway (the host relaying its own requests) are not clients.
require_once __DIR__ . '/http.php';

// nginx-proxy refreshes its snapshot every 10 s; an older one means the proxy or its helper stopped.
const METRICS_SECURITY_SNAPSHOT_AGE = 120;

function metrics_security_parts()
{
    return array('metrics_security_connections');
}

// One /proc/net/tcp(6) address ("0100007F:0050"): the IP (IPv4-mapped IPv6 as IPv4) and the port.
function metrics_security_address($hex)
{
    list($address, $port) = explode(':', $hex);
    $bytes = '';
    foreach (str_split($address, 8) as $word) {
        $bytes .= strrev((string) hex2bin($word));
    }
    if (strlen($bytes) === 16 && substr($bytes, 0, 12) === str_repeat("\0", 10) . "\xff\xff") $bytes = substr($bytes, 12);
    return array((string) inet_ntop($bytes), hexdec($port));
}

function metrics_security_client($ip, $excluded)
{
    if ($ip === '' || $ip === '::' || $ip === '0.0.0.0' || $ip === '::1' || strpos($ip, '127.') === 0) return false;
    return !in_array($ip, $excluded, true);
}

function metrics_security_count(&$counts, $table, $excluded)
{
    foreach (array_slice(explode("\n", $table), 1) as $line) {
        $fields = preg_split('/\s+/', trim($line));
        // sl, local address, remote address, state (0A: listening).
        if (count($fields) < 4 || $fields[3] === '0A') continue;
        list(, $port) = metrics_security_address($fields[1]);
        list($ip) = metrics_security_address($fields[2]);
        if (($port === 80 || $port === 443) && metrics_security_client($ip, $excluded)) {
            $counts[$ip] = (isset($counts[$ip]) ? $counts[$ip] : 0) + 1;
        }
    }
}

// The default route's gateway of a /proc/net/route table.
function metrics_security_gateway($routes)
{
    foreach (array_slice(explode("\n", $routes), 1) as $line) {
        $fields = preg_split('/\s+/', trim($line));
        if (count($fields) >= 3 && $fields[1] === '00000000' && $fields[2] !== '00000000') {
            return metrics_security_address($fields[2] . ':0')[0];
        }
    }
    return null;
}

function metrics_security_connections(Metrics $metrics)
{
    $counts = array();
    metrics_security_count($counts, metrics_proc('1/net/tcp'), array());
    try {
        metrics_security_count($counts, metrics_proc('1/net/tcp6'), array());
    } catch (RuntimeException $error) {
        // No IPv6 on this host.
    }
    $failure = null;
    $snapshot = metrics_http_directory() . '/proxy-net';
    $modified = @filemtime($snapshot . '/tcp');
    if ($modified === false) {
        $failure = new RuntimeException('no nginx-proxy connection snapshot in ' . $snapshot);
    } elseif (time() - $modified > METRICS_SECURITY_SNAPSHOT_AGE) {
        $failure = new RuntimeException('the nginx-proxy connection snapshot is ' . (time() - $modified) . ' s old');
    } else {
        $gateway = metrics_security_gateway((string) @file_get_contents($snapshot . '/route'));
        foreach (array('tcp', 'tcp6') as $table) {
            $text = @file_get_contents($snapshot . '/' . $table);
            if ($text !== false) metrics_security_count($counts, $text, $gateway === null ? array() : array($gateway));
        }
    }
    foreach (metrics_http_top($counts, 5) as $ip => $count) {
        $metrics->add('ddos_most_connections_ip_count', 'Number of connections per IP on port 80', $count, array('ip' => (string) $ip));
    }
    if ($failure !== null) throw $failure;
}
