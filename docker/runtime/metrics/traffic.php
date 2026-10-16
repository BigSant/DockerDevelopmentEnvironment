<?php
// Traffic analysis, the successor of scan_access_file.sh: nginx-proxy's access lines (the log collector's day files,
// tool sites left out as in the HTTP group) of a window, each request in one class, the first that matches:
//   attack  a request matching an attack pattern (SQL injection, path traversal, probe, XSS), whatever its agent;
//   (the project's own classes, config/metrics/traffic.php: traffic-config.php)
//   bot     a user agent naming a known crawler (setup's list, METRICS_BOTS and the project file's bots);
//   asset   a GET of a static file (image, script, style, font);
//   api     a client that is not a browser: an HTTP library or command-line tool (curl, Wget, python-requests,
//           GuzzleHttp, Java, Go-http-client ...) or no agent; a project extends, replaces or turns it off;
//   human   everything else.
// As the script did, bots are counted one by one by agent (an agent naming two counts for both, an attack's agent
// too). The requests per IP of the suspicious (an attack) and regular clients count the human and attack lines: the
// script counted every non-asset, non-bot line, but it had no class for scripts. An hour that has closed is
// aggregated once and cached (traffic/<hour>.json in the sampler's directory; the sampler prepares them, a page
// builds what is missing), so a 7-day window merges 168 small files instead of reading a week of logs. A cached hour
// keeps the busiest TRAFFIC_TOP entries of each list. Lists are also trimmed while a file is read (to 2 x TRAFFIC_TOP
// when one reaches 4 x), so in an hour with more distinct IPs, agents or URLs than that, counts below the busiest are
// approximate; the busiest, the classes, bots, attacks and response times are exact.

const TRAFFIC_BOTS = array(
    // scan_access_file.sh's list.
    'bingbot/2.0', 'yandex.com/bots', 'mj12bot.com', 'YandexBot', 'GPTBot', 'SemrushBot', 'GeedoProductSearch', 'PetalBot',
    'Facebot', 'Twitterbot', 'bytedance', 'Bytespider', 'ClaudeBot', 'DotBot', 'AhrefsBot', 'Googlebot', 'GoogleOther',
    'Amazonbot/0.1', 'AdsBot-Google', 'Applebot/0.1', 'PerplexityBot/1.0', 'openai.com/searchbot', 'DataForSeoBot/1.0',
    'ImagesiftBot', 'facebookexternalhit/1.1', 'Cookiebot/1.0', 'ChatGPT-User/1.0', 'Mail.RU_Bot/2.0', 'TelegramBot',
    'TwitterBot', 'Orbbot/1.1', 'YaK/1.0', 'coccocbot-web/1.0', 'keys-so-bot', 'archive.org_bot', 'SeznamBot', 'AwarioBot',
    'meta-externalagent', 'SBIntuitionsBot', 'BacklinksExtendedBot', 'DuckDuckBot', 'coccocbot',
    // More crawlers seen on shops.
    'Baiduspider', 'BLEXBot', 'Barkrowler', 'serpstatbot', 'MojeekBot', 'Qwantbot', 'Sogou', 'CCBot', 'OAI-SearchBot',
    'Claude-SearchBot', 'Claude-User', 'Pinterestbot', 'LinkedInBot', 'Slackbot', 'Discordbot', 'WhatsApp', 'Screaming Frog',
    'UptimeRobot', 'Bravebot', 'YisouSpider', 'AliyunSecBot', 'MegaIndex', 'ZoominfoBot', 'Diffbot', 'Timpibot', 'AmazonProductDiscovery',
);
const TRAFFIC_ASSET = '/\.(jpe?g|png|gif|webp|avif|ico|bmp|js|mjs|css|map|svg|ttf|otf|woff2?|eot)$/i';
const TRAFFIC_ATTACKS = array(
    // scan_access_file.sh's pattern.
    'sqli' => '/(select|union|insert|update|delete|drop|alter|create|truncate|replace|rename|outfile|load_file)%20/i',
    'traversal' => '#(\.\./|\.\.%2f|%2e%2e(/|%2f)|\.\.\\\\|\.\.%5c|/etc/passwd)#i',
    'probe' => '#(/\.env(\b|$)|/wp-(login|admin|content|includes)|/xmlrpc\.php|/\.git(/|$|\?)|/\.svn/|/\.aws/|/\.ssh/|/phpmyadmin|/\.ds_store|/server-status|/vendor/phpunit|/cgi-bin/)#i',
    'xss' => '/(<script|%3cscript|javascript:|onerror\s*=|onerror%3d|onload\s*=|onload%3d|%3csvg|<svg)/i',
);
const TRAFFIC_TOP = 500;
// The attack lines a cached hour keeps per type (shown as examples).
const TRAFFIC_LINES = 20;
// An hour is closed this long after it ends: the collector has written its lines by then.
const TRAFFIC_SETTLE = 120;
const TRAFFIC_KEEP_DAYS = 8;

require_once __DIR__ . '/traffic-config.php';

function traffic_empty()
{
    $rules = traffic_rules();
    return array('rules' => $rules['hash'], 'requests' => 0, 'classes' => array_fill_keys(array_keys($rules['labels']), 0), 'bots' => array(), 'attacks' => array(),
        'attack_lines' => array(), 'suspicious' => array(), 'ip_requests' => array(), 'ips' => array(), 'agents' => array(),
        'urls' => array(), 'errors4' => array(), 'errors5' => array(), 'times' => array(), 'durations' => array());
}

// One access line: [time, class, attack type or null, the line's fields] or null for a line that is not a site
// request (not an access line, a tool site).
function traffic_parse($line, $rules, $tools)
{
    $fields = metrics_http_access_fields($line);
    if ($fields === null) return null;
    $host = preg_match('/ host=(\S+)$/', $line, $found) ? strtolower($found[1]) : '';
    if ($tools && isset($tools[$host])) return null;
    $time = metrics_http_time('!d/M/Y:H:i:s O', $fields[2]);
    if ($time === null) return null;
    $request = $fields[3];
    $parts = explode(' ', $request);
    $method = $parts[0];
    $target = isset($parts[1]) ? $parts[1] : '';
    $path = (string) strtok($target, '?');
    $attack = null;
    foreach (TRAFFIC_ATTACKS as $type => $pattern) {
        if (preg_match($pattern, $request)) {
            $attack = $type;
            break;
        }
    }
    // As nginx wrote it: bytes outside ASCII stay \xHH escapes, so the agent is valid JSON (and HTML) text.
    $agent = $fields[7];
    $named = array();
    foreach ($rules['bots'] as $bot) {
        if (strpos($agent, $bot) !== false) $named[] = $bot;
    }
    $class = $attack !== null ? 'attack' : traffic_class($rules, $named, array('ip' => $fields[1], 'method' => $method,
        'path' => $path, 'url' => $target, 'status' => (int) $fields[4], 'agent' => $agent, 'host' => $host));
    $rt = preg_match('/ rt=(\d+(?:\.\d+)?) /', $line, $timing) ? (float) $timing[1] : null;
    return array($time, $class, $attack, array('ip' => $fields[1], 'path' => $path === '' ? $target : $path, 'status' => (int) $fields[4],
        'agent' => $agent, 'bots' => $named, 'rt' => $rt, 'line' => $line));
}

// The class of a request that is not an attack: the project's classes in their order, then bot, asset, api, human.
function traffic_class($rules, $bots, $request)
{
    foreach ($rules['classes'] as $name => $own) {
        if (traffic_matches($own[1], $request)) return $name;
    }
    if ($bots) return 'bot';
    if ($request['method'] === 'GET' && preg_match(TRAFFIC_ASSET, $request['path'])) return 'asset';
    foreach ($rules['api'] ?: array() as $api) {
        if (traffic_matches($api, $request)) return 'api';
    }
    return 'human';
}

function traffic_add(&$aggregate, $parsed)
{
    list(, $class, $attack, $request) = $parsed;
    $aggregate['requests']++;
    $aggregate['classes'][$class] = (isset($aggregate['classes'][$class]) ? $aggregate['classes'][$class] : 0) + 1;
    $ip = $request['ip'];
    $aggregate['ips'][$ip] = (isset($aggregate['ips'][$ip]) ? $aggregate['ips'][$ip] : 0) + 1;
    $agent = $request['agent'];
    $aggregate['agents'][$agent] = (isset($aggregate['agents'][$agent]) ? $aggregate['agents'][$agent] : 0) + 1;
    foreach ($request['bots'] as $bot) {
        $aggregate['bots'][$bot] = (isset($aggregate['bots'][$bot]) ? $aggregate['bots'][$bot] : 0) + 1;
    }
    if ($class === 'attack') {
        $aggregate['attacks'][$attack] = (isset($aggregate['attacks'][$attack]) ? $aggregate['attacks'][$attack] : 0) + 1;
        if (!isset($aggregate['attack_lines'][$attack]) || count($aggregate['attack_lines'][$attack]) < TRAFFIC_LINES) {
            $aggregate['attack_lines'][$attack][] = substr($request['line'], 0, 2048);
        }
        $aggregate['suspicious'][$ip] = 1;
    }
    if ($class === 'human' || $class === 'attack') {
        $aggregate['ip_requests'][$ip] = (isset($aggregate['ip_requests'][$ip]) ? $aggregate['ip_requests'][$ip] : 0) + 1;
    }
    if ($class === 'asset') return;
    $path = substr($request['path'], 0, 512);
    $aggregate['urls'][$path] = (isset($aggregate['urls'][$path]) ? $aggregate['urls'][$path] : 0) + 1;
    $status = $request['status'];
    if ($status >= 400 && $status < 500) $aggregate['errors4'][$path] = (isset($aggregate['errors4'][$path]) ? $aggregate['errors4'][$path] : 0) + 1;
    if ($status >= 500) $aggregate['errors5'][$path] = (isset($aggregate['errors5'][$path]) ? $aggregate['errors5'][$path] : 0) + 1;
    if ($request['rt'] !== null) {
        $time = isset($aggregate['times'][$path]) ? $aggregate['times'][$path] : array(0, 0.0, 0.0);
        $aggregate['times'][$path] = array($time[0] + 1, $time[1] + $request['rt'], max($time[2], $request['rt']));
        // Response times in milliseconds, counted per value: percentiles of any window.
        $milliseconds = (string) (int) round($request['rt'] * 1000);
        $aggregate['durations'][$milliseconds] = (isset($aggregate['durations'][$milliseconds]) ? $aggregate['durations'][$milliseconds] : 0) + 1;
    }
}

// The busiest $limit entries of each list (the slowest by total time for times); durations are kept whole, and the
// suspicious IPs (at most $limit) keep their request counts.
function traffic_trim(&$aggregate, $limit)
{
    foreach (array('ips', 'agents', 'urls', 'errors4', 'errors5', 'ip_requests', 'bots') as $list) {
        if (count($aggregate[$list]) <= $limit) continue;
        $kept = metrics_http_top($aggregate[$list], $limit);
        if ($list === 'ip_requests') {
            foreach ($aggregate['suspicious'] as $ip => $one) {
                if (isset($aggregate[$list][$ip])) $kept[$ip] = $aggregate[$list][$ip];
            }
        }
        $aggregate[$list] = $kept;
    }
    if (count($aggregate['times']) > $limit) {
        uasort($aggregate['times'], function ($a, $b) { return $b[1] <=> $a[1]; });
        $aggregate['times'] = array_slice($aggregate['times'], 0, $limit, true);
    }
    if (count($aggregate['suspicious']) > $limit) $aggregate['suspicious'] = array_slice($aggregate['suspicious'], 0, $limit, true);
}

function traffic_merge(&$into, $from)
{
    $into['requests'] += $from['requests'];
    foreach ($from['classes'] as $class => $count) $into['classes'][$class] = (isset($into['classes'][$class]) ? $into['classes'][$class] : 0) + $count;
    foreach (array('bots', 'attacks', 'ip_requests', 'ips', 'agents', 'urls', 'errors4', 'errors5', 'durations') as $list) {
        foreach ($from[$list] as $key => $count) {
            $into[$list][$key] = (isset($into[$list][$key]) ? $into[$list][$key] : 0) + $count;
        }
    }
    foreach ($from['suspicious'] as $ip => $one) $into['suspicious'][$ip] = 1;
    foreach ($from['attack_lines'] as $type => $lines) {
        $kept = isset($into['attack_lines'][$type]) ? $into['attack_lines'][$type] : array();
        $into['attack_lines'][$type] = array_slice(array_merge($kept, $lines), 0, TRAFFIC_LINES);
    }
    foreach ($from['times'] as $path => $time) {
        $have = isset($into['times'][$path]) ? $into['times'][$path] : array(0, 0.0, 0.0);
        $into['times'][$path] = array($have[0] + $time[0], $have[1] + $time[1], max($have[2], $time[2]));
    }
}

function traffic_directory()
{
    return metrics_snapshot_directory() . '/traffic';
}

// nginx-proxy's day files (local days in TZ) that hold lines of [$start, $end): the plain file, else the gzipped one.
function traffic_files($start, $end)
{
    $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
    $days = array();
    for ($moment = $start - 86400; $moment < $end + 86400; $moment += 3600) {
        $days[(new DateTime('@' . $moment))->setTimezone($zone)->format('Y-m-d')] = true;
    }
    $files = array();
    foreach (array_keys($days) as $day) {
        $base = metrics_logs_directory() . '/nginx-proxy/access-' . $day . '.log';
        $date = new DateTime($day . ' 00:00:00', $zone);
        $first = $date->getTimestamp();
        $next = $date->modify('+1 day')->getTimestamp();
        // A day file holds lines of its own local day (25 or 23 hours on a DST change), give or take a late second.
        if ($first - METRICS_HTTP_SLACK >= $end || $next + METRICS_HTTP_SLACK <= $start) continue;
        if (is_file($base)) $files[] = array($base, false, $first);
        elseif (is_file($base . '.gz')) $files[] = array($base . '.gz', true, $first);
    }
    return $files;
}

// One pass over the files of the pieces [[start, end], ...]: an aggregate per piece. A plain file is read backwards
// until its lines are older than the earliest piece, a gzipped one forwards; the lists are trimmed as they grow.
function traffic_collect($pieces)
{
    if (!$pieces) return array();
    $aggregates = array();
    foreach ($pieces as $index => $piece) $aggregates[$index] = traffic_empty();
    $from = min(array_map(function ($piece) { return $piece[0]; }, $pieces));
    $to = max(array_map(function ($piece) { return $piece[1]; }, $pieces));
    $rules = traffic_rules();
    $tools = metrics_http_tool_hosts();
    $seen = 0;
    $visit = function ($line) use ($pieces, &$aggregates, $rules, $tools, $from, &$seen) {
        $parsed = traffic_parse($line, $rules, $tools);
        if ($parsed === null) return true;
        foreach ($pieces as $index => $piece) {
            if ($parsed[0] >= $piece[0] && $parsed[0] < $piece[1]) {
                traffic_add($aggregates[$index], $parsed);
                break;
            }
        }
        if (++$seen % 20000 === 0) {
            foreach ($aggregates as &$aggregate) {
                foreach (array('ips', 'agents', 'urls', 'errors4', 'errors5', 'ip_requests') as $list) {
                    if (count($aggregate[$list]) > 4 * TRAFFIC_TOP) {
                        traffic_trim($aggregate, 2 * TRAFFIC_TOP);
                        break;
                    }
                }
            }
            unset($aggregate);
        }
        return $parsed[0] >= $from - METRICS_HTTP_SLACK;
    };
    foreach (traffic_files($from, $to) as $file) {
        list($path, $gzipped) = $file;
        if (!$gzipped) {
            metrics_http_backwards($path, $visit);
            continue;
        }
        $handle = @gzopen($path, 'rb');
        if ($handle === false) continue;
        while (($line = gzgets($handle, METRICS_HTTP_LINE_LIMIT)) !== false) {
            $visit(rtrim($line, "\r\n"));
        }
        gzclose($handle);
    }
    return $aggregates;
}

// The aggregate of [$start, $end): closed whole hours from the cache (built and cached when missing), the rest read.
function traffic_window($start, $end, $now)
{
    $total = traffic_empty();
    $pieces = array();
    $hours = array();
    $cursor = $start;
    while ($cursor < $end) {
        $hour = $cursor - $cursor % 3600;
        $next = min($end, $hour + 3600);
        if ($cursor === $hour && $next === $hour + 3600 && traffic_closed($hour, $now)) {
            $cached = traffic_cached($hour, true);
            if ($cached !== null) {
                traffic_merge($total, $cached);
                // 168 cached hours of 500 entries each would be 84 000: keep the merged lists bounded.
                if (count($total['ips']) > 8 * TRAFFIC_TOP || count($total['urls']) > 8 * TRAFFIC_TOP || count($total['agents']) > 8 * TRAFFIC_TOP) {
                    traffic_trim($total, 4 * TRAFFIC_TOP);
                }
            } else {
                $hours[count($pieces)] = $hour;
                $pieces[] = array($hour, $next);
            }
        } else {
            $pieces[] = array($cursor, $next);
        }
        $cursor = $next;
    }
    traffic_collect_cached($pieces, $hours, function ($aggregate) use (&$total) {
        traffic_merge($total, $aggregate);
        // A long window's merged lists stay bounded too.
        traffic_trim($total, 4 * TRAFFIC_TOP);
    });
    return $total;
}

// traffic_collect in batches of one local day's pieces (a day file is read once per batch), the pieces that are whole
// closed hours ($hours: piece index => hour) written to the cache and every batch handed to $each before the next is
// read: at most a day of aggregates is in memory, however many hours are missing.
function traffic_collect_cached($pieces, $hours, $each)
{
    $directory = traffic_directory();
    if ($hours && !is_dir($directory)) @mkdir($directory, 0755, true);
    $zone = new DateTimeZone(metrics_setting('TZ', date_default_timezone_get()));
    $batches = array();
    foreach ($pieces as $index => $piece) {
        $batches[(new DateTime('@' . $piece[0]))->setTimezone($zone)->format('Y-m-d')][$index] = $piece;
    }
    foreach ($batches as $batch) {
        $collected = traffic_collect(array_values($batch));
        foreach (array_keys($batch) as $position => $index) {
            $aggregate = $collected[$position];
            if (isset($hours[$index])) {
                traffic_trim($aggregate, TRAFFIC_TOP);
                try {
                    metrics_write_json($directory . '/' . $hours[$index] . '.json', $aggregate);
                } catch (RuntimeException $error) {
                    // Read-only or full: the window is still right, only slower next time.
                }
            }
            $each($aggregate);
        }
        unset($collected);
    }
}

// Whether an hour can be cached for good: it ended TRAFFIC_SETTLE ago and the log collector has written every line
// Docker held since then (its state's caught_up), so it has read the hour's lines. Before its first state (a first
// deploy), while it is stopped and while it back-fills after a start, nothing is cached and the lines it brings
// still count. A state without caught_up (a collector process started before setup wrote it) falls back to the
// time the state was saved.
function traffic_closed($hour, $now)
{
    if ($hour + 3600 + TRAFFIC_SETTLE > $now) return false;
    $read = traffic_collector_caught_up();
    return $read !== null && $read >= $hour + 3600 + TRAFFIC_SETTLE;
}

function traffic_collector_caught_up()
{
    static $cache = array();
    $state = metrics_logs_directory() . '/.collector-state.json';
    clearstatcache(true, $state);
    if (!is_file($state)) return null;
    $saved = (int) @filemtime($state);
    if (!isset($cache[$state]) || $cache[$state][0] !== $saved) {
        $data = metrics_read_json($state);
        $read = is_array($data) && array_key_exists('caught_up', $data) ? $data['caught_up'] : $saved;
        $cache[$state] = array($saved, $read === null ? null : (int) $read);
    }
    return $cache[$state][1];
}

// A cache file that is whole (a failed write leaves none, metrics_write_json; an empty one is not trusted) and built
// with the classes in effect: after the project's file, METRICS_BOTS or setup's own classes change, the sampler builds
// the hours again from the day files. $stale: a page takes an hour of the previous classes meanwhile, so it never
// reads a week of logs itself (the next traffic round, within minutes, brings the new ones).
function traffic_cached($hour, $stale = false)
{
    $cached = metrics_read_json(traffic_directory() . '/' . $hour . '.json');
    if ($cached === null || !isset($cached['requests'], $cached['classes'])) return null;
    return $stale || (isset($cached['rules']) && $cached['rules'] === traffic_rules()['hash']) ? $cached : null;
}

// The sampler's traffic loop: every closed hour of the last TRAFFIC_KEEP_DAYS - 1 days not cached yet, a local day
// at a time, and cached hours past TRAFFIC_KEEP_DAYS removed. After the first run only the hour that has just closed
// is missing, so pages find every closed hour cached.
function traffic_prepare($now)
{
    $directory = traffic_directory();
    $last = $now - TRAFFIC_SETTLE;
    $last -= $last % 3600;
    $pieces = $hours = array();
    for ($hour = $last - 3600; $hour >= $last - (TRAFFIC_KEEP_DAYS - 1) * 86400; $hour -= 3600) {
        if (!traffic_closed($hour, $now) || traffic_cached($hour) !== null) continue;
        $hours[count($pieces)] = $hour;
        $pieces[] = array($hour, $hour + 3600);
    }
    traffic_collect_cached($pieces, $hours, function ($aggregate) {});
    foreach (glob($directory . '/*.json') ?: array() as $file) {
        if ((int) basename($file, '.json') < $now - TRAFFIC_KEEP_DAYS * 86400) @unlink($file);
    }
}

// Percentile ($share, 0..1) of the response times in a durations map, in seconds; null without any.
function traffic_percentile($durations, $share)
{
    $total = array_sum($durations);
    if ($total === 0) return null;
    ksort($durations, SORT_NUMERIC);
    $rank = max(1, (int) ceil($share * $total));
    $seen = 0;
    foreach ($durations as $milliseconds => $count) {
        $seen += $count;
        if ($seen >= $rank) return $milliseconds / 1000;
    }
    return null;
}

// The analysis of a window as the dashboard and /feed show it.
function traffic_report($aggregate, $limit = 20)
{
    $suspicious = array();
    $regular = array();
    foreach ($aggregate['ip_requests'] as $ip => $count) {
        if (isset($aggregate['suspicious'][$ip])) $suspicious[$ip] = $count;
        else $regular[$ip] = $count;
    }
    foreach ($aggregate['suspicious'] as $ip => $one) {
        if (!isset($suspicious[$ip])) $suspicious[$ip] = 0;
    }
    $slowest = array();
    foreach ($aggregate['times'] as $path => $time) {
        $slowest[$path] = array('requests' => $time[0], 'average' => round($time[1] / max(1, $time[0]), 3), 'max' => $time[2]);
    }
    uasort($slowest, function ($a, $b) { return $b['average'] <=> $a['average'] ?: $b['max'] <=> $a['max']; });
    arsort($aggregate['attacks']);
    // Example lines in time order (a plain file is read from its end).
    foreach ($aggregate['attack_lines'] as &$lines) {
        usort($lines, function ($a, $b) {
            $x = metrics_http_access_fields($a);
            $y = metrics_http_access_fields($b);
            return ($x ? metrics_http_time('!d/M/Y:H:i:s O', $x[2]) : 0) <=> ($y ? metrics_http_time('!d/M/Y:H:i:s O', $y[2]) : 0);
        });
    }
    unset($lines);
    return array(
        'requests' => $aggregate['requests'],
        'classes' => $aggregate['classes'],
        'labels' => traffic_rules()['labels'],
        'bots' => metrics_http_top($aggregate['bots'], 100),
        'attacks' => $aggregate['attacks'],
        'attack_lines' => $aggregate['attack_lines'],
        'suspicious_ips' => metrics_http_top($suspicious, 100),
        'regular_ips' => metrics_http_top($regular, $limit),
        'ips' => metrics_http_top($aggregate['ips'], $limit),
        'agents' => metrics_http_top($aggregate['agents'], $limit),
        'urls' => metrics_http_top($aggregate['urls'], $limit),
        'errors4' => metrics_http_top($aggregate['errors4'], $limit),
        'errors5' => metrics_http_top($aggregate['errors5'], $limit),
        'slowest' => array_slice($slowest, 0, $limit, true),
        'p50' => traffic_percentile($aggregate['durations'], 0.5),
        'p95' => traffic_percentile($aggregate['durations'], 0.95),
    );
}
