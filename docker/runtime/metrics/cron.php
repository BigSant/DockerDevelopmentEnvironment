<?php
// Cron group: each job of the project crontab with its last run (start, exit code, duration) from the cron
// service's run history (data/cron/history.jsonl, written by cron-run.sh), mounted read-only.
// Jobs are labelled by their id (the cron UI's), crontab line and a readable name (cronJobName: the program and its
// targets, URLs without query, values and tokens masked), never by the command: commands can carry tokens (e.g.
// cron URLs with ?token=).
require_once __DIR__ . '/../cron/compose.php';

function metrics_cron_parts()
{
    return array('metrics_cron_jobs');
}

// The last run and the last successful run of each job id, streaming the run index line by line.
function metrics_cron_history($file)
{
    $runs = array();
    $handle = @fopen($file, 'r');
    if (!$handle) return $runs;  // No run recorded yet.
    while (($line = fgets($handle, 65536)) !== false) {
        $run = json_decode($line, true);
        if (!is_array($run) || !isset($run['job'], $run['started'])) continue;
        // A run the container stop cut short has no exit code (cron UI: interrupted); it counts as failed, -1.
        if (!empty($run['interrupted'])) $run['exit'] = -1;
        if (!isset($run['exit'])) continue;
        $job = (string) $run['job'];
        if (!isset($runs[$job])) $runs[$job] = array('last' => null, 'success' => null);
        $runs[$job]['last'] = $run;
        if ((int) $run['exit'] === 0) $runs[$job]['success'] = $run;
    }
    fclose($handle);
    return $runs;
}

function metrics_cron_jobs(Metrics $metrics)
{
    if (metrics_setting('METRICS_CRON', '0') !== '1') return;
    $history = metrics_setting('METRICS_CRON_HISTORY', '/host/cron-history');
    if (!is_dir($history)) throw new RuntimeException('no cron history directory ' . $history);
    $runs = metrics_cron_history($history . '/history.jsonl');
    $jobs = array();
    foreach (cronFiles(metrics_setting('METRICS_CRON_CONFIG', '/host/cron-config'), metrics_setting('ENV', '')) as $name => $path) {
        $jobs = array_merge($jobs, cronJobs((string) file_get_contents($path), $name));
    }
    // Schedules are read in the container time zone, as supercronic does (TZ).
    metrics_timezone();
    $now = time();
    // CRON_SCHEDULE=off (the cron service runs nothing on schedule): no expected time, so no missing-run alert.
    $scheduled = metrics_setting('METRICS_CRON_SCHEDULE', 'on') !== 'off';
    $metrics->add('setup_cron_schedule_enabled', 'Whether the cron service runs the jobs on their schedule (CRON_SCHEDULE)',
        $scheduled ? 1 : 0);
    // A command in crontab and crontab.<ENV> has one id (and one history), as in the cron UI: its figures once.
    $schedules = array();
    foreach ($jobs as $job) {
        if ($job['command'] === '' || cronFields($job['schedule']) === null) continue;
        $metrics->add('setup_cron_job_info', 'A job of the project crontab: its file, line, schedule and a name without secrets', 1,
            array('job' => $job['id'], 'source' => $job['source'], 'line' => (string) $job['line'], 'schedule' => $job['schedule'],
                  'name' => $job['name']));
        $schedules[$job['id']][] = $job['schedule'];
    }
    foreach ($schedules as $id => $list) {
        $labels = array('job' => (string) $id);
        $last = isset($runs[$id]) ? $runs[$id]['last'] : null;
        $success = isset($runs[$id]) ? $runs[$id]['success'] : null;
        if ($last !== null) {
            $metrics->add('setup_cron_job_last_run_timestamp_seconds', 'When the last recorded run started (scheduled or manual)',
                round((float) $last['started'] / 1000, 3), $labels);
            $metrics->add('setup_cron_job_last_exit_code', 'Exit code of the last recorded run (0 = success, -1 = interrupted)', (int) $last['exit'], $labels);
            $metrics->add('setup_cron_job_last_duration_seconds', 'Duration of the last recorded run',
                round((float) (isset($last['duration_ms']) ? $last['duration_ms'] : 0) / 1000, 3), $labels);
        }
        if ($success !== null) {
            $metrics->add('setup_cron_job_last_success_timestamp_seconds', 'When the last successful run started',
                round((float) $success['started'] / 1000, 3), $labels);
        }
        // The first scheduled time after the last recorded start (after now without runs): in the past, a run is
        // missing (not started, skipped while the previous one ran, or still running).
        $from = $last !== null ? (int) floor((float) $last['started'] / 1000) : $now;
        $expected = null;
        foreach ($list as $schedule) {
            $next = cronNextRun($schedule, $from);
            if ($next !== null && ($expected === null || $next < $expected)) $expected = $next;
        }
        if ($expected !== null && $scheduled) {
            $metrics->add('setup_cron_job_expected_run_timestamp_seconds',
                'The first scheduled time after the last recorded run; in the past means a run is missing or still running', $expected, $labels);
        }
    }
}
