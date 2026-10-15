<?php
// Dashboard of metrics.<domain>: the pages people open (the IP allowlist and optional METRICS_HTPASSWD of nginx-proxy
// guard them). It computes nothing itself: every figure comes from the sampler's snapshot and history
// (sampler.php) or, for the logs, from the log collector's files. Self-contained like the cron and backup UIs: no web
// fonts, scripts or styles from elsewhere.

// The tabs, each a file of its own (overview.php, traffic-tab.php, logs.php).
function dashboard_tabs()
{
    return array('/' => 'Overview', '/traffic' => 'Traffic', '/logs/' => 'Logs');
}

function h($value)
{
    return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8');
}

// A snapshot's families as one flat map, "name{labels}" => value: the history's form, so one function reads both.
function dashboard_flat($families)
{
    $values = array();
    foreach ($families as $name => $family) {
        foreach ($family['samples'] as $sample) {
            $values[$name . metrics_labels($sample[0])] = $sample[1];
        }
    }
    return $values;
}

// The samples of one family in a flat map: [labels text => value], e.g. '{cpu="all"}' => 5.
function dashboard_family($values, $name)
{
    $found = array();
    $length = strlen($name);
    foreach ($values as $key => $value) {
        if (strncmp($key, $name, $length) === 0 && (strlen($key) === $length || $key[$length] === '{')) {
            $found[(string) substr($key, $length)] = $value;
        }
    }
    return $found;
}

// Calls $each(time, values) for every history line since $since, oldest first, one line in memory at a time.
function dashboard_history($since, $each)
{
    $directory = metrics_snapshot_directory() . '/history';
    $files = glob($directory . '/*.jsonl') ?: array();
    sort($files);
    foreach ($files as $file) {
        $hour = DateTime::createFromFormat('!Y-m-d-H', basename($file, '.jsonl'), new DateTimeZone('UTC'));
        if ($hour === false || $hour->getTimestamp() + 3600 <= $since) continue;
        $handle = @fopen($file, 'rb');
        if ($handle === false) continue;
        while (($line = fgets($handle)) !== false) {
            $data = json_decode($line, true);
            if (!is_array($data) || !isset($data['t'], $data['v']) || (int) $data['t'] < $since) continue;
            $each((int) $data['t'], $data['v']);
        }
        fclose($handle);
    }
}

// An inline SVG line of [time => value] over [$from, $to]; the highest value of each slot is drawn, so a short
// peak stays visible. Empty when there are fewer than two points.
function dashboard_sparkline($points, $from, $to, $slots = 144)
{
    $buckets = array();
    foreach ($points as $time => $value) {
        if ($value === null) continue;
        $slot = (int) floor(($time - $from) / max(1, $to - $from) * ($slots - 1));
        if ($slot < 0 || $slot >= $slots) continue;
        $buckets[$slot] = isset($buckets[$slot]) ? max($buckets[$slot], $value) : $value;
    }
    if (count($buckets) < 2) return '';
    ksort($buckets);
    $low = min($buckets);
    $high = max($buckets);
    $span = $high - $low ?: 1;
    $path = array();
    foreach ($buckets as $slot => $value) {
        $path[] = round($slot / ($slots - 1) * 240, 1) . ',' . round(38 - ($value - $low) / $span * 34, 1);
    }
    return '<svg class="spark" viewBox="0 0 240 40" preserveAspectRatio="none" aria-hidden="true"><polyline points="'
        . implode(' ', $path) . '"/></svg>';
}

function dashboard_style()
{
    return <<<'CSS'
:root{--bg:#0c0e0b;--panel:#13170f;--rule:#283022;--line:#1c2217;--ink:#ece6d3;--dim:#8d917f;--faint:#5c6152;--amber:#ffb22e;--go:#4fe08c;--stop:#ff5b4f;--steel:#6f93c9;
--mono:ui-monospace,"JetBrains Mono","IBM Plex Mono","DejaVu Sans Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--amber);text-decoration:none}code{color:var(--ink)}
.top{display:flex;gap:18px;align-items:center;flex-wrap:wrap;padding:14px 28px;border-bottom:1px solid var(--rule);background:#11150d}
.mark{font-weight:800;letter-spacing:.32em;color:var(--amber)}.mark small{display:block;font-size:10px;letter-spacing:.18em;color:var(--dim);font-weight:400}
.sign{padding:4px 12px;font-weight:800;letter-spacing:.24em;text-transform:uppercase;font-size:12px;color:#fff;border-radius:3px;background:var(--steel)}
.sign.stage{background:#b8740f}.sign.prod{background:#b3261e}.spacer{flex:1}
nav.tabs{display:flex;gap:4px}nav.tabs a{padding:4px 10px;border:1px solid var(--rule);border-radius:3px;color:var(--dim);text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.06em}
nav.tabs a.on{color:var(--amber);border-color:var(--amber)}
.stamp{color:var(--faint);font-size:11px}
main{padding:22px 28px 60px;max-width:1400px;margin:0 auto}
.callout{border:1px solid var(--rule);border-left:4px solid var(--go);background:var(--panel);padding:10px 14px;margin:0 0 16px;border-radius:3px}
.callout.error{border-left-color:var(--stop)}.callout.wait{border-left-color:var(--amber)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.card{border:1px solid var(--rule);border-top:4px solid var(--faint);background:var(--panel);border-radius:3px;padding:10px 14px 8px}
.card.ok{border-top-color:var(--go)}.card.warn{border-top-color:var(--amber)}.card.bad{border-top-color:var(--stop)}
.card h2{margin:0;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);font-weight:600}
.card .value{font-size:24px;font-weight:700;margin:2px 0 0}.card.warn .value{color:var(--amber)}.card.bad .value{color:var(--stop)}
.card .note{color:var(--dim);font-size:11px;min-height:16px}
.spark{display:block;width:100%;height:40px;margin-top:6px}.spark polyline{fill:none;stroke:var(--steel);stroke-width:1.5;vector-effect:non-scaling-stroke}
.card.warn .spark polyline{stroke:var(--amber)}.card.bad .spark polyline{stroke:var(--stop)}
.card .nohistory{color:var(--faint);font-size:11px;margin-top:6px;height:40px}
.empty{padding:40px;text-align:center;color:var(--dim);border:1px dashed var(--rule);border-radius:4px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
button,input,select{font:inherit;color:var(--ink);background:#191e14;border:1px solid var(--rule);padding:4px 8px;border-radius:3px}
button{cursor:pointer;text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.06em}
table.list{width:100%;border-collapse:collapse}
.list th{text-align:left;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--rule)}
.list td{padding:3px 8px;border-bottom:1px solid var(--line)}.list .num{text-align:right;color:var(--dim)}a.quiet{color:var(--dim)}
.btn{padding:4px 10px;border:1px solid var(--rule);border-radius:3px;color:var(--dim);text-transform:uppercase;font-size:11px;font-weight:700}
.btn.on{color:var(--amber);border-color:var(--amber)}
.panels{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:14px;margin-top:14px}
.panel{border:1px solid var(--rule);background:var(--panel);border-radius:3px;padding:10px 14px;min-width:0}.panel.wide{grid-column:1/-1}
.panel h2{margin:0 0 6px;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);font-weight:600}
.panel h3{margin:8px 0 4px;font-size:12px;color:var(--stop)}
.list td.key{word-break:break-all}
pre.log{margin:0;padding:10px 12px;background:var(--panel);border:1px solid var(--rule);border-radius:3px;white-space:pre-wrap;word-break:break-all;font-size:12px;line-height:1.45}
CSS;
}

// The page around a tab's body. The body refreshes itself from ?fragment=1 every $refresh seconds (0: never).
function dashboard_page($current, $title, $body, $refresh = 60)
{
    $environment = metrics_setting('ENV', 'local');
    $project = metrics_setting('PROJECT_NAME', metrics_setting('DOMAIN', ''));
    $sign = in_array($environment, array('stage', 'prod'), true) ? $environment : '';
    $tabs = '';
    foreach (dashboard_tabs() as $path => $label) {
        $tabs .= '<a href="' . h($path) . '"' . ($path === $current ? ' class="on"' : '') . '>' . h($label) . '</a>';
    }
    $script = $refresh > 0 ? '<script>setInterval(function(){fetch(location.pathname+(location.search?location.search+"&":"?")'
        . '+"fragment=1",{cache:"no-store"}).then(function(r){return r.ok?r.text():null}).then(function(t){'
        . 'if(t!==null)document.querySelector("main").innerHTML=t})},' . (int) $refresh * 1000 . ')</script>' : '';
    header('Content-Type: text/html; charset=utf-8');
    header('Cache-Control: no-store');
    echo '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        . '<title>' . h($title) . ' · ' . h($project) . '</title><style>' . dashboard_style() . '</style></head><body>'
        . '<div class="top"><div class="mark">METRICS<small>' . h($project) . '</small></div>'
        . '<span class="sign ' . h($sign) . '">' . h($environment) . '</span><nav class="tabs">' . $tabs . '</nav>'
        . '<span class="spacer"></span><a class="stamp" href="/metrics">/metrics</a></div>'
        . '<main>' . $body . '</main>' . $script . '</body></html>';
    exit;
}
