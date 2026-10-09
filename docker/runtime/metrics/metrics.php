<?php
// Shared helpers of the target /metrics service: families, Prometheus text exposition, host files.
// Runs on every supported PHP image (7.0+): no typed properties, arrow functions or str_* helpers.

final class Metrics
{
    private $families = array();

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
                $labels = array();
                foreach ($sample[0] as $label => $value) {
                    $labels[] = $label . '="' . str_replace(array('\\', '"', "\n"), array('\\\\', '\\"', '\n'), (string) $value) . '"';
                }
                $lines[] = $name . ($labels ? '{' . implode(',', $labels) . '}' : '') . ' ' . metrics_number($sample[1]);
            }
        }
        return implode("\n", $lines) . "\n";
    }
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
