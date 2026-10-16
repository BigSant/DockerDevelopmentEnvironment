<?php
// Release group: what runs here, from the current.json make deploy and make deploy-images write into
// data/<env>/release (mounted read-only at METRICS_RELEASE_DIR): the setup version the images were built from, the
// application commit and ref last deployed, and when. Locally there is no release (make up runs the checkout):
// nothing is served; on stage and prod a missing file means no deploy has written it yet.

function metrics_release_parts()
{
    return array('metrics_release_info');
}

// The deployed release, or null where none is (local, test).
function metrics_release()
{
    $file = rtrim(metrics_setting('METRICS_RELEASE_DIR', '/host/release'), '/') . '/current.json';
    $info = metrics_read_json($file);
    if ($info !== null) return $info;
    if (in_array(metrics_setting('ENV', 'local'), array('local', 'test'), true)) return null;
    throw new RuntimeException('no release information in ' . $file . ' (make deploy-images writes it)');
}

function metrics_release_time($text)
{
    $time = is_string($text) ? strtotime($text) : false;
    return $time === false ? null : $time;
}

function metrics_release_info(Metrics $metrics)
{
    $info = metrics_release();
    if ($info === null) return;
    $text = function ($key) use ($info) { return isset($info[$key]) && $info[$key] !== null ? (string) $info[$key] : ''; };
    $metrics->add('setup_release_info', 'The release running here: setup version of its images, application ref and commit', 1,
        array('setup' => $text('setup'), 'ref' => $text('ref'), 'commit' => substr($text('commit'), 0, 12)));
    if (($deployed = metrics_release_time($text('deployed_at'))) !== null) {
        $metrics->add('setup_release_deployed_timestamp_seconds', 'When the application commit was last deployed (make deploy)', $deployed);
    }
    if (($images = metrics_release_time($text('images_at'))) !== null) {
        $metrics->add('setup_release_images_timestamp_seconds', 'When the release images were last started (make deploy-images)', $images);
    }
}
