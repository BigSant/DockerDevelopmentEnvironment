<?php
// Thin cron UI: jobs with their next run, "run now", run history and (local/test only) crontab editing.
// Reached only through nginx-proxy with the CRON_ALLOWED_IPS / ALLOWED_IPS allowlist; no docker.sock.
require __DIR__ . '/../compose.php';

$history = getenv('CRON_HISTORY_DIR') ?: '/var/lib/setup-cron';
$editable = in_array(getenv('ENV'), array('local', 'test'), true);
$tokenFile = getenv('CRON_UI_TOKEN_FILE') ?: '/tmp/cron-ui-token';
if (!is_file($tokenFile)) file_put_contents($tokenFile, bin2hex(random_bytes(16)));
$token = trim(file_get_contents($tokenFile));

function h($value) { return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8'); }

function page($title, $body)
{
    header('Content-Type: text/html; charset=utf-8');
    echo '<!doctype html><html><head><meta charset="utf-8"><title>' . h($title) . '</title><style>'
        . 'body{font:14px system-ui,sans-serif;margin:2em;color:#222}table{border-collapse:collapse;width:100%}'
        . 'td,th{border-bottom:1px solid #ddd;padding:.4em;text-align:left;vertical-align:top}code,pre{font:12px monospace}'
        . 'pre{background:#f6f6f6;padding:1em;overflow:auto}.ok{color:#080}.fail{color:#b00}.error{background:#fee;padding:.6em}'
        . 'textarea{width:100%;font:13px monospace}button{cursor:pointer}</style></head><body>'
        . '<h1>' . h($title) . '</h1>' . $body . '</body></html>';
    exit;
}

function runs($history, $job = null, $limit = 50)
{
    $file = "$history/history.jsonl";
    $runs = array();
    if (is_file($file)) {
        foreach (array_reverse(file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES)) as $line) {
            $run = json_decode($line, true);
            if (!is_array($run) || ($job !== null && $run['job'] !== $job)) continue;
            $runs[] = $run;
            if (count($runs) >= $limit) break;
        }
    }
    return $runs;
}

function jobById($id)
{
    foreach (cronFiles() as $name => $path) {
        foreach (cronJobs(file_get_contents($path), $name) as $job) if ($job['id'] === $id) return $job;
    }
    return null;
}

function status($run)
{
    if (!$run) return '—';
    return '<span class="' . ($run['exit'] === 0 ? 'ok' : 'fail') . '">exit ' . (int) $run['exit'] . '</span> '
        . h(date('Y-m-d H:i:s', (int) ($run['started'] / 1000))) . ', ' . h(round($run['duration_ms'] / 1000, 1)) . ' s, ' . h($run['trigger']);
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];
if ($method === 'POST' && !hash_equals($token, isset($_POST['token']) ? (string) $_POST['token'] : '')) {
    http_response_code(403);
    page('Cron', '<p class="error">The form expired; reload the page.</p>');
}

if ($path === '/run' && $method === 'POST') {
    $job = jobById(isset($_POST['job']) ? (string) $_POST['job'] : '');
    if (!$job) { http_response_code(404); page('Cron', '<p class="error">Unknown job.</p>'); }
    exec('nohup ' . escapeshellarg(CRON_RUN) . ' ' . escapeshellarg($job['id']) . ' manual '
        . escapeshellarg($job['command']) . ' > /dev/null 2>&1 &');
    header('Location: /history?job=' . rawurlencode($job['id']) . '&started=1', true, 303);
    exit;
}

if ($path === '/log') {
    $job = isset($_GET['job']) ? (string) $_GET['job'] : '';
    $file = isset($_GET['file']) ? (string) $_GET['file'] : '';
    if (!preg_match('/^[0-9a-f]{12}$/', $job) || !preg_match('/^\d{8}T\d{6}-\d+\.log$/', $file) || !is_file("$history/runs/$job/$file")) {
        http_response_code(404);
        page('Cron', '<p class="error">Log not found (older runs are pruned).</p>');
    }
    page("Output $file", '<p><a href="/history?job=' . h($job) . '">← history</a></p><pre>' . h(file_get_contents("$history/runs/$job/$file")) . '</pre>');
}

if ($path === '/history') {
    $id = isset($_GET['job']) ? (string) $_GET['job'] : '';
    $job = jobById($id);
    $body = '<p><a href="/">← jobs</a></p>';
    if (isset($_GET['started'])) $body .= '<p class="ok">Started; reload to see the run when it finishes.</p>';
    if ($job) $body .= '<p><code>' . h($job['schedule']) . '</code> <code>' . h($job['command']) . '</code></p>';
    $body .= '<table><tr><th>Started</th><th>Trigger</th><th>Exit</th><th>Duration</th><th>Output</th></tr>';
    foreach (runs($history, $id) as $run) {
        $body .= '<tr><td>' . h(date('Y-m-d H:i:s', (int) ($run['started'] / 1000))) . '</td><td>' . h($run['trigger'])
            . '</td><td class="' . ($run['exit'] === 0 ? 'ok' : 'fail') . '">' . (int) $run['exit'] . '</td><td>'
            . h(round($run['duration_ms'] / 1000, 1)) . ' s</td><td><a href="/log?job=' . h($run['job']) . '&file=' . h($run['log']) . '">view</a></td></tr>';
    }
    page('Cron history', $body . '</table>');
}

if ($path === '/edit') {
    if (!$editable) { http_response_code(403); page('Cron', '<p class="error">On ' . h(getenv('ENV')) . ' the crontab changes only through git.</p>'); }
    $files = array('crontab', 'crontab.' . getenv('ENV'));
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
            $message = '<div class="error">Not saved:<br>' . implode('<br>', array_map('h', $problems)) . '</div>';
        } elseif (@file_put_contents($target, $text) === false) {
            $message = '<div class="error">Not saved: ' . h($target) . ' is not writable.</div>';
        } else {
            cronCompose(cronFiles());
            $pidFile = getenv('CRON_PID_FILE') ?: '/tmp/supercronic.pid';
            $pid = is_file($pidFile) ? (int) file_get_contents($pidFile) : 0;
            $reloaded = $pid > 0 && (function_exists('posix_kill') ? posix_kill($pid, 12) : exec('kill -USR2 ' . $pid) !== false);
            $message = '<p class="ok">Saved' . ($reloaded ? ' and reloaded' : '; restart cron to apply it') . '. Commit '
                . h($name) . ' to keep the change.</p>';
        }
    }
    $tabs = implode(' · ', array_map(function ($file) { return '<a href="/edit?file=' . h($file) . '">' . h($file) . '</a>'; }, $files));
    page("Edit $name", '<p><a href="/">← jobs</a> · ' . $tabs . '</p>' . $message
        . '<form method="post"><input type="hidden" name="token" value="' . h($token) . '"><input type="hidden" name="file" value="' . h($name) . '">'
        . '<textarea name="crontab" rows="20">' . h($text) . '</textarea>'
        . '<p>Five fields (minute hour day month weekday) or @hourly, @daily, @weekly, @monthly, @yearly, then the command.</p>'
        . '<button>Validate and save</button></form>');
}

if ($path !== '/') { http_response_code(404); page('Cron', '<p class="error">Not found.</p>'); }

$body = '<p>Environment <strong>' . h(getenv('ENV')) . '</strong>; times in ' . h(date_default_timezone_get()) . '. '
    . ($editable ? '<a href="/edit">Edit crontab</a>' : 'The crontab changes only through git.') . '</p>';
$body .= '<table><tr><th>Schedule</th><th>Next run</th><th>Command</th><th>Last run</th><th></th></tr>';
$jobs = array();
foreach (cronFiles() as $name => $file) $jobs = array_merge($jobs, cronJobs(file_get_contents($file), $name));
if (!$jobs) $body .= '<tr><td colspan="5">No jobs in ' . h(CRON_DIRECTORY) . '/crontab.</td></tr>';
foreach ($jobs as $job) {
    $next = cronNextRun($job['schedule'], time());
    $last = runs($history, $job['id'], 1);
    $body .= '<tr><td><code>' . h($job['schedule']) . '</code><br><small>' . h($job['source']) . ':' . (int) $job['line'] . '</small></td><td>'
        . ($next ? h(date('Y-m-d H:i', $next)) : '<span class="fail">invalid</span>') . '</td><td><code>' . h($job['command']) . '</code></td><td>'
        . status($last ? $last[0] : null) . '</td><td><form method="post" action="/run"><input type="hidden" name="token" value="' . h($token) . '">'
        . '<input type="hidden" name="job" value="' . h($job['id']) . '"><button>Run now</button></form> <a href="/history?job=' . h($job['id']) . '">history</a></td></tr>';
}
page('Cron', $body . '</table>');
