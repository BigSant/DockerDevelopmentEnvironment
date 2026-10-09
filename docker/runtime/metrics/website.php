<?php
// Website group: response time and status per METRICS_URLS (the script's curl -L), the certificate
// expiry per https host, and the project's support level that the alerts route by.

function metrics_website_parts()
{
    return array('metrics_website_support', 'metrics_website_responses', 'metrics_website_certificates');
}

function metrics_website_urls()
{
    return preg_split('/\s+/', metrics_setting('METRICS_URLS', ''), -1, PREG_SPLIT_NO_EMPTY);
}

// METRICS_PROBE_ADDRESS: connect there instead of resolving the host (curl --connect-to), e.g. the
// server's public or VPN address; the URL's host still goes into Host and SNI.
function metrics_website_address($host)
{
    $address = metrics_setting('METRICS_PROBE_ADDRESS', '');
    if ($address === '') return $host;
    return strpos($address, ':') !== false ? '[' . $address . ']' : $address;
}

function metrics_website_support(Metrics $metrics)
{
    $metrics->add('support_level', 'Identify how important is to manage notifications',
        (int) metrics_setting('METRICS_SUPPORT_LEVEL', '0'));
}

// The script's own probe: curl -L, total time in seconds and the final status (0 when nothing answered).
function metrics_website_responses(Metrics $metrics)
{
    foreach (metrics_website_urls() as $url) {
        $command = 'curl -sS -L --max-redirs 10 --connect-timeout 10 --max-time 20 --proto =http,https --proto-redir =http,https'
            . ' -A setup-metrics -o /dev/null -w "%{time_total} %{http_code}"';
        if (metrics_setting('METRICS_PROBE_ADDRESS', '') !== '') {
            $command .= ' --connect-to ' . escapeshellarg('::' . metrics_website_address('') . ':');
        }
        $output = array();
        exec($command . ' ' . escapeshellarg($url) . ' 2>/dev/null', $output);
        $result = $output ? explode(' ', trim(end($output))) : array();
        if (count($result) !== 2 || !is_numeric($result[0])) throw new RuntimeException('curl is not available');
        $labels = array('url' => $url);
        $metrics->add('website_response_time', 'Website request response time', (float) $result[0], $labels);
        $metrics->add('website_response_status', 'Website request status', (int) $result[1], $labels);
    }
}

// The served certificate's notAfter, read without verification so an expired one is still reported.
function metrics_website_certificates(Metrics $metrics)
{
    $hosts = array();
    foreach (metrics_website_urls() as $url) {
        $parts = parse_url($url);
        if (strtolower($parts['scheme']) !== 'https' || empty($parts['host'])) continue;
        $port = isset($parts['port']) ? (int) $parts['port'] : 443;
        $hosts[$parts['host'] . ($port === 443 ? '' : ':' . $port)] = array(trim($parts['host'], '[]'), $port);
    }
    $failed = array();
    foreach ($hosts as $domain => $target) {
        list($host, $port) = $target;
        $context = stream_context_create(array('ssl' => array('peer_name' => $host, 'SNI_enabled' => true,
            'verify_peer' => false, 'verify_peer_name' => false, 'allow_self_signed' => true, 'capture_peer_cert' => true)));
        $connect = metrics_website_address(strpos($host, ':') !== false ? '[' . $host . ']' : $host);
        $stream = @stream_socket_client('ssl://' . $connect . ':' . $port, $code, $message, 10, STREAM_CLIENT_CONNECT, $context);
        $params = $stream ? stream_context_get_params($stream) : array();
        $certificate = isset($params['options']['ssl']['peer_certificate']) ? openssl_x509_parse($params['options']['ssl']['peer_certificate']) : false;
        if ($stream) fclose($stream);
        if (!$certificate || empty($certificate['validTo_time_t'])) {
            $failed[] = $domain;
            continue;
        }
        $metrics->add('website_ssl_expiry_timestamp_seconds', 'Unix time when the served TLS certificate expires',
            (int) $certificate['validTo_time_t'], array('domain' => $domain));
    }
    if ($failed) throw new RuntimeException('no certificate from ' . implode(', ', $failed));
}
