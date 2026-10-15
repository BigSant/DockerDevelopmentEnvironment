<?php
// Cache group (with SERVICES="redis metrics" or "memcached metrics"): Redis's INFO and memcached's stats over their
// own protocols on the metrics network, METRICS_REDIS / METRICS_MEMCACHED (host:port) set by docker/metrics/redis.yml
// and memcached.yml. Without either service the group is not served at all.

function metrics_cache_enabled()
{
    return metrics_setting('METRICS_REDIS', '') !== '' || metrics_setting('METRICS_MEMCACHED', '') !== '';
}

function metrics_cache_parts()
{
    $parts = array();
    if (metrics_setting('METRICS_REDIS', '') !== '') $parts[] = 'metrics_cache_redis';
    if (metrics_setting('METRICS_MEMCACHED', '') !== '') $parts[] = 'metrics_cache_memcached';
    return $parts;
}

// One command and the whole answer up to $end (a line memcached ends with) or the length Redis announces.
function metrics_cache_ask($address, $command, $end = null)
{
    $socket = @stream_socket_client('tcp://' . $address, $errno, $error, 5);
    if (!$socket) throw new RuntimeException('cannot reach ' . $address . ': ' . $error);
    stream_set_timeout($socket, 5);
    fwrite($socket, $command);
    $answer = '';
    if ($end === null) {
        // RESP bulk string: "$<length>\r\n<data>\r\n".
        $header = (string) fgets($socket);
        if (!preg_match('/^\$(\d+)\r\n$/', $header, $match)) throw new RuntimeException('no INFO answer from ' . $address . ': ' . trim($header));
        while (strlen($answer) < (int) $match[1] && !feof($socket)) {
            $chunk = fread($socket, (int) $match[1] - strlen($answer));
            if ($chunk === false || $chunk === '') break;
            $answer .= $chunk;
        }
    } else {
        while (($line = fgets($socket)) !== false) {
            if (rtrim($line, "\r\n") === $end) break;
            $answer .= $line;
        }
    }
    fclose($socket);
    return $answer;
}

// "key:value" (Redis) or "STAT key value" (memcached) lines as a map.
function metrics_cache_fields($text, $pattern)
{
    $fields = array();
    foreach (preg_split('/\r?\n/', $text) as $line) {
        if (preg_match($pattern, $line, $match)) $fields[$match[1]] = $match[2];
    }
    return $fields;
}

function metrics_cache_redis(Metrics $metrics)
{
    $info = metrics_cache_fields(metrics_cache_ask(metrics_setting('METRICS_REDIS', ''), "*1\r\n\$4\r\nINFO\r\n"), '/^([a-z_0-9]+):(.*)$/');
    if (!isset($info['used_memory'])) throw new RuntimeException('no used_memory in Redis INFO');
    $hits = (int) $info['keyspace_hits'];
    $misses = (int) $info['keyspace_misses'];
    $metrics->add('setup_redis_memory_used_bytes', 'Memory Redis holds for its data (used_memory)', (int) $info['used_memory']);
    $metrics->add('setup_redis_memory_max_bytes', 'Redis\'s memory limit (maxmemory, REDIS_MAXMEMORY_MB; 0 = none)', (int) $info['maxmemory']);
    $metrics->add('setup_redis_keyspace_hits_total', 'Key lookups that found the key since Redis started', $hits, array(), 'counter');
    $metrics->add('setup_redis_keyspace_misses_total', 'Key lookups that did not find the key since Redis started', $misses, array(), 'counter');
    $metrics->add('setup_redis_hit_rate', 'Share of key lookups that found the key since Redis started (percent)',
        $hits + $misses ? round($hits / ($hits + $misses) * 100, 2) : 0);
    $metrics->add('setup_redis_evicted_keys_total', 'Keys removed to stay within maxmemory since Redis started', (int) $info['evicted_keys'], array(), 'counter');
    $metrics->add('setup_redis_expired_keys_total', 'Keys removed at their expiry since Redis started', (int) $info['expired_keys'], array(), 'counter');
    $metrics->add('setup_redis_connected_clients', 'Client connections now', (int) $info['connected_clients']);
    $keys = 0;
    foreach ($info as $name => $value) {
        if (preg_match('/^db\d+$/', $name) && preg_match('/keys=(\d+)/', $value, $match)) $keys += (int) $match[1];
    }
    $metrics->add('setup_redis_keys', 'Keys in every database', $keys);
}

function metrics_cache_memcached(Metrics $metrics)
{
    $stats = metrics_cache_fields(metrics_cache_ask(metrics_setting('METRICS_MEMCACHED', ''), "stats\r\n", 'END'), '/^STAT (\S+) (.*)$/');
    if (!isset($stats['bytes'])) throw new RuntimeException('no bytes in memcached stats');
    $hits = (int) $stats['get_hits'];
    $misses = (int) $stats['get_misses'];
    $metrics->add('setup_memcached_memory_used_bytes', 'Bytes memcached holds for its items', (int) $stats['bytes']);
    $metrics->add('setup_memcached_memory_max_bytes', 'memcached\'s memory limit (MEMCACHED_CACHE_MB)', (int) $stats['limit_maxbytes']);
    $metrics->add('setup_memcached_get_hits_total', 'Reads that found the item since memcached started', $hits, array(), 'counter');
    $metrics->add('setup_memcached_get_misses_total', 'Reads that did not find the item since memcached started', $misses, array(), 'counter');
    $metrics->add('setup_memcached_hit_rate', 'Share of reads that found the item since memcached started (percent)',
        $hits + $misses ? round($hits / ($hits + $misses) * 100, 2) : 0);
    $metrics->add('setup_memcached_evictions_total', 'Items removed to make room since memcached started', (int) $stats['evictions'], array(), 'counter');
    $metrics->add('setup_memcached_items', 'Items held now', (int) $stats['curr_items']);
    $metrics->add('setup_memcached_connections', 'Client connections now', (int) $stats['curr_connections']);
}
