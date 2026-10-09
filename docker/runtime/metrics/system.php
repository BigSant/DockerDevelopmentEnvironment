<?php
// System group: the old monitoring script's mpstat, free, df, iostat, sar, uptime and dd numbers,
// under the same names, labels and units, read from the host's /proc and filesystem.

function metrics_system_parts()
{
    // The I/O part follows the sampled part: both read /proc/stat, iostat's figures are since boot.
    return array('metrics_system_sampled', 'metrics_system_memory', 'metrics_system_uptime', 'metrics_system_disk',
        'metrics_system_io', 'metrics_system_disk_write');
}

// /proc/stat cpu lines: name => user nice system idle iowait irq softirq steal guest guest_nice.
function metrics_system_cpu_times()
{
    $cpus = array();
    foreach (explode("\n", metrics_proc('stat')) as $line) {
        if (!preg_match('/^cpu(\d*)\s+(.*)$/', $line, $match)) continue;
        $fields = array_map('floatval', preg_split('/\s+/', trim($match[2])));
        $cpus[$match[1] === '' ? 'all' : $match[1]] = array_pad(array_slice($fields, 0, 10), 10, 0.0);
    }
    if (!isset($cpus['all'])) throw new RuntimeException('no cpu line in /proc/stat');
    return $cpus;
}

// /proc/<host pid 1>/net/dev, the host's network namespace: name => rx bytes, rx packets, tx bytes, tx packets.
function metrics_system_interfaces()
{
    $interfaces = array();
    foreach (explode("\n", metrics_proc('1/net/dev')) as $line) {
        if (strpos($line, ':') === false) continue;
        list($name, $counters) = explode(':', $line, 2);
        $fields = preg_split('/\s+/', trim($counters));
        if (count($fields) >= 10) {
            $interfaces[trim($name)] = array((float) $fields[0], (float) $fields[1], (float) $fields[8], (float) $fields[9]);
        }
    }
    return $interfaces;
}

// METRICS_NETWORK_INTERFACE, else the host's default route, else its busiest interface (the script's setting).
function metrics_system_interface($interfaces)
{
    $configured = metrics_setting('METRICS_NETWORK_INTERFACE', '');
    if ($configured !== '') return $configured;
    $best = null;
    try {
        foreach (array_slice(explode("\n", metrics_proc('1/net/route')), 1) as $line) {
            $fields = preg_split('/\s+/', trim($line));
            if (count($fields) >= 8 && $fields[1] === '00000000' && (hexdec($fields[3]) & 1)
                && ($best === null || (int) $fields[6] < $best[1])) {
                $best = array($fields[0], (int) $fields[6]);
            }
        }
    } catch (RuntimeException $error) {
    }
    if ($best !== null) return $best[0];
    $busiest = null;
    foreach ($interfaces as $name => $counters) {
        if ($name !== 'lo' && ($busiest === null || $counters[0] + $counters[2] > $interfaces[$busiest][0] + $interfaces[$busiest][2])) {
            $busiest = $name;
        }
    }
    if ($busiest === null) throw new RuntimeException('no network interface');
    return $busiest;
}

// mpstat -P ALL 1 1 and sar -n DEV 1 1: one sample over METRICS_SAMPLE_SECONDS (1 s, as the script).
function metrics_system_sampled(Metrics $metrics)
{
    $failure = null;
    $cpuBefore = metrics_system_cpu_times();
    try {
        $netBefore = metrics_system_interfaces();
    } catch (RuntimeException $error) {
        $failure = $error;
    }
    $started = microtime(true);
    usleep((int) (1000000 * (float) metrics_setting('METRICS_SAMPLE_SECONDS', '1')));
    $cpuAfter = metrics_system_cpu_times();
    $elapsed = max(microtime(true) - $started, 0.001);

    $columns = array('usr' => 'User', 'nice' => 'Nice', 'sys' => 'System', 'iowait' => 'IOWait', 'irq' => 'IRQ',
        'soft' => 'Soft', 'steal' => 'Steal', 'guest' => 'Guest', 'gnice' => 'GNice', 'idle' => 'Idle');
    $usage = array();
    foreach ($cpuAfter as $cpu => $after) {
        $before = isset($cpuBefore[$cpu]) ? $cpuBefore[$cpu] : $after;
        $d = array();
        foreach ($after as $index => $value) $d[$index] = max(0, $value - $before[$index]);
        // user and nice include guest and guest_nice; the total counts them once.
        $total = $d[0] + $d[1] + $d[2] + $d[3] + $d[4] + $d[5] + $d[6] + $d[7];
        $parts = array('usr' => $d[0] - $d[8], 'nice' => $d[1] - $d[9], 'sys' => $d[2], 'iowait' => $d[4], 'irq' => $d[5],
            'soft' => $d[6], 'steal' => $d[7], 'guest' => $d[8], 'gnice' => $d[9], 'idle' => $d[3]);
        foreach ($parts as $column => $value) {
            $usage[$column][$cpu] = $total > 0 ? round(max(0, $value) * 100 / $total, 2) : ($column === 'idle' ? 100 : 0);
        }
    }
    foreach ($columns as $column => $label) {
        foreach ($usage[$column] as $cpu => $value) {
            $metrics->add('cpu_usage_' . $column, $label . ' CPU usage', $value, array('cpu' => $cpu));
        }
    }

    if ($failure) throw $failure;
    $netAfter = metrics_system_interfaces();
    $interface = metrics_system_interface($netAfter);
    if (!isset($netAfter[$interface], $netBefore[$interface])) throw new RuntimeException('no interface ' . $interface);
    $rate = function ($index, $scale) use ($netAfter, $netBefore, $interface, $elapsed) {
        return round(max(0, $netAfter[$interface][$index] - $netBefore[$interface][$index]) / $scale / $elapsed, 2);
    };
    $metrics->add('network_rxpck', 'Incoming packets per second', $rate(1, 1));
    $metrics->add('network_txpck', 'Outgoing packets per second', $rate(3, 1));
    $metrics->add('network_rxkb', 'Kilobytes received per second', $rate(0, 1024));
    $metrics->add('network_txkb', 'Kilobytes transmitted per second', $rate(2, 1024));
}

// free: KiB; used = total - free - buff/cache (procps 3.3, as on the monitored servers).
function metrics_system_memory(Metrics $metrics)
{
    $memory = array();
    foreach (explode("\n", metrics_proc('meminfo')) as $line) {
        if (preg_match('/^(\w+(?:\(\w+\))?):\s+(\d+)/', $line, $match)) $memory[$match[1]] = (int) $match[2];
    }
    if (!isset($memory['MemTotal'], $memory['MemFree'])) throw new RuntimeException('no MemTotal in /proc/meminfo');
    $get = function ($key) use ($memory) { return isset($memory[$key]) ? $memory[$key] : 0; };
    $cache = $get('Buffers') + $get('Cached') + $get('SReclaimable');
    $used = $memory['MemTotal'] - $memory['MemFree'] - $cache;
    $metrics->add('ram_total', 'Server RAM total', $memory['MemTotal']);
    $metrics->add('ram_used', 'Server RAM used', $used >= 0 ? $used : $memory['MemTotal'] - $memory['MemFree']);
    $metrics->add('ram_free', 'Server RAM free', $memory['MemFree']);
    $metrics->add('ram_shared', 'Server RAM shared', $get('Shmem'));
    $metrics->add('ram_buff_cache', 'Server RAM buff/cache', $cache);
    $metrics->add('ram_available', 'Server RAM available', isset($memory['MemAvailable']) ? $memory['MemAvailable'] : $memory['MemFree'] + $cache);
}

// uptime: seconds and load averages. The 10min name carries the 5-minute load, as the script's parser did.
function metrics_system_uptime(Metrics $metrics)
{
    $uptime = preg_split('/\s+/', trim(metrics_proc('uptime')));
    $load = preg_split('/\s+/', trim(metrics_proc('loadavg')));
    if (count($load) < 3 || !is_numeric($uptime[0])) throw new RuntimeException('unreadable uptime or loadavg');
    $metrics->add('uptime_current_time', 'Server time when data generated', time());
    $metrics->add('uptime', 'Server uptime in seconds', (int) floor((float) $uptime[0]));
    $metrics->add('uptime_average_load_1min', 'Average system load in last 1min', (float) $load[0]);
    $metrics->add('uptime_average_load_10min', 'Average system load in last 5min (name kept for the dashboards)', (float) $load[1]);
    $metrics->add('uptime_average_load_15min', 'Average system load in last 15min', (float) $load[2]);
}

// df -k and df -i of the filesystem holding the project data (mounted read-only at /host/disk).
// The *_available_percentage names carry df's Use% column, as the script's parser did.
function metrics_system_disk(Metrics $metrics)
{
    $path = metrics_setting('METRICS_DISK_PATH', '/host/disk');
    exec('LC_ALL=C stat -f -c "%S %b %f %a %c %d" ' . escapeshellarg($path) . ' 2>/dev/null', $output, $status);
    $fields = $status === 0 && $output ? preg_split('/\s+/', trim($output[0])) : array();
    if (count($fields) !== 6) throw new RuntimeException('stat -f failed for ' . $path);
    list($block, $blocks, $free, $available, $inodes, $freeInodes) = array_map('floatval', $fields);
    $kib = function ($count) use ($block) { return (int) ceil($count * $block / 1024); };
    $used = $blocks - $free;
    $metrics->add('disk_size', 'Server total disk size', $kib($blocks));
    $metrics->add('disk_used', 'Server used disk size', $kib($used));
    $metrics->add('disk_available', 'Server free disk size', $kib($available));
    $metrics->add('disk_available_percentage', 'Server used disk size in percentages (df Use%)',
        $used + $available > 0 ? (int) ceil($used * 100 / ($used + $available)) : 0);
    $metrics->add('disk_inodes_total', 'Server total disk inodes count', (int) $inodes);
    $metrics->add('disk_inodes_used', 'Server used disk inodes count', (int) ($inodes - $freeInodes));
    $metrics->add('disk_inodes_available', 'Server free disk inodes count', (int) $freeInodes);
    $metrics->add('disk_inodes_available_percentage', 'Server used disk inodes count in percentages (df IUse%)',
        $inodes > 0 ? (int) ceil(($inodes - $freeInodes) * 100 / $inodes) : 0);
}

// iostat -x avg-cpu: CPU shares since boot, under the dashboard's names (iousage_usr, ip_usage_nice included).
function metrics_system_io(Metrics $metrics)
{
    $cpus = metrics_system_cpu_times();
    $t = $cpus['all'];
    $total = $t[0] + $t[1] + $t[2] + $t[3] + $t[4] + $t[5] + $t[6] + $t[7];
    if ($total <= 0) throw new RuntimeException('empty cpu line in /proc/stat');
    $share = function ($value) use ($total) { return round(max(0, $value) * 100 / $total, 2); };
    $metrics->add('iousage_usr', 'User CPU share since boot (iostat)', $share($t[0] - $t[8]));
    $metrics->add('ip_usage_nice', 'Nice CPU share since boot (iostat)', $share($t[1] - $t[9]));
    $metrics->add('io_usage_sys', 'System CPU share since boot (iostat)', $share($t[2] + $t[5] + $t[6]));
    $metrics->add('io_usage_iowait', 'IOWait CPU share since boot (iostat)', $share($t[4]));
    $metrics->add('io_usage_steal', 'Steal CPU share since boot (iostat)', $share($t[7]));
    $metrics->add('io_usage_idle', 'Idle CPU share since boot (iostat)', $share($t[3]));
}

// dd of METRICS_DISK_WRITE_MB with fdatasync, at most hourly, converted exactly as the script's parser did.
function metrics_system_disk_write(Metrics $metrics)
{
    $size = (int) metrics_setting('METRICS_DISK_WRITE_MB', '100');
    if ($size <= 0) return;
    $state = metrics_setting('METRICS_STATE_DIR', sys_get_temp_dir() . '/setup-metrics');
    if (!is_dir($state) && !@mkdir($state, 0700, true)) throw new RuntimeException('cannot create ' . $state);
    $cache = $state . '/disk-write-speed';
    $cached = is_file($cache) ? explode(' ', trim((string) @file_get_contents($cache))) : array();
    if (count($cached) === 2 && time() - (int) $cached[0] < 3600) {
        $speed = (int) $cached[1];
    } else {
        $file = $state . '/disk-write-test';
        exec('LC_ALL=C dd if=/dev/zero of=' . escapeshellarg($file) . ' bs=1M count=' . $size . ' conv=fdatasync 2>&1', $output);
        @unlink($file);
        $last = $output ? end($output) : '';
        if (!preg_match('/, ([0-9.]+) (\wB)\/s/', $last, $match)) throw new RuntimeException('dd failed: ' . $last);
        $factors = array('GB' => 1048576, 'MB' => 1024, 'KB' => 1, 'B' => 1 / 1024);
        $unit = strtoupper($match[2]);
        $speed = (int) ((float) $match[1] * (isset($factors[$unit]) ? $factors[$unit] : 0));
        @file_put_contents($cache, time() . ' ' . $speed);
    }
    $metrics->add('disk_write_speed', 'Disk write speed', $speed);
}
