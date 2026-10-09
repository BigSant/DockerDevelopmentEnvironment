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
                        'source' => $source, 'line' => $number + 1);
    }
    return $jobs;
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

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    $jobs = cronCompose(cronFiles());
    foreach (cronFiles() as $name => $path) {
        foreach (cronProblems(file_get_contents($path), $name) as $problem) fwrite(STDERR, "Warning: $problem\n");
    }
    echo 'Cron: ' . count($jobs) . " jobs from " . implode(', ', array_keys(cronFiles()) ?: array('no crontab')) . "\n";
}
