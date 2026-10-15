<?php
// Shared helpers of the target /metrics service: families, Prometheus text exposition, host files.
// Runs on every supported PHP image (7.0+): no typed properties, arrow functions or str_* helpers.

final class Metrics
{
    private $families = array();

    // A snapshot's families (families()) served again.
    public static function fromFamilies($families)
    {
        $metrics = new Metrics();
        $metrics->families = is_array($families) ? $families : array();
        return $metrics;
    }

    public function families()
    {
        return $this->families;
    }

    // One sample; the first call for a name fixes its HELP text and TYPE.
    public function add($name, $help, $value, $labels = array(), $type = 'gauge')
    {
        if (!isset($this->families[$name])) {
            $this->families[$name] = array('help' => $help, 'type' => $type, 'samples' => array());
        }
        $this->families[$name]['samples'][] = array($labels, $value);
    }

    public function render()
    {
        $lines = array();
        foreach ($this->families as $name => $family) {
            $lines[] = '# HELP ' . $name . ' ' . str_replace(array('\\', "\n"), array('\\\\', '\n'), $family['help']);
            $lines[] = '# TYPE ' . $name . ' ' . $family['type'];
            foreach ($family['samples'] as $sample) {
                $lines[] = $name . metrics_labels($sample[0]) . ' ' . metrics_number($sample[1]);
            }
        }
        return implode("\n", $lines) . "\n";
    }
}

// {label="value",...} as the exposition writes it; "" without labels.
function metrics_labels($labels)
{
    $parts = array();
    foreach ($labels as $label => $value) {
        $parts[] = $label . '="' . str_replace(array('\\', '"', "\n"), array('\\\\', '\\"', '\n'), (string) $value) . '"';
    }
    return $parts ? '{' . implode(',', $parts) . '}' : '';
}

function metrics_number($value)
{
    if (is_int($value)) return (string) $value;
    $value = (float) $value;
    if (is_nan($value)) return 'NaN';
    if (is_infinite($value)) return $value > 0 ? '+Inf' : '-Inf';
    if (floor($value) == $value && abs($value) < 1e15) return sprintf('%.0f', $value);
    return rtrim(rtrim(sprintf('%.6F', $value), '0'), '.');
}

function metrics_respond($status, $body)
{
    http_response_code($status);
    header('Content-Type: ' . ($status === 200 ? 'text/plain; version=0.0.4; charset=utf-8' : 'text/plain; charset=utf-8'));
    header('Cache-Control: no-store');
    echo $body;
    exit;
}

function metrics_setting($name, $default)
{
    $value = getenv($name);
    return $value === false || $value === '' ? $default : (string) $value;
}

// Host /proc (bind-mounted read-only at /host/proc), so the numbers describe the host, not this container.
function metrics_proc($path)
{
    $file = rtrim(metrics_setting('METRICS_PROC_ROOT', '/host/proc'), '/') . '/' . $path;
    $text = @file_get_contents($file);
    if ($text === false) throw new RuntimeException('cannot read ' . $file);
    return $text;
}

// Every group the target serves, one file each, runtime/metrics/<group>.php with metrics_<group>_parts(). host
// follows database (it reuses the database group's connection for the version).
function metrics_groups()
{
    return array('system', 'website', 'http', 'security', 'database', 'host', 'containers', 'cron', 'registry');
}

// The sampler's directory: the snapshot /metrics serves, the history, the website probes.
function metrics_snapshot_directory()
{
    return rtrim(metrics_setting('METRICS_SNAPSHOT_DIR', '/var/lib/setup-metrics'), '/');
}

function metrics_read_json($file)
{
    $data = is_file($file) ? json_decode((string) @file_get_contents($file), true) : null;
    return is_array($data) ? $data : null;
}

// Replaced in one step, so a reader never sees half a file.
function metrics_write_json($file, $data)
{
    $temporary = $file . '.' . getmypid();
    if (@file_put_contents($temporary, json_encode($data)) === false) throw new RuntimeException('cannot write ' . $temporary);
    if (!@rename($temporary, $file)) throw new RuntimeException('cannot replace ' . $file);
}

// The latest snapshot while it is fresh (taken within three sampler intervals), else null.
function metrics_snapshot($now)
{
    $snapshot = metrics_read_json(metrics_snapshot_directory() . '/snapshot.json');
    if ($snapshot === null || !isset($snapshot['taken'], $snapshot['interval'], $snapshot['families'])) return null;
    return $now - (int) $snapshot['taken'] <= 3 * max(1, (int) $snapshot['interval']) ? $snapshot : null;
}

// Collect one group: each part is independent, a failed part only clears the group's up flag.
function metrics_collect(Metrics $metrics, $group)
{
    require_once __DIR__ . '/' . $group . '.php';
    $complete = true;
    foreach (call_user_func('metrics_' . $group . '_parts') as $part) {
        try {
            call_user_func($part, $metrics);
        } catch (Throwable $error) {
            $complete = false;
            error_log('metrics ' . $part . ': ' . $error->getMessage());
        }
    }
    $metrics->add('setup_metrics_group_up', 'Whether every part of the metrics group was collected', $complete ? 1 : 0,
        array('group' => $group));
}
