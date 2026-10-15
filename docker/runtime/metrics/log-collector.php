<?php
// Log collector of the metrics service (service metrics-logs): keeps this project's container logs past the
// containers' life. docker logs lose a container's lines when it is recreated (every deploy-images) and keep only
// CONTAINER_LOG_MAX_SIZE x CONTAINER_LOG_MAX_FILES; this process follows each container's stdout and stderr through
// the Docker API and appends the lines, as the containers wrote them, to one file per service, stream and day:
//   <LOGS_DIR>/<service>/<access|output|error>-YYYY-MM-DD.log
// stdout is "access" for the web services (their request lines), "output" for the others; stderr is "error".
// The day is the line's Docker time in TZ. A finished day is gzipped (<name>.log.gz, zcat/zless read it); files older
// than LOGS_RETENTION_DAYS days go, and the oldest go first while all of them exceed LOGS_MAX_MB. A day's files are
// bounded as they are written (LogWriter): one file takes a quarter of LOGS_MAX_MB at most, all of a day's files
// LOGS_MAX_MB. The time of the last line read from each container is kept, so a restarted collector or a recreated
// container loses nothing docker logs still hold.
// Like metrics-docker it holds the root-equivalent Docker socket, so it sends GET requests only, for the project's
// own containers, without network, capabilities or a writable root; it writes only the log volume.
// Usage: php log-collector.php                       follow the containers (the service)
//        php log-collector.php --once                read what the containers hold now, maintain the files, exit
//        php log-collector.php --print DAY [SERVICE]  print one day's files (make logs day=...)
require_once __DIR__ . '/docker-collector.php';

// The services whose stdout carries one line per request (Container logs in ARCHITECTURE).
const LOGS_ACCESS_SERVICES = array('nginx-proxy', 'webserver', 'php-fpm');
// The collector's own lines (its errors) stay in docker logs: following itself would feed its own writes back.
const LOGS_SELF = 'metrics-logs';
// A line longer than this is cut (docker sends lines over 16 KiB in parts; a runaway one must not fill memory).
const LOGS_LINE_LIMIT = 1048576;
// A finished day is compressed this long after its last line: late lines may still arrive, and the metrics HTTP
// group reads the plain file of yesterday while its window (60 minutes at most) reaches back before midnight.
const LOGS_COMPRESS_AFTER = 7200;
// The end of the note a day's file gets when it reaches its bound (LogWriter).
const LOGS_FULL_END = ' are only in docker logs';

function logs_directory()
{
    return rtrim(collector_setting('LOGS_DIR', '/var/lib/setup-logs'), '/');
}

function logs_kind($service, $stream)
{
    if ($stream === 2) return 'error';
    return in_array($service, LOGS_ACCESS_SERVICES, true) ? 'access' : 'output';
}

// Compose service names are lower-case letters, digits, "-" and "_": never a path.
function logs_valid_service($service)
{
    return (bool) preg_match('/^[a-z0-9][a-z0-9_-]*$/', $service);
}

// Docker's RFC 3339 time with up to nine fractional digits -> "<seconds>.<nine digits>", which sorts as text
// within the same number of digits and goes to the API's since= as it is.
function logs_time($text)
{
    if (!preg_match('/^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)$/', $text, $match)) return null;
    $date = DateTime::createFromFormat('Y-m-d\TH:i:sP', $match[1] . ($match[3] === 'Z' ? '+00:00' : $match[3]));
    if ($date === false) return null;
    return $date->getTimestamp() . '.' . str_pad(isset($match[2]) ? $match[2] : '', 9, '0');
}

function logs_after($a, $b)
{
    list($as, $an) = explode('.', $a);
    list($bs, $bn) = explode('.', $b);
    return (int) $as === (int) $bs ? strcmp($an, $bn) > 0 : (int) $as > (int) $bs;
}

// LOGS_MAX_MB in bytes.
function logs_limit()
{
    return max(1, (int) collector_setting('LOGS_MAX_MB', '2048')) * 1048576;
}

final class LogWriter
{
    private $handles = array();
    // Per day lines arrive for: [sizes => [path => bytes], total => bytes, full => [path => true]].
    private $days = array();

    public function write($service, $stream, $time, $line)
    {
        $seconds = (int) explode('.', $time)[0];
        $day = date('Y-m-d', $seconds);
        $path = logs_directory() . '/' . $service . '/' . logs_kind($service, $stream) . '-' . $day . '.log';
        $full = $this->full($day, $path, strlen($line) + 1);
        if ($full === null) {
            $this->append($path, $line);
        } elseif (!isset($this->days[$day]['full'][$path])) {
            // The first line past the bound leaves a note; the rest of the day's lines stay in docker logs only.
            $this->days[$day]['full'][$path] = true;
            $note = 'metrics-logs: ' . $full . ' reached, further lines of ' . $day . LOGS_FULL_END;
            $this->append($path, $note);
            fwrite(STDERR, 'metrics-logs: ' . $service . '/' . basename($path) . ': ' . $full . " reached, its lines are dropped\n");
        }
    }

    // The maintenance deletes past days only, so a crash loop or a message on every request would fill the disk
    // within a day: a file takes a quarter of LOGS_MAX_MB at most (one noisy stream leaves room for the others),
    // all of a day's files LOGS_MAX_MB. Null while the line fits, else which bound it passes.
    private function full($day, $path, $bytes)
    {
        if (!isset($this->days[$day])) {
            // A day first seen here (or a restarted collector): the files it has already.
            $sizes = $full = array();
            foreach (glob(logs_directory() . '/*/*-' . $day . '.log*') ?: array() as $file) {
                if (!preg_match('/\.log(\.gz)?$/', $file)) continue;
                $plain = preg_replace('/\.gz$/', '', $file);
                $size = (int) @filesize($file);
                $sizes[$plain] = (isset($sizes[$plain]) ? $sizes[$plain] : 0) + $size;
                // A file that ends with the note has it already.
                if ($plain === $file && $size > 0 && substr((string) @file_get_contents($file, false, null, max(0, $size - 256)), -strlen(LOGS_FULL_END) - 1)
                        === LOGS_FULL_END . "\n") {
                    $full[$plain] = true;
                }
            }
            $this->days[$day] = array('sizes' => $sizes, 'total' => array_sum($sizes), 'full' => $full);
            // Today's and the days late lines still come for.
            ksort($this->days);
            while (count($this->days) > 3) array_shift($this->days);
            if (!isset($this->days[$day])) return null;
        }
        $state = &$this->days[$day];
        $limit = logs_limit();
        $size = isset($state['sizes'][$path]) ? $state['sizes'][$path] : 0;
        if ($state['total'] + $bytes > $limit) return 'LOGS_MAX_MB for the day';
        if ($size + $bytes > $limit / 4) return 'a quarter of LOGS_MAX_MB for one file';
        $state['sizes'][$path] = $size + $bytes;
        $state['total'] += $bytes;
        return null;
    }

    private function append($path, $line)
    {
        if (is_file($path . '.gz')) {
            // A late line of a day already compressed: one more gzip member, which zcat reads as part of the file.
            $this->close($path);
            $gzip = gzopen($path . '.gz', 'ab6');
            if ($gzip === false) throw new RuntimeException('cannot append to ' . $path . '.gz');
            gzwrite($gzip, $line . "\n");
            gzclose($gzip);
            return;
        }
        if (!isset($this->handles[$path])) {
            $directory = dirname($path);
            if (!is_dir($directory) && !@mkdir($directory, 0755, true) && !is_dir($directory)) {
                throw new RuntimeException('cannot create ' . $directory);
            }
            $handle = @fopen($path, 'ab');
            if ($handle === false) throw new RuntimeException('cannot open ' . $path);
            // A file of another day of the same service and stream is finished.
            $prefix = substr($path, 0, -strlen('YYYY-MM-DD.log'));
            foreach (array_keys($this->handles) as $open) {
                if (strpos($open, $prefix) === 0) $this->close($open);
            }
            $this->handles[$path] = $handle;
        }
        fwrite($this->handles[$path], $line . "\n");
    }

    public function close($path)
    {
        if (isset($this->handles[$path])) {
            fclose($this->handles[$path]);
            unset($this->handles[$path]);
        }
    }

    public function flush()
    {
        foreach ($this->handles as $handle) {
            fflush($handle);
        }
    }
}

// One followed container: the HTTP answer of GET /containers/{id}/logs, read as it arrives.
final class LogStream
{
    public $id;
    public $service;
    public $socket;
    // since= sends the lines of the saved time again: [that time, how many of its lines were written].
    public $replay = array(null, 0);
    private $tty;
    private $buffer = '';
    private $headers = true;
    private $pending = array(1 => null, 2 => null);

    public function __construct($id, $service, $tty, $since, $follow)
    {
        $this->id = $id;
        $this->service = $service;
        $this->tty = $tty;
        $socket = collector_setting('METRICS_DOCKER_SOCKET', '/var/run/docker.sock');
        $this->socket = @stream_socket_client('unix://' . $socket, $errno, $error, 10);
        if (!$this->socket) throw new RuntimeException('cannot connect to ' . $socket . ': ' . $error);
        $query = 'follow=' . ($follow ? 1 : 0) . '&stdout=1&stderr=1&timestamps=1' . ($since === null ? '' : '&since=' . $since);
        // HTTP/1.0: the daemon streams the body without chunking and closes it when the container stops.
        fwrite($this->socket, 'GET /containers/' . rawurlencode($id) . '/logs?' . $query . " HTTP/1.0\r\nHost: docker\r\n\r\n");
        stream_set_blocking($this->socket, false);
    }

    // Reads what arrived; calls $line(stream, time, text) per complete line. False once the answer has ended.
    public function read($line)
    {
        $data = fread($this->socket, 262144);
        if ($data !== false && $data !== '') {
            $this->buffer .= $data;
            $this->parse($line);
        }
        if (feof($this->socket)) {
            fclose($this->socket);
            foreach (array(1, 2) as $stream) {
                if ($this->pending[$stream] !== null) $line($stream, $this->pending[$stream][0], $this->pending[$stream][1]);
            }
            return false;
        }
        return true;
    }

    private function parse($line)
    {
        if ($this->headers) {
            $end = strpos($this->buffer, "\r\n\r\n");
            if ($end === false) return;
            $status = substr($this->buffer, 0, $end);
            if (!preg_match('#^HTTP/\d\.\d 200#', $status)) {
                throw new RuntimeException('GET logs of ' . $this->service . ': ' . strtok($status, "\r\n"));
            }
            $this->buffer = (string) substr($this->buffer, $end + 4);
            $this->headers = false;
        }
        if ($this->tty) {
            // A terminal's output has no frames: everything is stdout, read a whole line at a time (an unfinished one,
            // even its timestamp, waits for the rest). Docker's 16 KiB parts of a long terminal line are not joined.
            $end = strrpos($this->buffer, "\n");
            if ($end === false) {
                if (strlen($this->buffer) > LOGS_LINE_LIMIT) $this->buffer = substr($this->buffer, 0, LOGS_LINE_LIMIT);
                return;
            }
            $this->payload(1, substr($this->buffer, 0, $end + 1), $line);
            $this->buffer = (string) substr($this->buffer, $end + 1);
            return;
        }
        // Frames: stream (1 stdout, 2 stderr), three zero bytes, the payload's size (big-endian), the payload.
        while (strlen($this->buffer) >= 8) {
            $header = unpack('Cstream/x3/Nsize', substr($this->buffer, 0, 8));
            if (strlen($this->buffer) < 8 + $header['size']) break;
            $this->payload($header['stream'] === 2 ? 2 : 1, (string) substr($this->buffer, 8, $header['size']), $line);
            $this->buffer = (string) substr($this->buffer, 8 + $header['size']);
        }
    }

    // A payload holds lines "<RFC 3339 time> <text>"; a line docker split in parts has the time on each part.
    private function payload($stream, $data, $line)
    {
        $parts = explode("\n", $data);
        $last = array_pop($parts);
        foreach ($parts as $part) {
            $this->part($stream, $part, true, $line);
        }
        if ($last !== '') $this->part($stream, $last, false, $line);
    }

    private function part($stream, $text, $complete, $line)
    {
        $space = strpos($text, ' ');
        $time = $space === false ? null : logs_time(substr($text, 0, $space));
        if ($time === null) {
            // A continuation without a time (terminal output split by the buffer): the previous part's time.
            $time = $this->pending[$stream] !== null ? $this->pending[$stream][0] : null;
            $body = $text;
        } else {
            $body = (string) substr($text, $space + 1);
        }
        if ($this->pending[$stream] !== null) {
            $body = substr($this->pending[$stream][1] . $body, 0, LOGS_LINE_LIMIT);
            $time = $this->pending[$stream][0];
        }
        if ($time === null) return;
        if ($complete) {
            $this->pending[$stream] = null;
            $line($stream, $time, rtrim($body, "\r"));
        } else {
            $this->pending[$stream] = array($time, $body);
        }
    }
}

final class LogCollector
{
    private $project;
    private $follow;
    private $writer;
    private $streams = array();
    private $state = array();
    private $changed = false;

    // $follow false (--once): read what each container holds now; every answer then ends.
    public function __construct($project, $follow)
    {
        $this->project = $project;
        $this->follow = $follow;
        $this->writer = new LogWriter();
        $saved = is_file($this->stateFile()) ? json_decode((string) @file_get_contents($this->stateFile()), true) : null;
        if (is_array($saved) && isset($saved['project'], $saved['containers']) && $saved['project'] === $project) {
            $this->state = $saved['containers'];
        }
    }

    private function stateFile()
    {
        return logs_directory() . '/.collector-state.json';
    }

    // Opens a stream for every container of the project that may have unread lines: a running one not followed
    // yet, a stopped one not read to its end. Forgets containers that are gone.
    public function refresh()
    {
        $label = 'com.docker.compose.project=' . $this->project;
        $listed = collector_json('/containers/json?all=1&filters=' . collector_filters(array('label' => array($label))));
        $present = array();
        foreach ($listed as $container) {
            $id = (string) $container['Id'];
            $labels = isset($container['Labels']) && is_array($container['Labels']) ? $container['Labels'] : array();
            $service = isset($labels['com.docker.compose.service']) ? (string) $labels['com.docker.compose.service'] : '';
            if (!logs_valid_service($service) || $service === LOGS_SELF) continue;
            $present[$id] = true;
            $running = isset($container['State']) && $container['State'] === 'running';
            $known = isset($this->state[$id]) ? $this->state[$id]
                : array('service' => $service, 'last' => null, 'same' => 0, 'ended' => false, 'started' => '');
            if (isset($this->streams[$id])) continue;
            try {
                $inspect = collector_json('/containers/' . rawurlencode($id) . '/json');
            } catch (RuntimeException $error) {
                // Removed between the list and this call.
                continue;
            }
            // A stopped container read to its end has nothing new, unless it ran again between two looks (a restart
            // that ended within the 5 seconds): its start time tells.
            $started = isset($inspect['State']['StartedAt']) ? (string) $inspect['State']['StartedAt'] : '';
            if (!$running && $known['ended'] && isset($known['started']) && $known['started'] === $started) continue;
            $tty = !empty($inspect['Config']['Tty']);
            $known['service'] = $service;
            $known['started'] = $started;
            $known['ended'] = false;
            $this->state[$id] = $known;
            $this->streams[$id] = new LogStream($id, $service, $tty, $known['last'], $this->follow);
            $this->streams[$id]->replay = array($known['last'], (int) $known['same']);
        }
        foreach (array_keys($this->state) as $id) {
            if (!isset($present[$id]) && !isset($this->streams[$id])) unset($this->state[$id]);
        }
    }

    // Waits up to $seconds for lines and writes them; returns whether any stream is still open.
    public function pump($seconds)
    {
        if (!$this->streams) {
            usleep((int) ($seconds * 1000000));
            return false;
        }
        $read = array();
        foreach ($this->streams as $stream) {
            $read[] = $stream->socket;
        }
        $write = $except = null;
        if (@stream_select($read, $write, $except, (int) $seconds, (int) (fmod($seconds, 1) * 1000000)) === false) {
            // Interrupted (a signal): wait a little rather than spin.
            usleep(200000);
            return true;
        }
        foreach ($this->streams as $id => $stream) {
            if (!in_array($stream->socket, $read, true)) continue;
            $state = &$this->state[$id];
            $writer = $this->writer;
            try {
                $changed = &$this->changed;
                $open = $stream->read(function ($kind, $time, $text) use (&$state, $writer, $stream, &$changed) {
                    // since= sends the lines of the saved time again. Lines can share a time (stdout and stderr
                    // of one write), so the state counts the ones written and exactly that many are skipped.
                    if ($time === $stream->replay[0] && $stream->replay[1] > 0) {
                        $stream->replay[1]--;
                        return;
                    }
                    // Everything else is written: stdout and stderr reach the daemon's file side by side, so a
                    // line may follow one with a later time.
                    $writer->write($stream->service, $kind, $time, $text);
                    $changed = true;
                    if ($state['last'] === null || logs_after($time, $state['last'])) {
                        $state['last'] = $time;
                        $state['same'] = 1;
                    } elseif ($time === $state['last']) {
                        $state['same']++;
                    }
                });
            } catch (RuntimeException $error) {
                fwrite(STDERR, 'metrics-logs: ' . $error->getMessage() . "\n");
                @fclose($stream->socket);
                $open = false;
            }
            unset($state);
            if (!$open) {
                // The container stopped (or the daemon closed the answer): read again when it runs.
                $this->state[$id]['ended'] = true;
                unset($this->streams[$id]);
            }
        }
        $this->writer->flush();
        return (bool) $this->streams;
    }

    // Whether lines were written since the last save.
    public function changed()
    {
        return $this->changed;
    }

    public function save()
    {
        $this->changed = false;
        $file = $this->stateFile();
        $temporary = $file . '.' . getmypid();
        if (@file_put_contents($temporary, json_encode(array('project' => $this->project, 'containers' => $this->state))) === false) {
            throw new RuntimeException('cannot write ' . $temporary);
        }
        if (!@rename($temporary, $file)) throw new RuntimeException('cannot replace ' . $file);
    }

    public function maintain($now)
    {
        logs_maintain($this->writer, $now);
    }
}

// The day files: [path, service, day, size], oldest day first.
function logs_files()
{
    $files = array();
    foreach (glob(logs_directory() . '/*/*-????-??-??.log*') ?: array() as $path) {
        if (!preg_match('#/([a-z0-9][a-z0-9_-]*)/(access|output|error)-(\d{4}-\d\d-\d\d)\.log(\.gz)?$#', $path, $match)) continue;
        $files[] = array($path, $match[1], $match[3], (int) @filesize($path));
    }
    usort($files, function ($a, $b) { return strcmp($a[2], $b[2]) ?: $b[3] - $a[3]; });
    return $files;
}

// Gzips the files of finished days, deletes those past the retention, then the oldest while over the size limit.
// Today's files are never deleted (LogWriter bounds them). A day that cannot be compressed (a full disk, a directory
// it cannot write) is reported and tried again next time; the deletions still run, they are what frees the disk.
function logs_maintain(LogWriter $writer, $now)
{
    $today = date('Y-m-d', $now);
    // A compression a crash interrupted leaves <name>.log.gz.<pid>; the plain file is still there.
    foreach (glob(logs_directory() . '/*/*.log.gz.*') ?: array() as $temporary) {
        if ($now - (int) @filemtime($temporary) > 3600) @unlink($temporary);
    }
    foreach (logs_files() as $file) {
        list($path, , $day) = $file;
        if ($day >= $today || substr($path, -3) === '.gz' || $now - (int) @filemtime($path) < LOGS_COMPRESS_AFTER) continue;
        $writer->close($path);
        try {
            logs_compress($path);
        } catch (RuntimeException $error) {
            fwrite(STDERR, 'metrics-logs: ' . $error->getMessage() . "\n");
        }
    }
    $keep = date('Y-m-d', strtotime($today . ' -' . max(1, (int) collector_setting('LOGS_RETENTION_DAYS', '14')) . ' days'));
    $files = array();
    foreach (logs_files() as $file) {
        if ($file[2] < $keep) {
            $writer->close($file[0]);
            @unlink($file[0]);
        } else {
            $files[] = $file;
        }
    }
    $limit = logs_limit();
    $total = array_sum(array_map(function ($file) { return $file[3]; }, $files));
    foreach ($files as $file) {
        if ($total <= $limit || $file[2] >= $today) break;
        $writer->close($file[0]);
        if (@unlink($file[0])) {
            $total -= $file[3];
            fwrite(STDERR, 'metrics-logs: over LOGS_MAX_MB, removed ' . basename(dirname($file[0])) . '/' . basename($file[0]) . "\n");
        }
    }
}

// <path>.log -> <path>.log.gz, in pieces; appended when late lines already made a .gz.
function logs_compress($path)
{
    $target = $path . '.gz';
    $temporary = $target . '.' . getmypid();
    $existing = is_file($target);
    $source = @fopen($path, 'rb');
    if ($source === false) return;
    if ($existing && !@copy($target, $temporary)) {
        fclose($source);
        @unlink($temporary);
        throw new RuntimeException('cannot copy ' . $target);
    }
    $gzip = @gzopen($temporary, $existing ? 'ab6' : 'wb6');
    if ($gzip === false) {
        fclose($source);
        @unlink($temporary);
        throw new RuntimeException('cannot write ' . $temporary);
    }
    // A short write (a full disk) keeps the plain file: the day stays readable and is compressed next time.
    $written = true;
    while ($written && !feof($source)) {
        $chunk = fread($source, 1048576);
        if ($chunk === false) break;
        $written = $chunk === '' || @gzwrite($gzip, $chunk) === strlen($chunk);
    }
    fclose($source);
    if (!@gzclose($gzip) || !$written) {
        @unlink($temporary);
        throw new RuntimeException('cannot compress ' . basename($path) . ' (disk full?)');
    }
    chmod($temporary, 0644);
    if (!@rename($temporary, $target)) throw new RuntimeException('cannot replace ' . $target);
    @touch($target, (int) @filemtime($path));
    @unlink($path);
}

// make logs day=...: the day's files of one service (or all), each after a header line, oldest kind first.
function logs_print($day, $service)
{
    if ($day === 'today') $day = date('Y-m-d');
    if ($day === 'yesterday') $day = date('Y-m-d', strtotime('yesterday'));
    if (!preg_match('/^\d{4}-\d\d-\d\d$/', $day)) throw new RuntimeException('day must be YYYY-MM-DD, today or yesterday');
    if ($service !== '' && !logs_valid_service($service)) throw new RuntimeException('not a service name: ' . $service);
    $found = false;
    foreach (logs_files() as $file) {
        if ($file[2] !== $day || ($service !== '' && $file[1] !== $service)) continue;
        $found = true;
        fwrite(STDOUT, '==> ' . $file[1] . '/' . basename($file[0]) . " <==\n");
        readgzfile($file[0]);
    }
    if (!$found) {
        fwrite(STDERR, 'No logs of ' . ($service === '' ? 'any service' : $service) . ' on ' . $day . ' in ' . logs_directory() . "\n");
        exit(1);
    }
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    date_default_timezone_set(collector_setting('TZ', 'UTC'));
    umask(022);
    if (isset($argv[1]) && $argv[1] === '--print') {
        try {
            logs_print(isset($argv[2]) ? $argv[2] : '', isset($argv[3]) ? $argv[3] : '');
        } catch (RuntimeException $error) {
            fwrite(STDERR, $error->getMessage() . "\n");
            exit(64);
        }
        exit(0);
    }
    $project = collector_setting('METRICS_DOCKER_PROJECT', '');
    if (!preg_match('/^[a-z0-9][a-z0-9_-]*$/', $project)) {
        fwrite(STDERR, "metrics-logs: METRICS_DOCKER_PROJECT is not a project name\n");
        exit(1);
    }
    $once = in_array('--once', $argv, true);
    $collector = new LogCollector($project, !$once);
    $next = array('refresh' => 0, 'save' => 0, 'maintain' => 0);
    do {
        $now = time();
        try {
            if ($now >= $next['refresh']) {
                $collector->refresh();
                $next['refresh'] = $now + 5;
            }
            // --once: until every answer has ended.
            $open = $collector->pump(1);
            // Saved every second lines were written (at least every 5 s): after a stop, at most a second of lines is
            // written again.
            if ($now >= $next['save'] || ($once && !$open) || $collector->changed()) {
                $collector->save();
                $next['save'] = $now + 5;
            }
            if ($now >= $next['maintain'] || ($once && !$open)) {
                // Set first: a maintenance that fails is retried in a minute, not on every pass of the loop (each
                // failure sleeps 5 s, which would leave the streams one read per pass behind Docker's rotation).
                $next['maintain'] = $now + 60;
                $collector->maintain($now);
            }
        } catch (RuntimeException $error) {
            fwrite(STDERR, 'metrics-logs: ' . $error->getMessage() . "\n");
            if ($once) exit(1);
            sleep(5);
        }
    } while (!$once || $open);
}
