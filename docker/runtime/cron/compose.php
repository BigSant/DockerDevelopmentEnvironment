<?php
// The project crontab (git) is the only source of truth: this turns it into the crontab supercronic
// runs, with every job wrapped by cron-run.sh so each run is recorded. Used by the cron UI too.

define('CRON_DIRECTORY', getenv('CRON_DIRECTORY') ?: '/etc/cron.d');
define('CRON_RUN', getenv('CRON_RUN') ?: '/opt/setup/runtime/cron/cron-run.sh');
const CRON_MACROS = array('@yearly' => '0 0 1 1 *', '@annually' => '0 0 1 1 *', '@monthly' => '0 0 1 * *',
                          '@weekly' => '0 0 * * 0', '@daily' => '0 0 * * *', '@midnight' => '0 0 * * *',
                          '@hourly' => '0 * * * *');

function cronFiles($directory = CRON_DIRECTORY, $environment = null)
{
    $environment = $environment === null ? getenv('ENV') : $environment;
    $files = array();
    foreach (array('crontab', 'crontab.' . $environment) as $name) {
        if (is_file("$directory/$name")) $files[$name] = "$directory/$name";
    }
    return $files;
}

/**
 * Jobs of one crontab text: id, schedule, command, line; null schedule marks an unsupported line.
 * The id follows the command, so history survives a schedule change; a command repeated in the
 * same file (other schedules) gets its occurrence number, so its runs are not mixed up.
 */
function cronJobs($text, $source)
{
    $jobs = array();
    $seen = array();
    foreach (preg_split('/\r?\n/', $text) as $number => $line) {
        $trimmed = trim($line);
        if ($trimmed === '' || $trimmed[0] === '#' || preg_match('/^[A-Za-z_][A-Za-z0-9_]*=/', $trimmed)) continue;
        if ($trimmed[0] === '@') {
            $parts = preg_split('/\s+/', $trimmed, 2);
            $schedule = $parts[0];
            $command = isset($parts[1]) ? $parts[1] : '';
        } else {
            $parts = preg_split('/\s+/', $trimmed, 6);
            $schedule = implode(' ', array_slice($parts, 0, 5));
            $command = isset($parts[5]) ? $parts[5] : '';
        }
        $seen[$command] = isset($seen[$command]) ? $seen[$command] + 1 : 1;
        $key = $seen[$command] === 1 ? $command : $command . "\0" . $seen[$command];
        $jobs[] = array('id' => substr(sha1($key), 0, 12), 'schedule' => $schedule, 'command' => $command,
                        'source' => $source, 'line' => $number + 1, 'name' => cronJobName($command));
    }
    return $jobs;
}

/**
 * A readable name for a job that never shows a secret: the program and up to three of its subcommands, targets or
 * script names ("make run-tasks-daily", "php console app:sync", "curl shop.example/cron"), then the line's trailing
 * comment. Wrappers (flock, nice, timeout, env, cd … &&), options and their values and redirections are left out; a
 * URL keeps its host and path (no user, password or query), a key=value keeps its key, a long token becomes "…".
 */
function cronJobName($command)
{
    $mask = function ($word) {
        if (preg_match('#^[a-z][a-z0-9+.-]*://#i', $word)) {
            $parts = parse_url($word);
            return isset($parts['host']) ? $parts['host'] . (isset($parts['path']) ? rtrim($parts['path'], '/') : '') : '…';
        }
        if (preg_match('/^([A-Za-z_][A-Za-z0-9_.-]*)=/', $word, $match)) return $match[1] . '=…';
        if (strlen($word) >= 20 && preg_match('/[0-9]/', $word) && preg_match('/[A-Za-z]/', $word) && !preg_match('#[/:]#', $word)) return '…';
        return $word;
    };
    $comment = '';
    if (preg_match('/(?:^|\s)#\s*(.*)$/', $command, $match)) {
        $comment = implode(' ', array_map($mask, preg_split('/\s+/', trim($match[1]), -1, PREG_SPLIT_NO_EMPTY)));
        $command = substr($command, 0, -strlen($match[0]));
    }
    $wrappers = array('flock', 'nice', 'ionice', 'timeout', 'nohup', 'env', 'sudo', 'chronic', 'time', 'exec');
    // Options that take the next word as their value.
    $valued = array('-C', '--directory', '-f', '--file', '--makefile', '-d', '-c', '-u', '--user', '-o', '--output', '-O',
        '-H', '--header', '-X', '--request', '-A', '--user-agent', '-e', '-n', '-w', '-E', '--lock-file', '-g', '-s', '-k',
        '--adjustment', '--class', '--signal', '--kill-after');
    $name = '';
    foreach (preg_split('/\s*(?:&&|\|\||;|\|)\s*/', trim($command)) as $segment) {
        $words = preg_split('/\s+/', trim(str_replace(array('"', "'"), '', $segment)), -1, PREG_SPLIT_NO_EMPTY);
        $program = null;
        $arguments = array();
        for ($index = 0; $index < count($words); $index++) {
            $word = $words[$index];
            if (preg_match('/^\d*[<>]/', $word)) {
                // A redirection, and its target when it is a word of its own.
                if (preg_match('/^\d*[<>]+&?$/', $word)) $index++;
                continue;
            }
            if ($program === null) {
                if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*=/', $word)) continue;
                $base = basename($word);
                if (in_array($base, $wrappers, true)) {
                    // A wrapper's options (nice -n 10), flock's lock file and timeout's duration come before the command.
                    while (isset($words[$index + 1])) {
                        $next = $words[$index + 1];
                        if ($next[0] === '-') {
                            $index += strpos($next, '=') === false && in_array($next, $valued, true) ? 2 : 1;
                        } elseif (($base === 'flock' && strpos($next, '/') !== false) || ($base === 'timeout' && preg_match('/^\d+[smhd]?$/', $next))) {
                            $index++;
                        } else {
                            break;
                        }
                    }
                    continue;
                }
                $program = $base;
                continue;
            }
            if ($word[0] === '-') {
                if (strpos($word, '=') === false && in_array($word, $valued, true)) $index++;
                continue;
            }
            $shown = $mask($word);
            // A path is named by its last part (the script), a URL by its host and path.
            if ($shown === $word && strpos($word, '/') !== false) $shown = basename($word);
            if ($shown !== '' && count($arguments) < 3) $arguments[] = $shown;
        }
        // cd only moves; the job is what follows it.
        if ($program === null || in_array($program, array('cd', 'true', ':'), true)) continue;
        $name = trim($program . ' ' . implode(' ', $arguments));
        break;
    }
    if ($name === '') $name = 'job';
    if (strlen($name) > 60) $name = substr($name, 0, 59) . '…';
    if ($comment !== '') $name .= ' · ' . (strlen($comment) > 40 ? substr($comment, 0, 39) . '…' : $comment);
    return $name;
}

/** Problems of one crontab text (empty when every job is a 5-field or macro schedule with a command). */
function cronProblems($text, $source)
{
    $problems = array();
    foreach (cronJobs($text, $source) as $job) {
        if ($job['command'] === '') $problems[] = "$source:{$job['line']}: no command after the schedule";
        elseif (cronFields($job['schedule']) === null) $problems[] = "$source:{$job['line']}: invalid schedule \"{$job['schedule']}\" (use 5 fields or @hourly, @daily, @weekly, @monthly, @yearly)";
    }
    return $problems;
}

function cronWrapped($text, $source)
{
    $jobs = array();
    foreach (cronJobs($text, $source) as $job) $jobs[$job['line']] = $job;
    $output = array();
    foreach (preg_split('/\r?\n/', $text) as $number => $line) {
        if (!isset($jobs[$number + 1])) { $output[] = $line; continue; }
        $job = $jobs[$number + 1];
        $output[] = $job['schedule'] . ' ' . CRON_RUN . ' ' . $job['id'] . ' scheduled ' . escapeshellarg($job['command']);
    }
    return implode("\n", $output) . "\n";
}

function cronCompose($files, $target = null)
{
    $target = $target ?: (getenv('CRON_CRONTAB') ?: '/tmp/crontab');
    $crontab = '';
    $jobs = array();
    foreach ($files as $name => $path) {
        $text = file_get_contents($path);
        $crontab .= "# $name\n" . cronWrapped($text, $name);
        $jobs = array_merge($jobs, cronJobs($text, $name));
    }
    file_put_contents("$target.tmp", $crontab);
    rename("$target.tmp", $target);
    return $jobs;
}

/** Allowed values per field, or null for an invalid schedule. */
function cronFields($schedule)
{
    if (isset(CRON_MACROS[strtolower($schedule)])) $schedule = CRON_MACROS[strtolower($schedule)];
    $parts = preg_split('/\s+/', trim($schedule));
    if (count($parts) !== 5) return null;
    $ranges = array(array(0, 59), array(0, 23), array(1, 31), array(1, 12), array(0, 7));
    $names = array(3 => array('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'),
                   4 => array('SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'));
    $fields = array();
    foreach ($parts as $index => $part) {
        list($low, $high) = $ranges[$index];
        $values = array();
        foreach (explode(',', strtoupper($part)) as $item) {
            if (isset($names[$index])) {
                foreach ($names[$index] as $position => $name) $item = str_replace($name, (string) ($position + ($index === 3 ? 1 : 0)), $item);
            }
            if (!preg_match('/^(\*|\d+(?:-\d+)?)(?:\/(\d+))?$/', $item, $match)) return null;
            $step = isset($match[2]) ? (int) $match[2] : 1;
            if ($step < 1) return null;
            if ($match[1] === '*') { $from = $low; $to = $high; }
            else {
                $bounds = explode('-', $match[1]);
                $from = (int) $bounds[0];
                $to = isset($bounds[1]) ? (int) $bounds[1] : (isset($match[2]) ? $high : $from);
            }
            if ($from < $low || $to > $high || $from > $to) return null;
            for ($value = $from; $value <= $to; $value += $step) $values[$value] = true;
        }
        if ($index === 4 && isset($values[7])) $values[0] = true;
        $fields[] = array('values' => $values, 'any' => $parts[$index][0] === '*');
    }
    return $fields;
}

/** Next run after $from (a timestamp), within a year; null when the schedule never fires. */
function cronNextRun($schedule, $from)
{
    $fields = cronFields($schedule);
    if ($fields === null) return null;
    $time = (int) (floor($from / 60) * 60) + 60;
    $limit = $time + 366 * 86400;
    while ($time < $limit) {
        list($minute, $hour, $day, $month, $weekday) = array_map('intval', explode(' ', date('i G j n w', $time)));
        if (!isset($fields[3]['values'][$month])) { $time = mktime(0, 0, 0, $month + 1, 1, (int) date('Y', $time)); continue; }
        // Cron runs when either day field matches if both are restricted.
        $dayMatch = $fields[2]['any'] || $fields[4]['any']
            ? isset($fields[2]['values'][$day]) && isset($fields[4]['values'][$weekday])
            : isset($fields[2]['values'][$day]) || isset($fields[4]['values'][$weekday]);
        if (!$dayMatch) { $time = mktime(0, 0, 0, $month, $day + 1, (int) date('Y', $time)); continue; }
        if (!isset($fields[1]['values'][$hour])) { $time = mktime($hour + 1, 0, 0, $month, $day, (int) date('Y', $time)); continue; }
        if (isset($fields[0]['values'][$minute])) return $time;
        $time += 60;
    }
    return null;
}

/** Whether the cron-run.sh of a running marker is still alive: its PID runs cron-run.sh for that job. */
function cronRunAlive($run)
{
    $pid = isset($run['pid']) ? (int) $run['pid'] : 0;
    if ($pid <= 1) return false;
    $command = @file_get_contents("/proc/$pid/cmdline");
    return is_string($command) && strpos($command, 'cron-run.sh') !== false && strpos($command, (string) $run['job']) !== false;
}

/** Markers cron-run.sh writes while a job runs ($history/running/<job>-<pid>.json), newest first; alive ones only. */
function cronRunning($history, $alive = true)
{
    $runs = array();
    foreach (glob("$history/running/*.json") ?: array() as $file) {
        $run = json_decode((string) @file_get_contents($file), true);
        if (!is_array($run) || !isset($run['job'], $run['started'], $run['log'])) continue;
        if (cronRunAlive($run) !== $alive) continue;
        $run['marker'] = $file;
        $runs[] = $run;
    }
    usort($runs, function ($a, $b) { return $a['started'] === $b['started'] ? 0 : ($a['started'] < $b['started'] ? 1 : -1); });
    return $runs;
}

/**
 * Markers whose cron-run.sh is gone (the container stopped mid-run, the wrapper was killed) become history
 * entries with exit null and interrupted: the run ended without an exit code. Their output stays; the
 * duration is up to the log's last write. Returns how many were closed.
 */
function cronCloseInterrupted($history)
{
    $recorded = array();
    $file = "$history/history.jsonl";
    if (is_file($file)) {
        foreach (file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) as $line) {
            $run = json_decode($line, true);
            if (is_array($run) && isset($run['job'], $run['log'])) $recorded[$run['job'] . '/' . $run['log']] = true;
        }
    }
    $closed = 0;
    foreach (cronRunning($history, false) as $run) {
        // cron-run.sh records the run before it removes the marker: a recorded one only lost its marker.
        if (!isset($recorded[$run['job'] . '/' . $run['log']])) {
            $log = "$history/runs/{$run['job']}/{$run['log']}";
            $ended = is_file($log) ? filemtime($log) * 1000 : (int) $run['started'];
            file_put_contents($file, json_encode(array('job' => $run['job'], 'trigger' => $run['trigger'],
                'started' => (int) $run['started'], 'duration_ms' => max(0, $ended - (int) $run['started']), 'exit' => null,
                'log' => $run['log'], 'interrupted' => true)) . "\n", FILE_APPEND | LOCK_EX);
            $closed++;
        }
        @unlink($run['marker']);
    }
    return $closed;
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    $closed = cronCloseInterrupted(getenv('CRON_HISTORY_DIR') ?: '/var/lib/setup-cron');
    if ($closed) echo "Cron: $closed run(s) interrupted by the last stop recorded without an exit code\n";
    $jobs = cronCompose(cronFiles());
    foreach (cronFiles() as $name => $path) {
        foreach (cronProblems(file_get_contents($path), $name) as $problem) fwrite(STDERR, "Warning: $problem\n");
    }
    echo 'Cron: ' . count($jobs) . " jobs from " . implode(', ', array_keys(cronFiles()) ?: array('no crontab')) . "\n";
}
