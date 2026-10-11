<?php
// Thin cron UI: jobs with their next run, "run now", run history and (local/test only) crontab editing.
// With CRON_SCHEDULE=off nothing runs on schedule (no supercronic): the UI marks it and "run now" still works.
// Reached only through nginx-proxy with the CRON_ALLOWED_IPS / ALLOWED_IPS allowlist; no docker.sock.
// Self-contained on purpose (servers are reached over a VPN): no web fonts, scripts or styles from elsewhere.
require __DIR__ . '/../compose.php';

// Other containers on the project network (e.g. an SSRF in the shop) would bypass the allowlist:
// answer only nginx-proxy, or loopback for checks inside this container.
$peer = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '';
$proxy = gethostbynamel(getenv('CRON_UI_PROXY') ?: 'nginx-proxy') ?: array();
if (!in_array($peer, array_merge(array('127.0.0.1', '::1'), $proxy), true)) {
    http_response_code(403);
    exit("Reach the cron UI through nginx-proxy.\n");
}

$history = getenv('CRON_HISTORY_DIR') ?: '/var/lib/setup-cron';
$environment = getenv('ENV') ?: 'local';
$editable = in_array($environment, array('local', 'test'), true);
$scheduled = getenv('CRON_SCHEDULE') !== 'off';
$tokenFile = getenv('CRON_UI_TOKEN_FILE') ?: '/tmp/cron-ui-token';
if (!is_file($tokenFile)) file_put_contents($tokenFile, bin2hex(random_bytes(16)));
$token = trim(file_get_contents($tokenFile));

function h($value) { return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8'); }

// A railway departures board: each job is a departure, its next run the time on the flaps, its recent
// runs a row of signal lamps. Dark board, amber flaps, green/red signals; the environment is the
// station sign (prod: red with hazard stripes). Monospace throughout: it is a board of times and commands.
const CRON_UI_STYLE = <<<'CSS'
:root{--board:#0c0e0b;--panel:#13170f;--panel-2:#191e14;--rule:#283022;--ink:#ece6d3;--dim:#8d917f;--faint:#5c6152;
--amber:#ffb22e;--amber-glow:rgba(255,178,46,.35);--go:#4fe08c;--stop:#ff5b4f;--hazard:#f4c20d;--steel:#6f93c9;
--mono:ui-monospace,"JetBrains Mono","Iosevka","IBM Plex Mono","SF Mono","Cascadia Code","DejaVu Sans Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}
html{background:var(--board)}
body{margin:0;min-height:100vh;color:var(--ink);font:13px/1.5 var(--mono);font-variant-numeric:tabular-nums;
background:radial-gradient(1200px 500px at 15% -10%,rgba(255,178,46,.07),transparent 60%),
repeating-linear-gradient(0deg,rgba(255,255,255,.012) 0 1px,transparent 1px 3px),var(--board)}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--amber);outline-offset:2px}
code{font:inherit;color:var(--ink)}
.top{display:flex;align-items:center;gap:18px;flex-wrap:wrap;padding:14px 28px;border-bottom:1px solid var(--rule);
background:linear-gradient(180deg,#171c12,#10130d)}
.mark{font-weight:800;letter-spacing:.32em;font-size:15px;color:var(--amber);text-shadow:0 0 14px var(--amber-glow)}
.mark small{display:block;letter-spacing:.18em;font-size:10px;color:var(--dim);font-weight:400;text-shadow:none}
.sign{padding:5px 12px 4px;font-weight:800;letter-spacing:.24em;text-transform:uppercase;font-size:12px;color:#fff;
border-radius:3px;background:var(--steel);box-shadow:inset 0 0 0 2px rgba(255,255,255,.25)}
.sign.stage{background:#b8740f}
.sign.prod{background:#b3261e;border:5px solid;border-image:repeating-linear-gradient(-45deg,var(--hazard) 0 5px,#111 5px 10px) 5}
.spacer{flex:1}
.env-stage .top{border-bottom:3px solid #b8740f}
.env-prod .top{border-bottom:3px solid #b3261e;box-shadow:0 3px 0 rgba(179,38,30,.25)}
.clock{text-align:right;color:var(--dim);font-size:11px;letter-spacing:.08em}
.clock b{display:block;color:var(--ink);font-size:20px;letter-spacing:.06em;font-weight:600}
.nav{display:flex;gap:6px}
.btn,button{font:inherit;cursor:pointer;color:var(--ink);background:var(--panel-2);border:1px solid var(--rule);
padding:6px 12px;border-radius:3px;letter-spacing:.06em;text-transform:uppercase;font-size:11px;font-weight:700;display:inline-block}
.btn:hover,button:hover{border-color:var(--amber);color:var(--amber);text-decoration:none}
button.run{background:var(--amber);border-color:var(--amber);color:#1a1205}
button.run:hover{background:#ffc75e;color:#1a1205;box-shadow:0 0 18px var(--amber-glow)}
main{padding:22px 28px 60px;max-width:1400px;margin:0 auto}
.crumbs{color:var(--dim);margin:0 0 14px;font-size:12px}
h1{font-size:12px;letter-spacing:.3em;text-transform:uppercase;color:var(--dim);margin:0 0 12px;font-weight:600}
.note{color:var(--dim);margin:0 0 18px}
.tape{position:relative;margin:0 0 20px;padding:12px 18px;color:#111;font-weight:800;letter-spacing:.06em;
background:repeating-linear-gradient(-45deg,var(--hazard) 0 18px,#171717 18px 36px);border-radius:3px}
.tape span{background:var(--hazard);padding:3px 10px;box-shadow:0 0 0 3px var(--hazard)}
.tape .off{color:#111;font-size:15px;letter-spacing:.2em;text-transform:uppercase}
.tape small{display:block;margin-top:8px;font-weight:600;letter-spacing:0}
.tape small span{box-shadow:none}
.callout{border:1px solid var(--rule);border-left:4px solid var(--go);background:var(--panel);padding:10px 14px;margin:0 0 16px;border-radius:3px}
.callout.error{border-left-color:var(--stop)}
.callout.wait{border-left-color:var(--amber)}
.board{width:100%;border-collapse:separate;border-spacing:0 6px}
.board th{white-space:nowrap;text-align:left;font-size:10px;letter-spacing:.24em;text-transform:uppercase;color:var(--faint);font-weight:600;padding:0 14px 2px}
.board td{background:var(--panel);padding:12px 14px;vertical-align:middle;border-top:1px solid var(--rule);border-bottom:1px solid #0a0c08}
.board td:first-child{border-radius:4px 0 0 4px;border-left:3px solid var(--amber);min-width:200px;white-space:nowrap}
.board td:last-child{border-radius:0 4px 4px 0;text-align:right;white-space:nowrap}
.board tr.failing td:first-child{border-left-color:var(--stop)}
.board tr.idle td:first-child{border-left-color:var(--faint)}
.board tbody tr{animation:arrive .5s cubic-bezier(.2,.8,.2,1) both;animation-delay:calc(var(--i,0) * 45ms)}
@keyframes arrive{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.flaps{display:flex;gap:3px;margin-bottom:5px}
.flap{display:inline-block;min-width:22px;padding:4px 0 3px;text-align:center;font-size:22px;font-weight:700;line-height:1;color:var(--amber);
background:linear-gradient(180deg,#22281b 0 49%,#0b0d09 49% 51%,#1b2016 51%);border-radius:3px;
box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 2px 0 #050604;text-shadow:0 0 10px var(--amber-glow)}
.flap.sep{min-width:10px;background:none;box-shadow:none;color:var(--dim);text-shadow:none}
.halted .flap{color:var(--faint);text-shadow:none;text-decoration:line-through;text-decoration-color:var(--stop)}
time.next{color:var(--dim);font-size:11px}
.rel{color:var(--ink);font-size:11px;margin-left:6px}
.off{color:var(--hazard);font-weight:800;letter-spacing:.12em;text-transform:uppercase}
.cmd{color:var(--ink);word-break:break-word}
.where{display:block;color:var(--faint);font-size:11px;margin-top:3px}
.expr{display:inline-block;padding:2px 8px;border:1px solid var(--rule);border-radius:3px;color:var(--amber);background:#0f120c;white-space:nowrap}
.lamps{display:flex;gap:4px;align-items:center;margin-bottom:5px}
.lamp{width:11px;height:11px;border-radius:50%;background:#20261a;box-shadow:inset 0 0 0 1px #2c3324}
.lamp.ok{background:var(--go);box-shadow:0 0 8px rgba(79,224,140,.55)}
.lamp.fail{background:var(--stop);box-shadow:0 0 8px rgba(255,91,79,.6)}
.lamp.manual{outline:1px dashed rgba(236,230,211,.5);outline-offset:2px}
.last{color:var(--dim);font-size:11px}
.ok{color:var(--go)}.fail{color:var(--stop)}
.chip{display:inline-block;padding:1px 7px;border-radius:2px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;
border:1px solid var(--rule);color:var(--dim)}
.chip.ok{color:var(--go);border-color:rgba(79,224,140,.4)}.chip.fail{color:var(--stop);border-color:rgba(255,91,79,.45)}
.chip.manual{color:var(--amber);border-color:var(--amber-dim,#6b4e12)}
.actions{display:inline-flex;gap:8px;align-items:center}
.actions form{margin:0}
.empty{padding:40px;text-align:center;color:var(--dim);border:1px dashed var(--rule);border-radius:4px}
.job{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:start;background:var(--panel);border:1px solid var(--rule);
border-left:3px solid var(--amber);border-radius:4px;padding:16px 18px;margin:0 0 18px}
.job .cmd{font-size:15px}
.stats{display:flex;gap:28px;flex-wrap:wrap;margin:0 0 18px}
.stat b{display:block;font-size:22px;color:var(--ink);font-weight:700}
.stat span{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint)}
.runs{list-style:none;margin:0;padding:0;border-top:1px solid var(--rule)}
.runs li{display:grid;grid-template-columns:18px 180px 100px 80px 1fr 110px;gap:14px;align-items:center;padding:9px 6px;border-bottom:1px solid var(--rule)}
.runs li:hover{background:var(--panel)}
.bar{height:6px;background:#1c2217;border-radius:3px;overflow:hidden}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--amber),#ffd27a);border-radius:3px}
.bar.fail i{background:linear-gradient(90deg,var(--stop),#ff9a8f)}
.term{background:#070806;border:1px solid var(--rule);border-radius:4px;overflow:auto;max-height:75vh;box-shadow:inset 0 0 40px rgba(0,0,0,.6)}
.term ol{margin:0;padding:12px 0;counter-reset:l;list-style:none}
.term li{counter-increment:l;display:grid;grid-template-columns:56px 1fr;white-space:pre-wrap;word-break:break-all;color:#d8e8c8}
.term li::before{content:counter(l);text-align:right;padding-right:14px;color:var(--faint);user-select:none}
.term li:hover{background:#10140c}
.editor{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:18px}
.tabs{display:flex;gap:4px;margin:0 0 -1px}
.tabs a{padding:7px 14px;border:1px solid var(--rule);border-bottom:none;border-radius:4px 4px 0 0;color:var(--dim);background:var(--board)}
.tabs a.on{color:var(--amber);background:var(--panel)}
textarea{width:100%;min-height:420px;resize:vertical;font:13px/1.7 var(--mono);color:var(--ink);background:var(--panel);
border:1px solid var(--rule);border-radius:0 4px 4px 4px;padding:14px 16px;tab-size:4;caret-color:var(--amber)}
textarea:focus{outline:none;border-color:var(--amber)}
.cheat{align-self:start;background:var(--panel);border:1px solid var(--rule);border-radius:4px;padding:14px 16px;color:var(--dim);font-size:12px}
.cheat pre{margin:6px 0 12px;color:var(--amber);font:inherit;line-height:1.35}
.form-foot{display:flex;justify-content:space-between;align-items:center;margin-top:12px;gap:12px}
@media (max-width:900px){.board thead{display:none}.board,.board tbody,.board tr,.board td{display:block;width:100%}
.board tr{margin-bottom:10px;border-radius:4px;overflow:hidden}.board td{border-radius:0!important;border-left:3px solid var(--amber);border-top:none;border-bottom:none;padding:8px 14px}
.board tr.failing td{border-left-color:var(--stop)}.board tr.idle td{border-left-color:var(--faint)}.board td:last-child{text-align:left}
.runs li{grid-template-columns:18px 1fr 80px}.runs li .bar,.runs li .trig{display:none}.editor{grid-template-columns:1fr}.top{padding:12px 16px}main{padding:18px 16px}}
@media (prefers-reduced-motion:reduce){.board tbody tr{animation:none}}
CSS;

// Live clock in the board's time zone and relative times that keep counting; nothing else needs script.
const CRON_UI_SCRIPT = <<<'JS'
(function(){var zone=document.body.getAttribute('data-zone');
function clock(){var e=document.getElementById('clock');if(!e)return;try{e.textContent=new Date().toLocaleTimeString('en-GB',{timeZone:zone,hour12:false});}catch(x){e.textContent=new Date().toLocaleTimeString('en-GB',{hour12:false});}}
function rel(){var now=Date.now();document.querySelectorAll('[data-at]').forEach(function(e){var d=Math.round((+e.getAttribute('data-at')-now)/1000),a=Math.abs(d),t;
if(a<45)t='now';else if(a<3600)t=Math.round(a/60)+' min';else if(a<172800)t=Math.round(a/3600)+' h';else t=Math.round(a/86400)+' d';
e.textContent=t==='now'?'now':(d>0?'in '+t:t+' ago');});}
document.querySelectorAll('form[data-confirm]').forEach(function(f){f.addEventListener('submit',function(ev){if(!window.confirm(f.getAttribute('data-confirm')))ev.preventDefault();});});
clock();rel();setInterval(clock,1000);setInterval(rel,30000);})();
JS;

function page($title, $body, $refresh = 0)
{
    global $environment, $editable;
    header('Content-Type: text/html; charset=utf-8');
    $project = getenv('PROJECT_NAME') ?: '';
    $zone = date_default_timezone_get();
    echo '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        . ($refresh > 0 ? '<meta http-equiv="refresh" content="' . (int) $refresh . '">' : '')
        . '<title>' . h($title) . ($project !== '' ? ' · ' . h($project) : '') . '</title><style>' . CRON_UI_STYLE . '</style></head>'
        . '<body class="env-' . h($environment) . '" data-zone="' . h($zone) . '"><header class="top"><a class="mark" href="/">CRON<small>'
        . h($project !== '' ? $project : 'scheduled jobs') . '</small></a>'
        . '<span class="sign ' . h($environment) . '" title="Environment">' . h($environment) . '</span><span class="spacer"></span>'
        . '<nav class="nav"><a class="btn" href="/">Board</a>' . ($editable ? '<a class="btn" href="/edit">Edit crontab</a>' : '') . '</nav>'
        . '<div class="clock"><b id="clock">' . h(date('H:i:s')) . '</b>' . h($zone) . '</div></header>'
        . '<main><h1>' . h($title) . '</h1>' . $body . '</main><script>' . CRON_UI_SCRIPT . '</script></body></html>';
    exit;
}

function allRuns($history, $limit = 5000)
{
    $file = "$history/history.jsonl";
    $runs = array();
    if (is_file($file)) {
        foreach (array_reverse(file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES)) as $line) {
            $run = json_decode($line, true);
            if (!is_array($run) || !isset($run['job'])) continue;
            $runs[] = $run;
            if (count($runs) >= $limit) break;
        }
    }
    return $runs; // newest first
}

function runs($history, $job = null, $limit = 50)
{
    $out = array();
    foreach (allRuns($history) as $run) {
        if ($job !== null && $run['job'] !== $job) continue;
        $out[] = $run;
        if (count($out) >= $limit) break;
    }
    return $out;
}

function jobById($id)
{
    foreach (cronFiles() as $name => $path) {
        foreach (cronJobs(file_get_contents($path), $name) as $job) if ($job['id'] === $id) return $job;
    }
    return null;
}

function stamp($ms) { return date('Y-m-d H:i:s', (int) ($ms / 1000)); }

function seconds($ms)
{
    $s = $ms / 1000;
    if ($s < 60) return round($s, 1) . ' s';
    return floor($s / 60) . ' m ' . str_pad((string) round(fmod($s, 60)), 2, '0', STR_PAD_LEFT) . ' s';
}

function relative($timestamp)
{
    $d = $timestamp - time();
    $a = abs($d);
    if ($a < 45) $t = 'now';
    elseif ($a < 3600) $t = round($a / 60) . ' min';
    elseif ($a < 172800) $t = round($a / 3600) . ' h';
    else $t = round($a / 86400) . ' d';
    $text = $t === 'now' ? 'now' : ($d > 0 ? "in $t" : "$t ago");
    return '<span class="rel" data-at="' . (int) ($timestamp * 1000) . '">' . h($text) . '</span>';
}

function flaps($timestamp)
{
    $out = '';
    foreach (str_split(date('H:i', $timestamp)) as $char) {
        $out .= '<span class="flap' . ($char === ':' ? ' sep' : '') . '">' . h($char) . '</span>';
    }
    return '<div class="flaps" aria-hidden="true">' . $out . '</div>';
}

function lamps($runs)
{
    $out = '';
    $recent = array_reverse(array_slice($runs, 0, 12)); // oldest left, newest right
    for ($i = count($recent); $i < 12; $i++) $out .= '<span class="lamp"></span>';
    foreach ($recent as $run) {
        $out .= '<span class="lamp ' . ($run['exit'] === 0 ? 'ok' : 'fail') . ($run['trigger'] === 'manual' ? ' manual' : '')
            . '" title="' . h(stamp($run['started']) . ' · exit ' . (int) $run['exit'] . ' · ' . seconds($run['duration_ms']) . ' · ' . $run['trigger']) . '"></span>';
    }
    return '<div class="lamps" role="img" aria-label="Last ' . count($recent) . ' runs">' . $out . '</div>';
}

function runForm($job, $token)
{
    global $environment;
    $confirm = in_array($environment, array('stage', 'prod'), true)
        ? ' data-confirm="' . h('Run this job on ' . strtoupper($environment) . ' now?' . "\n\n" . $job['command']) . '"' : '';
    return '<form method="post" action="/run"' . $confirm . '><input type="hidden" name="token" value="' . h($token) . '">'
        . '<input type="hidden" name="job" value="' . h($job['id']) . '"><button class="run">Run now</button></form>';
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];
if ($method === 'POST' && !hash_equals($token, isset($_POST['token']) ? (string) $_POST['token'] : '')) {
    http_response_code(403);
    page('Cron', '<div class="callout error">The form expired; reload the page.</div>');
}

if ($path === '/run' && $method === 'POST') {
    $job = jobById(isset($_POST['job']) ? (string) $_POST['job'] : '');
    if (!$job) { http_response_code(404); page('Cron', '<div class="callout error">Unknown job.</div>'); }
    exec('nohup ' . escapeshellarg(CRON_RUN) . ' ' . escapeshellarg($job['id']) . ' manual '
        . escapeshellarg($job['command']) . ' > /dev/null 2>&1 &');
    // The history page refreshes itself until a run newer than this moment is recorded.
    header('Location: /history?job=' . rawurlencode($job['id']) . '&started=' . (int) floor(microtime(true) * 1000), true, 303);
    exit;
}

if ($path === '/log') {
    $job = isset($_GET['job']) ? (string) $_GET['job'] : '';
    $file = isset($_GET['file']) ? (string) $_GET['file'] : '';
    if (!preg_match('/^[0-9a-f]{12}$/', $job) || !preg_match('/^\d{8}T\d{6}-\d+\.log$/', $file) || !is_file("$history/runs/$job/$file")) {
        http_response_code(404);
        page('Cron', '<div class="callout error">Log not found (older runs are pruned).</div>');
    }
    $meta = '';
    foreach (runs($history, $job, 1000) as $run) {
        if (isset($run['log']) && $run['log'] === $file) {
            $meta = ' · <span class="chip ' . ($run['exit'] === 0 ? 'ok' : 'fail') . '">exit ' . (int) $run['exit'] . '</span> '
                . h(stamp($run['started'])) . ' · ' . h(seconds($run['duration_ms'])) . ' · ' . h($run['trigger']);
            break;
        }
    }
    $definition = jobById($job);
    $lines = explode("\n", rtrim(file_get_contents("$history/runs/$job/$file"), "\n"));
    $body = '<p class="crumbs"><a href="/">Board</a> / <a href="/history?job=' . h($job) . '">History</a> / ' . h($file) . $meta . '</p>'
        . ($definition ? '<p class="note"><code class="cmd">' . h($definition['command']) . '</code></p>' : '')
        . '<div class="term"><ol><li>' . implode('</li><li>', array_map('h', $lines)) . '</li></ol></div>';
    page('Output', $body);
}

if ($path === '/history') {
    $id = isset($_GET['job']) ? (string) $_GET['job'] : '';
    $job = jobById($id);
    $list = runs($history, $id);
    $body = '<p class="crumbs"><a href="/">Board</a> / History</p>';
    $refresh = 0;
    if (isset($_GET['started']) && preg_match('/^\d{10,16}$/', (string) $_GET['started'])) {
        $since = (int) $_GET['started'];
        if ($list && $list[0]['started'] >= $since - 1000) {
            $body .= '<div class="callout">Finished: ' . ($list[0]['exit'] === 0 ? '<span class="ok">exit 0</span>' : '<span class="fail">exit ' . (int) $list[0]['exit'] . '</span>')
                . ' after ' . h(seconds($list[0]['duration_ms'])) . '. <a href="/log?job=' . h($id) . '&file=' . h($list[0]['log']) . '">Open the output</a></div>';
        } elseif (microtime(true) * 1000 - $since < 900000) {
            $body .= '<div class="callout wait">Started. This page refreshes every 3 seconds until the run is recorded.</div>';
            $refresh = 3;
        }
    }
    if ($job) {
        $next = cronNextRun($job['schedule'], time());
        $body .= '<section class="job"><div><code class="cmd">' . h($job['command']) . '</code>'
            . '<span class="where">' . h($job['source']) . ':' . (int) $job['line'] . '</span></div><div style="text-align:right">'
            . '<span class="expr">' . h($job['schedule']) . '</span><br><span class="last">next ' . ($next === null ? 'invalid'
                : h(date('Y-m-d H:i', $next))) . '</span></div></section>';
    }
    $done = count($list);
    $passed = 0; $total = 0; $longest = 1;
    foreach ($list as $run) {
        if ($run['exit'] === 0) $passed++;
        $total += $run['duration_ms'];
        $longest = max($longest, $run['duration_ms']);
    }
    if ($done) {
        $body .= '<div class="stats"><div class="stat"><b>' . $done . '</b><span>runs shown</span></div>'
            . '<div class="stat"><b class="' . ($passed === $done ? 'ok' : 'fail') . '">' . round(100 * $passed / $done) . '%</b><span>exit 0</span></div>'
            . '<div class="stat"><b>' . h(seconds($total / $done)) . '</b><span>average</span></div>'
            . '<div class="stat"><b>' . h(seconds($longest)) . '</b><span>longest</span></div></div>';
        $body .= '<ol class="runs">';
        foreach ($list as $run) {
            $ok = $run['exit'] === 0;
            $body .= '<li><span class="lamp ' . ($ok ? 'ok' : 'fail') . '"></span><span>' . h(stamp($run['started'])) . '</span>'
                . '<span class="trig"><span class="chip ' . ($run['trigger'] === 'manual' ? 'manual' : '') . '">' . h($run['trigger']) . '</span></span>'
                . '<span><span class="chip ' . ($ok ? 'ok' : 'fail') . '">exit ' . (int) $run['exit'] . '</span></span>'
                . '<span class="bar' . ($ok ? '' : ' fail') . '" title="' . h(seconds($run['duration_ms'])) . '"><i style="width:'
                . max(2, round(100 * $run['duration_ms'] / $longest)) . '%"></i></span>'
                . '<span style="text-align:right">' . h(seconds($run['duration_ms'])) . ' · <a href="/log?job=' . h($run['job']) . '&file=' . h($run['log']) . '">view</a></span></li>';
        }
        $body .= '</ol>';
    } else {
        $body .= '<div class="empty">No runs recorded yet.</div>';
    }
    if ($job) $body .= '<p style="margin-top:18px">' . runForm($job, $token) . '</p>';
    page('History', $body, $refresh);
}

if ($path === '/edit') {
    if (!$editable) { http_response_code(403); page('Cron', '<div class="callout error">On ' . h($environment) . ' the crontab changes only through git.</div>'); }
    $files = array('crontab', 'crontab.' . $environment);
    $name = isset($_REQUEST['file']) && in_array($_REQUEST['file'], $files, true) ? $_REQUEST['file'] : 'crontab';
    $target = CRON_DIRECTORY . '/' . $name;
    $text = is_file($target) ? file_get_contents($target) : '';
    $message = '';
    if ($method === 'POST') {
        $text = str_replace("\r\n", "\n", isset($_POST['crontab']) ? (string) $_POST['crontab'] : '');
        $problems = cronProblems($text, $name);
        if (!$problems) {
            // supercronic is the authority on what it will accept.
            $candidate = tempnam('/tmp', 'crontab');
            file_put_contents($candidate, cronWrapped($text, $name));
            exec('supercronic -test ' . escapeshellarg($candidate) . ' 2>&1', $output, $code);
            unlink($candidate);
            if ($code !== 0) $problems[] = 'supercronic: ' . implode(' ', $output);
        }
        if ($problems) {
            $message = '<div class="callout error">Not saved:<br>' . implode('<br>', array_map('h', $problems)) . '</div>';
        } elseif (@file_put_contents($target, $text) === false) {
            $message = '<div class="callout error">Not saved: ' . h($target) . ' is not writable.</div>';
        } else {
            cronCompose(cronFiles());
            if ($scheduled) {
                $pidFile = getenv('CRON_PID_FILE') ?: '/tmp/supercronic.pid';
                $pid = is_file($pidFile) ? (int) file_get_contents($pidFile) : 0;
                $reloaded = $pid > 0 && (function_exists('posix_kill') ? posix_kill($pid, 12) : exec('kill -USR2 ' . $pid) !== false);
                $applied = $reloaded ? ' and reloaded' : '; restart cron to apply it';
            } else {
                // No supercronic to reload: the jobs list follows the file, nothing runs on schedule.
                $applied = '; the schedule is off (CRON_SCHEDULE=off), so nothing runs on it';
            }
            $message = '<div class="callout">Saved' . $applied . '. Commit ' . h($name) . ' to keep the change.</div>';
        }
    }
    $tabs = '';
    foreach ($files as $file) {
        $tabs .= '<a class="' . ($file === $name ? 'on' : '') . '" href="/edit?file=' . h($file) . '">' . h($file) . '</a>';
    }
    $body = '<p class="crumbs"><a href="/">Board</a> / Edit</p>' . $message
        . '<form method="post" class="editor"><div><input type="hidden" name="token" value="' . h($token) . '"><input type="hidden" name="file" value="' . h($name) . '">'
        . '<nav class="tabs">' . $tabs . '</nav><textarea name="crontab" spellcheck="false" rows="20">' . h($text) . '</textarea>'
        . '<div class="form-foot"><span class="note" style="margin:0">' . h(CRON_DIRECTORY . '/' . $name) . ($name === 'crontab' ? ' · every environment' : ' · ' . h($environment) . ' only') . '</span>'
        . '<button class="run">Validate and save</button></div></div>'
        . '<aside class="cheat"><strong>One job per line</strong><pre>.---------- minute   0-59
| .-------- hour     0-23
| | .------ day      1-31
| | | .---- month    1-12
| | | | .-- weekday  0-6 (0 = Sunday)
| | | | |
* * * * * command</pre>Or <code>@hourly</code>, <code>@daily</code>, <code>@weekly</code>, <code>@monthly</code>, <code>@yearly</code> then the command. '
        . 'No user column. Lines starting with <code>#</code> are comments. supercronic checks the file before it is saved.</aside></form>';
    page("Edit $name", $body);
}

if ($path !== '/') { http_response_code(404); page('Cron', '<div class="callout error">Not found.</div>'); }

$jobs = array();
foreach (cronFiles() as $name => $file) $jobs = array_merge($jobs, cronJobs(file_get_contents($file), $name));
foreach ($jobs as $i => $job) {
    $jobs[$i]['next'] = cronNextRun($job['schedule'], time());
    $jobs[$i]['order'] = $i;
}
usort($jobs, function ($a, $b) {
    $x = $a['next'] === null ? PHP_INT_MAX : $a['next'];
    $y = $b['next'] === null ? PHP_INT_MAX : $b['next'];
    return $x === $y ? $a['order'] - $b['order'] : ($x < $y ? -1 : 1);
});
$byJob = array();
foreach (allRuns($history) as $run) {
    if (!isset($byJob[$run['job']])) $byJob[$run['job']] = array();
    if (count($byJob[$run['job']]) < 12) $byJob[$run['job']][] = $run;
}

$body = '<p class="note">' . count($jobs) . ' ' . (count($jobs) === 1 ? 'job' : 'jobs') . ' from ' . h(CRON_DIRECTORY) . '. '
    . ($editable ? 'Edits are checked by supercronic and saved to the crontab; commit them.' : 'On ' . h($environment) . ' the crontab changes only through git.') . '</p>';
if (!$scheduled) {
    $body .= '<div class="tape"><span class="off">Schedule off</span><small><span>CRON_SCHEDULE=off: no job runs on its schedule. '
        . '"Run now" still runs a job and records it.</span></small></div>';
}
$body .= '<table class="board"><thead><tr><th>' . ($scheduled ? 'Next run' : 'Next run (schedule off)') . '</th><th>Job</th><th>Schedule</th><th>Last runs</th><th></th></tr></thead><tbody>';
if (!$jobs) $body .= '<tr><td colspan="5"><div class="empty">No jobs in ' . h(CRON_DIRECTORY) . '/crontab.</div></td></tr>';
foreach ($jobs as $i => $job) {
    $next = $job['next'];
    $recent = isset($byJob[$job['id']]) ? $byJob[$job['id']] : array();
    $last = $recent ? $recent[0] : null;
    $state = $last === null ? 'idle' : ($last['exit'] === 0 ? '' : 'failing');
    if ($next === null) {
        $when = '<span class="fail">invalid schedule</span>';
    } elseif ($scheduled) {
        $when = flaps($next) . '<time class="next" datetime="' . h(date('c', $next)) . '">' . h(date('Y-m-d H:i', $next)) . '</time>' . relative($next);
    } else {
        $when = '<div class="halted">' . flaps($next) . '</div><span class="off">off</span><br><small>would be ' . h(date('Y-m-d H:i', $next)) . '</small>';
    }
    $summary = $last === null ? 'never run'
        : '<span class="' . ($last['exit'] === 0 ? 'ok' : 'fail') . '">exit ' . (int) $last['exit'] . '</span> · '
            . relative((int) ($last['started'] / 1000)) . ' · ' . h(seconds($last['duration_ms'])) . ' · ' . h($last['trigger']);
    $body .= '<tr class="' . $state . '" style="--i:' . (int) $i . '"><td>' . $when . '</td>'
        . '<td><code class="cmd">' . h($job['command']) . '</code><span class="where">' . h($job['source']) . ':' . (int) $job['line'] . '</span></td>'
        . '<td><span class="expr">' . h($job['schedule']) . '</span></td>'
        . '<td>' . lamps($recent) . '<span class="last">' . $summary . '</span></td>'
        . '<td><span class="actions">' . runForm($job, $token) . '<a class="btn" href="/history?job=' . h($job['id']) . '">History</a></span></td></tr>';
}
page('Departures', $body . '</tbody></table>');
