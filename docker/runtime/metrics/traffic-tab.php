<?php
// Traffic tab (/traffic) and its JSON (/traffic.json): traffic.php's analysis of a window, the last 15 minutes, hour,
// 24 hours or 7 days, or one local day. Windows of a day or longer start at the whole hour before, so every hour but
// the current one comes from the cache.
require_once __DIR__ . '/dashboard.php';
require_once __DIR__ . '/traffic.php';

const TRAFFIC_WINDOWS = array('15m' => array('15 min', 900), '1h' => array('1 hour', 3600), '24h' => array('24 hours', 86400),
    '7d' => array('7 days', 604800));

// [start, end, label] of the asked window; the day must be YYYY-MM-DD.
function traffic_tab_window($now)
{
    $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
    $day = isset($_GET['day']) && is_string($_GET['day']) ? $_GET['day'] : '';
    if ($day !== '') {
        $date = DateTime::createFromFormat('!Y-m-d', $day, $zone);
        if ($date === false || $date->format('Y-m-d') !== $day) metrics_respond(400, "day must be YYYY-MM-DD\n");
        $start = $date->getTimestamp();
        $end = min($now, (clone $date)->modify('+1 day')->getTimestamp());
        return array($start, max($start, $end), $day, '');
    }
    $key = isset($_GET['window']) && is_string($_GET['window']) && isset(TRAFFIC_WINDOWS[$_GET['window']]) ? $_GET['window'] : '1h';
    $span = TRAFFIC_WINDOWS[$key][1];
    // A day or longer starts at the whole hour before, so it covers at least the span and every hour but the current
    // one is cached.
    $start = $now - $span;
    if ($span >= 86400) $start -= $start % 3600;
    return array($start, $now, TRAFFIC_WINDOWS[$key][0], $key);
}

function traffic_tab_rows($title, $rows, $format = null)
{
    if (!$rows) return '<section class="panel"><h2>' . h($title) . '</h2><p class="stamp">none</p></section>';
    $body = '<section class="panel"><h2>' . h($title) . '</h2><table class="list"><tbody>';
    foreach ($rows as $key => $value) {
        $body .= '<tr><td class="key">' . h($key) . '</td><td class="num">' . ($format ? $format($value) : h(number_format($value, 0, '.', ' '))) . '</td></tr>';
    }
    return $body . '</tbody></table></section>';
}

function traffic_tab_body($report, $start, $end, $label, $key)
{
    $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
    $time = function ($moment) use ($zone) { return (new DateTime('@' . $moment))->setTimezone($zone)->format('Y-m-d H:i'); };
    $bar = '<form method="get" action="/traffic" class="bar">';
    foreach (TRAFFIC_WINDOWS as $window => $option) {
        $bar .= '<a class="btn' . ($window === $key ? ' on' : '') . '" href="/traffic?window=' . $window . '">' . h($option[0]) . '</a>';
    }
    $bar .= '<input type="date" name="day" value="' . h($key === '' ? $label : '') . '"><button>Day</button>'
        . '<span class="spacer"></span><span class="stamp">' . h($time($start)) . ' – ' . h($time($end)) . '</span></form>';
    $classes = '<div class="cards">';
    foreach (array('human' => 'People', 'api' => 'Scripts & API', 'bot' => 'Bots', 'asset' => 'Static files', 'attack' => 'Attacks') as $class => $title) {
        $classes .= '<section class="card' . ($class === 'attack' && $report['classes']['attack'] ? ' bad' : '') . '"><h2>' . h($title) . '</h2>'
            . '<div class="value">' . h(number_format($report['classes'][$class], 0, '.', ' ')) . '</div>'
            . '<div class="note">' . ($report['requests'] ? round($report['classes'][$class] / $report['requests'] * 100) : 0) . ' % of '
            . h(number_format($report['requests'], 0, '.', ' ')) . ' requests</div></section>';
    }
    $classes .= '<section class="card"><h2>Response time</h2><div class="value">'
        . ($report['p95'] === null ? '–' : h(number_format($report['p95'], 2)) . ' s') . '</div><div class="note">p95'
        . ($report['p50'] === null ? '' : ' · median ' . h(number_format($report['p50'], 2)) . ' s') . '</div></section></div>';
    $attacks = '<section class="panel wide"><h2>Attacks by type</h2>';
    if (!$report['attacks']) {
        $attacks .= '<p class="stamp">none</p>';
    }
    foreach ($report['attacks'] as $type => $count) {
        $attacks .= '<h3>' . h($type) . ' · ' . h($count) . '</h3><pre class="log">';
        foreach ($report['attack_lines'][$type] as $line) $attacks .= h($line) . "\n";
        $attacks .= '</pre>';
    }
    $attacks .= '</section>';
    $seconds = function ($value) {
        return h(number_format($value['average'], 3)) . ' s avg · ' . h(number_format($value['max'], 3)) . ' s max · ' . h($value['requests']) . ' req.';
    };
    return $bar . $classes . '<div class="panels">' . $attacks
        . traffic_tab_rows('Bots', $report['bots']) . traffic_tab_rows('Suspicious IPs (an attack)', $report['suspicious_ips'])
        . traffic_tab_rows('Top IPs', $report['ips']) . traffic_tab_rows('Regular IPs (pages, no attack)', $report['regular_ips'])
        . traffic_tab_rows('Top user agents', $report['agents']) . traffic_tab_rows('Top URLs', $report['urls'])
        . traffic_tab_rows('4xx by URL', $report['errors4']) . traffic_tab_rows('5xx by URL', $report['errors5'])
        . traffic_tab_rows('Slowest URLs', $report['slowest'], $seconds) . '</div>'
        . '<p class="stamp">Classes: an attack pattern first, then a known bot, then a static file, then a client that is not a browser '
        . '(scripts & API: curl, python, Go, Java, an app\'s own client, no agent). Bots: setup\'s list and METRICS_BOTS. '
        . 'Classes, bots, attacks and response times are exact; the lists keep each hour\'s ' . TRAFFIC_TOP . ' busiest entries, '
        . 'so in a busy window the counts below the top ones are approximate.</p>';
}

function traffic_tab_route($path)
{
    $now = time();
    list($start, $end, $label, $key) = traffic_tab_window($now);
    if (!is_dir(metrics_logs_directory())) {
        $body = '<div class="empty">No log collector files: the traffic analysis reads them (service metrics-logs).</div>';
        if ($path === '/traffic.json') metrics_respond(503, "no log collector files\n");
        dashboard_page('/traffic', 'Traffic', $body, 0);
    }
    $report = traffic_report(traffic_window($start, $end, $now));
    if ($path === '/traffic.json') {
        header('Content-Type: application/json');
        header('Cache-Control: no-store');
        exit(json_encode(array('start' => $start, 'end' => $end) + $report));
    }
    dashboard_page('/traffic', 'Traffic', traffic_tab_body($report, $start, $end, $label, $key), 0);
}
