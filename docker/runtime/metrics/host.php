<?php
// Host group: the old script's "Top CPU/RAM-consuming Processes" (ps -eo %mem,%cpu,cmd, summed per command,
// ten each) under the viewer's names, plus I/O wait counters and the server's versions, from the host's /proc.
require_once __DIR__ . '/containers.php';

function metrics_host_parts()
{
    return array('metrics_host_processes', 'metrics_host_io_wait', 'metrics_host_server');
}

// getconf once per scrape: clock ticks per second (stat times) and the page size (stat rss).
function metrics_host_sysconf($name, $default)
{
    static $values = array();
    if (!isset($values[$name])) {
        $value = trim((string) @shell_exec('getconf ' . $name . ' 2>/dev/null'));
        $values[$name] = ctype_digit($value) && (int) $value > 0 ? (int) $value : $default;
    }
    return $values[$name];
}

// The command of one process without its arguments (they can carry secrets, e.g. the runner's OAuth
// arguments): argv[0] as ps shows it, a kernel thread as [name]. Null when the process is gone.
function metrics_host_command($root, $pid, $name)
{
    $cmdline = @file_get_contents($root . '/' . $pid . '/cmdline');
    if ($cmdline === false) return null;
    $command = trim(explode("\0", $cmdline, 2)[0]);
    if ($command === '') return '[' . $name . ']';
    return function_exists('mb_strcut') ? mb_strcut($command, 0, 120, 'UTF-8') : substr($command, 0, 120);
}

// ps's %cpu (CPU time over the process's lifetime) and %mem (resident share of RAM), each floored to a
// tenth as ps prints them, summed per command with the process count, as the script's awk did.
function metrics_host_process_table()
{
    $root = rtrim(metrics_setting('METRICS_PROC_ROOT', '/host/proc'), '/');
    $uptime = (float) preg_split('/\s+/', trim(metrics_proc('uptime')))[0];
    if (!preg_match('/^MemTotal:\s+(\d+)/m', metrics_proc('meminfo'), $match) || (int) $match[1] <= 0) {
        throw new RuntimeException('no MemTotal in /proc/meminfo');
    }
    $memory = (int) $match[1];
    $hertz = metrics_host_sysconf('CLK_TCK', 100);
    $page = metrics_host_sysconf('PAGESIZE', 4096);
    $directory = @opendir($root);
    if (!$directory) throw new RuntimeException('cannot list ' . $root);
    $commands = array();
    while (($pid = readdir($directory)) !== false) {
        if (!ctype_digit($pid)) continue;
        $stat = @file_get_contents($root . '/' . $pid . '/stat');
        // "pid (name) state ...": the name may hold spaces and parentheses, the fields follow the last ')'.
        $close = $stat === false ? false : strrpos($stat, ')');
        if ($close === false) continue;
        $open = strpos($stat, '(');
        $fields = preg_split('/\s+/', trim(substr($stat, $close + 1)));
        if (count($fields) < 22) continue;
        $command = metrics_host_command($root, $pid, substr($stat, $open + 1, $close - $open - 1));
        if ($command === null) continue;
        // Fields after the name: utime 11, stime 12, starttime 19, rss 21 (0-based).
        $elapsed = (int) ($uptime - (float) $fields[19] / $hertz);
        $cpu = $elapsed > 0 ? floor(((float) $fields[11] + (float) $fields[12]) * 1000 / $hertz / $elapsed) / 10 : 0.0;
        $mem = floor((float) $fields[21] * $page / 1024 * 1000 / $memory) / 10;
        if (!isset($commands[$command])) $commands[$command] = array(0.0, 0.0, 0);
        $commands[$command][0] += $cpu;
        $commands[$command][1] += $mem;
        $commands[$command][2]++;
    }
    closedir($directory);
    return $commands;
}

function metrics_host_top($commands, $index)
{
    uksort($commands, function ($a, $b) use ($commands, $index) {
        $x = $commands[$a][$index];
        $y = $commands[$b][$index];
        return $x == $y ? strcmp($a, $b) : ($x < $y ? 1 : -1);
    });
    return array_slice($commands, 0, 10, true);
}

function metrics_host_processes(Metrics $metrics)
{
    $commands = metrics_host_process_table();
    foreach (array(array('cpu', 0, 'CPU'), array('memory', 1, 'memory')) as $kind) {
        $top = metrics_host_top($commands, $kind[1]);
        foreach ($top as $command => $values) {
            $metrics->add('processes_consuming_' . $kind[0], 'Process ' . $kind[2] . ' usage', round($values[$kind[1]], 2),
                array('process' => (string) $command));
        }
        foreach ($top as $command => $values) {
            $metrics->add('processes_consuming_' . $kind[0] . '_total', 'Process total', $values[2],
                array('process' => (string) $command));
        }
    }
}

// I/O wait as counters (rate() in Grafana): CPU time waiting for I/O, processes blocked on I/O now and the
// time each whole disk was busy (/proc/diskstats io_ticks; partitions, loop, nbd and RAM devices left out).
function metrics_host_io_wait(Metrics $metrics)
{
    $stat = metrics_proc('stat');
    if (!preg_match('/^cpu\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)/m', $stat, $match)) {
        throw new RuntimeException('no cpu line in /proc/stat');
    }
    $hertz = metrics_host_sysconf('CLK_TCK', 100);
    $metrics->add('setup_cpu_iowait_seconds_total', 'CPU time spent waiting for I/O, all CPUs (/proc/stat iowait)',
        round((float) $match[5] / $hertz, 2), array(), 'counter');
    if (preg_match('/^procs_blocked\s+(\d+)/m', $stat, $match)) {
        $metrics->add('setup_processes_blocked', 'Processes blocked waiting for I/O now (/proc/stat procs_blocked)', (int) $match[1]);
    }
    foreach (explode("\n", metrics_proc('diskstats')) as $line) {
        $fields = preg_split('/\s+/', trim($line));
        if (count($fields) < 14) continue;
        $device = $fields[2];
        if (preg_match('/^(loop|ram|zram|nbd|fd|sr)\d/', $device)
            || preg_match('/^((sd|vd|xvd|hd)[a-z]+\d+|(nvme\d+n\d+|mmcblk\d+)p\d+)$/', $device)) {
            continue;
        }
        $metrics->add('setup_disk_io_time_seconds_total', 'Time the disk was busy with I/O (/proc/diskstats io_ticks)',
            round((float) $fields[12] / 1000, 3), array('device' => $device), 'counter');
    }
}

// One info sample: the host's OS and Docker (from the Docker collector), kernel and database version;
// an unknown version is an empty label (the containers and database groups report why).
function metrics_host_server(Metrics $metrics)
{
    $kernel = trim(metrics_proc('sys/kernel/osrelease'));
    $server = array('os' => '', 'docker' => '');
    try {
        $state = metrics_containers_state();
        foreach ($server as $key => $value) {
            if (isset($state['server'][$key])) $server[$key] = (string) $state['server'][$key];
        }
    } catch (RuntimeException $error) {
        // The containers group reports a missing or stale collector.
    }
    $database = '';
    try {
        require_once __DIR__ . '/database.php';
        $row = metrics_database_server_row();
        if (isset($row['version'])) $database = (string) $row['version'];
    } catch (Throwable $error) {
        // The database group reports its own failure.
    }
    $metrics->add('setup_server_info', 'Server versions: OS and Docker (Docker API), kernel, database (VERSION())', 1,
        array('os' => $server['os'], 'kernel' => $kernel, 'docker' => $server['docker'], 'database' => $database));
}
