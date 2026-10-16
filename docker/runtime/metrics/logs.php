<?php
// Logs tab: the log collector's files (read-only volume, METRICS_LOGS_DIR) by service and day, the end of one file
// filtered by level and text, and the file itself to download. A file is named only by "<service>/<kind>-<day>.log"
// or ".log.gz" matched against a fixed pattern and found by listing the directory, never by a path from the
// request. Files are read as streams with a bounded number of lines kept, gzipped ones too.
require_once __DIR__ . '/dashboard.php';

const LOGS_TAIL_LINES = array(100, 500, 2000);
// A shown line is cut here; the download keeps it whole.
const LOGS_SHOWN_BYTES = 4096;
const LOGS_LEVELS = array(
    '' => array('All lines', ''),
    'error' => array('Errors', '/\b(error|fatal|crit|critical|alert|emerg|exception)\b/i'),
    'warning' => array('Warnings', '/\b(warn|warning)\b/i'),
    '5xx' => array('5xx responses', '/" 5\d\d /'),
    '4xx' => array('4xx responses', '/" 4\d\d /'),
);

// [service => [[name, kind, day, bytes, gzipped], ...]] newest day first: the collector's regular files only, never
// a symbolic link (which could lead out of its directory).
function logs_tab_files()
{
    $files = array();
    $root = realpath(metrics_logs_directory());
    foreach (glob(metrics_logs_directory() . '/*/*-????-??-??.log*') ?: array() as $path) {
        if (!preg_match('#/([a-z0-9][a-z0-9_-]*)/((access|output|error)-(\d{4}-\d\d-\d\d)\.log(\.gz)?)$#', $path, $match)) continue;
        if (is_link($path) || is_link(dirname($path)) || !is_file($path) || $root === false
            || strpos((string) realpath($path), $root . '/') !== 0) continue;
        $files[$match[1]][] = array($match[2], $match[3], $match[4], (int) @filesize($path), isset($match[5]) && $match[5] !== '');
    }
    ksort($files);
    foreach ($files as &$list) {
        usort($list, function ($a, $b) { return strcmp($b[2], $a[2]) ?: strcmp($a[1], $b[1]); });
    }
    return $files;
}

// The file a request names, or null: "<service>/<name>" of a listed file only.
function logs_tab_file($asked)
{
    if (!is_string($asked) || !preg_match('#^([a-z0-9][a-z0-9_-]*)/((?:access|output|error)-\d{4}-\d\d-\d\d\.log(?:\.gz)?)$#', $asked, $match)) {
        return null;
    }
    $files = logs_tab_files();
    if (!isset($files[$match[1]])) return null;
    foreach ($files[$match[1]] as $file) {
        if ($file[0] === $match[2]) return array(metrics_logs_directory() . '/' . $asked, $file);
    }
    return null;
}

function logs_tab_size($bytes)
{
    foreach (array('B', 'KB', 'MB', 'GB') as $unit) {
        if ($bytes < 1024 || $unit === 'GB') return ($unit === 'B' ? $bytes : number_format($bytes, $bytes < 10 ? 1 : 0, '.', '')) . ' ' . $unit;
        $bytes /= 1024;
    }
}

// The last $count lines that match, oldest first. A plain file is read backwards in chunks until enough lines are
// found; a gzipped one forwards, keeping only the last $count matches.
function logs_tab_tail($path, $gzipped, $count, $match)
{
    if ($gzipped) {
        $handle = @gzopen($path, 'rb');
        if ($handle === false) throw new RuntimeException('cannot open ' . basename($path));
        $kept = array();
        while (!gzeof($handle)) {
            $line = gzgets($handle, 1048576);
            if ($line === false) break;
            $line = rtrim($line, "\r\n");
            if (!$match($line)) continue;
            $kept[] = substr($line, 0, LOGS_SHOWN_BYTES + 1);
            if (count($kept) > $count) array_shift($kept);
        }
        gzclose($handle);
        return $kept;
    }
    $handle = @fopen($path, 'rb');
    if ($handle === false) throw new RuntimeException('cannot open ' . basename($path));
    $position = (int) filesize($path);
    $rest = '';
    $found = array();
    while ($position > 0 && count($found) < $count) {
        $read = min(65536, $position);
        $position -= $read;
        fseek($handle, $position);
        $chunk = fread($handle, $read) . $rest;
        $lines = explode("\n", $chunk);
        // The first piece may be the end of a line that starts in the previous chunk.
        $rest = $position > 0 ? array_shift($lines) : '';
        if (strlen($rest) > 1048576) $rest = substr($rest, -1048576);
        for ($index = count($lines) - 1; $index >= 0 && count($found) < $count; $index--) {
            $line = rtrim($lines[$index], "\r");
            if ($line !== '' && $match($line)) $found[] = substr($line, 0, LOGS_SHOWN_BYTES + 1);
        }
    }
    if ($position === 0 && $rest !== '' && count($found) < $count && $match($rest)) $found[] = substr($rest, 0, LOGS_SHOWN_BYTES + 1);
    fclose($handle);
    return array_reverse($found);
}

function logs_tab_list()
{
    $files = logs_tab_files();
    if (!is_dir(metrics_logs_directory())) {
        return '<div class="empty">No log collector files: the log collector runs with SERVICES=metrics (service metrics-logs).</div>';
    }
    if (!$files) return '<div class="empty">No log files yet.</div>';
    // A table per service, so a file is never read as another service's.
    $body = '<div class="panels">';
    foreach ($files as $service => $list) {
        $body .= '<section class="panel"><h2>' . h($service) . '</h2><table class="list"><thead><tr><th>Day</th><th>File</th>'
            . '<th class="num">Size</th><th></th></tr></thead><tbody>';
        foreach ($list as $file) {
            list($name, $kind, $day, $bytes) = $file;
            $id = rawurlencode($service . '/' . $name);
            $body .= '<tr><td class="id">' . h($day) . '</td><td class="id"><a href="/logs/view?file=' . $id . '">' . h($name) . '</a></td>'
                . '<td class="num" data-v="' . (int) $bytes . '">' . h(logs_tab_size($bytes)) . '</td>'
                . '<td class="id"><a class="quiet" href="/logs/download?file=' . $id . '">download</a></td></tr>';
        }
        $body .= '</tbody></table></section>';
    }
    return $body . '</div>';
}

function logs_tab_view($asked, $lines, $level, $text)
{
    $file = logs_tab_file($asked);
    if ($file === null) {
        http_response_code(404);
        return '<div class="callout error">No such log file.</div>' . logs_tab_list();
    }
    list($path, $info) = $file;
    $pattern = LOGS_LEVELS[$level][1];
    $match = function ($line) use ($pattern, $text) {
        return ($pattern === '' || preg_match($pattern, $line)) && ($text === '' || stripos($line, $text) !== false);
    };
    $found = logs_tab_tail($path, $info[4], $lines, $match);
    $id = h($asked);
    $form = '<form method="get" action="/logs/view" class="bar"><input type="hidden" name="file" value="' . $id . '">'
        . '<a href="/logs/">&larr; all files</a><b>' . $id . '</b><span class="stamp">' . h(logs_tab_size($info[3])) . '</span>'
        . '<select name="level">';
    foreach (LOGS_LEVELS as $key => $option) {
        $form .= '<option value="' . h($key) . '"' . ($key === $level ? ' selected' : '') . '>' . h($option[0]) . '</option>';
    }
    $form .= '</select><select name="lines">';
    foreach (LOGS_TAIL_LINES as $option) {
        $form .= '<option' . ($option === $lines ? ' selected' : '') . '>' . $option . '</option>';
    }
    $form .= '</select><input name="q" value="' . h($text) . '" placeholder="text"><button>Show</button>'
        . '<span class="spacer"></span><a href="/logs/download?file=' . rawurlencode($asked) . '">download</a></form>';
    if (!$found) return $form . '<div class="empty">No matching lines.</div>';
    $shown = '';
    foreach ($found as $line) {
        $shown .= h(strlen($line) > LOGS_SHOWN_BYTES ? substr($line, 0, LOGS_SHOWN_BYTES) . ' …' : $line) . "\n";
    }
    return $form . '<p class="stamp">The last ' . count($found) . ' matching lines, oldest first.</p><pre class="log">' . $shown . '</pre>';
}

// The file as it is: gzip for a finished day (zcat, zless read it), text for today's.
function logs_tab_download($asked)
{
    $file = logs_tab_file($asked);
    if ($file === null) metrics_respond(404, "no such log file\n");
    list($path) = $file;
    $handle = @fopen($path, 'rb');
    if ($handle === false) metrics_respond(404, "no such log file\n");
    // Today's file grows while it is sent: exactly the bytes it had when the download began.
    $size = (int) fstat($handle)['size'];
    header('Content-Type: ' . ($file[1][4] ? 'application/gzip' : 'text/plain; charset=utf-8'));
    header('Content-Disposition: attachment; filename="' . basename($asked) . '"');
    header('Content-Length: ' . $size);
    header('Cache-Control: no-store');
    $output = fopen('php://output', 'wb');
    stream_copy_to_stream($handle, $output, $size);
    fclose($handle);
    exit;
}

function logs_tab_route($path)
{
    if ($path === '/logs/download') logs_tab_download(isset($_GET['file']) ? $_GET['file'] : null);
    if ($path === '/logs/view') {
        $lines = isset($_GET['lines']) && in_array((int) $_GET['lines'], LOGS_TAIL_LINES, true) ? (int) $_GET['lines'] : LOGS_TAIL_LINES[0];
        $level = isset($_GET['level']) && is_string($_GET['level']) && isset(LOGS_LEVELS[$_GET['level']]) ? $_GET['level'] : '';
        $text = isset($_GET['q']) && is_string($_GET['q']) ? substr($_GET['q'], 0, 200) : '';
        $body = logs_tab_view(isset($_GET['file']) ? $_GET['file'] : null, $lines, $level, $text);
        dashboard_page('/logs/', 'Logs', $body, 0);
    }
    if ($path === '/logs/' || $path === '/logs') dashboard_page('/logs/', 'Logs', logs_tab_list(), 0);
    metrics_respond(404, "not found\n");
}
