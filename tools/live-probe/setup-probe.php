<?php
/**
 * setup LIVE environment probe (setup v2, github.com/BigSant/DockerDevelopmentEnvironment).
 *
 * One read-only PHP file that is uploaded to a LIVE server for a short time. It reports the
 * setup settings (env/common.env, env/prod.env, config/php, config/mysql, a project Dockerfile)
 * that rebuild the LIVE environment in setup, plus the details and risks an LLM needs to prepare
 * the project's environment repository.
 *
 * Guarantees: read-only (SELECT/SHOW only; the only write is ?action=delete removing the probe
 * itself), no PrestaShop boot, no personal data (no customer/order rows, no e-mail addresses),
 * no secret values (passwords and keys are reported as present/absent and scrubbed from every
 * output). Runs on PHP 5.6 - 8.5. Usage: tools/live-probe/README.md in the setup repository.
 *
 * REMOVAL IS PART OF THE JOB: delete the probe directory as soon as you have the result.
 */

// ============================ CONFIGURATION (fill in before upload) ============================
// Every empty value refuses to run over HTTP (no defaults on purpose). CLI runs need none of them.

// At least 24 random characters: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
define('PROBE_TOKEN', '');
// Your public address(es), comma-separated (curl -s https://ifconfig.me). Compared with REMOTE_ADDR only.
define('PROBE_ALLOWED_IPS', '');
// Hard expiry in UTC, at most 7 days ahead, format 'YYYY-MM-DD HH:MM'.
define('PROBE_EXPIRES', '');
// Optional: the application root ('' = the directory above the probe directory).
define('PROBE_ROOT', '');
// Optional: a generic application's DB config relative to the root ('.env', 'config/db.php' with define()s).
define('PROBE_APP_CONFIG', '');

// ===============================================================================================

define('SP_VERSION', '1.0.0');
define('SP_MAX_EXPIRY_DAYS', 7);
define('SP_SCAN_SECONDS', 8);
define('SP_SCAN_FILES', 300000);

$GLOBALS['SP_NOTES'] = array();
$GLOBALS['SP_SECRETS'] = array();

sp_main();

// ------------------------------------------------------------------------------------------------
// Entry, options and the gate
// ------------------------------------------------------------------------------------------------

function sp_main()
{
    $cli = PHP_SAPI === 'cli';
    @ini_set('display_errors', '0');
    @ini_set('html_errors', '0');
    error_reporting(E_ALL);
    set_error_handler('sp_error_handler');
    // Old PHP warns on every date call without date.timezone: remember its guess, then work in UTC.
    $GLOBALS['SP_TZ_DEFAULT'] = @date_default_timezone_get();
    if ((string) ini_get('date.timezone') === '') {
        @date_default_timezone_set('UTC');
    }
    if (sp_fn('set_time_limit')) {
        @set_time_limit(120);
    }
    $opts = $cli ? sp_cli_options() : sp_web_options();
    if ($cli && $opts['help']) {
        echo sp_help_text();
        return;
    }
    if ($cli && $opts['setup_data']) {
        echo sp_json_encode(sp_setup_data()) . "\n";
        return;
    }
    if (!$cli) {
        sp_send_headers('text/plain');
        $denied = sp_gate($opts);
        if ($denied !== null) {
            http_response_code(403);
            echo '403 Forbidden: ' . $denied . "\n\n" . sp_reminder_text() . "\n";
            return;
        }
    }
    if ($opts['action'] === 'delete') {
        echo sp_scrub(sp_delete_probe());
        return;
    }
    try {
        $result = sp_collect($opts);
        $text = sp_render($result, $opts['format']);
    } catch (Exception $e) {
        $text = sp_failure_text($e);
    } catch (Throwable $e) {
        $text = sp_failure_text($e);
    }
    if (!$cli && $opts['format'] === 'json') {
        sp_send_headers('application/json');
    }
    echo sp_scrub($text);
}

/** Last resort: a probe error still answers with the reason, the notes and the reminder. */
function sp_failure_text($error)
{
    return "setup LIVE probe " . SP_VERSION . " failed: " . get_class($error) . ': ' . $error->getMessage()
        . ' (line ' . $error->getLine() . ")\nNotes: " . implode('; ', $GLOBALS['SP_NOTES']) . "\n\n" . sp_reminder_text() . "\n";
}

/** Runs one collection step; a failure becomes a note and the step's fallback value. */
function sp_try($function, $args, $fallback)
{
    try {
        return call_user_func_array($function, $args);
    } catch (Exception $e) {
        sp_note($function . ' failed: ' . $e->getMessage());
    } catch (Throwable $e) {
        sp_note($function . ' failed: ' . $e->getMessage());
    }
    return $fallback;
}

function sp_help_text()
{
    return "setup LIVE probe " . SP_VERSION . "\n\n"
        . "php setup-probe.php [--format=md|env|json] [--root=DIR] [--config=FILE]\n"
        . "php setup-probe.php --action=delete     remove the probe (its directory when dedicated)\n"
        . "php setup-probe.php --setup-data        the setup tables embedded in the probe (JSON)\n\n"
        . "--root    application root (default: the directory above the probe, then the probe directory)\n"
        . "--config  generic application DB config relative to the root (.env or a PHP file with define())\n";
}

function sp_cli_options()
{
    $opts = array('format' => 'md', 'root' => '', 'config' => '', 'action' => '', 'help' => false, 'setup_data' => false);
    $argv = isset($_SERVER['argv']) && is_array($_SERVER['argv']) ? $_SERVER['argv'] : array();
    foreach (array_slice($argv, 1) as $arg) {
        $arg = (string) $arg;
        if ($arg === '--help' || $arg === '-h') {
            $opts['help'] = true;
        } elseif ($arg === '--setup-data') {
            $opts['setup_data'] = true;
        } elseif ($arg === '--delete' || $arg === '--action=delete') {
            $opts['action'] = 'delete';
        } elseif (preg_match('/^--format=(md|env|json)$/', $arg, $m)) {
            $opts['format'] = $m[1];
        } elseif (preg_match('/^--root=(.+)$/', $arg, $m)) {
            $opts['root'] = $m[1];
        } elseif (preg_match('/^--config=(.+)$/', $arg, $m)) {
            $opts['config'] = $m[1];
        } else {
            sp_note('unknown option ignored: ' . $arg);
        }
    }
    return $opts;
}

function sp_web_options()
{
    $format = isset($_GET['format']) && is_string($_GET['format']) ? $_GET['format'] : 'md';
    if (!in_array($format, array('md', 'env', 'json'), true)) {
        $format = 'md';
    }
    $action = isset($_GET['action']) && $_GET['action'] === 'delete' ? 'delete' : '';
    // The root and the config file come from the constants only: never from the request.
    return array('format' => $format, 'root' => '', 'config' => '', 'action' => $action,
                 'help' => false, 'setup_data' => false);
}

function sp_send_headers($type)
{
    if (headers_sent()) {
        return;
    }
    header('Content-Type: ' . $type . '; charset=utf-8');
    header('X-Robots-Tag: noindex, nofollow');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    header('Referrer-Policy: no-referrer');
}

/** Returns null when the request may continue, otherwise the reason it is refused. */
function sp_gate($opts)
{
    $problems = sp_config_problems();
    if ($problems) {
        return 'probe not configured (' . implode('; ', $problems) . ')';
    }
    $given = '';
    if (isset($_GET['token']) && is_string($_GET['token'])) {
        $given = $_GET['token'];
    } elseif (isset($_SERVER['HTTP_X_PROBE_TOKEN']) && is_string($_SERVER['HTTP_X_PROBE_TOKEN'])) {
        $given = $_SERVER['HTTP_X_PROBE_TOKEN'];
    }
    // hash_equals: constant-time comparison; an absent token can never equal the configured one.
    if ($given === '' || !hash_equals((string) PROBE_TOKEN, $given)) {
        return 'bad token';
    }
    // REMOTE_ADDR only: X-Forwarded-For is set by the client and is useless for an allowlist.
    $ip = isset($_SERVER['REMOTE_ADDR']) ? (string) $_SERVER['REMOTE_ADDR'] : '';
    $allowed = false;
    foreach (sp_allowed_ips() as $candidate) {
        if (sp_same_ip($candidate, $ip)) {
            $allowed = true;
        }
    }
    if (!$allowed) {
        return 'ip not allowed (REMOTE_ADDR ' . $ip . ')';
    }
    // An expired probe still accepts ?action=delete: removing it is the point.
    if ($opts['action'] !== 'delete' && time() > sp_expiry()) {
        return 'probe expired at ' . PROBE_EXPIRES . ' UTC; remove it with ?action=delete';
    }
    return null;
}

function sp_config_problems()
{
    $problems = array();
    $token = (string) PROBE_TOKEN;
    if (strlen($token) < 24) {
        $problems[] = 'PROBE_TOKEN must hold at least 24 random characters';
    } elseif (count(array_unique(str_split($token))) < 10) {
        $problems[] = 'PROBE_TOKEN is not random enough';
    }
    $ips = sp_allowed_ips();
    if (!$ips) {
        $problems[] = 'PROBE_ALLOWED_IPS is empty';
    }
    foreach ($ips as $ip) {
        if (filter_var($ip, FILTER_VALIDATE_IP) === false) {
            $problems[] = 'PROBE_ALLOWED_IPS holds an invalid address';
            break;
        }
    }
    $expiry = sp_expiry();
    if ($expiry === null) {
        $problems[] = 'PROBE_EXPIRES must be a UTC time "YYYY-MM-DD HH:MM"';
    } elseif ($expiry > time() + SP_MAX_EXPIRY_DAYS * 86400 + 3600) {
        $problems[] = 'PROBE_EXPIRES is more than ' . SP_MAX_EXPIRY_DAYS . ' days ahead';
    }
    return $problems;
}

function sp_allowed_ips()
{
    $ips = array();
    foreach (preg_split('/[\s,]+/', (string) PROBE_ALLOWED_IPS) as $ip) {
        if ($ip !== '') {
            $ips[] = $ip;
        }
    }
    return $ips;
}

function sp_same_ip($a, $b)
{
    if ($a === '' || $b === '') {
        return false;
    }
    $pa = @inet_pton($a);
    $pb = @inet_pton($b);
    return $pa !== false && $pb !== false && $pa === $pb;
}

/** Unix time of PROBE_EXPIRES, or null when empty or unreadable (that refuses, never "forever"). */
function sp_expiry()
{
    $value = trim((string) PROBE_EXPIRES);
    if (!preg_match('/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$/', $value)) {
        return null;
    }
    $time = strtotime(str_replace('T', ' ', $value) . ' UTC');
    return $time === false ? null : $time;
}

function sp_reminder_text()
{
    return 'REMINDER: remove the probe from the server as soon as you have the result: '
        . 'request setup-probe.php?action=delete&token=<TOKEN> (or delete its directory), then check that '
        . 'the URL returns 404. Removal is part of the job.';
}

/** Removes the probe; its directory too when it holds nothing but the probe files. */
function sp_delete_probe()
{
    $dir = __DIR__;
    $self = __FILE__;
    $own = array(basename($self), '.htaccess', 'README.md');
    $entries = @scandir($dir);
    $foreign = array();
    if (is_array($entries)) {
        foreach ($entries as $entry) {
            if ($entry !== '.' && $entry !== '..' && !in_array($entry, $own, true)) {
                $foreign[] = $entry;
            }
        }
    }
    $docroot = sp_server('DOCUMENT_ROOT');
    $is_docroot = $docroot !== '' && @realpath($docroot) === @realpath($dir);
    $dedicated = is_array($entries) && !$foreign && !$is_docroot && !sp_looks_like_app($dir);
    $lines = array('setup probe removal');
    if ($dedicated) {
        foreach ($own as $file) {
            if (file_exists($dir . '/' . $file)) {
                $lines[] = (@unlink($dir . '/' . $file) ? 'deleted ' : 'could NOT delete ') . $file;
            }
        }
        $lines[] = (@rmdir($dir) ? 'removed directory ' : 'could NOT remove directory ') . basename($dir);
    } else {
        $lines[] = (@unlink($self) ? 'deleted ' : 'could NOT delete ') . basename($self);
        $lines[] = 'directory kept: ' . ($foreign ? 'it also holds ' . implode(', ', array_slice($foreign, 0, 10))
            : 'it is the application or document root') . ' (delete the probe .htaccess yourself only if it is the probe\'s)';
    }
    clearstatcache();
    $lines[] = file_exists($self) ? 'WARNING: the probe file is STILL PRESENT - delete it by FTP/SSH now.'
        : 'Check: the probe URL must now return 404. Delete local copies that hold the token too.';
    return implode("\n", $lines) . "\n";
}

// ------------------------------------------------------------------------------------------------
// Small helpers (PHP 5.6 compatible: no ??, no scalar type hints, no arrow functions)
// ------------------------------------------------------------------------------------------------

function sp_error_handler($number, $message, $file = '', $line = 0)
{
    if (!(error_reporting() & $number)) {
        return true;
    }
    if (count($GLOBALS['SP_NOTES']) < 40) {
        $GLOBALS['SP_NOTES'][] = 'PHP notice: ' . $message;
    }
    return true;
}

function sp_note($message)
{
    if (count($GLOBALS['SP_NOTES']) < 60) {
        $GLOBALS['SP_NOTES'][] = $message;
    }
}

/** Remembers a secret value so that sp_scrub() removes it from any output. */
function sp_secret($value)
{
    if (is_string($value) && strlen($value) >= 6) {
        $GLOBALS['SP_SECRETS'][$value] = true;
    }
}

function sp_scrub($text)
{
    $secrets = array_keys($GLOBALS['SP_SECRETS']);
    usort($secrets, 'sp_longer_first');
    foreach ($secrets as $secret) {
        $secret = (string) $secret;
        $escaped = substr((string) json_encode($secret, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), 1, -1);
        $text = str_replace(array($secret, $escaped), '[redacted]', $text);
    }
    return $text;
}

function sp_longer_first($a, $b)
{
    return strlen((string) $b) - strlen((string) $a);
}

function sp_get($array, $key, $default = null)
{
    return is_array($array) && array_key_exists($key, $array) ? $array[$key] : $default;
}

function sp_server($key)
{
    return isset($_SERVER[$key]) && is_scalar($_SERVER[$key]) ? (string) $_SERVER[$key] : '';
}

function sp_disabled_functions()
{
    static $disabled = null;
    if ($disabled === null) {
        $disabled = array();
        foreach (explode(',', strtolower((string) ini_get('disable_functions'))) as $name) {
            if (trim($name) !== '') {
                $disabled[] = trim($name);
            }
        }
    }
    return $disabled;
}

function sp_fn($name)
{
    return function_exists($name) && !in_array(strtolower($name), sp_disabled_functions(), true);
}

function sp_read($path, $max = 1048576)
{
    if (!@is_file($path) || !@is_readable($path)) {
        return null;
    }
    $data = @file_get_contents($path, false, null, 0, $max);
    return $data === false ? null : $data;
}

function sp_ini($key)
{
    $value = @ini_get($key);
    return $value === false ? null : (string) $value;
}

/** Bytes of an ini shorthand (512M, 1G, 4096K, -1); null when empty. */
function sp_bytes($value)
{
    $value = trim((string) $value);
    if ($value === '') {
        return null;
    }
    if (!preg_match('/^(-?\d+(?:\.\d+)?)\s*([kmgt]?)b?$/i', $value, $m)) {
        return null;
    }
    $number = (float) $m[1];
    $units = array('' => 1, 'k' => 1024, 'm' => 1048576, 'g' => 1073741824, 't' => 1099511627776);
    return (int) round($number * $units[strtolower($m[2])]);
}

function sp_human($bytes)
{
    if ($bytes === null) {
        return 'unknown';
    }
    $units = array('B', 'KB', 'MB', 'GB', 'TB');
    $i = 0;
    $value = (float) $bytes;
    while ($value >= 1024 && $i < 4) {
        $value /= 1024;
        $i++;
    }
    return ($i === 0 ? (string) (int) $value : number_format($value, 1, '.', '')) . ' ' . $units[$i];
}

function sp_is_on($value)
{
    return in_array(strtolower(trim((string) $value)), array('1', 'on', 'yes', 'true'), true);
}

/** Masks secrets inside free text (connection strings, query parameters, URLs, e-mail addresses). */
function sp_redact_text($text)
{
    $text = (string) $text;
    $text = preg_replace('#(//[^/\s:@]+:)[^@/\s]+@#', '$1[redacted]@', $text);
    $text = preg_replace('/((?:auth|password|passwd|pass|pwd|secret|token|key|secure_key|apikey|api_key|hash)=)[^&\s,;"\']+/i', '$1[redacted]', $text);
    $text = preg_replace('/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/', '[email]', $text);
    return $text;
}

function sp_utf8($value)
{
    if (is_array($value)) {
        $out = array();
        foreach ($value as $key => $item) {
            $out[sp_utf8($key)] = sp_utf8($item);
        }
        return $out;
    }
    if (is_string($value) && !preg_match('//u', $value)) {
        return preg_replace('/[\x80-\xFF]/', '?', $value);
    }
    return $value;
}

function sp_json_encode($value)
{
    $flags = JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE;
    if (defined('JSON_PARTIAL_OUTPUT_ON_ERROR')) {
        $flags |= JSON_PARTIAL_OUTPUT_ON_ERROR;
    }
    $json = json_encode(sp_utf8($value), $flags);
    return $json === false ? '{"error": "json_encode failed"}' : $json;
}

function sp_branch($version)
{
    $parts = explode('.', (string) $version);
    return count($parts) >= 2 ? $parts[0] . '.' . $parts[1] : (string) $version;
}

function sp_rel($root, $path)
{
    $root = rtrim($root, '/');
    return strpos($path, $root . '/') === 0 ? substr($path, strlen($root) + 1) : $path;
}

// ------------------------------------------------------------------------------------------------
// setup tables embedded in the probe (tests/test_live_probe.py keeps them in sync with setup)
// ------------------------------------------------------------------------------------------------

function sp_setup_data()
{
    return array(
        'probe_version' => SP_VERSION,
        'setup_reference' => '2.3.0',
        'php_branches' => array('5.6', '7.0', '7.1', '7.2', '7.3', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'),
        'defaults' => array(
            'PHP_VERSION' => '8.1', 'MYSQL_VERSION' => '8.4.0', 'MARIADB_VERSION' => '11.3.2', 'TZ' => 'Europe/Vilnius',
            'PHP_MEMORY_LIMIT' => '512M', 'PHP_MAX_EXECUTION_TIME' => '30', 'PHP_UPLOAD_MAX_FILESIZE' => '20M',
            'PHP_POST_MAX_SIZE' => '20M', 'PS_DATABASE_PREFIX' => 'ps_', 'DOCUMENT_ROOT' => '/',
        ),
        // docker/compatibility.json
        'compatibility' => array(
            '1.6' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3', '10.4')),
            '1.7.0' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3')),
            '1.7.1' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3')),
            '1.7.2' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3')),
            '1.7.3' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3')),
            '1.7.4' => array('php' => array('5.6', '7.0', '7.1'), 'mysql' => array('5.5', '5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3')),
            '1.7.5' => array('php' => array('5.6', '7.0', '7.1', '7.2'), 'mysql' => array('5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3', '10.4')),
            '1.7.6' => array('php' => array('5.6', '7.0', '7.1', '7.2'), 'mysql' => array('5.6', '5.7'), 'mariadb' => array('10.1', '10.2', '10.3', '10.4')),
            '1.7.7' => array('php' => array('7.1', '7.2', '7.3'), 'mysql' => array('5.6', '5.7', '8.0'), 'mariadb' => array('10.2', '10.3', '10.4', '10.5')),
            '1.7.8' => array('php' => array('7.1', '7.2', '7.3', '7.4'), 'mysql' => array('5.6', '5.7', '8.0'), 'mariadb' => array('10.2', '10.3', '10.4', '10.5', '10.6')),
            '8.0' => array('php' => array('7.2', '7.3', '7.4', '8.0', '8.1'), 'mysql' => array('5.6', '5.7', '8.0'), 'mariadb' => array('10.2', '10.3', '10.4', '10.5', '10.6', '10.11')),
            '8.1' => array('php' => array('7.2', '7.3', '7.4', '8.0', '8.1'), 'mysql' => array('5.6', '5.7', '8.0'), 'mariadb' => array('10.2', '10.3', '10.4', '10.5', '10.6', '10.11')),
            '8.2' => array('php' => array('7.2', '7.3', '7.4', '8.0', '8.1'), 'mysql' => array('5.6', '5.7', '8.0', '8.4'), 'mariadb' => array('10.2', '10.3', '10.4', '10.5', '10.6', '10.11')),
            '9.0' => array('php' => array('8.1', '8.2', '8.3', '8.4'), 'mysql' => array('5.7', '8.0', '8.4'), 'mariadb' => array('10.4', '10.5', '10.6', '10.11', '11.4')),
            '9.1' => array('php' => array('8.1', '8.2', '8.3', '8.4', '8.5'), 'mysql' => array('5.7', '8.0', '8.4'), 'mariadb' => array('10.4', '10.5', '10.6', '10.11', '11.4')),
        ),
        // docker/docker/php-fpm/<branch>/conf/php.ini (values a project may need to change with a drop-in)
        'core_ini' => sp_core_ini(),
        'core_ini_by_branch' => array(
            'opcache.memory_consumption' => array('default' => '256', '8.0' => '128', '8.1' => '128', '8.3' => '128', '8.4' => '128', '8.5' => '128'),
            'opcache.max_accelerated_files' => array('default' => '20000', '8.0' => '30000', '8.1' => '30000', '8.3' => '30000', '8.4' => '30000', '8.5' => '30000'),
        ),
        // ini values setup sets from env (docker/docker/php-fpm/*/conf/php-fpm.conf, runtime/php-policy)
        'env_ini' => array(
            'memory_limit' => 'PHP_MEMORY_LIMIT', 'max_execution_time' => 'PHP_MAX_EXECUTION_TIME',
            'upload_max_filesize' => 'PHP_UPLOAD_MAX_FILESIZE', 'post_max_size' => 'PHP_POST_MAX_SIZE',
            'date.timezone' => 'TZ', 'opcache.enable' => 'PHP_OPCACHE', 'opcache.validate_timestamps' => 'PHP_OPCACHE_VALIDATE_TIMESTAMPS',
            'opcache.revalidate_freq' => 'PHP_OPCACHE_REVALIDATE_FREQ', 'apc.enabled' => 'PHP_APCU',
        ),
        // docker-php-ext-install / pecl lines of docker/docker/php-fpm/<branch>/Dockerfile
        'installed_extensions' => sp_installed_extensions(),
        'optional_extensions' => array('redis', 'memcached'),
        // modules of the CORE Apache image: httpd:2.4 defaults + docker/docker/apache/conf/httpd.custom.conf
        'apache_modules' => sp_setup_apache_modules(),
        // docker/database-profile/prestashop/table-registry.txt (names without {p})
        'ps_registry' => array('known' => sp_ps_registry_known(), 'skip' => sp_ps_registry_skip()),
    );
}

function sp_core_ini()
{
    return array(
        'max_input_vars' => array('20000', 'int'), 'max_input_time' => array('60', 'int'),
        'max_file_uploads' => array('20', 'int'), 'max_input_nesting_level' => array('64', 'int'),
        'realpath_cache_size' => array('4096K', 'size'), 'realpath_cache_ttl' => array('600', 'int'),
        'allow_url_fopen' => array('On', 'bool'), 'short_open_tag' => array('Off', 'bool'),
        'output_buffering' => array('4096', 'int'), 'default_socket_timeout' => array('60', 'int'),
        'default_charset' => array('UTF-8', 'string'), 'precision' => array('14', 'int'),
        'serialize_precision' => array('17', 'int'), 'session.save_handler' => array('files', 'string'),
        'session.gc_maxlifetime' => array('1440', 'int'), 'session.cookie_lifetime' => array('0', 'int'),
        'opcache.memory_consumption' => array('128', 'int'), 'opcache.interned_strings_buffer' => array('16', 'int'),
        'opcache.max_accelerated_files' => array('30000', 'int'), 'pcre.backtrack_limit' => array('1000000', 'int'),
        'apc.shm_size' => array('32M', 'size'), 'mbstring.func_overload' => array('0', 'int'),
        'zend.assertions' => array('-1', 'int'),
    );
}

function sp_installed_extensions()
{
    $common = array('iconv', 'intl', 'pdo_mysql', 'mbstring', 'soap', 'gd', 'zip', 'bcmath', 'exif', 'opcache');
    $out = array('5.6' => array());
    foreach (array('7.0', '7.1') as $branch) {
        $out[$branch] = array_merge($common, array('mcrypt'));
    }
    foreach (array('7.2', '7.3', '7.4') as $branch) {
        $out[$branch] = $common;
    }
    foreach (array('8.0', '8.1', '8.2', '8.3', '8.4', '8.5') as $branch) {
        $out[$branch] = array_merge($common, array('apcu'));
    }
    // PHP 8.5 builds OPcache into the binary: the Dockerfile no longer installs it.
    $out['8.5'] = array_values(array_diff($out['8.5'], array('opcache')));
    return $out;
}

/** Extensions loaded in the CORE image of a PHP branch: official php image defaults + setup installs. */
function sp_core_extensions($branch)
{
    $base = array('core', 'ctype', 'curl', 'date', 'dom', 'fileinfo', 'filter', 'ftp', 'hash', 'iconv', 'json',
        'libxml', 'mbstring', 'mysqlnd', 'openssl', 'pcre', 'pdo', 'pdo_sqlite', 'phar', 'posix', 'readline',
        'reflection', 'session', 'simplexml', 'spl', 'sqlite3', 'standard', 'tokenizer', 'xml', 'xmlreader',
        'xmlwriter', 'zlib');
    if ($branch === '5.6') {
        $base = array_merge($base, array('ereg', 'mhash'));
    }
    if (version_compare($branch, '7.2', '>=')) {
        $base[] = 'sodium';
    }
    if (version_compare($branch, '8.2', '>=')) {
        $base[] = 'random';
    }
    if (version_compare($branch, '8.5', '>=')) {
        $base = array_merge($base, array('lexbor', 'uri', 'opcache'));
    }
    $installed = sp_installed_extensions();
    return array_values(array_unique(array_merge($base, isset($installed[$branch]) ? $installed[$branch] : array())));
}

function sp_setup_apache_modules()
{
    return array('authn_file', 'authn_core', 'authz_host', 'authz_groupfile', 'authz_user', 'authz_core',
        'access_compat', 'auth_basic', 'reqtimeout', 'filter', 'mime', 'log_config', 'env', 'headers',
        'setenvif', 'version', 'unixd', 'status', 'autoindex', 'dir', 'alias', 'proxy', 'proxy_fcgi',
        'proxy_http', 'rewrite', 'expires', 'deflate', 'remoteip', 'mpm_event');
}

function sp_ps_registry_known()
{
    return preg_split('/\s+/', trim('
accessory address alias attachment attachment_lang product_attachment carrier carrier_lang carrier_zone cart
cart_rule cart_rule_lang cart_rule_country cart_rule_group cart_rule_carrier cart_rule_combination
cart_rule_product_rule_group cart_rule_product_rule cart_rule_product_rule_value cart_cart_rule
cart_rule_shop cart_product category category_group category_lang category_product cms cms_lang cms_category
cms_category_lang cms_category_shop configuration configuration_lang configuration_kpi configuration_kpi_lang
connections connections_page connections_source contact contact_lang country country_lang currency
currency_lang customer customer_group customer_message customer_message_sync_imap customer_thread
customization customization_field customization_field_lang customized_data date_range delivery employee
employee_shop feature feature_lang feature_product feature_value feature_value_lang group group_lang
group_reduction product_group_reduction_cache product_carrier guest hook hook_alias hook_module
hook_module_exceptions image image_lang manufacturer manufacturer_lang message message_readed meta meta_lang
module authorization_role module_access module_country module_currency module_group module_carrier
operating_system orders order_detail_tax order_invoice order_detail order_cart_rule order_history
order_message order_message_lang order_return order_return_detail order_return_state order_return_state_lang
order_slip order_slip_detail order_state order_state_lang pack page page_type page_viewed order_payment
product product_attribute product_attribute_lang product_attribute_shop product_attribute_combination
product_attribute_image product_download product_lang product_sale product_tag profile profile_lang
quick_access quick_access_lang range_price range_weight search_engine search_index search_word specific_price
state supplier supplier_lang tag tag_count tax tax_lang timezone web_browser zone carrier_group store
webservice_account webservice_permission required_field memcached_servers product_country_tax tax_rule
tax_rules_group specific_price_priority log import_match country_shop carrier_shop address_format cms_shop
currency_shop contact_shop image_shop feature_shop group_shop tax_rules_group_shop zone_shop
manufacturer_shop supplier_shop store_lang store_shop module_shop webservice_account_shop stock_mvt_reason
stock_mvt_reason_lang stock warehouse warehouse_product_location warehouse_shop warehouse_carrier
stock_available supply_order supply_order_detail supply_order_history supply_order_state
supply_order_state_lang supply_order_receipt_history product_supplier order_carrier specific_price_rule
specific_price_rule_condition_group specific_price_rule_condition category_shop module_preference
tab_module_preference carrier_tax_rules_group_shop order_invoice_tax order_invoice_payment smarty_cache
smarty_lazy_cache smarty_last_flush employee_session customer_session translation lang lang_shop mutation
image_type attribute_group attribute_group_lang attribute_group_shop attribute attribute_lang attribute_shop
tab tab_lang admin_filter shop shop_url shop_group module_history feature_flag api_client stock_mvt access
product_shop mail statssearch pagenotfound sekeyword referrer referrer_cache referrer_shop
layered_price_index layered_product_attribute layered_filter_block layered_category layered_filter
layered_filter_shop layered_indexable_attribute_group layered_indexable_attribute_group_lang_value
layered_indexable_attribute_lang_value layered_indexable_feature layered_indexable_feature_lang_value
layered_indexable_feature_value_lang_value emailsubscription newsletter linksmenutop linksmenutop_lang
cms_role cms_role_lang request_sql gender gender_lang risk risk_lang theme theme_meta theme_specific advice
advice_lang badge badge_lang condition condition_advice condition_badge tab_advice compare compare_product
product_comment product_comment_criterion'));
}

function sp_ps_registry_skip()
{
    return preg_split('/\s+/', trim('
connections connections_page connections_source date_range guest page_viewed search_index
search_word log smarty_cache smarty_lazy_cache smarty_last_flush employee_session customer_session
mail statssearch pagenotfound sekeyword referrer_cache layered_price_index compare compare_product'));
}

// ------------------------------------------------------------------------------------------------
// Collection
// ------------------------------------------------------------------------------------------------

function sp_collect($opts)
{
    $cli = PHP_SAPI === 'cli';
    $root_info = sp_root($opts);
    $root = $root_info[0];
    $result = array();
    $result['_meta'] = array(
        'probe' => 'setup-live-probe',
        'version' => SP_VERSION,
        'setup_reference' => '2.3.0',
        'collected_at' => gmdate('Y-m-d\TH:i:s\Z'),
        'expires_at' => $cli ? null : PROBE_EXPIRES . ' UTC',
        'mode' => $cli ? 'cli' : 'web',
        'root' => $root,
        'root_source' => $root_info[1],
        'reminder' => sp_reminder_text(),
        'guarantees' => array(
            'read_only' => true,
            'select_and_show_only' => true,
            'no_prestashop_boot' => true,
            'no_personal_data' => true,
            'no_secret_values' => true,
            'row_counts_are_exact' => false,
        ),
    );
    $result['php'] = sp_collect_php();
    $result['webserver'] = sp_try('sp_collect_webserver', array($root, $cli), array(
        'type' => 'unknown', 'server_software' => null, 'sapi' => PHP_SAPI, 'php_handler' => PHP_SAPI, 'apache_modules' => null,
        'https' => 'unknown', 'proxy_hints' => array(), 'http_host' => null, 'document_root' => null, 'document_root_relation' => 'unknown',
        'document_root_setting' => null, 'htaccess' => array('present' => false), 'user_ini' => array(), 'panel' => array()));
    $platform = sp_try('sp_detect_platform', array($root), array('type' => 'generic', 'name' => 'unknown', 'version' => null, 'source' => null, 'notes' => array()));
    $result['application'] = $platform;
    $result['composer'] = sp_try('sp_collect_composer', array($root), array('present' => false));
    $public = array('file' => null, 'format' => null, 'keys' => array(), 'values' => array(), 'env_names' => array());
    $config = sp_try('sp_app_config', array($root, $platform, $opts['config'] !== '' ? $opts['config'] : (string) PROBE_APP_CONFIG),
        array('db' => null, 'public' => $public));
    $result['application']['config'] = $config['public'];
    $db = sp_try('sp_db_connect', array($config['db']), array('ok' => false, 'driver' => null, 'link' => null, 'error' => 'probe error'));
    $result['database'] = sp_try('sp_collect_database', array($db, $config['db']),
        array('configured' => false, 'config_source' => null, 'available' => false, 'error' => 'probe error (see notes)'));
    if (!empty($result['database']['available']) && !isset($result['database']['tables'])) {
        $result['database']['available'] = false;
    }
    $result['prestashop'] = sp_is_ps($platform) ? sp_try('sp_collect_prestashop', array($root, $platform, $config, $db, $result['database']), null) : null;
    $result['host'] = sp_try('sp_collect_host', array($root, $platform), array(
        'os' => PHP_OS, 'uname' => null, 'hostname' => null, 'timezone' => array('php_default' => $GLOBALS['SP_TZ_DEFAULT'], 'etc_timezone' => '', 'env_tz' => null),
        'locale' => array('lc_all' => null, 'lang' => null), 'disk' => array('free_bytes' => null, 'total_bytes' => null, 'used_bytes' => null),
        'uid' => null, 'gid' => null, 'uid_source' => 'unknown', 'open_basedir' => null,
        'services' => array('redis' => array('extension' => false, 'localhost_port' => 6379, 'reachable' => null),
                            'memcached' => array('extension' => false, 'localhost_port' => 11211, 'reachable' => null)),
        'directories' => array('limit' => '', 'entries' => array())));
    $result['cron'] = sp_try('sp_collect_cron', array($root), array('available' => false, 'reason' => 'probe error'));
    $result['extensions'] = sp_analyse_extensions($result);
    $result['setup'] = sp_plan($result);
    $result['risks'] = sp_risks($result);
    $result['_meta']['warnings'] = $GLOBALS['SP_NOTES'];
    return $result;
}

function sp_looks_like_app($dir)
{
    foreach (array('config/settings.inc.php', 'app/config/parameters.php', 'app/AppKernel.php', 'composer.json',
                   'index.php', 'artisan', 'wp-config.php') as $marker) {
        if (@file_exists($dir . '/' . $marker)) {
            return true;
        }
    }
    return false;
}

function sp_root($opts)
{
    if ($opts['root'] !== '') {
        $real = @realpath($opts['root']);
        return array($real !== false ? $real : rtrim($opts['root'], '/'), '--root');
    }
    if ((string) PROBE_ROOT !== '') {
        $real = @realpath(PROBE_ROOT);
        return array($real !== false ? $real : rtrim(PROBE_ROOT, '/'), 'PROBE_ROOT');
    }
    $parent = dirname(__DIR__);
    if (sp_looks_like_app($parent)) {
        return array($parent, 'the directory above the probe');
    }
    if (sp_looks_like_app(__DIR__)) {
        return array(__DIR__, 'the probe directory');
    }
    $docroot = sp_server('DOCUMENT_ROOT');
    if ($docroot !== '' && sp_looks_like_app($docroot)) {
        return array(rtrim($docroot, '/'), 'DOCUMENT_ROOT');
    }
    sp_note('no application markers found; using the directory above the probe as the root');
    return array($parent, 'the directory above the probe (no application markers found)');
}

function sp_collect_php()
{
    $keys = array_merge(array_keys(sp_core_ini()), array(
        'memory_limit', 'max_execution_time', 'upload_max_filesize', 'post_max_size', 'date.timezone',
        'opcache.enable', 'opcache.enable_cli', 'opcache.validate_timestamps', 'opcache.revalidate_freq',
        'opcache.jit', 'opcache.jit_buffer_size', 'apc.enabled', 'display_errors', 'display_startup_errors',
        'log_errors', 'error_reporting', 'expose_php', 'auto_prepend_file', 'auto_append_file',
        'open_basedir', 'disable_functions', 'disable_classes', 'session.save_path', 'session.cookie_secure',
        'session.use_strict_mode', 'upload_tmp_dir', 'sys_temp_dir', 'user_ini.filename', 'allow_url_include',
        'mysqli.allow_local_infile', 'pdo_mysql.default_socket', 'mysqli.default_socket', 'sendmail_path',
        'mail.add_x_header', 'zend.enable_gc', 'max_input_nesting_level', 'pcre.jit', 'xdebug.mode'));
    $ini = array();
    foreach ($keys as $key) {
        $value = sp_ini($key);
        if ($key === 'session.save_path' || $key === 'sendmail_path') {
            $value = $value === null ? null : sp_redact_text($value);
        }
        $ini[$key] = $value;
    }
    $opcache = null;
    if (sp_fn('opcache_get_status')) {
        $status = @opcache_get_status(false);
        if (is_array($status)) {
            $memory = sp_get($status, 'memory_usage', array());
            $opcache = array(
                'enabled' => (bool) sp_get($status, 'opcache_enabled'),
                'used_memory' => sp_get($memory, 'used_memory'),
                'free_memory' => sp_get($memory, 'free_memory'),
                'cached_scripts' => sp_get(sp_get($status, 'opcache_statistics', array()), 'num_cached_scripts'),
            );
        }
    }
    $scanned = array();
    if (sp_fn('php_ini_scanned_files')) {
        foreach (explode(',', (string) php_ini_scanned_files()) as $file) {
            if (trim($file) !== '') {
                $scanned[] = basename(trim($file));
            }
        }
    }
    return array(
        'version' => PHP_VERSION,
        'branch' => PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION,
        'sapi' => PHP_SAPI,
        'os' => PHP_OS,
        'int_size' => PHP_INT_SIZE,
        'ini' => $ini,
        'ini_source' => 'ini_get() of this ' . PHP_SAPI . ' process (web runs include .htaccess php_value and .user.ini)',
        'loaded_ini_file' => sp_fn('php_ini_loaded_file') ? php_ini_loaded_file() : null,
        'scanned_ini_files' => $scanned,
        'disable_functions' => sp_disabled_functions(),
        'opcache' => $opcache,
        'extensions' => sp_loaded_extensions(),
        'zend_extensions' => array_map('strtolower', (array) @get_loaded_extensions(true)),
    );
}

function sp_loaded_extensions()
{
    $names = array();
    foreach ((array) get_loaded_extensions() as $name) {
        $names[] = sp_ext_name($name);
    }
    foreach ((array) @get_loaded_extensions(true) as $name) {
        $names[] = sp_ext_name($name);
    }
    $names = array_values(array_unique($names));
    sort($names);
    return $names;
}

function sp_ext_name($name)
{
    $name = strtolower(trim((string) $name));
    $map = array('zend opcache' => 'opcache', 'the ioncube php loader' => 'ioncube loader', 'xdebug' => 'xdebug');
    return isset($map[$name]) ? $map[$name] : $name;
}

function sp_collect_webserver($root, $cli)
{
    $software = sp_server('SERVER_SOFTWARE');
    $sapi = PHP_SAPI;
    $type = 'unknown';
    if ($cli) {
        $type = 'cli (run over HTTP to see the web server)';
    } elseif (stripos($software, 'litespeed') !== false || stripos($sapi, 'litespeed') !== false) {
        $type = 'litespeed';
    } elseif (stripos($software, 'nginx') !== false || stripos($software, 'openresty') !== false) {
        $type = 'nginx';
    } elseif (stripos($software, 'apache') !== false || $sapi === 'apache2handler') {
        $type = 'apache';
    } elseif (stripos($software, 'microsoft-iis') !== false) {
        $type = 'iis';
    } elseif ($sapi === 'cli-server') {
        $type = 'php built-in server';
    }
    $handlers = array('apache2handler' => 'mod_php', 'fpm-fcgi' => 'php-fpm (FastCGI)',
        'cgi-fcgi' => 'CGI/FastCGI (fcgid, suPHP or lsphp)', 'litespeed' => 'LiteSpeed LSAPI',
        'cli' => 'command line', 'cli-server' => 'PHP built-in server');
    $modules = null;
    if (sp_fn('apache_get_modules')) {
        $modules = array();
        foreach ((array) @apache_get_modules() as $module) {
            $modules[] = preg_replace('/^mod_/', '', (string) $module);
        }
    }
    $https = 'unknown';
    if (!$cli) {
        $https = (sp_server('HTTPS') !== '' && strtolower(sp_server('HTTPS')) !== 'off') || sp_server('SERVER_PORT') === '443'
            ? 'on' : (strtolower(sp_server('HTTP_X_FORWARDED_PROTO')) === 'https' ? 'on (X-Forwarded-Proto from a proxy)' : 'off');
    }
    $proxy = array();
    foreach (array('HTTP_CF_RAY' => 'Cloudflare', 'HTTP_X_LITESPEED_CACHE' => 'LiteSpeed cache', 'HTTP_X_VARNISH' => 'Varnish',
                   'HTTP_X_SUCURI_CLIENTIP' => 'Sucuri', 'HTTP_X_FORWARDED_FOR' => 'a proxy (X-Forwarded-For)',
                   'HTTP_X_FORWARDED_PROTO' => 'a TLS proxy (X-Forwarded-Proto)', 'HTTP_CDN_LOOP' => 'a CDN (CDN-Loop)') as $header => $label) {
        if (sp_server($header) !== '') {
            $proxy[] = $label;
        }
    }
    $docroot = rtrim(sp_server('DOCUMENT_ROOT'), '/');
    $real_docroot = $docroot !== '' ? @realpath($docroot) : false;
    $real_root = @realpath($root);
    $relation = 'unknown';
    $document_root_setting = null;
    if ($real_docroot !== false && $real_root !== false) {
        if ($real_docroot === $real_root) {
            $relation = 'same';
            $document_root_setting = '/';
        } elseif (strpos($real_docroot, $real_root . '/') === 0) {
            $relation = 'document root is inside the application';
            $document_root_setting = substr($real_docroot, strlen($real_root) + 1);
        } elseif (strpos($real_root, $real_docroot . '/') === 0) {
            $relation = 'application lives in a subdirectory of the document root (/' . substr($real_root, strlen($real_docroot) + 1) . '/)';
        }
    }
    if ($document_root_setting === null) {
        foreach (array('public', 'web', 'pub', 'htdocs') as $sub) {
            if (@is_file($root . '/' . $sub . '/index.php') && !@is_file($root . '/index.php')) {
                $document_root_setting = $sub;
                $relation = 'guessed from ' . $sub . '/index.php';
            }
        }
    }
    return array(
        'type' => $type,
        'server_software' => $software !== '' ? $software : null,
        'sapi' => $sapi,
        'php_handler' => isset($handlers[$sapi]) ? $handlers[$sapi] : $sapi,
        'apache_modules' => $modules,
        'https' => $https,
        'proxy_hints' => $proxy,
        'http_host' => sp_server('HTTP_HOST') !== '' ? sp_server('HTTP_HOST') : null,
        'document_root' => $docroot !== '' ? $docroot : null,
        'document_root_relation' => $relation,
        'document_root_setting' => $document_root_setting,
        'htaccess' => sp_htaccess($root . '/.htaccess'),
        'user_ini' => sp_user_ini($root),
        'panel' => sp_panel_guess($root),
    );
}

function sp_panel_guess($root)
{
    $hints = array();
    if (preg_match('#/domains/[^/]+/public_html#', $root)) {
        $hints[] = 'DirectAdmin (path /domains/<domain>/public_html)';
    } elseif (strpos($root, '/public_html') !== false) {
        $hints[] = 'cPanel or DirectAdmin (path public_html)';
    }
    if (strpos($root, '/httpdocs') !== false || strpos($root, '/var/www/vhosts/') === 0) {
        $hints[] = 'Plesk (path httpdocs / vhosts)';
    }
    foreach (array('/usr/local/cpanel' => 'cPanel', '/usr/local/directadmin' => 'DirectAdmin', '/usr/local/psa' => 'Plesk',
                   '/etc/cloudlinux-release' => 'CloudLinux') as $path => $label) {
        if (@file_exists($path)) {
            $hints[] = $label . ' (' . $path . ')';
        }
    }
    return $hints;
}

/** Directives of the root .htaccess that need an Apache module the CORE image does not load. */
function sp_htaccess($path)
{
    $text = sp_read($path, 4194304);
    if ($text === null) {
        return array('present' => false);
    }
    $needs = array('rewriteengine' => 'rewrite', 'rewriterule' => 'rewrite', 'rewritecond' => 'rewrite', 'rewritebase' => 'rewrite',
        'header' => 'headers', 'requestheader' => 'headers', 'expiresactive' => 'expires', 'expiresbytype' => 'expires',
        'expiresdefault' => 'expires', 'setoutputfilter' => 'deflate', 'deflatecompressionlevel' => 'deflate',
        'php_value' => 'php', 'php_flag' => 'php', 'php_admin_value' => 'php', 'php_admin_flag' => 'php',
        'authtype' => 'auth_basic', 'authuserfile' => 'authn_file', 'brotlicompressionquality' => 'brotli',
        'secruleengine' => 'security2', 'secfilterengine' => 'security', 'cacheenable' => 'cache', 'modpagespeed' => 'pagespeed',
        'suphp_configpath' => 'suphp', 'fcgidwrapper' => 'fcgid', 'passengerenabled' => 'passenger', 'cachelookup' => 'litespeed');
    $setup_modules = sp_setup_apache_modules();
    $stack = array();
    $findings = array();
    $php_values = array();
    $handlers = array();
    $rewrite_base = null;
    $lines = preg_split('/\r\n|\r|\n/', $text);
    foreach ($lines as $number => $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#') {
            continue;
        }
        if (preg_match('/^<IfModule\s+(!?)([^>]+)>/i', $line, $m)) {
            $stack[] = $m[1] . trim($m[2]);
            continue;
        }
        if (preg_match('#^</IfModule#i', $line)) {
            array_pop($stack);
            continue;
        }
        if (!preg_match('/^([A-Za-z_]+)\b\s*(.*)$/', $line, $m)) {
            continue;
        }
        $directive = strtolower($m[1]);
        $argument = $m[2];
        $module = isset($needs[$directive]) ? $needs[$directive] : null;
        if ($directive === 'addoutputfilterbytype') {
            $module = stripos($argument, 'BROTLI') !== false ? 'brotli' : 'deflate';
        }
        if (($directive === 'addhandler' || $directive === 'sethandler' || $directive === 'addtype') && stripos($argument, 'php') !== false) {
            $handlers[] = array('line' => $number + 1, 'text' => sp_redact_text($line));
            continue;
        }
        if ($directive === 'rewritebase') {
            $rewrite_base = trim($argument);
        }
        if (substr($directive, 0, 4) === 'php_') {
            $parts = preg_split('/\s+/', trim($argument), 2);
            $php_values[] = array('line' => $number + 1, 'directive' => $directive, 'name' => $parts[0],
                                  'value' => sp_redact_text(isset($parts[1]) ? trim($parts[1], " \"'") : ''),
                                  'guarded' => (bool) $stack);
        }
        if ($module !== null && !in_array($module, $setup_modules, true)) {
            $key = $module . ($stack ? ':guarded' : ':unguarded');
            if (!isset($findings[$key])) {
                $findings[$key] = array('module' => $module, 'guarded' => (bool) $stack, 'first_line' => $number + 1,
                                        'directive' => $m[1], 'count' => 0);
            }
            $findings[$key]['count']++;
        }
    }
    return array(
        'present' => true,
        'path' => '.htaccess',
        'bytes' => strlen($text),
        'lines' => count($lines),
        'prestashop_section' => strpos($text, '~~start~~') !== false,
        'rewrite_base' => $rewrite_base,
        'modules_not_in_setup' => array_values($findings),
        'php_values' => $php_values,
        'php_handlers' => $handlers,
    );
}

function sp_user_ini($root)
{
    $name = sp_ini('user_ini.filename');
    $name = $name ? $name : '.user.ini';
    $out = array();
    foreach (array($root, rtrim(sp_server('DOCUMENT_ROOT'), '/')) as $dir) {
        if ($dir === '' || isset($out[$dir])) {
            continue;
        }
        $text = sp_read($dir . '/' . $name, 65536);
        if ($text === null) {
            continue;
        }
        $values = array();
        foreach (preg_split('/\r\n|\r|\n/', $text) as $line) {
            if (preg_match('/^\s*([A-Za-z0-9_.]+)\s*=\s*(.*?)\s*$/', $line, $m)) {
                $values[$m[1]] = sp_redact_text(trim($m[2], "\"'"));
            }
        }
        $out[$dir] = array('file' => sp_rel($root, $dir . '/' . $name), 'values' => $values);
    }
    return array_values($out);
}

// ------------------------------------------------------------------------------------------------
// Application: platform, composer, configuration files
// ------------------------------------------------------------------------------------------------

function sp_is_ps($platform)
{
    return in_array($platform['type'], array('prestashop', 'thirtybees'), true);
}

function sp_detect_platform($root)
{
    $out = array('type' => 'generic', 'name' => 'generic PHP', 'version' => null, 'source' => null, 'notes' => array());
    $ps_markers = @is_file($root . '/config/defines.inc.php') || @is_dir($root . '/classes');
    $settings = sp_read($root . '/config/settings.inc.php', 262144);
    $settings_version = null;
    if ($settings !== null && preg_match('/[\'"]_PS_VERSION_[\'"]\s*,\s*[\'"]([0-9][0-9.]*)[\'"]/', $settings, $m)) {
        $settings_version = $m[1];
    }
    if ($ps_markers) {
        foreach (array('src/Core/Version.php', 'app/AppKernel.php') as $rel) {
            $text = sp_read($root . '/' . $rel, 262144);
            if ($out['version'] === null && $text !== null && preg_match('/const\s+VERSION\s*=\s*[\'"]([0-9][0-9.]*)[\'"]/', $text, $m)) {
                $out = array('type' => 'prestashop', 'name' => 'PrestaShop', 'version' => $m[1], 'source' => $rel . '::VERSION', 'notes' => array());
            }
        }
        if ($out['version'] === null && $settings_version !== null) {
            $out = array('type' => 'prestashop', 'name' => 'PrestaShop', 'version' => $settings_version,
                         'source' => 'config/settings.inc.php::_PS_VERSION_', 'notes' => array());
        }
        foreach (array('install/install_version.php', 'install-dev/install_version.php') as $rel) {
            $text = sp_read($root . '/' . $rel, 65536);
            if ($out['version'] === null && $text !== null && preg_match('/[\'"]([0-9]+\.[0-9][0-9.]*)[\'"]/', $text, $m)) {
                $out = array('type' => 'prestashop', 'name' => 'PrestaShop', 'version' => $m[1], 'source' => $rel, 'notes' => array());
            }
        }
        if ($out['type'] === 'prestashop' && $settings_version !== null && $settings_version !== $out['version']) {
            $out['notes'][] = 'config/settings.inc.php says _PS_VERSION_ ' . $settings_version . ' while ' . $out['source'] . ' says ' . $out['version'];
            $out['settings_inc_version'] = $settings_version;
        }
        $defines = sp_read($root . '/config/defines.inc.php', 262144);
        if ($defines !== null && strpos($defines, '_TB_VERSION_') !== false) {
            $out['type'] = 'thirtybees';
            $out['name'] = 'thirty bees';
            if ($settings !== null && preg_match('/[\'"]_TB_VERSION_[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]/', $settings, $m)) {
                $out['thirtybees_version'] = $m[1];
            }
        }
        if ($out['type'] !== 'generic') {
            return $out;
        }
    }
    $composer = sp_read_json($root . '/composer.json');
    $require = is_array($composer) ? array_merge((array) sp_get($composer, 'require', array()), (array) sp_get($composer, 'require-dev', array())) : array();
    if (@is_file($root . '/artisan') || isset($require['laravel/framework'])) {
        $out = array('type' => 'laravel', 'name' => 'Laravel', 'version' => sp_get($require, 'laravel/framework'), 'source' => 'artisan / composer.json', 'notes' => array());
    } elseif (isset($require['akeneo/pim-community-dev']) || isset($require['akeneo/pim-enterprise-dev'])) {
        $out = array('type' => 'akeneo', 'name' => 'Akeneo PIM', 'version' => sp_get($require, 'akeneo/pim-community-dev'), 'source' => 'composer.json', 'notes' => array());
    } elseif (isset($require['symfony/framework-bundle']) || (@is_file($root . '/bin/console') && @is_file($root . '/config/bundles.php'))) {
        $out = array('type' => 'symfony', 'name' => 'Symfony', 'version' => sp_get($require, 'symfony/framework-bundle'), 'source' => 'composer.json / bin/console', 'notes' => array());
    } elseif (@is_file($root . '/wp-config.php') || @is_file($root . '/wp-includes/version.php')) {
        $version = null;
        $text = sp_read($root . '/wp-includes/version.php', 65536);
        if ($text !== null && preg_match('/\$wp_version\s*=\s*[\'"]([^\'"]+)/', $text, $m)) {
            $version = $m[1];
        }
        $out = array('type' => 'wordpress', 'name' => 'WordPress', 'version' => $version, 'source' => 'wp-includes/version.php', 'notes' => array());
    } elseif (@is_file($root . '/app/etc/env.php')) {
        $out = array('type' => 'magento', 'name' => 'Magento 2', 'version' => null, 'source' => 'app/etc/env.php', 'notes' => array());
    } elseif (is_array($composer)) {
        $out = array('type' => 'composer', 'name' => 'Composer application', 'version' => null, 'source' => 'composer.json', 'notes' => array());
    }
    return $out;
}

function sp_read_json($path, $max = 8388608)
{
    $text = sp_read($path, $max);
    if ($text === null) {
        return null;
    }
    $data = json_decode($text, true);
    return is_array($data) ? $data : null;
}

function sp_collect_composer($root)
{
    $json = sp_read_json($root . '/composer.json');
    if (!is_array($json)) {
        return array('present' => false);
    }
    $require = (array) sp_get($json, 'require', array());
    $platform = (array) sp_get((array) sp_get($json, 'config', array()), 'platform', array());
    $extensions = array();
    foreach (array_merge(array_keys($require), array_keys($platform)) as $name) {
        if (strpos($name, 'ext-') === 0) {
            $extensions[] = strtolower(substr($name, 4));
        }
    }
    $lock_extensions = array();
    $frameworks = array();
    $lock = sp_read_json($root . '/composer.lock', 16777216);
    if (is_array($lock)) {
        foreach ((array) sp_get($lock, 'packages', array()) as $package) {
            $name = (string) sp_get($package, 'name', '');
            if (in_array($name, array('laravel/framework', 'symfony/framework-bundle', 'symfony/http-kernel', 'akeneo/pim-community-dev',
                                      'doctrine/orm', 'prestashop/prestashop', 'magento/framework'), true)) {
                $frameworks[$name] = sp_get($package, 'version');
            }
            foreach (array_keys((array) sp_get($package, 'require', array())) as $dependency) {
                if (strpos($dependency, 'ext-') === 0) {
                    $lock_extensions[] = strtolower(substr($dependency, 4));
                }
            }
        }
    }
    $lock_extensions = array_values(array_unique($lock_extensions));
    sort($lock_extensions);
    return array(
        'present' => true,
        'name' => sp_get($json, 'name'),
        'php' => sp_get($require, 'php'),
        'platform_php' => sp_get($platform, 'php'),
        'extensions' => array_values(array_unique($extensions)),
        'lock_present' => is_array($lock),
        'lock_extensions' => $lock_extensions,
        'lock_platform' => is_array($lock) ? sp_get($lock, 'platform') : null,
        'frameworks' => $frameworks,
    );
}

/** Includes a configuration file that returns an array, without letting it print anything. */
function sp_include_array($file)
{
    if (!@is_file($file) || !@is_readable($file)) {
        return null;
    }
    $value = null;
    ob_start();
    try {
        $value = include $file;
    } catch (Exception $e) {
        $value = null;
    } catch (Throwable $e) {
        $value = null;
    }
    ob_end_clean();
    return is_array($value) ? $value : null;
}

/** define('NAME', 'value') pairs of a PHP file, read with a regex (the file is never executed). */
function sp_php_defines($text)
{
    $out = array();
    if (preg_match_all('/define\(\s*[\'"]([A-Za-z0-9_]+)[\'"]\s*,\s*(?:\'((?:[^\'\\\\]|\\\\.)*)\'|"((?:[^"\\\\]|\\\\.)*)"|([A-Za-z0-9_.+-]+))\s*\)/', (string) $text, $all, PREG_SET_ORDER)) {
        foreach ($all as $m) {
            $value = isset($m[4]) && $m[4] !== '' ? $m[4] : (isset($m[3]) && $m[3] !== '' ? stripcslashes($m[3]) : stripslashes($m[2]));
            $out[$m[1]] = $value;
        }
    }
    return $out;
}

function sp_dotenv($text)
{
    $out = array();
    foreach (preg_split('/\r\n|\r|\n/', (string) $text) as $line) {
        if (preg_match('/^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*(.*)$/', $line, $m)) {
            $value = trim($m[2]);
            if ($value !== '' && ($value[0] === '"' || $value[0] === "'")) {
                $quote = $value[0];
                $end = strrpos($value, $quote);
                $value = $end > 0 ? substr($value, 1, $end - 1) : substr($value, 1);
            } else {
                $value = preg_replace('/\s+#.*$/', '', $value);
            }
            $out[$m[1]] = $value;
        }
    }
    return $out;
}

function sp_is_secret_name($name)
{
    return (bool) preg_match('/PASS|PWD|SECRET|KEY|TOKEN|SALT|AUTH|PRIVATE|CREDENTIAL|DSN|_URL$|^DATABASE_URL$|COOKIE/i', $name);
}

/**
 * The application's DB connection and key presence. 'db' holds the private connection values
 * (never rendered); 'public' is what the report may show.
 */
function sp_app_config($root, $platform, $config_path)
{
    $db = null;
    $public = array('file' => null, 'format' => null, 'keys' => array(), 'values' => array(), 'env_names' => array());
    if (sp_is_ps($platform)) {
        $modern_file = $root . '/app/config/parameters.php';
        $yml_file = $root . '/app/config/parameters.yml';
        $legacy_file = $root . '/config/settings.inc.php';
        $parameters = null;
        if (@is_file($modern_file)) {
            $data = sp_include_array($modern_file);
            $parameters = is_array($data) && isset($data['parameters']) && is_array($data['parameters']) ? $data['parameters'] : null;
            $public['file'] = 'app/config/parameters.php';
            $public['format'] = 'parameters.php';
            if ($parameters === null) {
                sp_note('app/config/parameters.php did not return an array with "parameters"');
            }
        } elseif (@is_file($yml_file)) {
            $parameters = array();
            foreach (preg_split('/\r\n|\r|\n/', (string) sp_read($yml_file, 262144)) as $line) {
                if (preg_match('/^\s+([a-z_]+):\s*(.*?)\s*$/', $line, $m)) {
                    $parameters[$m[1]] = trim($m[2], "\"'") === '~' ? null : trim($m[2], "\"'");
                }
            }
            $public['file'] = 'app/config/parameters.yml';
            $public['format'] = 'parameters.yml';
        }
        if (is_array($parameters)) {
            foreach (array('database_password', 'mailer_password', 'secret', 'cookie_key', 'cookie_iv', 'new_cookie_key',
                           'api_private_key', 'api_public_key') as $name) {
                sp_secret(isset($parameters[$name]) && is_string($parameters[$name]) ? $parameters[$name] : null);
            }
            $db = array('host' => (string) sp_get($parameters, 'database_host', ''), 'port' => (string) sp_get($parameters, 'database_port', ''),
                        'name' => (string) sp_get($parameters, 'database_name', ''), 'user' => (string) sp_get($parameters, 'database_user', ''),
                        'password' => (string) sp_get($parameters, 'database_password', ''), 'prefix' => (string) sp_get($parameters, 'database_prefix', ''),
                        'source' => $public['file']);
            $map = array('cookie_key' => 'PS_COOKIE_KEY', 'cookie_iv' => 'PS_COOKIE_IV', 'new_cookie_key' => 'PS_NEW_COOKIE_KEY',
                         'secret' => 'PS_SECRET', 'api_private_key' => 'PS_API_PRIVATE_KEY', 'api_public_key' => 'PS_API_PUBLIC_KEY');
            foreach ($map as $file_key => $env) {
                $public['keys'][$env] = array('file_key' => $file_key, 'present' => isset($parameters[$file_key]) && (string) $parameters[$file_key] !== '');
            }
            foreach (array('database_engine', 'ps_caching', 'ps_cache_enable', 'ps_creation_date', 'locale', 'use_debug_toolbar',
                           'mailer_transport', 'mailer_host') as $name) {
                if (array_key_exists($name, $parameters) && !is_array($parameters[$name])) {
                    $public['values'][$name] = $parameters[$name];
                }
            }
        }
        if ($db === null && @is_file($legacy_file)) {
            $defines = sp_php_defines(sp_read($legacy_file, 262144));
            foreach (array('_DB_PASSWD_', '_COOKIE_KEY_', '_COOKIE_IV_', '_RIJNDAEL_KEY_', '_RIJNDAEL_IV_') as $name) {
                sp_secret(sp_get($defines, $name));
            }
            $db = array('host' => (string) sp_get($defines, '_DB_SERVER_', ''), 'port' => '', 'name' => (string) sp_get($defines, '_DB_NAME_', ''),
                        'user' => (string) sp_get($defines, '_DB_USER_', ''), 'password' => (string) sp_get($defines, '_DB_PASSWD_', ''),
                        'prefix' => (string) sp_get($defines, '_DB_PREFIX_', ''), 'source' => 'config/settings.inc.php');
            $public['file'] = 'config/settings.inc.php';
            $public['format'] = 'settings.inc.php';
            $map = array('_COOKIE_KEY_' => 'PS_COOKIE_KEY', '_COOKIE_IV_' => 'PS_COOKIE_IV', '_RIJNDAEL_KEY_' => 'PS_RIJNDAEL_KEY',
                         '_RIJNDAEL_IV_' => 'PS_RIJNDAEL_IV');
            foreach ($map as $file_key => $env) {
                $public['keys'][$env] = array('file_key' => $file_key, 'present' => (string) sp_get($defines, $file_key, '') !== '');
            }
            foreach (array('_MYSQL_ENGINE_', '_PS_CACHING_SYSTEM_', '_PS_CACHE_ENABLED_', '_PS_CREATION_DATE_') as $name) {
                if (isset($defines[$name])) {
                    $public['values'][$name] = $defines[$name];
                }
            }
        }
        return array('db' => $db, 'public' => $public);
    }

    // Generic applications: an explicit file first, then the usual places.
    $candidates = array();
    if ($config_path !== '') {
        $candidates[] = $config_path[0] === '/' ? $config_path : $root . '/' . $config_path;
    }
    foreach (array('.env.local.php', '.env.local', '.env', 'wp-config.php', 'config/db.php', 'config/database.php') as $rel) {
        $candidates[] = $root . '/' . $rel;
    }
    foreach ($candidates as $file) {
        if (!@is_file($file)) {
            continue;
        }
        $rel = sp_rel($root, $file);
        $values = array();
        if (substr($file, -14) === '.env.local.php') {
            $values = (array) sp_include_array($file);
        } elseif (substr($file, -4) === '.php') {
            $text = (string) sp_read($file, 262144);
            $values = sp_php_defines($text);
            if (preg_match('/\$table_prefix\s*=\s*[\'"]([A-Za-z0-9_]*)[\'"]/', $text, $m)) {
                $values['TABLE_PREFIX'] = $m[1];
            }
        } else {
            $values = sp_dotenv(sp_read($file, 262144));
        }
        if (!$values) {
            continue;
        }
        foreach ($values as $name => $value) {
            if (is_string($value) && sp_is_secret_name($name)) {
                sp_secret($value);
            }
        }
        $public['file'] = $rel;
        $public['format'] = substr($file, -4) === '.php' ? 'php' : 'dotenv';
        $public['env_names'] = array_keys($values);
        foreach (array('APP_ENV', 'APP_DEBUG', 'DB_CONNECTION', 'WP_DEBUG') as $name) {
            if (isset($values[$name]) && is_scalar($values[$name])) {
                $public['values'][$name] = (string) $values[$name];
            }
        }
        $db = sp_generic_db($values, $rel);
        if ($db !== null) {
            if (isset($values['DATABASE_URL']) && preg_match('/serverVersion=([^&\s]+)/', (string) $values['DATABASE_URL'], $m)) {
                $public['values']['DATABASE_URL serverVersion'] = urldecode($m[1]);
            }
            break;
        }
    }
    return array('db' => $db, 'public' => $public);
}

function sp_generic_db($values, $source)
{
    $url = isset($values['DATABASE_URL']) ? (string) $values['DATABASE_URL'] : '';
    if ($url !== '' && preg_match('#^(mysql|mariadb|pdo-mysql|mysql2)://#', $url)) {
        $parts = @parse_url($url);
        if (is_array($parts)) {
            sp_secret(isset($parts['pass']) ? urldecode($parts['pass']) : null);
            return array('host' => (string) sp_get($parts, 'host', ''), 'port' => (string) sp_get($parts, 'port', ''),
                         'name' => ltrim((string) sp_get($parts, 'path', ''), '/'), 'user' => urldecode((string) sp_get($parts, 'user', '')),
                         'password' => urldecode((string) sp_get($parts, 'pass', '')), 'prefix' => '', 'source' => $source . ' (DATABASE_URL)');
        }
    }
    $sets = array(
        array('DB_HOST', 'DB_PORT', 'DB_DATABASE', 'DB_USERNAME', 'DB_PASSWORD'),
        array('DATABASE_HOST', 'DATABASE_PORT', 'DATABASE_NAME', 'DATABASE_USER', 'DATABASE_PASSWORD'),
        array('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD'),
        array('MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_DATABASE', 'MYSQL_USER', 'MYSQL_PASSWORD'),
    );
    foreach ($sets as $set) {
        if (isset($values[$set[2]]) && (string) $values[$set[2]] !== '') {
            return array('host' => (string) sp_get($values, $set[0], 'localhost'), 'port' => (string) sp_get($values, $set[1], ''),
                         'name' => (string) $values[$set[2]], 'user' => (string) sp_get($values, $set[3], ''),
                         'password' => (string) sp_get($values, $set[4], ''), 'prefix' => (string) sp_get($values, 'TABLE_PREFIX', ''),
                         'source' => $source);
        }
    }
    return null;
}

// ------------------------------------------------------------------------------------------------
// Database (read-only: every statement passes sp_db_query, which refuses all but SELECT and SHOW)
// ------------------------------------------------------------------------------------------------

function sp_db_connect($cfg)
{
    $state = array('ok' => false, 'driver' => null, 'link' => null, 'error' => null);
    if (!is_array($cfg) || $cfg['name'] === '') {
        $state['error'] = 'no database configuration found';
        return $state;
    }
    $host = $cfg['host'] !== '' ? $cfg['host'] : 'localhost';
    $port = $cfg['port'];
    $socket = '';
    if ($host[0] === '/') {
        $socket = $host;
        $host = 'localhost';
    } elseif (strpos($host, ':') !== false && substr_count($host, ':') === 1) {
        $pieces = explode(':', $host, 2);
        if (preg_match('/^\d+$/', $pieces[1])) {
            $host = $pieces[0];
            $port = $port !== '' ? $port : $pieces[1];
        } elseif ($pieces[1] !== '' && $pieces[1][0] === '/') {
            $host = $pieces[0];
            $socket = $pieces[1];
        }
    }
    $errors = array();
    if (class_exists('PDO') && in_array('mysql', PDO::getAvailableDrivers(), true)) {
        $dsn = 'mysql:' . ($socket !== '' ? 'unix_socket=' . $socket : 'host=' . $host . ($port !== '' ? ';port=' . (int) $port : ''))
            . ';dbname=' . $cfg['name'] . ';charset=utf8';
        try {
            $pdo = new PDO($dsn, $cfg['user'], $cfg['password'], array(
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
                PDO::ATTR_TIMEOUT => 5,
            ));
            return array('ok' => true, 'driver' => 'pdo_mysql', 'link' => $pdo, 'error' => null);
        } catch (Exception $e) {
            $errors[] = 'pdo_mysql: ' . substr($e->getMessage(), 0, 200);
        }
    }
    if (sp_fn('mysqli_init')) {
        $link = mysqli_init();
        @mysqli_options($link, MYSQLI_OPT_CONNECT_TIMEOUT, 5);
        if (function_exists('mysqli_report')) {
            @mysqli_report(MYSQLI_REPORT_OFF);
        }
        try {
            $ok = @mysqli_real_connect($link, $host, $cfg['user'], $cfg['password'], $cfg['name'],
                $port !== '' ? (int) $port : 3306, $socket !== '' ? $socket : null);
        } catch (Exception $e) {
            $ok = false;
        }
        if ($ok) {
            @mysqli_set_charset($link, 'utf8');
            return array('ok' => true, 'driver' => 'mysqli', 'link' => $link, 'error' => null);
        }
        $errors[] = 'mysqli: ' . substr((string) mysqli_connect_error(), 0, 200);
    }
    $state['error'] = $errors ? implode('; ', $errors) : 'neither pdo_mysql nor mysqli is available';
    return $state;
}

/** Runs one read-only statement; returns rows or null on failure. */
function sp_db_query($db, $sql, $params = array())
{
    if (!$db['ok']) {
        return null;
    }
    if (!preg_match('/^\s*(SELECT|SHOW)\b/i', $sql) || strpos($sql, ';') !== false) {
        sp_note('refused a statement that is not a single SELECT/SHOW');
        return null;
    }
    try {
        if ($db['driver'] === 'pdo_mysql') {
            $statement = $db['link']->prepare($sql);
            $statement->execute($params);
            return $statement->fetchAll(PDO::FETCH_ASSOC);
        }
        $link = $db['link'];
        $index = 0;
        $final = preg_replace_callback('/\?/', function () use (&$index, $params, $link) {
            $value = isset($params[$index]) ? $params[$index] : '';
            $index++;
            return "'" . mysqli_real_escape_string($link, (string) $value) . "'";
        }, $sql);
        $result = @mysqli_query($link, $final);
        if (!$result) {
            throw new Exception((string) mysqli_error($link));
        }
        $rows = array();
        while ($row = mysqli_fetch_assoc($result)) {
            $rows[] = $row;
        }
        return $rows;
    } catch (Exception $e) {
        sp_note('query failed: ' . substr(preg_replace('/\s+/', ' ', $sql), 0, 80) . '... (' . substr($e->getMessage(), 0, 160) . ')');
        return null;
    }
}

function sp_db_value($db, $sql, $params = array())
{
    $rows = sp_db_query($db, $sql, $params);
    if (!$rows) {
        return null;
    }
    $row = array_values($rows[0]);
    return $row[0];
}

function sp_db_variable_names()
{
    return array('version', 'version_comment', 'sql_mode', 'character_set_server', 'collation_server', 'character_set_database',
        'time_zone', 'system_time_zone', 'innodb_buffer_pool_size', 'max_allowed_packet', 'lower_case_table_names',
        'innodb_file_per_table', 'innodb_strict_mode', 'innodb_default_row_format', 'innodb_large_prefix', 'innodb_file_format',
        'default_storage_engine', 'explicit_defaults_for_timestamp', 'group_concat_max_len', 'ft_min_word_len',
        'innodb_ft_min_token_size', 'event_scheduler', 'max_connections', 'wait_timeout', 'tmp_table_size',
        'max_heap_table_size', 'table_open_cache', 'innodb_log_file_size', 'innodb_redo_log_capacity', 'performance_schema',
        'query_cache_type', 'query_cache_size', 'default_authentication_plugin', 'transaction_isolation', 'tx_isolation',
        'local_infile', 'log_bin', 'innodb_flush_log_at_trx_commit', 'sort_buffer_size', 'join_buffer_size', 'version_compile_os');
}

function sp_collect_database($db, $cfg)
{
    $out = array('configured' => is_array($cfg) && $cfg['name'] !== '', 'config_source' => is_array($cfg) ? $cfg['source'] : null,
                 'available' => false);
    if (is_array($cfg)) {
        $out['host'] = $cfg['host'];
        $out['port'] = $cfg['port'];
        $out['name'] = $cfg['name'];
        $out['prefix'] = $cfg['prefix'];
        $out['user'] = $cfg['user'] !== '' ? 'present (not shown)' : 'empty';
        $out['password'] = $cfg['password'] !== '' ? 'present (not shown)' : 'empty';
    }
    if (!$db['ok']) {
        $out['error'] = $db['error'];
        return $out;
    }
    $out['available'] = true;
    $out['driver'] = $db['driver'];
    $names = "'" . implode("','", sp_db_variable_names()) . "'";
    $variables = array();
    $rows = sp_db_query($db, 'SHOW GLOBAL VARIABLES WHERE Variable_name IN (' . $names . ')');
    if ($rows === null) {
        $rows = sp_db_query($db, 'SHOW VARIABLES WHERE Variable_name IN (' . $names . ')');
    }
    foreach ((array) $rows as $row) {
        $row = array_values($row);
        $variables[strtolower($row[0])] = $row[1];
    }
    $version = isset($variables['version']) ? $variables['version'] : sp_db_value($db, 'SELECT VERSION()');
    $comment = isset($variables['version_comment']) ? $variables['version_comment'] : '';
    $engine = stripos($version . ' ' . $comment, 'mariadb') !== false ? 'mariadb' : 'mysql';
    preg_match('/^(\d+)\.(\d+)\.(\d+)/', (string) $version, $m);
    $out['version'] = $version;
    $out['version_comment'] = $comment;
    $out['engine'] = $engine;
    $out['flavour'] = stripos($comment, 'percona') !== false ? 'Percona Server' : ($engine === 'mariadb' ? 'MariaDB' : 'MySQL');
    $out['version_number'] = $m ? $m[1] . '.' . $m[2] . '.' . $m[3] : null;
    $out['branch'] = $m ? $m[1] . '.' . $m[2] : null;
    $out['variables'] = $variables;
    $out['variables_source'] = $rows === null ? 'unavailable' : 'SHOW GLOBAL VARIABLES';
    $schema = sp_db_query($db, 'SELECT DEFAULT_CHARACTER_SET_NAME AS charset_name, DEFAULT_COLLATION_NAME AS collation_name '
        . 'FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = ?', array($cfg['name']));
    $out['schema_charset'] = $schema ? $schema[0]['charset_name'] : null;
    $out['schema_collation'] = $schema ? $schema[0]['collation_name'] : null;
    $schemas = array();
    foreach ((array) sp_db_query($db, 'SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA') as $row) {
        if (!in_array(strtolower($row['name']), array('information_schema', 'performance_schema', 'mysql', 'sys', strtolower($cfg['name'])), true)) {
            $schemas[] = $row['name'];
        }
    }
    $out['other_visible_schemas'] = $schemas;

    $tables = sp_db_query($db, 'SELECT TABLE_NAME AS name, TABLE_TYPE AS type, ENGINE AS engine, TABLE_ROWS AS approx_rows, '
        . 'DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes, TABLE_COLLATION AS collation_name, ROW_FORMAT AS row_format '
        . 'FROM information_schema.TABLES WHERE TABLE_SCHEMA = ?', array($cfg['name']));
    $tables = (array) $tables;
    $base = array();
    $views = 0;
    $engines = array();
    $collations = array();
    $total_data = 0;
    $total_index = 0;
    $uppercase = array();
    foreach ($tables as $table) {
        if (strtoupper((string) $table['type']) === 'VIEW') {
            $views++;
            continue;
        }
        $base[] = $table;
        $engine_name = $table['engine'] !== null ? $table['engine'] : 'unknown';
        $engines[$engine_name] = isset($engines[$engine_name]) ? $engines[$engine_name] + 1 : 1;
        $collation = $table['collation_name'] !== null ? $table['collation_name'] : 'unknown';
        $collations[$collation] = isset($collations[$collation]) ? $collations[$collation] + 1 : 1;
        $total_data += (int) $table['data_bytes'];
        $total_index += (int) $table['index_bytes'];
        if (strtolower($table['name']) !== $table['name']) {
            $uppercase[] = $table['name'];
        }
    }
    usort($base, 'sp_table_size_desc');
    $largest = array();
    foreach (array_slice($base, 0, 20) as $table) {
        $largest[] = array('name' => $table['name'], 'approx_rows' => (int) $table['approx_rows'],
                           'bytes' => (int) $table['data_bytes'] + (int) $table['index_bytes'], 'engine' => $table['engine']);
    }
    arsort($engines);
    arsort($collations);
    $out['tables'] = array(
        'count' => count($base), 'views' => $views, 'data_bytes' => $total_data, 'index_bytes' => $total_index,
        'total_bytes' => $total_data + $total_index, 'engines' => $engines, 'collations' => $collations,
        'uppercase_names' => array_slice($uppercase, 0, 50), 'largest' => $largest,
        'note' => 'approx_rows is information_schema.TABLES.TABLE_ROWS (an estimate; data tables are never counted)',
    );
    $out['table_names'] = array();
    foreach ($base as $table) {
        $out['table_names'][] = $table['name'];
    }
    sort($out['table_names']);

    $definer_user = $cfg['user'];
    $mask = function ($definer) use ($definer_user) {
        $definer = (string) $definer;
        $pieces = explode('@', $definer, 2);
        return ($definer_user !== '' && $pieces[0] === $definer_user ? '<application user>' : $pieces[0])
            . (isset($pieces[1]) ? '@' . $pieces[1] : '');
    };
    $objects = array();
    foreach ((array) sp_db_query($db, 'SELECT TABLE_NAME AS name, DEFINER AS definer, SECURITY_TYPE AS security FROM information_schema.VIEWS WHERE TABLE_SCHEMA = ?', array($cfg['name'])) as $row) {
        $objects[] = array('kind' => 'view', 'name' => $row['name'], 'definer' => $mask($row['definer']), 'security' => $row['security']);
    }
    foreach ((array) sp_db_query($db, 'SELECT TRIGGER_NAME AS name, EVENT_OBJECT_TABLE AS on_table, DEFINER AS definer FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = ?', array($cfg['name'])) as $row) {
        $objects[] = array('kind' => 'trigger', 'name' => $row['name'] . ' (on ' . $row['on_table'] . ')', 'definer' => $mask($row['definer']), 'security' => 'DEFINER');
    }
    foreach ((array) sp_db_query($db, 'SELECT ROUTINE_NAME AS name, ROUTINE_TYPE AS type, DEFINER AS definer, SECURITY_TYPE AS security FROM information_schema.ROUTINES WHERE ROUTINE_SCHEMA = ?', array($cfg['name'])) as $row) {
        $objects[] = array('kind' => strtolower($row['type']), 'name' => $row['name'], 'definer' => $mask($row['definer']), 'security' => $row['security']);
    }
    foreach ((array) sp_db_query($db, 'SELECT EVENT_NAME AS name, DEFINER AS definer, STATUS AS status FROM information_schema.EVENTS WHERE EVENT_SCHEMA = ?', array($cfg['name'])) as $row) {
        $objects[] = array('kind' => 'event', 'name' => $row['name'] . ' (' . $row['status'] . ')', 'definer' => $mask($row['definer']), 'security' => 'DEFINER');
    }
    $out['stored_objects'] = $objects;
    return $out;
}

function sp_table_size_desc($a, $b)
{
    $x = (int) $a['data_bytes'] + (int) $a['index_bytes'];
    $y = (int) $b['data_bytes'] + (int) $b['index_bytes'];
    return $x === $y ? 0 : ($x < $y ? 1 : -1);
}

// ------------------------------------------------------------------------------------------------
// PrestaShop (files + configuration tables; never customer or order rows)
// ------------------------------------------------------------------------------------------------

function sp_ps_configuration_names()
{
    // Explicit list: password and key rows (PS_MAIL_PASSWD, API keys) are never selected.
    return array('PS_VERSION_DB', 'PS_INSTALL_VERSION', 'PS_SHOP_DEFAULT', 'PS_MULTISHOP_FEATURE_ACTIVE', 'PS_SHOP_ENABLE',
        'PS_SHOP_DOMAIN', 'PS_SHOP_DOMAIN_SSL', 'PS_SSL_ENABLED', 'PS_SSL_ENABLED_EVERYWHERE', 'PS_REWRITING_SETTINGS',
        'PS_TIMEZONE', 'PS_LOCALE_LANGUAGE', 'PS_LOCALE_COUNTRY', 'PS_LANG_DEFAULT', 'PS_CIPHER_ALGORITHM',
        'PS_SMARTY_CACHE', 'PS_SMARTY_FORCE_COMPILE', 'PS_SMARTY_CACHING_TYPE', 'PS_SMARTY_CLEAR_CACHE', 'PS_CSS_THEME_CACHE',
        'PS_JS_THEME_CACHE', 'PS_HTML_THEME_COMPRESSION', 'PS_JS_HTML_THEME_COMPRESSION', 'PS_JS_DEFER', 'PS_DISABLE_OVERRIDES',
        'PS_DISABLE_NON_NATIVE_MODULE', 'PS_MAIL_METHOD', 'PS_MAIL_SERVER', 'PS_MAIL_SMTP_PORT', 'PS_MAIL_SMTP_ENCRYPTION',
        'PS_MAIL_USER', 'PS_MAIL_TYPE', 'PS_LOG_EMAILS', 'PS_MEDIA_SERVERS', 'PS_MEDIA_SERVER_1', 'PS_MEDIA_SERVER_2',
        'PS_MEDIA_SERVER_3', 'PS_HTACCESS_CACHE_CONTROL', 'PS_HTACCESS_DISABLE_MULTIVIEWS', 'PS_HTACCESS_DISABLE_MODSEC',
        'PS_WEBSERVICE', 'PS_IMAGE_FORMAT', 'PS_MAINTENANCE_IP', 'PS_THEME', 'PS_TOKEN_ENABLE', 'PS_COOKIE_SAMESITE');
}

function sp_collect_prestashop($root, $platform, $config, $db, $database)
{
    $public = $config['public'];
    $version = (string) $platform['version'];
    $out = array('version' => $version, 'version_source' => $platform['source'], 'config_file' => $public['file'],
                 'config_format' => $public['format'], 'key_presence' => $public['keys'], 'parameters' => $public['values']);
    $out['required_keys'] = sp_ps_required_keys($version);
    $out['debug'] = sp_ps_debug($root);
    $out['cache'] = sp_ps_cache($public, $version);
    $out['overrides'] = sp_list_files($root . '/override', $root, 400, array('index.php'));
    $themes = array();
    foreach ((array) @scandir($root . '/themes') as $entry) {
        if ($entry !== '.' && $entry !== '..' && @is_dir($root . '/themes/' . $entry)) {
            $themes[] = $entry;
        }
    }
    $out['theme_directories'] = $themes;
    $out['module_directories'] = 0;
    foreach ((array) @scandir($root . '/modules') as $entry) {
        if ($entry !== '.' && $entry !== '..' && @is_dir($root . '/modules/' . $entry)) {
            $out['module_directories']++;
        }
    }
    $out['admin_directory'] = sp_ps_admin_dir($root);
    $out['encoded_files'] = sp_ps_encoded_modules($root);
    $out['composer_vendor'] = @is_dir($root . '/vendor');

    $prefix = isset($database['prefix']) ? (string) $database['prefix'] : '';
    if (!$db['ok']) {
        $out['database'] = 'not reachable: shop, module and configuration details are missing';
        return $out;
    }
    if (!preg_match('/^[A-Za-z0-9_]{0,64}$/', $prefix)) {
        $out['database'] = 'unexpected table prefix: database details skipped';
        return $out;
    }
    $tables = array_flip((array) sp_get($database, 'table_names', array()));
    $names = sp_ps_configuration_names();
    $rows = sp_db_query($db, 'SELECT name, value, id_shop, id_shop_group FROM `' . $prefix . 'configuration` WHERE name IN ('
        . implode(',', array_fill(0, count($names), '?')) . ') ORDER BY name, id_shop, id_shop_group', $names);
    $configuration = array();
    $per_shop = array();
    foreach ((array) $rows as $row) {
        $name = $row['name'];
        $value = $row['value'];
        if ($name === 'PS_MAIL_USER') {
            $value = (string) $value !== '' ? 'present (not shown)' : '';
        } elseif ($name === 'PS_MAINTENANCE_IP') {
            $value = (string) $value !== '' ? count(preg_split('/[\s,;]+/', trim((string) $value))) . ' address(es) (not shown)' : '';
        }
        // Rows come global first (NULL ids sort first); a shop-only value stands in when no global row exists.
        $global = ($row['id_shop'] === null || (string) $row['id_shop'] === '0') && ($row['id_shop_group'] === null || (string) $row['id_shop_group'] === '0');
        if (!array_key_exists($name, $configuration)) {
            $configuration[$name] = $value;
        }
        if (!$global) {
            $per_shop[$name][] = array('id_shop' => $row['id_shop'], 'value' => $value);
        }
    }
    $out['configuration'] = $configuration;
    $out['configuration_per_shop'] = $per_shop;

    $shop_sql = 'SELECT s.id_shop, s.name, s.active, s.id_shop_group, %s AS theme, u.id_shop_url, u.domain, u.domain_ssl, '
        . 'u.physical_uri, u.virtual_uri, u.main, u.active AS url_active FROM `' . $prefix . 'shop` s '
        . 'LEFT JOIN `' . $prefix . 'shop_url` u ON u.id_shop = s.id_shop ORDER BY s.id_shop, u.main DESC';
    $shops = isset($tables[$prefix . 'theme']) && !version_compare($version, '1.7', '>=')
        ? sp_db_query($db, sprintf($shop_sql, '(SELECT t.directory FROM `' . $prefix . 'theme` t WHERE t.id_theme = s.id_theme)'))
        : sp_db_query($db, sprintf($shop_sql, 's.theme_name'));
    $out['shops'] = (array) $shops;
    $languages = sp_db_query($db, 'SELECT iso_code, active FROM `' . $prefix . 'lang` ORDER BY id_lang');
    $out['languages'] = (array) $languages;
    $modules = sp_db_query($db, 'SELECT m.name, m.version, m.active FROM `' . $prefix . 'module` m ORDER BY m.name');
    $out['modules'] = (array) $modules;
    $cron = array();
    foreach ((array) $modules as $module) {
        if (preg_match('/cron/i', $module['name'])) {
            $cron[] = $module['name'];
        }
    }
    $out['cron_modules'] = $cron;
    if (isset($tables[$prefix . 'cronjobs'])) {
        $out['cronjobs_tasks'] = (int) sp_db_value($db, 'SELECT COUNT(*) FROM `' . $prefix . 'cronjobs`');
    }
    if (isset($tables[$prefix . 'memcached_servers'])) {
        $out['memcached_servers'] = (array) sp_db_query($db, 'SELECT ip, port FROM `' . $prefix . 'memcached_servers`');
    }
    return $out;
}

function sp_ps_required_keys($version)
{
    if (strpos($version, '1.6') === 0) {
        return array('PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_RIJNDAEL_KEY', 'PS_RIJNDAEL_IV');
    }
    $keys = array('PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_NEW_COOKIE_KEY', 'PS_SECRET');
    if (version_compare($version, '9', '>=')) {
        $keys[] = 'PS_API_PRIVATE_KEY';
        $keys[] = 'PS_API_PUBLIC_KEY';
    }
    return $keys;
}

function sp_ps_debug($root)
{
    $out = array('mode_dev' => null, 'source' => null);
    foreach (array('config/defines_custom.inc.php', 'config/defines.inc.php') as $rel) {
        $text = sp_read($root . '/' . $rel, 262144);
        if ($text !== null && preg_match('/define\(\s*[\'"]_PS_MODE_DEV_[\'"]\s*,\s*(true|false)\s*\)/i', $text, $m)) {
            $out = array('mode_dev' => strtolower($m[1]) === 'true', 'source' => $rel);
            break;
        }
    }
    return $out;
}

function sp_ps_cache($public, $version)
{
    $values = $public['values'];
    if (strpos($version, '1.6') === 0) {
        return array('enabled' => sp_is_on(sp_get($values, '_PS_CACHE_ENABLED_', '0')), 'system' => sp_get($values, '_PS_CACHING_SYSTEM_'),
                     'source' => 'config/settings.inc.php');
    }
    return array('enabled' => sp_is_on(sp_get($values, 'ps_cache_enable', '0')), 'system' => sp_get($values, 'ps_caching'),
                 'source' => $public['file']);
}

function sp_ps_admin_dir($root)
{
    foreach ((array) @scandir($root) as $entry) {
        if ($entry === '.' || $entry === '..' || !@is_dir($root . '/' . $entry)) {
            continue;
        }
        if (@is_file($root . '/' . $entry . '/init.php') && (@is_file($root . '/' . $entry . '/ajax-tab.php')
                || @is_file($root . '/' . $entry . '/get-file-admin.php') || @is_file($root . '/' . $entry . '/backup.php'))) {
            return $entry;
        }
    }
    return null;
}

/** Module PHP files encoded with ionCube or SourceGuardian (their loader must be in the image). */
function sp_ps_encoded_modules($root)
{
    $found = array();
    $checked = 0;
    $deadline = microtime(true) + 2;
    foreach ((array) @scandir($root . '/modules') as $module) {
        if ($module === '.' || $module === '..' || !@is_dir($root . '/modules/' . $module)) {
            continue;
        }
        foreach ((array) @glob($root . '/modules/' . $module . '/*.php') as $file) {
            if ($checked++ > 3000 || microtime(true) > $deadline) {
                break 2;
            }
            $head = @file_get_contents($file, false, null, 0, 600);
            if (is_string($head) && (stripos($head, 'ionCube') !== false || strpos($head, 'sg_load(') !== false)) {
                $found[] = 'modules/' . $module . ' (' . (stripos($head, 'ionCube') !== false ? 'ionCube' : 'SourceGuardian') . ')';
                break;
            }
        }
    }
    return $found;
}

function sp_list_files($dir, $root, $limit, $skip)
{
    if (!@is_dir($dir)) {
        return array('present' => false, 'count' => 0, 'files' => array());
    }
    $files = array();
    $count = 0;
    $stack = array($dir);
    while ($stack) {
        $current = array_pop($stack);
        foreach ((array) @scandir($current) as $entry) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $path = $current . '/' . $entry;
            if (@is_dir($path)) {
                $stack[] = $path;
            } elseif (substr($entry, -4) === '.php' && !in_array($entry, $skip, true)) {
                $count++;
                if (count($files) < $limit) {
                    $files[] = sp_rel($root, $path);
                }
            }
        }
    }
    sort($files);
    return array('present' => true, 'count' => $count, 'files' => $files);
}

// ------------------------------------------------------------------------------------------------
// Host, disk, cron
// ------------------------------------------------------------------------------------------------

function sp_collect_host($root, $platform)
{
    $out = array('os' => PHP_OS, 'uname' => sp_fn('php_uname') ? @php_uname('s') . ' ' . @php_uname('r') . ' ' . @php_uname('m') : null);
    $release = sp_read('/etc/os-release', 8192);
    if ($release !== null && preg_match('/^PRETTY_NAME="?([^"\n]+)"?/m', $release, $m)) {
        $out['os_release'] = $m[1];
    }
    $out['hostname'] = sp_fn('gethostname') ? @gethostname() : null;
    $out['timezone'] = array('php_default' => $GLOBALS['SP_TZ_DEFAULT'], 'etc_timezone' => trim((string) sp_read('/etc/timezone', 256)),
                             'env_tz' => getenv('TZ') !== false ? getenv('TZ') : null);
    $out['locale'] = array('lc_all' => sp_fn('setlocale') ? @setlocale(LC_ALL, 0) : null, 'lang' => getenv('LANG') !== false ? getenv('LANG') : null);
    $free = sp_fn('disk_free_space') ? @disk_free_space($root) : false;
    $total = sp_fn('disk_total_space') ? @disk_total_space($root) : false;
    $out['disk'] = array('free_bytes' => $free === false ? null : (int) $free, 'total_bytes' => $total === false ? null : (int) $total,
                         'used_bytes' => ($free === false || $total === false) ? null : (int) ($total - $free));
    $meminfo = sp_read('/proc/meminfo', 65536);
    if ($meminfo !== null) {
        foreach (array('MemTotal' => 'memory_total_bytes', 'MemAvailable' => 'memory_available_bytes') as $key => $name) {
            if (preg_match('/^' . $key . ':\s+(\d+)\s*kB/m', $meminfo, $m)) {
                $out[$name] = (int) $m[1] * 1024;
            }
        }
    }
    $cpuinfo = sp_read('/proc/cpuinfo', 4194304);
    if ($cpuinfo !== null) {
        $out['cpu_count'] = preg_match_all('/^processor\s*:/m', $cpuinfo, $unused);
    }
    $cgroup_memory = trim((string) sp_read('/sys/fs/cgroup/memory.max', 64));
    if ($cgroup_memory !== '') {
        $out['cgroup_memory_max'] = $cgroup_memory;
    }
    $cgroup_cpu = trim((string) sp_read('/sys/fs/cgroup/cpu.max', 64));
    if ($cgroup_cpu !== '') {
        $out['cgroup_cpu_max'] = $cgroup_cpu;
    }
    if (sp_fn('sys_getloadavg')) {
        $load = @sys_getloadavg();
        $out['load_average'] = is_array($load) ? array(round($load[0], 2), round($load[1], 2), round($load[2], 2)) : null;
    }
    $out['uid'] = sp_fn('posix_getuid') ? posix_getuid() : (sp_fn('getmyuid') ? getmyuid() : null);
    $out['gid'] = sp_fn('posix_getgid') ? posix_getgid() : (sp_fn('getmygid') ? getmygid() : null);
    $out['uid_source'] = sp_fn('posix_getuid') ? 'posix_getuid()/posix_getgid() of the PHP process' : 'getmyuid()/getmygid() (owner of the probe file)';
    $out['open_basedir'] = sp_ini('open_basedir');
    $out['services'] = array();
    foreach (array('redis' => 6379, 'memcached' => 11211) as $service => $port) {
        $reachable = null;
        if (sp_fn('fsockopen')) {
            $errno = 0;
            $errstr = '';
            $socket = @fsockopen('127.0.0.1', $port, $errno, $errstr, 0.5);
            $reachable = $socket !== false;
            if ($socket) {
                fclose($socket);
            }
        }
        $out['services'][$service] = array('extension' => extension_loaded($service), 'localhost_port' => $port,
                                           'reachable' => $reachable, 'test' => 'TCP connect only, nothing sent');
    }
    $out['services']['memcache_extension'] = extension_loaded('memcache');
    $out['directories'] = sp_directory_sizes($root, $platform);
    return $out;
}

function sp_directory_sizes($root, $platform)
{
    $list = sp_is_ps($platform)
        ? array('img', 'upload', 'download', 'var/cache', 'cache', 'var/logs', 'log', 'modules', 'themes', 'override', 'vendor')
        : array('storage', 'var', 'var/cache', 'public/uploads', 'public/media', 'uploads', 'wp-content/uploads', 'vendor', 'node_modules');
    $deadline = microtime(true) + SP_SCAN_SECONDS;
    $budget = SP_SCAN_FILES;
    $out = array();
    foreach ($list as $rel) {
        $usage = sp_dir_usage($root . '/' . $rel, $deadline, $budget);
        if ($usage !== null) {
            $out[$rel] = $usage;
        }
    }
    return array('limit' => SP_SCAN_SECONDS . ' s and ' . SP_SCAN_FILES . ' files in total; "complete": false means at least that much',
                 'entries' => $out);
}

function sp_dir_usage($path, $deadline, &$budget)
{
    if (!@is_dir($path)) {
        return null;
    }
    $bytes = 0;
    $files = 0;
    $complete = true;
    $stack = array($path);
    while ($stack) {
        if ($budget <= 0 || microtime(true) > $deadline) {
            $complete = false;
            break;
        }
        $dir = array_pop($stack);
        $handle = @opendir($dir);
        if (!$handle) {
            continue;
        }
        while (($entry = readdir($handle)) !== false) {
            if ($entry === '.' || $entry === '..') {
                continue;
            }
            $item = $dir . '/' . $entry;
            if (@is_link($item)) {
                continue;
            }
            if (@is_dir($item)) {
                $stack[] = $item;
                continue;
            }
            $size = @filesize($item);
            $bytes += $size === false ? 0 : $size;
            $files++;
            $budget--;
            if ($budget <= 0 || (($files & 511) === 0 && microtime(true) > $deadline)) {
                $complete = false;
                break;
            }
        }
        closedir($handle);
        if (!$complete) {
            break;
        }
    }
    return array('bytes' => $bytes, 'files' => $files, 'complete' => $complete);
}

function sp_collect_cron($root)
{
    $runner = sp_fn('shell_exec') ? 'shell_exec' : (sp_fn('exec') ? 'exec' : null);
    if ($runner === null) {
        return array('available' => false, 'reason' => 'shell_exec and exec are disabled; copy the crontab from the hosting panel');
    }
    if ($runner === 'shell_exec') {
        $raw = @shell_exec('crontab -l 2>/dev/null');
    } else {
        $lines = array();
        @exec('crontab -l 2>/dev/null', $lines);
        $raw = implode("\n", $lines);
    }
    if (!is_string($raw) || trim($raw) === '') {
        return array('available' => true, 'entries' => array(), 'reason' => 'crontab -l returned nothing (no crontab or no permission)');
    }
    $entries = array();
    $variables = 0;
    foreach (preg_split('/\r\n|\r|\n/', $raw) as $line) {
        $line = trim($line);
        if ($line === '' || $line[0] === '#') {
            continue;
        }
        if (preg_match('/^[A-Za-z_][A-Za-z0-9_]*\s*=/', $line)) {
            $variables++;
            continue;
        }
        if (!preg_match('/^(@\w+|(?:\S+\s+){4}\S+)\s+(.+)$/', $line, $m)) {
            continue;
        }
        $command = sp_redact_text($m[2]);
        // The account crontab may serve other sites too: a job is the application's when it names the root.
        $relevant = strpos($m[2], rtrim($root, '/')) !== false || stripos($m[2], 'cronjobs') !== false;
        $entries[] = array('schedule' => $m[1], 'command' => $command, 'relevant' => $relevant,
                           'setup' => sp_cron_translate($m[1], $m[2], $root));
    }
    return array('available' => true, 'entries' => $entries, 'variable_lines_omitted' => $variables);
}

/** A config/cron/crontab line for one LIVE job (PHP CLI jobs only; URL jobs need a decision). */
function sp_cron_translate($schedule, $command, $root)
{
    if (preg_match('#https?://#i', $command)) {
        return array('kind' => 'url', 'line' => null,
                     'note' => 'calls a URL: in setup it would hit LIVE; rewrite it as a PHP CLI call or against ${DOMAIN} of the target');
    }
    $root = rtrim($root, '/');
    if (preg_match('#(?:^|[\s;&|])(?:/\S*/)?php[0-9.]*(?:-cli)?\s+(?:-[a-zA-Z]\s*\S+\s+)*(\S+\.php)(.*)$#', $command, $m)) {
        $script = $m[1];
        if (strpos($script, $root . '/') === 0) {
            $script = substr($script, strlen($root) + 1);
        } elseif ($script[0] === '/') {
            return array('kind' => 'php-outside-root', 'line' => null, 'note' => 'runs a PHP file outside the application root');
        }
        $line = $schedule . ' cd /var/www/html && php ' . $script . sp_redact_text(rtrim(preg_replace('/\s*[>|&].*$/', '', $m[2])));
        return array('kind' => 'php', 'line' => $line, 'note' => null);
    }
    return array('kind' => 'other', 'line' => null, 'note' => 'not a PHP CLI call: check whether the target needs it');
}

// ------------------------------------------------------------------------------------------------
// Analysis: extensions, the setup plan, risks
// ------------------------------------------------------------------------------------------------

function sp_target_php_branch($result)
{
    $branch = $result['php']['branch'];
    $data = sp_setup_data();
    if (in_array($branch, $data['php_branches'], true)) {
        return array($branch, false);
    }
    foreach ($data['php_branches'] as $candidate) {
        if (version_compare($candidate, $branch, '>=')) {
            return array($candidate, true);
        }
    }
    return array('8.5', true);
}

function sp_ps_line($version)
{
    $parts = explode('.', (string) $version);
    if (count($parts) < 2) {
        return null;
    }
    $line = $parts[0] === '1' && $parts[1] === '6' ? '1.6' : ($parts[0] === '1' ? implode('.', array_slice($parts, 0, 3)) : $parts[0] . '.' . $parts[1]);
    $data = sp_setup_data();
    return isset($data['compatibility'][$line]) ? $line : null;
}

function sp_ext_install($ext, $branch)
{
    $bundled = array('bcmath' => '', 'bz2' => 'libbz2-dev', 'calendar' => '', 'dba' => '', 'enchant' => 'libenchant-2-dev',
        'exif' => '', 'ffi' => 'libffi-dev', 'gettext' => '', 'gmp' => 'libgmp-dev', 'intl' => 'libicu-dev',
        'ldap' => 'libldap2-dev', 'mysqli' => '', 'opcache' => '', 'pcntl' => '', 'pdo_mysql' => '', 'pdo_pgsql' => 'libpq-dev',
        'pgsql' => 'libpq-dev', 'pdo_odbc' => 'unixodbc-dev', 'pspell' => 'libpspell-dev', 'shmop' => '', 'snmp' => 'libsnmp-dev',
        'soap' => 'libxml2-dev', 'sockets' => '', 'sysvmsg' => '', 'sysvsem' => '', 'sysvshm' => '', 'tidy' => 'libtidy-dev',
        'xsl' => 'libxslt1-dev', 'zip' => 'libzip-dev', 'gd' => 'libpng-dev libjpeg62-turbo-dev libfreetype6-dev',
        'mbstring' => 'libonig-dev', 'iconv' => '', 'pdo_sqlite' => 'libsqlite3-dev', 'sqlite3' => 'libsqlite3-dev');
    if (version_compare($branch, '8.0', '<')) {
        $bundled['xmlrpc'] = 'libxml2-dev';
    }
    if (version_compare($branch, '7.4', '<')) {
        $bundled['wddx'] = 'libxml2-dev';
        $bundled['recode'] = 'librecode-dev';
    }
    if (version_compare($branch, '8.4', '<')) {
        $bundled['imap'] = 'libc-client-dev libkrb5-dev (docker-php-ext-configure imap --with-kerberos --with-imap-ssl)';
    }
    if (version_compare($branch, '7.2', '<')) {
        $bundled['mcrypt'] = 'libmcrypt-dev';
    }
    if ($branch === '5.6') {
        $bundled['mysql'] = '';
    }
    $pecl = array('imagick' => 'libmagickwand-dev', 'apcu' => '', 'igbinary' => '', 'msgpack' => '', 'mongodb' => 'libssl-dev',
        'memcache' => 'zlib1g-dev', 'ssh2' => 'libssh2-1-dev', 'yaml' => 'libyaml-dev', 'uuid' => 'uuid-dev',
        'amqp' => 'librabbitmq-dev', 'oauth' => '', 'geoip' => 'libgeoip-dev', 'mailparse' => '', 'zstd' => '',
        'protobuf' => '', 'grpc' => 'zlib1g-dev', 'swoole' => '', 'ds' => '', 'decimal' => 'libmpdec-dev', 'event' => 'libevent-dev',
        'mcrypt' => 'libmcrypt-dev', 'xmlrpc' => 'libxml2-dev', 'imap' => 'libc-client-dev libkrb5-dev', 'timezonedb' => '',
        'raphf' => '', 'solr' => 'libcurl4-openssl-dev libxml2-dev', 'vips' => 'libvips-dev', 'brotli' => '', 'lzf' => '',
        'redis' => '', 'memcached' => 'libmemcached-dev zlib1g-dev', 'xhprof' => '');
    if (isset($bundled[$ext])) {
        return array('method' => 'docker-php-ext-install', 'apt' => $bundled[$ext]);
    }
    if (isset($pecl[$ext])) {
        return array('method' => 'pecl', 'apt' => $pecl[$ext]);
    }
    if (in_array($ext, array('ioncube loader', 'sourceguardian'), true)) {
        return array('method' => 'manual', 'apt' => '', 'note' => 'download the loader for PHP ' . $branch . ' and add zend_extension=... in the Dockerfile');
    }
    return array('method' => 'unknown', 'apt' => '', 'note' => 'probably pecl install ' . $ext . ' (verify the package name)');
}

function sp_host_only_extensions()
{
    return array('cgi-fcgi', 'litespeed', 'apache2handler', 'fpm-fcgi', 'newrelic', 'blackfire', 'tideways', 'tideways_xhprof',
        'ddtrace', 'datadog-profiling', 'zend guard loader', 'suhosin', 'i360', 'imunify360', 'clos_ssa', 'snuffleupagus',
        'pinba', 'scoutapm', 'elastic_apm', 'opentelemetry', 'xdebug', 'pcov', 'uopz', 'xhprof', 'sqlsrv', 'pdo_sqlsrv',
        'phpdbg_webhelper');
}

function sp_analyse_extensions($result)
{
    $target = sp_target_php_branch($result);
    $branch = $target[0];
    $core = sp_core_extensions($branch);
    $platform = $result['application'];
    $required = array();
    if (sp_is_ps($platform)) {
        $required = array('curl', 'dom', 'fileinfo', 'gd', 'iconv', 'intl', 'json', 'mbstring', 'openssl', 'pdo', 'pdo_mysql', 'simplexml', 'zip');
        if (strpos((string) $platform['version'], '1.6') === 0) {
            $required = array('curl', 'dom', 'gd', 'json', 'mbstring', 'openssl', 'pdo', 'pdo_mysql', 'simplexml', 'zip');
            $cipher = sp_get(sp_get((array) $result['prestashop'], 'configuration', array()), 'PS_CIPHER_ALGORITHM');
            if ((string) $cipher === '1') {
                $required[] = 'mcrypt';
            }
        }
    }
    $composer = $result['composer'];
    $composer_exts = array_merge((array) sp_get($composer, 'extensions', array()), (array) sp_get($composer, 'lock_extensions', array()));
    $required = array_values(array_unique(array_merge($required, $composer_exts)));
    $live = $result['php']['extensions'];
    $missing = array();
    $optional = array();
    $host_only = array();
    foreach ($live as $ext) {
        if (in_array($ext, $core, true)) {
            continue;
        }
        if (in_array($ext, array('redis', 'memcached'), true)) {
            $optional[] = $ext;
            continue;
        }
        if (in_array($ext, sp_host_only_extensions(), true) || strpos($ext, 'cgi') !== false) {
            $host_only[] = $ext;
            continue;
        }
        $install = sp_ext_install($ext, $branch);
        $missing[] = array('extension' => $ext, 'needed' => in_array($ext, $required, true) ? 'required by the application'
            : (in_array($ext, array('ioncube loader', 'sourceguardian'), true) && sp_get((array) $result['prestashop'], 'encoded_files')
                ? 'encoded modules found' : 'check whether the code uses it'),
            'method' => $install['method'], 'apt' => $install['apt'], 'note' => sp_get($install, 'note'));
    }
    $required_missing_everywhere = array();
    foreach ($required as $ext) {
        if (!in_array($ext, $core, true) && !in_array($ext, $live, true) && !in_array($ext, array('redis', 'memcached'), true)) {
            $required_missing_everywhere[] = $ext;
        }
    }
    return array(
        'target_branch' => $branch,
        'target_is_guess' => $target[1],
        'core' => $core,
        'required' => $required,
        'missing_in_core' => $missing,
        'available_not_enabled' => $optional,
        'host_specific' => $host_only,
        'required_but_missing' => $required_missing_everywhere,
        'core_but_not_live' => array_values(array_diff($core, $live)),
    );
}

function sp_normalise_ini($value, $type)
{
    if ($value === null) {
        return null;
    }
    if ($type === 'bool') {
        return sp_is_on($value) ? 'On' : 'Off';
    }
    if ($type === 'size') {
        $bytes = sp_bytes($value);
        return $bytes === null ? (string) $value : (string) $bytes;
    }
    if ($type === 'int') {
        if (strtolower(trim((string) $value)) === 'on') {
            return '1';
        }
        if (strtolower(trim((string) $value)) === 'off' || trim((string) $value) === '') {
            return '0';
        }
        $bytes = sp_bytes($value);
        return $bytes === null ? (string) $value : (string) $bytes;
    }
    return strtolower(trim((string) $value));
}

function sp_core_ini_value($key, $branch)
{
    $data = sp_setup_data();
    if (isset($data['core_ini_by_branch'][$key])) {
        $by = $data['core_ini_by_branch'][$key];
        return isset($by[$branch]) ? $by[$branch] : $by['default'];
    }
    return $data['core_ini'][$key][0];
}

function sp_entry($key, $value, $live, $source, $kind, $note = null, $commented = false)
{
    return array('key' => $key, 'value' => $value, 'live' => $live, 'source' => $source, 'kind' => $kind,
                 'note' => $note, 'commented' => $commented);
}

/** The setup settings and files that reproduce LIVE. kind: live | guess | default | hint. */
function sp_plan($r)
{
    $data = sp_setup_data();
    $platform = $r['application'];
    $ps = $r['prestashop'];
    $database = $r['database'];
    $php = $r['php'];
    $cli = $r['_meta']['mode'] === 'cli';
    $common = array();
    $prod = array();
    $local = array();
    $secrets = array();
    $ini = array();
    $cnf = array();
    $after_import = array();

    // Application kind
    if (sp_is_ps($platform)) {
        $common[] = sp_entry('PROFILE', 'prestashop', $platform['name'] . ' ' . $platform['version'], $platform['source'], 'live');
        $common[] = sp_entry('PS_VERSION', (string) $platform['version'], $platform['version'], $platform['source'], 'live');
    } elseif ($platform['type'] === 'akeneo') {
        $common[] = sp_entry('PROFILE', 'akeneo', 'Akeneo PIM', $platform['source'], 'live');
    } else {
        $common[] = sp_entry('PROFILE', '', $platform['name'], (string) $platform['source'], 'live', 'empty = generic PHP application');
    }

    // PHP version
    $target = $r['extensions']['target_branch'];
    $php_note = null;
    $php_kind = 'live';
    if ($r['extensions']['target_is_guess']) {
        $php_kind = 'guess';
        $php_note = 'setup has no PHP ' . $php['branch'] . ' image; nearest newer branch';
    }
    $compat_off = false;
    $line = sp_is_ps($platform) ? sp_ps_line($platform['version']) : null;
    if (sp_is_ps($platform) && $platform['type'] === 'thirtybees') {
        $compat_off = true;
    } elseif ($line !== null && !in_array($target, $data['compatibility'][$line]['php'], true)) {
        $compat_off = true;
        $php_note = 'LIVE runs PHP ' . $target . ', outside setup\'s list for PrestaShop ' . $line . ' ('
            . implode(', ', $data['compatibility'][$line]['php']) . '): either keep it with SETUP_COMPATIBILITY_CHECK=off or pick a listed version';
    } elseif (sp_is_ps($platform) && $line === null) {
        $compat_off = true;
        $php_note = 'PrestaShop ' . $platform['version'] . ' is not in setup\'s compatibility table';
    }
    $common[] = sp_entry('PHP_VERSION', $target, $php['version'] . ' (' . $php['sapi'] . ')', 'PHP_VERSION', $php_kind, $php_note);
    if ($compat_off) {
        $common[] = sp_entry('SETUP_COMPATIBILITY_CHECK', 'off', null, 'compatibility.json', 'hint',
            $platform['type'] === 'thirtybees' ? 'thirty bees fork' : 'only if you keep the LIVE PHP version (decision)');
    }

    // Database engine and version
    if ($database['available']) {
        $engine = $database['engine'];
        $common[] = sp_entry('DATABASE_ENGINE', $engine, $database['flavour'] . ' ' . $database['version'], 'SELECT VERSION() / version_comment', 'live');
        $version_key = $engine === 'mariadb' ? 'MARIADB_VERSION' : 'MYSQL_VERSION';
        $common[] = sp_entry($version_key, (string) $database['version_number'], $database['version'], 'SELECT VERSION()', 'live',
            'Docker Hub tag ' . ($engine === 'mariadb' ? 'mariadb' : 'mysql') . ':' . $database['version_number'] . '; if it does not exist use ' . $database['branch']);
    } else {
        $common[] = sp_entry('DATABASE_ENGINE', 'mysql', null, 'database not reachable', 'guess', 'unknown: the database was not reachable', true);
    }
    if (sp_is_ps($platform)) {
        $prefix = isset($database['prefix']) ? (string) $database['prefix'] : '';
        if ($prefix !== '' && $prefix !== 'ps_') {
            $common[] = sp_entry('PS_DATABASE_PREFIX', $prefix, $prefix, (string) $database['config_source'], 'live');
        }
        $locale = sp_get((array) sp_get((array) $ps, 'parameters', array()), 'locale');
        if (is_string($locale) && $locale !== '' && $locale !== 'en-US') {
            $common[] = sp_entry('PS_LOCALE', $locale, $locale, (string) $ps['config_file'], 'live', 'used only when setup creates a missing parameters.php');
        }
    }

    // Time zone
    $tz = null;
    $tz_source = null;
    $ps_tz = sp_get((array) sp_get((array) $ps, 'configuration', array()), 'PS_TIMEZONE');
    if (is_string($ps_tz) && $ps_tz !== '') {
        $tz = $ps_tz;
        $tz_source = 'configuration PS_TIMEZONE';
    } elseif ((string) $php['ini']['date.timezone'] !== '') {
        $tz = $php['ini']['date.timezone'];
        $tz_source = 'ini date.timezone';
    } elseif ($r['host']['timezone']['etc_timezone'] !== '') {
        $tz = $r['host']['timezone']['etc_timezone'];
        $tz_source = '/etc/timezone';
    }
    $common[] = $tz !== null ? sp_entry('TZ', $tz, $tz, $tz_source, 'live')
        : sp_entry('TZ', 'Europe/Vilnius', null, 'setup default', 'default', 'LIVE time zone not found');

    // HTTP limits (the FPM pool sets them from env)
    $limits = array('memory_limit' => 'PHP_MEMORY_LIMIT', 'max_execution_time' => 'PHP_MAX_EXECUTION_TIME',
                    'upload_max_filesize' => 'PHP_UPLOAD_MAX_FILESIZE', 'post_max_size' => 'PHP_POST_MAX_SIZE');
    $overrides = sp_web_overrides($r);
    foreach ($limits as $ini_key => $env) {
        $value = $php['ini'][$ini_key];
        if ($cli && isset($overrides[$ini_key])) {
            $common[] = sp_entry($env, $overrides[$ini_key][0], $overrides[$ini_key][0], $overrides[$ini_key][1], 'live',
                'the web value; the CLI shows ' . $value);
            continue;
        }
        if ($cli && in_array($ini_key, array('memory_limit', 'max_execution_time'), true)) {
            $common[] = sp_entry($env, $data['defaults'][$env], $value, 'ini_get(' . $ini_key . ') in CLI', 'default',
                'CLI value is not the web value: run the probe over HTTP for this limit');
            continue;
        }
        $common[] = sp_entry($env, (string) $value, $value, 'ini_get(' . $ini_key . ')', 'live',
            (string) $value === $data['defaults'][$env] ? 'same as the setup default' : null);
    }

    // Document root
    $setting = $r['webserver']['document_root_setting'];
    if ($setting !== null && $setting !== '/') {
        $common[] = sp_entry('DOCUMENT_ROOT', $setting, $r['webserver']['document_root'], 'DOCUMENT_ROOT vs application root', 'live');
    }

    // Services
    $services = array();
    $service_notes = array();
    $handler = strtolower((string) $php['ini']['session.save_handler']);
    $ps_cache = sp_get((array) $ps, 'cache', array());
    $ps_cache_system = strtolower((string) sp_get($ps_cache, 'system', ''));
    $ps_cache_on = (bool) sp_get($ps_cache, 'enabled', false);
    $composer_exts = array_merge((array) sp_get($r['composer'], 'extensions', array()), (array) sp_get($r['composer'], 'lock_extensions', array()));
    foreach (array('redis', 'memcached') as $service) {
        $why = array();
        if (strpos($handler, $service === 'redis' ? 'redis' : 'memcache') !== false) {
            $why[] = 'session.save_handler=' . $handler;
        }
        if ($ps_cache_on && strpos($ps_cache_system, $service === 'redis' ? 'redis' : 'memcache') !== false) {
            $why[] = 'PrestaShop cache ' . sp_get($ps_cache, 'system');
        }
        if (in_array($service, $composer_exts, true)) {
            $why[] = 'composer requires ext-' . $service;
        }
        if ($why) {
            $services[] = $service;
            $service_notes[] = $service . ': ' . implode(', ', $why);
            $ini[] = sp_entry('extension', $service . '.so', 'loaded on LIVE', implode(', ', $why), 'live', 'config/php/cache.ini');
        } elseif (sp_get(sp_get($r['host']['services'], $service, array()), 'reachable')) {
            $service_notes[] = $service . ' answers on localhost but the application does not seem to use it';
        }
    }
    $cron_jobs = 0;
    foreach ((array) sp_get($r['cron'], 'entries', array()) as $entry) {
        if ($entry['relevant']) {
            $cron_jobs++;
        }
    }
    if ($cron_jobs > 0 || sp_get((array) $ps, 'cron_modules')) {
        $services[] = 'cron';
        $service_notes[] = 'cron: ' . $cron_jobs . ' crontab line(s)' . (sp_get((array) $ps, 'cron_modules') ? ', modules ' . implode(', ', $ps['cron_modules']) : '');
    }
    $common[] = sp_entry('SERVICES', implode(' ', $services), $service_notes ? implode('; ', $service_notes) : 'no cache or cron use found',
        'session handler, application cache, crontab', $services ? 'live' : 'hint',
        'local.env usually adds the tools: SERVICES="pma mailpit' . ($services ? ' ' . implode(' ', $services) : '') . '"',
        !$services);
    if ($platform['type'] !== 'prestashop' && $platform['type'] !== 'thirtybees') {
        $names = array();
        foreach ((array) sp_get($platform['config'], 'env_names', array()) as $name) {
            if (!sp_setup_owned_name($name)) {
                $names[] = $name;
            }
        }
        if ($names && sp_get($platform['config'], 'format') === 'dotenv') {
            $common[] = sp_entry('APP_ENVIRONMENT', implode(' ', array_slice($names, 0, 60)), 'names from ' . $platform['config']['file'],
                $platform['config']['file'], 'hint', 'names only; values go to env files / Deployment variables', true);
        }
    }
    if (!empty($database['other_visible_schemas'])) {
        $common[] = sp_entry('DATABASE_EXTRA_SCHEMAS', implode(' ', $database['other_visible_schemas']), 'schemas the application user can see',
            'information_schema.SCHEMATA', 'hint', 'only if the application really uses them', true);
    }

    // prod.env
    $domain = null;
    $domain_source = null;
    foreach ((array) sp_get((array) $ps, 'shops', array()) as $shop) {
        if ($domain === null && (string) $shop['main'] === '1' && (string) $shop['domain'] !== '') {
            $domain = (string) $shop['domain_ssl'] !== '' ? (string) $shop['domain_ssl'] : (string) $shop['domain'];
            $domain_source = 'shop_url (main URL of shop ' . $shop['id_shop'] . ')';
        }
    }
    if ($domain === null && $r['webserver']['http_host'] !== null) {
        $domain = preg_replace('/:\d+$/', '', $r['webserver']['http_host']);
        $domain_source = 'HTTP_HOST';
    }
    $prod[] = $domain !== null ? sp_entry('DOMAIN', $domain, $domain, $domain_source, 'live')
        : sp_entry('DOMAIN', 'example.com', null, 'not found', 'guess', 'set the LIVE host name', true);
    if (isset($database['name']) && $database['name'] !== '') {
        $prod[] = sp_entry('DATABASE_NAME', $database['name'], $database['name'], (string) $database['config_source'], 'live');
    }
    $prod[] = sp_entry('DATABASE_USER', '<choose>', 'present (not shown)', (string) sp_get($database, 'config_source'), 'hint',
        'the container user; it does not have to be the LIVE user', true);
    $cpu = sp_get($r['host'], 'cpu_count');
    $cpus = $cpu !== null && $cpu < 2 ? (string) max(1, $cpu) : '2';
    $prod[] = sp_entry('PHP_MEMORY', '2g', null, 'setup doc 09 example', 'guess', 'size it to the target server');
    $prod[] = sp_entry('PHP_CPUS', $cpus, $cpu !== null ? $cpu . ' CPU(s) visible on LIVE' : null, '/proc/cpuinfo', 'guess');
    $pool = sp_buffer_pool_guess($database);
    $prod[] = sp_entry('DATABASE_MEMORY', $pool['memory'], $pool['live'], $pool['source'], 'guess', 'about 60 % of it becomes the InnoDB buffer pool');
    if ($pool['pool'] !== null) {
        $prod[] = sp_entry('DATABASE_BUFFER_POOL', $pool['pool'], $pool['live'], $pool['source'], 'guess', $pool['note']);
    }
    $prod[] = sp_entry('DATABASE_CPUS', $cpus, null, 'setup doc 09 example', 'guess');
    $prod[] = sp_entry('WEBSERVER_MEMORY', '256m', null, 'setup doc 09 example', 'guess');
    $prod[] = sp_entry('WEBSERVER_CPUS', '0.5', null, 'setup doc 09 example', 'guess');
    $prod[] = sp_entry('PROXY_MEMORY', '128m', null, 'setup doc 09 example', 'guess');
    $prod[] = sp_entry('PROXY_CPUS', '0.5', null, 'setup doc 09 example', 'guess');
    $uid = sp_get($r['host'], 'uid');
    $gid = sp_get($r['host'], 'gid');
    if ($uid !== null) {
        $prod[] = sp_entry('APP_UID', (string) $uid, (string) $uid, (string) $r['host']['uid_source'], 'guess', 'the hosting user\'s id on the Docker server (id -u)');
        $prod[] = sp_entry('APP_GID', (string) $gid, (string) $gid, (string) $r['host']['uid_source'], 'guess', 'id -g of the same user');
    }
    $prod[] = sp_entry('DEPLOY_PATH', '/home/<user>/<project>', null, 'not measurable', 'hint', 'directory with app/ and data/ on the Docker server', true);

    // Secrets (names only)
    $secrets[] = array('name' => 'DATABASE_PASSWORD', 'where' => 'Bitbucket Deployment variable (prod/stage); env/local.env locally',
                       'from' => 'choose a new one or reuse the LIVE password from ' . sp_get($database, 'config_source', 'the config file'));
    $secrets[] = array('name' => 'DATABASE_ROOT_PASSWORD', 'where' => 'Deployment variable; make init generates the local one',
                       'from' => 'new random value (must differ from DATABASE_PASSWORD)');
    if (sp_is_ps($platform)) {
        foreach ((array) sp_get((array) $ps, 'required_keys', array()) as $key) {
            $presence = sp_get((array) sp_get((array) $ps, 'key_presence', array()), $key);
            $present = is_array($presence) && $presence['present'];
            $from = $present ? 'copy `' . $presence['file_key'] . '` from LIVE ' . $ps['config_file']
                : 'MISSING on LIVE (' . (is_array($presence) ? $presence['file_key'] : $key) . ')';
            $secrets[] = array('name' => $key, 'where' => 'env/local.env (private) and the prod/stage Deployment variables', 'from' => $from);
            $local[] = sp_entry($key, '<' . ($present ? $presence['file_key'] : $key) . ' from LIVE>', $present ? 'present' : 'missing',
                (string) $ps['config_file'], 'hint', 'value never printed: copy it by hand', true);
        }
    } else {
        foreach ((array) sp_get($platform['config'], 'env_names', array()) as $name) {
            if (sp_is_secret_name($name)) {
                $secrets[] = array('name' => $name, 'where' => 'APP_ENVIRONMENT name; value in env/local.env and Deployment variables',
                                   'from' => 'LIVE ' . $platform['config']['file']);
            }
        }
    }

    // config/php drop-in
    $branch = $target;
    foreach (sp_core_ini() as $key => $spec) {
        $live = $php['ini'][$key];
        $live_source = 'ini_get(' . $key . ')';
        if ($cli && isset($overrides[$key])) {
            $live = $overrides[$key][0];
            $live_source = $overrides[$key][1];
        }
        if ($live === null || ($key === 'mbstring.func_overload' && !sp_is_on($live) && (string) $live !== '2')) {
            continue;
        }
        if ($key === 'apc.shm_size' && !extension_loaded('apcu')) {
            continue;
        }
        if ($key === 'session.save_handler') {
            continue;
        }
        // The CLI SAPI forces these whatever php.ini says: only a web run shows the site's value.
        if ($cli && !isset($overrides[$key]) && in_array($key, array('output_buffering', 'max_input_time'), true)) {
            continue;
        }
        $core_value = sp_core_ini_value($key, $branch);
        if (sp_normalise_ini($live, $spec[1]) !== sp_normalise_ini($core_value, $spec[1])) {
            $ini[] = sp_entry($key, $live, $live, $live_source, 'live', 'setup CORE: ' . $core_value);
        }
    }
    $skip = array('display_errors', 'display_startup_errors', 'error_reporting', 'html_errors', 'log_errors', 'error_log',
                  'auto_prepend_file', 'auto_append_file', 'open_basedir', 'session.save_path', 'upload_tmp_dir', 'sys_temp_dir');
    foreach ($overrides as $key => $override) {
        if (!isset($data['env_ini'][$key]) && !array_key_exists($key, sp_core_ini()) && !in_array($key, $skip, true)) {
            $ini[] = sp_entry($key, $override[0], $override[0], $override[1], 'live', 'set per directory on LIVE');
        }
    }
    if (strpos($handler, 'redis') !== false) {
        $ini[] = sp_entry('session.save_handler', 'redis', $handler, 'ini_get(session.save_handler)', 'live');
        $ini[] = sp_entry('session.save_path', '"tcp://redis:6379"', $php['ini']['session.save_path'], 'ini_get(session.save_path)', 'guess', 'setup Redis service address');
    } elseif (strpos($handler, 'memcache') !== false) {
        $ini[] = sp_entry('session.save_handler', 'memcached', $handler, 'ini_get(session.save_handler)', 'live');
        $ini[] = sp_entry('session.save_path', '"memcached:11211"', $php['ini']['session.save_path'], 'ini_get(session.save_path)', 'guess', 'setup Memcached service address');
    } elseif ($handler !== '' && $handler !== 'files') {
        $ini[] = sp_entry('session.save_handler', $handler, $handler, 'ini_get(session.save_handler)', 'live', 'needs a matching service');
    }

    // config/mysql (or config/mariadb) drop-in
    if ($database['available']) {
        $cnf = sp_cnf_plan($database);
    }

    // after-import SQL drafts (PrestaShop)
    if (sp_is_ps($platform) && is_array($ps)) {
        $after_import = sp_after_import_plan($ps, $database);
    }

    $dockerfile = array();
    foreach ($r['extensions']['missing_in_core'] as $missing) {
        $dockerfile[] = $missing;
    }
    foreach ($r['extensions']['required_but_missing'] as $ext) {
        $install = sp_ext_install($ext, $branch);
        $dockerfile[] = array('extension' => $ext, 'needed' => 'required by the application (not loaded on LIVE either)',
                              'method' => $install['method'], 'apt' => $install['apt'], 'note' => sp_get($install, 'note'));
    }

    return array(
        'common_env' => $common,
        'prod_env' => $prod,
        'local_env' => $local,
        'secrets' => $secrets,
        'php_ini' => $ini,
        'database_cnf' => $cnf,
        'database_cnf_directory' => $database['available'] && $database['engine'] === 'mariadb' ? 'config/mariadb' : 'config/mysql',
        'dockerfile' => $dockerfile,
        'after_import' => $after_import,
        'registry_draft' => sp_registry_draft($r),
        'crontab' => sp_crontab_plan($r),
    );
}

/** PHP values LIVE sets per directory (.htaccess php_value, .user.ini): name => array(value, source). */
function sp_web_overrides($r)
{
    $out = array();
    foreach ((array) sp_get($r['webserver']['htaccess'], 'php_values', array()) as $value) {
        $out[strtolower($value['name'])] = array($value['value'], '.htaccess line ' . $value['line']);
    }
    foreach ((array) $r['webserver']['user_ini'] as $user_ini) {
        foreach ($user_ini['values'] as $key => $value) {
            $out[strtolower($key)] = array($value, $user_ini['file']);
        }
    }
    return $out;
}

function sp_setup_owned_name($name)
{
    if (preg_match('/^(DATABASE_|APP_DATABASE_|MYSQL_|MARIADB_|PS_|PHP_|XDEBUG_|CRON_|PROJECT_|SETUP_|COMPOSE_|DOCKER_)/', $name)) {
        return true;
    }
    return in_array($name, array('ENV', 'PROFILE', 'DOMAIN', 'SERVICES', 'TZ', 'HOST_UID', 'HOST_GID', 'APP_UID', 'APP_GID',
        'APP_ENVIRONMENT', 'DOCUMENT_ROOT', 'CACHE_MODE', 'MAIL_MODE'), true);
}

function sp_buffer_pool_guess($database)
{
    $out = array('memory' => '2g', 'pool' => null, 'live' => null, 'source' => 'setup default sizing', 'note' => null);
    if (!$database['available']) {
        return $out;
    }
    $total = (int) $database['tables']['total_bytes'];
    $live_pool = (int) sp_get($database['variables'], 'innodb_buffer_pool_size', 0);
    $mb = (int) (ceil(($total / 1048576) * 1.25 / 128) * 128);
    $mb = max(256, min(8192, $mb));
    if ($live_pool > 0 && $live_pool / 1048576 < $mb) {
        $mb = max(128, (int) ($live_pool / 1048576));
    }
    $memory_mb = (int) (ceil(($mb / 0.6) / 512) * 512);
    $out['memory'] = $memory_mb % 1024 === 0 ? ($memory_mb / 1024) . 'g' : $memory_mb . 'm';
    $out['pool'] = $mb . 'M';
    $out['live'] = 'data+index ' . sp_human($total) . ', LIVE innodb_buffer_pool_size ' . sp_human($live_pool);
    $out['source'] = 'information_schema.TABLES + SHOW GLOBAL VARIABLES';
    $out['note'] = 'about 1.25 x the data size (capped by the LIVE pool and 8 GB); the LIVE pool serves the whole server';
    return $out;
}

function sp_target_sql_mode_default($engine, $branch)
{
    if ($engine === 'mariadb') {
        return 'STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION';
    }
    if (version_compare((string) $branch, '8.0', '>=')) {
        return 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';
    }
    return 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION';
}

function sp_cnf_plan($database)
{
    $v = $database['variables'];
    $engine = $database['engine'];
    $branch = (string) $database['branch'];
    $mysql8 = $engine === 'mysql' && version_compare($branch, '8.0', '>=');
    $out = array();
    if (isset($v['sql_mode'])) {
        $modes = array_filter(explode(',', (string) $v['sql_mode']));
        if ($mysql8) {
            $modes = array_diff($modes, array('NO_AUTO_CREATE_USER'));
        }
        $live = implode(',', $modes);
        $default = sp_target_sql_mode_default($engine, $branch);
        $sorted_live = $modes;
        sort($sorted_live);
        $sorted_default = explode(',', $default);
        sort($sorted_default);
        if ($sorted_live !== $sorted_default) {
            $out[] = sp_entry('sql_mode', '"' . $live . '"', $v['sql_mode'], 'SHOW GLOBAL VARIABLES', 'live', 'image default: ' . $default);
        }
    }
    // MySQL images default to utf8mb4/utf8mb4_0900_ai_ci (8.x) or latin1 (5.x); MariaDB images vary by build: always explicit.
    $charset_default = $engine === 'mysql' ? ($mysql8 ? array('character_set_server' => 'utf8mb4', 'collation_server' => 'utf8mb4_0900_ai_ci')
        : array('character_set_server' => 'latin1', 'collation_server' => 'latin1_swedish_ci')) : array();
    foreach (array('character_set_server' => 'character-set-server', 'collation_server' => 'collation-server') as $name => $option) {
        if (isset($v[$name]) && (string) sp_get($charset_default, $name, '') !== (string) $v[$name]) {
            $out[] = sp_entry($option, $v[$name], $v[$name], 'SHOW GLOBAL VARIABLES', 'live', 'new tables and databases inherit it');
        }
    }
    $packet = isset($v['max_allowed_packet']) ? (int) $v['max_allowed_packet'] : 0;
    $default_packet = $engine === 'mariadb' ? 16777216 : 67108864;
    if ($packet > $default_packet) {
        $out[] = sp_entry('max_allowed_packet', (int) ($packet / 1048576) . 'M', sp_human($packet), 'SHOW GLOBAL VARIABLES', 'live', 'large rows in the dump need it for the import');
    }
    if (isset($v['lower_case_table_names']) && (string) $v['lower_case_table_names'] !== '0') {
        $out[] = sp_entry('lower_case_table_names', $v['lower_case_table_names'], $v['lower_case_table_names'], 'SHOW GLOBAL VARIABLES', 'live',
            'MySQL 8 accepts it only when the data directory is created: add it before the first start');
    }
    if (isset($v['group_concat_max_len']) && (int) $v['group_concat_max_len'] !== 1024) {
        $out[] = sp_entry('group_concat_max_len', $v['group_concat_max_len'], $v['group_concat_max_len'], 'SHOW GLOBAL VARIABLES', 'live');
    }
    if (isset($v['ft_min_word_len']) && (int) $v['ft_min_word_len'] !== 4) {
        $out[] = sp_entry('ft_min_word_len', $v['ft_min_word_len'], $v['ft_min_word_len'], 'SHOW GLOBAL VARIABLES', 'live', 'MyISAM full-text search');
    }
    if (isset($v['innodb_ft_min_token_size']) && (int) $v['innodb_ft_min_token_size'] !== 3) {
        $out[] = sp_entry('innodb_ft_min_token_size', $v['innodb_ft_min_token_size'], $v['innodb_ft_min_token_size'], 'SHOW GLOBAL VARIABLES', 'live', 'InnoDB full-text search');
    }
    if (isset($v['innodb_strict_mode']) && !sp_is_on($v['innodb_strict_mode'])) {
        $out[] = sp_entry('innodb_strict_mode', 'OFF', $v['innodb_strict_mode'], 'SHOW GLOBAL VARIABLES', 'live', 'wide LIVE tables may fail to import with strict mode on');
    }
    if ($engine === 'mysql' && isset($v['explicit_defaults_for_timestamp']) && !sp_is_on($v['explicit_defaults_for_timestamp']) && $mysql8) {
        $out[] = sp_entry('explicit_defaults_for_timestamp', 'OFF', $v['explicit_defaults_for_timestamp'], 'SHOW GLOBAL VARIABLES', 'live', 'MySQL 8 default is ON');
    }
    if (isset($v['time_zone']) && strtoupper((string) $v['time_zone']) !== 'SYSTEM') {
        $out[] = sp_entry('default-time-zone', "'" . $v['time_zone'] . "'", $v['time_zone'], 'SHOW GLOBAL VARIABLES', 'live',
            'named zones need the time zone tables; otherwise rely on TZ');
    }
    return $out;
}

function sp_after_import_plan($ps, $database)
{
    $out = array();
    $default_shop = (string) sp_get((array) sp_get($ps, 'configuration', array()), 'PS_SHOP_DEFAULT', '1');
    $others = array();
    foreach ((array) sp_get($ps, 'shops', array()) as $shop) {
        if ($shop['id_shop_url'] === null) {
            continue;
        }
        if ((string) $shop['id_shop'] === $default_shop && (string) $shop['main'] === '1') {
            continue;
        }
        $others[] = $shop;
    }
    if ($others) {
        $sql = "-- Other shops and URLs of the multistore: setup's safe default moves only the default shop's main URL.\n"
            . "-- Each other URL is served under the environment's DOMAIN with its own virtual URI.\n";
        foreach ($others as $shop) {
            $virtual = trim(preg_replace('/[^a-z0-9]+/', '-', strtolower((string) $shop['name'])), '-');
            $virtual = ($virtual !== '' ? $virtual : 'shop' . $shop['id_shop']) . '-' . $shop['id_shop_url'];
            $sql .= "UPDATE `\${DB_PREFIX}shop_url` SET `domain` = '\${DOMAIN}', `domain_ssl` = '\${DOMAIN}', `physical_uri` = '/', "
                . "`virtual_uri` = '" . $virtual . "/' WHERE `id_shop_url` = " . (int) $shop['id_shop_url'] . ";  -- LIVE: "
                . $shop['domain'] . $shop['physical_uri'] . $shop['virtual_uri'] . "\n";
        }
        $out[] = array('file' => 'database/after-import/local/020-multistore-urls.sql (and test/, stage/)', 'sql' => $sql,
                       'why' => count($others) . ' more shop URL(s) would keep LIVE domains');
    }
    $configuration = (array) sp_get($ps, 'configuration', array());
    $media = array();
    foreach (array('PS_MEDIA_SERVER_1', 'PS_MEDIA_SERVER_2', 'PS_MEDIA_SERVER_3') as $name) {
        if ((string) sp_get($configuration, $name, '') !== '') {
            $media[] = $name;
        }
    }
    if ($media) {
        $out[] = array('file' => 'database/after-import/local/030-media-servers.sql (and test/, stage/)',
            'sql' => "UPDATE `\${DB_PREFIX}configuration` SET `value` = '' WHERE `name` IN ('" . implode("', '", $media) . "');\n"
                . "UPDATE `\${DB_PREFIX}configuration` SET `value` = '0' WHERE `name` = 'PS_MEDIA_SERVERS';\n",
            'why' => 'LIVE media servers (CDN) would serve assets of another environment');
    }
    $cache = (array) sp_get($ps, 'cache', array());
    if (sp_get($ps, 'memcached_servers') && !empty($cache['enabled']) && stripos((string) sp_get($cache, 'system', ''), 'memcache') !== false) {
        $out[] = array('file' => 'database/after-import/common/040-memcached-servers.sql',
            'sql' => "UPDATE `\${DB_PREFIX}memcached_servers` SET `ip` = 'memcached', `port` = 11211;\n",
            'why' => 'PrestaShop memcached servers point to LIVE addresses; setup serves memcached:11211 (SERVICES=memcached)');
    }
    foreach ((array) sp_get($database, 'stored_objects', array()) as $object) {
        if ($object['kind'] === 'event') {
            $out[] = array('file' => 'database/after-import/local/050-events.sql (and test/, stage/)',
                'sql' => "-- ALTER EVENT `<name>` DISABLE;  -- one line per event listed in the report\n",
                'why' => 'LIVE events would run on every copy (the event scheduler is on in MySQL 8)');
            break;
        }
    }
    return $out;
}

function sp_registry_draft($r)
{
    $database = $r['database'];
    if (!$database['available']) {
        return array();
    }
    $is_ps = sp_is_ps($r['application']);
    $prefix = (string) sp_get($database, 'prefix', '');
    $known = array_flip(sp_ps_registry_known());
    $sizes = array();
    foreach ($database['tables']['largest'] as $table) {
        $sizes[$table['name']] = $table['bytes'];
    }
    $lines = array();
    foreach ($database['table_names'] as $name) {
        $short = $is_ps && $prefix !== '' && strpos($name, $prefix) === 0 ? substr($name, strlen($prefix)) : null;
        if ($is_ps && $short !== null && isset($known[$short])) {
            continue;
        }
        if ($name === 'setup_migrations') {
            continue;
        }
        $display = $short !== null ? '{p}' . $short : $name;
        $volatile = preg_match('/(^|_)(log|logs|cache|stats?|session|sessions|tmp|temp|queue|history|search|index|backup|bak|old)(_|$)|_bak|_old|_copy|_\d{6,}/i', $name);
        $group = $short !== null ? (strpos($short, '_') !== false ? substr($short, 0, strpos($short, '_')) : $short) : 'app';
        $rule = $volatile ? ($group === 'app' ? 'cache' : $group) . '  empty  skip' : $group . '  full  import';
        $lines[] = array('line' => str_pad($display, 50) . ' ' . $rule, 'suggested' => $volatile ? 'volatile name' : 'module/application data',
                         'bytes' => isset($sizes[$name]) ? $sizes[$name] : null);
        if (count($lines) >= 300) {
            break;
        }
    }
    return $lines;
}

function sp_crontab_plan($r)
{
    $lines = array();
    foreach ((array) sp_get($r['cron'], 'entries', array()) as $entry) {
        if (!$entry['relevant']) {
            $lines[] = '# ' . $entry['schedule'] . ' ' . $entry['command'] . '   <- does not name the application root (another site?)';
        } elseif ($entry['setup']['line'] !== null) {
            $lines[] = $entry['setup']['line'];
        } else {
            $lines[] = '# ' . $entry['schedule'] . ' ' . $entry['command'] . '   <- ' . $entry['setup']['note'];
        }
    }
    return $lines;
}

/** Mismatches and risks: level high | medium | low | info. */
function sp_risks($r)
{
    $risks = array();
    $data = sp_setup_data();
    $platform = $r['application'];
    $ps = $r['prestashop'];
    $database = $r['database'];
    $php = $r['php'];
    $web = $r['webserver'];
    $ext = $r['extensions'];
    $add = function ($level, $id, $title, $fix) use (&$risks) {
        $risks[] = array('level' => $level, 'id' => $id, 'title' => $title, 'fix' => $fix);
    };

    if ($r['_meta']['mode'] === 'cli') {
        $add('info', 'cli-run', 'Collected from the command line: PHP limits, the web server and HTTPS are those of the CLI, not of the website.',
            'Run the probe over HTTP once for the web values (memory_limit, max_execution_time, .htaccess php_value, .user.ini).');
    }
    if ($ext['target_is_guess']) {
        $add('high', 'php-branch', 'LIVE PHP ' . $php['branch'] . ' has no setup image; the plan uses PHP ' . $ext['target_branch'] . '.',
            'Test the application on PHP ' . $ext['target_branch'] . ' (make composer cmd=\'check-platform-reqs\').');
    }
    if (sp_is_ps($platform)) {
        $line = sp_ps_line($platform['version']);
        if ($line === null) {
            $add('medium', 'ps-line', 'PrestaShop ' . $platform['version'] . ' is not in setup\'s compatibility table.',
                'SETUP_COMPATIBILITY_CHECK=off (or check the version string).');
        } else {
            if (!in_array($ext['target_branch'], $data['compatibility'][$line]['php'], true)) {
                $add('high', 'php-compat', 'PHP ' . $ext['target_branch'] . ' is outside setup\'s list for PrestaShop ' . $line . ' ('
                    . implode(', ', $data['compatibility'][$line]['php']) . '): make check stops with an error.',
                    'Either SETUP_COMPATIBILITY_CHECK=off to mirror LIVE, or PHP_VERSION=' . end($data['compatibility'][$line]['php']) . ' and test.');
            }
            if ($database['available']) {
                $list = $data['compatibility'][$line][$database['engine']];
                if (!in_array($database['branch'], $list, true)) {
                    $add('low', 'db-compat', $database['flavour'] . ' ' . $database['branch'] . ' is outside the versions listed for PrestaShop ' . $line
                        . ' (' . implode(', ', $list) . '): make check warns.', 'Keep the LIVE version to mirror LIVE; the warning is informational.');
                }
            }
        }
        if (isset($platform['settings_inc_version'])) {
            $add('high', 'ps-settings-leftover', 'config/settings.inc.php still says _PS_VERSION_ ' . $platform['settings_inc_version']
                . ': setup reads that file first and would report "PS_VERSION does not match the shop code".',
                'Remove the 1.6 leftover config/settings.inc.php from the shop repository (after checking nothing includes it).');
        }
        if (is_array($ps)) {
            foreach ((array) $ps['required_keys'] as $key) {
                $presence = sp_get((array) $ps['key_presence'], $key);
                if (!is_array($presence) || !$presence['present']) {
                    $add('high', 'ps-key-missing', 'LIVE ' . $ps['config_file'] . ' has no value for ' . $key . '.',
                        'Stage/prod need it as a Deployment variable; find the real value before migrating.');
                }
            }
            if ($ps['config_format'] === 'parameters.yml') {
                $add('high', 'ps-parameters-yml', 'The shop uses app/config/parameters.yml; setup handles parameters.php only.',
                    'Convert parameters.yml to parameters.php (PrestaShop 1.7.4+ format) in the shop before running setup.');
            }
            if (sp_get($ps['debug'], 'mode_dev') === true) {
                $add('high', 'ps-debug-live', 'Debug mode (_PS_MODE_DEV_) is ON on LIVE (' . $ps['debug']['source'] . ').',
                    'Turn it off on LIVE; in setup use PS_DEBUG_MODE (off on prod).');
            }
            $shops = (array) sp_get($ps, 'shops', array());
            $shop_ids = array();
            foreach ($shops as $shop) {
                $shop_ids[$shop['id_shop']] = true;
                if ((string) $shop['main'] === '1' && (string) $shop['physical_uri'] !== '/' && (string) $shop['physical_uri'] !== '') {
                    $add('medium', 'ps-subfolder', 'Shop ' . $shop['id_shop'] . ' lives in the subfolder ' . $shop['physical_uri'] . ' on LIVE; setup serves the shop at /.',
                        'After import the default shop gets physical_uri "/"; regenerate .htaccess (RewriteBase) in the shop and check hard-coded paths.');
                }
            }
            if (count($shop_ids) > 1 || count($shops) > 1) {
                $add('medium', 'ps-multistore', count($shop_ids) . ' shop(s) with ' . count($shops) . ' URL(s): only the default shop\'s main URL follows DOMAIN after import.',
                    'Add the after-import SQL drafted below (database/after-import/<env>/) so other shops answer under the environment DOMAIN.');
            }
            $configuration = (array) sp_get($ps, 'configuration', array());
            if ((string) sp_get($configuration, 'PS_MEDIA_SERVERS', '0') === '1') {
                $add('medium', 'ps-media-servers', 'Media servers (CDN) are on: copies would load assets from LIVE hosts.', 'Use the after-import SQL drafted below.');
            }
            $cache = (array) $ps['cache'];
            if ($cache['enabled'] && $cache['system'] !== null) {
                $system = strtolower((string) $cache['system']);
                if (strpos($system, 'apc') !== false && version_compare($ext['target_branch'], '8.0', '<')) {
                    $add('medium', 'ps-cache-apcu', 'PrestaShop cache ' . $cache['system'] . ' needs APCu; setup images below PHP 8.0 have no APCu.',
                        'Add `pecl install apcu` to the project Dockerfile or switch the cache.');
                }
                if (strpos($system, 'xcache') !== false) {
                    $add('medium', 'ps-cache-xcache', 'PrestaShop cache CacheXcache: XCache does not exist in setup images.', 'Switch the cache system to CacheFs or Memcached.');
                }
            }
            if (sp_get($ps, 'encoded_files')) {
                $add('high', 'encoded-modules', 'Encoded module files: ' . implode(', ', $ps['encoded_files']) . '.',
                    'The project Dockerfile must install the matching loader (ionCube / SourceGuardian) for PHP ' . $ext['target_branch'] . '.');
            }
            if ((string) sp_get($configuration, 'PS_SSL_ENABLED', '1') === '0') {
                $add('low', 'ps-ssl-off', 'PS_SSL_ENABLED is 0 on LIVE.', 'Local copies get PS_SSL=on by default; set PS_SSL=off in env if the shop must run without SSL.');
            }
            if (sp_get($ps, 'overrides') && $ps['overrides']['count'] > 0) {
                $add('info', 'ps-overrides', $ps['overrides']['count'] . ' override file(s) under override/.',
                    'They travel with the shop repository; after an import clear var/cache (or cache/class_index.php on 1.6).');
            }
        }
    }
    if ($database['configured'] && !$database['available']) {
        $add('high', 'db-unreachable', 'The database was not reachable: ' . sp_get($database, 'error', 'unknown error') . '.',
            'Check the credentials file, or run the probe where the database is reachable; the DB part of the plan is missing.');
    } elseif (!$database['configured']) {
        $add('medium', 'db-config', 'No database configuration found.', 'Set PROBE_APP_CONFIG (or --config) to the application\'s DB config file.');
    }
    if ($database['available']) {
        $v = $database['variables'];
        if ($database['engine'] === 'mariadb') {
            $add('info', 'db-mariadb', 'LIVE runs MariaDB: use DATABASE_ENGINE=mariadb. A MariaDB dump does not import cleanly into MySQL 8 (and back).',
                'Keep the engine; switch engines only with a deliberate dump conversion.');
        }
        if ($database['engine'] === 'mysql' && version_compare((string) $database['branch'], '8.0', '<')) {
            $add('medium', 'db-mysql5', 'LIVE runs MySQL ' . $database['branch'] . ': mirror it with MYSQL_VERSION=' . $database['version_number']
                . '; moving to 8.x changes sql_mode, the default collation and reserved words.', 'Upgrade only as a separate, tested step.');
        }
        if (isset($v['sql_mode']) && strpos((string) $v['sql_mode'], 'ONLY_FULL_GROUP_BY') === false && $database['engine'] === 'mysql') {
            $add('medium', 'db-sql-mode', 'LIVE sql_mode lacks ONLY_FULL_GROUP_BY (' . $v['sql_mode'] . '); the MySQL image enables it and queries may fail.',
                'Ship the sql_mode line of the database drop-in below.');
        }
        if (isset($v['character_set_server']) && stripos((string) $v['character_set_server'], 'utf8mb4') === false) {
            $add('low', 'db-charset', 'character_set_server is ' . $v['character_set_server'] . ' on LIVE.', 'Ship the charset lines of the database drop-in.');
        }
        if (count($database['tables']['collations']) > 1) {
            $add('low', 'db-mixed-collations', 'Tables use ' . count($database['tables']['collations']) . ' collations: '
                . implode(', ', array_keys($database['tables']['collations'])) . '.', 'Joins across collations can fail; keep as is unless you convert deliberately.');
        }
        if (isset($v['lower_case_table_names']) && (string) $v['lower_case_table_names'] !== '0') {
            $add('medium', 'db-lower-case', 'lower_case_table_names=' . $v['lower_case_table_names'] . ' on LIVE; Docker MySQL defaults to 0 (case-sensitive).',
                'Put it in the database drop-in before the first DB start (MySQL 8 refuses it later).');
        } elseif ($database['tables']['uppercase_names']) {
            $add('low', 'db-uppercase', count($database['tables']['uppercase_names']) . ' table name(s) contain capitals, e.g. ' . $database['tables']['uppercase_names'][0] . '.',
                'Linux MySQL is case-sensitive: code must use the exact names.');
        }
        if (isset($database['tables']['engines']['MyISAM'])) {
            $add('info', 'db-myisam', $database['tables']['engines']['MyISAM'] . ' MyISAM table(s).', 'They import as MyISAM; nothing to do unless you convert them.');
        }
        $definers = array();
        $events = 0;
        foreach ($database['stored_objects'] as $object) {
            $definers[$object['definer']] = true;
            if ($object['kind'] === 'event') {
                $events++;
            }
        }
        if ($database['stored_objects']) {
            $add('medium', 'db-definers', count($database['stored_objects']) . ' view/trigger/routine/event object(s) with DEFINER '
                . implode(', ', array_keys($definers)) . '.',
                'setup imports as root, so creation works, but SQL SECURITY DEFINER objects fail when the definer user does not exist in the container: '
                . 'strip DEFINER from the dump or recreate them in after-import SQL. The schema snapshot holds tables only.');
        }
        if ($events) {
            $add('medium', 'db-events', $events . ' event(s) would run on every copy.', 'Disable them in local/test after-import SQL (draft below).');
        }
        if ($database['tables']['total_bytes'] > 5368709120) {
            $add('medium', 'db-size', 'The database holds ' . sp_human($database['tables']['total_bytes']) . '.',
                'Mark large volatile tables "skip" in database/table-registry.txt and use make db-load set=dev for daily work.');
        }
        if (!empty($database['other_visible_schemas'])) {
            $add('info', 'db-other-schemas', 'The application user sees other schemas: ' . implode(', ', $database['other_visible_schemas']) . '.',
                'Add them to DATABASE_EXTRA_SCHEMAS only if the application uses them.');
        }
    }
    foreach ($ext['missing_in_core'] as $missing) {
        $level = $missing['needed'] === 'check whether the code uses it' ? 'low' : 'high';
        $add($level, 'ext-' . $missing['extension'], 'Extension ' . $missing['extension'] . ' is loaded on LIVE but not in the setup PHP ' . $ext['target_branch']
            . ' image (' . $missing['needed'] . ').', 'Project Dockerfile: ' . $missing['method'] . ($missing['apt'] !== '' ? ' (apt: ' . $missing['apt'] . ')' : '') . '.');
    }
    foreach ($ext['required_but_missing'] as $missing) {
        $add('medium', 'ext-required-' . $missing, 'Extension ' . $missing . ' is required (composer/PrestaShop) but neither LIVE nor the setup image loads it.',
            'Add it to the project Dockerfile if the code path is used.');
    }
    if ($ext['target_branch'] === '5.6') {
        $add('high', 'php56-core', 'The setup PHP 5.6 image installs no extensions beyond the official image (gd, intl, pdo_mysql, zip, soap, bcmath, exif, opcache are missing).',
            'Install the listed extensions in the project Dockerfile.');
    }
    $htaccess = $web['htaccess'];
    if (sp_get($htaccess, 'present')) {
        foreach ($htaccess['modules_not_in_setup'] as $finding) {
            if ($finding['module'] === 'php') {
                if (!$finding['guarded']) {
                    $add('high', 'htaccess-php-value', '.htaccess sets PHP values (' . $finding['directive'] . ', line ' . $finding['first_line']
                        . ') outside <IfModule>: setup runs PHP-FPM and Apache answers 500 to these lines.',
                        'Move the values to config/php/*.ini and remove the lines (or wrap them in <IfModule mod_php.c>) in the shop repository.');
                }
                continue;
            }
            $add($finding['guarded'] ? 'low' : 'high', 'htaccess-' . $finding['module'],
                '.htaccess uses ' . $finding['directive'] . ' (mod_' . $finding['module'] . ', ' . $finding['count'] . ' line(s), first ' . $finding['first_line'] . ')'
                . ($finding['guarded'] ? ' inside <IfModule>' : ' without <IfModule>: Apache in setup does not load that module and answers 500'),
                'Load the module in config/apache/*.conf (LoadModule ' . $finding['module'] . '_module modules/mod_' . $finding['module'] . '.so) or drop the lines.');
        }
        if ($htaccess['php_handlers']) {
            $add('medium', 'htaccess-handler', '.htaccess selects a hosting PHP handler (' . $htaccess['php_handlers'][0]['text'] . ').',
                'Remove or guard it: setup passes PHP to php-fpm itself and such lines can make Apache offer .php files for download.');
        }
    }
    if ($web['type'] === 'nginx') {
        $add('medium', 'nginx-live', 'LIVE runs nginx: the .htaccess file is not used there, but setup\'s Apache reads it.',
            'Make sure .htaccess is current (PrestaShop: regenerate it from the back office); port custom nginx rules to config/apache.');
    }
    if ($web['type'] === 'litespeed') {
        $add('low', 'litespeed', 'LIVE runs LiteSpeed: LiteSpeed cache rules (<IfModule LiteSpeed>) do nothing under setup\'s Apache.', 'Nothing to do unless the site depends on LSCache.');
    }
    foreach ($web['user_ini'] as $user_ini) {
        $add('medium', 'user-ini', $user_ini['file'] . ' sets ' . implode(', ', array_keys($user_ini['values'])) . ' on LIVE.',
            'Carry the needed values into config/php/*.ini (memory/time/upload limits into the PHP_* env settings).');
    }
    if (sp_is_on($php['ini']['display_errors']) && $r['_meta']['mode'] === 'web') {
        $add('medium', 'display-errors', 'display_errors is On on LIVE.', 'Leave it off in prod; use config/php/local/*.ini for local display.');
    }
    if ((string) $php['ini']['auto_prepend_file'] !== '') {
        $add('low', 'auto-prepend', 'auto_prepend_file is set on LIVE (' . $php['ini']['auto_prepend_file'] . '), often a security scanner of the hosting.',
            'Do not copy it unless the application depends on it.');
    }
    if ($r['_meta']['mode'] === 'web' && $web['https'] === 'off') {
        $add('medium', 'http-plain', 'The probe was called over plain HTTP: the token travelled unencrypted.', 'Delete the probe now and use HTTPS next time.');
    }
    foreach ((array) sp_get($r['cron'], 'entries', array()) as $entry) {
        if ($entry['relevant'] && $entry['setup']['kind'] === 'url') {
            $add('medium', 'cron-url', 'A crontab job calls a URL (' . $entry['command'] . ').',
                'In setup it must not call LIVE: rewrite it as a PHP CLI command in config/cron/crontab (SERVICES=cron).');
            break;
        }
    }
    if (!sp_get($r['cron'], 'available', false)) {
        $add('info', 'cron-unknown', 'The crontab could not be read (' . sp_get($r['cron'], 'reason', '') . ').', 'Copy the cron jobs from the hosting panel.');
    }
    $order = array('high' => 0, 'medium' => 1, 'low' => 2, 'info' => 3);
    usort($risks, function ($a, $b) use ($order) {
        return $order[$a['level']] - $order[$b['level']];
    });
    return $risks;
}

// ------------------------------------------------------------------------------------------------
// Rendering: env, md, json
// ------------------------------------------------------------------------------------------------

function sp_render($result, $format)
{
    if ($format === 'json') {
        return sp_json_encode($result) . "\n";
    }
    if ($format === 'env') {
        return sp_render_env($result);
    }
    return sp_render_md($result);
}

function sp_env_value($value)
{
    $value = (string) $value;
    if ($value === '' || preg_match('/^[A-Za-z0-9_.:\/@+-]+$/', $value)) {
        return $value;
    }
    return '"' . str_replace('"', '\\"', $value) . '"';
}

function sp_env_line($entry)
{
    $line = ($entry['commented'] ? '# ' : '') . $entry['key'] . '=' . sp_env_value($entry['value']);
    $comment = array();
    if ($entry['kind'] === 'guess') {
        $comment[] = 'GUESS';
    } elseif ($entry['kind'] === 'default') {
        $comment[] = 'setup default';
    } elseif ($entry['kind'] === 'hint') {
        $comment[] = 'HINT';
    }
    if ($entry['live'] !== null && $entry['live'] !== '') {
        $comment[] = 'LIVE: ' . $entry['live'] . ($entry['source'] ? ' [' . $entry['source'] . ']' : '');
    } elseif ($entry['source']) {
        $comment[] = '[' . $entry['source'] . ']';
    }
    if ($entry['note']) {
        $comment[] = $entry['note'];
    }
    return str_pad($line, 44) . ' # ' . implode('; ', $comment);
}

function sp_ini_line($entry)
{
    $line = $entry['key'] . ' = ' . $entry['value'];
    $comment = 'LIVE: ' . $entry['live'] . ($entry['note'] ? '; ' . $entry['note'] : '');
    return str_pad($line, 44) . ' ; ' . $comment;
}

function sp_render_env($r)
{
    $plan = $r['setup'];
    $out = array();
    $out[] = '# setup LIVE probe ' . SP_VERSION . ' - ' . $r['_meta']['collected_at'] . ' - root ' . $r['_meta']['root'];
    $out[] = '# ' . $r['_meta']['reminder'];
    $out[] = '# Review every line. GUESS = estimate, HINT = decide yourself; LIVE: value [source]. Secrets are never printed.';
    $out[] = '';
    $out[] = '# ---- env/common.env (committed, no secrets) ----';
    foreach ($plan['common_env'] as $entry) {
        $out[] = sp_env_line($entry);
    }
    $out[] = '';
    $out[] = '# ---- env/prod.env.example (committed without secrets; env/prod.env on the server) ----';
    foreach ($plan['prod_env'] as $entry) {
        $out[] = sp_env_line($entry);
    }
    if ($plan['local_env']) {
        $out[] = '';
        $out[] = '# ---- env/local.env (private, never committed): values copied by hand from LIVE ----';
        foreach ($plan['local_env'] as $entry) {
            $out[] = sp_env_line($entry);
        }
    }
    $out[] = '';
    $out[] = '# ---- Secrets: names only. Prod/stage: Bitbucket Deployment variables; local: env/local.env ----';
    foreach ($plan['secrets'] as $secret) {
        $out[] = '#   ' . str_pad($secret['name'], 22) . ' ' . $secret['where'] . ' <- ' . $secret['from'];
    }
    $out[] = '';
    $out[] = '# The blocks below are other files: copy their lines without the leading "# ".';
    $out[] = '# ---- config/php/20-live.ini (setup has no env setting for these; omit lines you do not need) ----';
    $extensions = array();
    foreach ($plan['php_ini'] as $entry) {
        if ($entry['key'] === 'extension') {
            $extensions[] = '# extension=' . $entry['value'] . '   ; ' . $entry['source'];
        } else {
            $out[] = '# ' . sp_ini_line($entry);
        }
    }
    if (count($extensions) === count($plan['php_ini'])) {
        $out[] = '# nothing differs from setup CORE';
    }
    if ($extensions) {
        $out[] = '# ---- config/php/cache.ini ----';
        $out = array_merge($out, $extensions);
    }
    $out[] = '';
    $out[] = '# ---- ' . $plan['database_cnf_directory'] . '/20-live.cnf ----';
    if ($plan['database_cnf']) {
        $out[] = '# [mysqld]';
        foreach ($plan['database_cnf'] as $entry) {
            $out[] = '# ' . sp_ini_line($entry);
        }
    } else {
        $out[] = '# ' . ($r['database']['available'] ? 'nothing differs from the image defaults' : 'database not reachable: unknown');
    }
    $out[] = '';
    $out[] = '# ---- app/Dockerfile (extensions the setup PHP ' . $r['extensions']['target_branch'] . ' image lacks) ----';
    if ($plan['dockerfile']) {
        foreach (explode("\n", sp_dockerfile_text($plan['dockerfile'])) as $line) {
            $out[] = '# ' . $line;
        }
    } else {
        $out[] = '# none needed';
    }
    $out[] = '';
    $out[] = '# ' . $r['_meta']['reminder'];
    return implode("\n", $out) . "\n";
}

function sp_dockerfile_text($items)
{
    $apt = array();
    $core = array();
    $pecl = array();
    $manual = array();
    foreach ($items as $item) {
        if ($item['apt'] !== '' && $item['method'] !== 'manual') {
            foreach (preg_split('/\s+/', preg_replace('/\(.*\)/', '', $item['apt'])) as $package) {
                if ($package !== '') {
                    $apt[$package] = true;
                }
            }
        }
        if ($item['method'] === 'docker-php-ext-install') {
            $core[] = $item['extension'];
        } elseif ($item['method'] === 'pecl' || $item['method'] === 'unknown') {
            $pecl[] = $item['extension'];
        } else {
            $manual[] = $item['extension'] . ': ' . $item['note'];
        }
    }
    $lines = array('ARG SETUP_PHP_IMAGE', 'FROM ${SETUP_PHP_IMAGE}');
    if ($apt) {
        $lines[] = 'RUN apt-get update \\';
        $lines[] = '    && apt-get install -y --no-install-recommends ' . implode(' ', array_keys($apt)) . ' \\';
        $lines[] = '    && rm -rf /var/lib/apt/lists/*';
    }
    if ($core) {
        $lines[] = 'RUN docker-php-ext-install ' . implode(' ', $core);
    }
    foreach ($pecl as $name) {
        $lines[] = 'RUN pecl install ' . $name . ' && docker-php-ext-enable ' . $name;
    }
    foreach ($manual as $note) {
        $lines[] = '# ' . $note;
    }
    return implode("\n", $lines);
}

function sp_md_cell($value)
{
    if (is_bool($value)) {
        $value = $value ? 'yes' : 'no';
    }
    if ($value === null) {
        $value = '-';
    }
    if (is_array($value)) {
        $value = implode(', ', array_map('strval', $value));
    }
    return str_replace(array('|', "\n", "\r"), array('\\|', ' ', ''), (string) $value);
}

function sp_md_table($headers, $rows)
{
    $out = '| ' . implode(' | ', $headers) . " |\n|" . str_repeat(' --- |', count($headers)) . "\n";
    foreach ($rows as $row) {
        $out .= '| ' . implode(' | ', array_map('sp_md_cell', $row)) . " |\n";
    }
    return $out;
}

function sp_render_md($r)
{
    $plan = $r['setup'];
    $meta = $r['_meta'];
    $platform = $r['application'];
    $php = $r['php'];
    $database = $r['database'];
    $web = $r['webserver'];
    $ps = $r['prestashop'];
    $host = $r['host'];
    $ext = $r['extensions'];
    $title = sp_get($web, 'http_host') !== null ? $web['http_host'] : basename($meta['root']);
    $md = '# setup LIVE probe: ' . $title . "\n\n";
    $md .= '> **' . $meta['reminder'] . "**\n\n";
    $md .= 'Probe ' . SP_VERSION . ' (setup reference ' . $meta['setup_reference'] . '), collected ' . $meta['collected_at']
        . ($meta['expires_at'] ? ', probe expires ' . $meta['expires_at'] : '') . ', mode ' . $meta['mode'] . ', root `' . $meta['root']
        . '` (' . $meta['root_source'] . ").\n";
    $md .= "Read-only: SELECT/SHOW only, no PrestaShop boot, no personal data, no secret values.\n\n";

    $md .= "## 1. Summary\n\n";
    $md .= '- Application: **' . $platform['name'] . ($platform['version'] ? ' ' . $platform['version'] : '') . '**'
        . ($platform['source'] ? ' (`' . $platform['source'] . '`)' : '') . "\n";
    $md .= '- PHP: **' . $php['version'] . '** (' . $php['sapi'] . ') -> `PHP_VERSION=' . $ext['target_branch'] . '`'
        . ($ext['target_is_guess'] ? ' (nearest setup image)' : '') . "\n";
    if ($database['available']) {
        $md .= '- Database: **' . $database['flavour'] . ' ' . $database['version'] . '** -> `DATABASE_ENGINE=' . $database['engine'] . '`, '
            . $database['tables']['count'] . ' tables, ' . sp_human($database['tables']['total_bytes']) . "\n";
    } else {
        $md .= '- Database: **not reachable** (' . sp_md_cell(sp_get($database, 'error', 'no configuration')) . ")\n";
    }
    $md .= '- Web server: ' . $web['type'] . ($web['server_software'] ? ' (`' . $web['server_software'] . '`)' : '') . ', PHP handler ' . $web['php_handler']
        . ', HTTPS ' . $web['https'] . ($web['panel'] ? ', ' . implode(', ', $web['panel']) : '') . "\n";
    $counts = array('high' => 0, 'medium' => 0, 'low' => 0, 'info' => 0);
    foreach ($r['risks'] as $risk) {
        $counts[$risk['level']]++;
    }
    $md .= '- Risks: ' . $counts['high'] . ' high, ' . $counts['medium'] . ' medium, ' . $counts['low'] . ' low, ' . $counts['info'] . " info (section 4)\n\n";

    $md .= "## 2. Setup settings\n\nSame content as `?format=env`. GUESS = estimate, HINT = decide yourself.\n\n```dotenv\n" . sp_render_env($r) . "```\n\n";
    if ($plan['php_ini']) {
        $md .= "### config/php/20-live.ini\n\n```ini\n";
        foreach ($plan['php_ini'] as $entry) {
            if ($entry['key'] !== 'extension') {
                $md .= sp_ini_line($entry) . "\n";
            }
        }
        $md .= "```\n\n";
        $extensions = array();
        foreach ($plan['php_ini'] as $entry) {
            if ($entry['key'] === 'extension') {
                $extensions[] = 'extension=' . $entry['value'];
            }
        }
        if ($extensions) {
            $md .= "### config/php/cache.ini\n\n```ini\n" . implode("\n", $extensions) . "\n```\n\n";
        }
    }
    if ($plan['database_cnf']) {
        $md .= '### ' . $plan['database_cnf_directory'] . "/20-live.cnf\n\n```ini\n[mysqld]\n";
        foreach ($plan['database_cnf'] as $entry) {
            $md .= sp_ini_line($entry) . "\n";
        }
        $md .= "```\n\n";
    }
    if ($plan['dockerfile']) {
        $md .= "### app/Dockerfile (+ app/.dockerignore with `**` and `!Dockerfile`)\n\n```dockerfile\n" . sp_dockerfile_text($plan['dockerfile']) . "\n```\n\n";
        $rows = array();
        foreach ($plan['dockerfile'] as $item) {
            $rows[] = array($item['extension'], $item['needed'], $item['method'], $item['apt'], sp_get($item, 'note'));
        }
        $md .= sp_md_table(array('Extension', 'Why', 'Method', 'apt packages', 'Note'), $rows) . "\n";
    }
    foreach ($plan['after_import'] as $item) {
        $md .= '### ' . $item['file'] . "\n\n" . $item['why'] . ".\n\n```sql\n" . $item['sql'] . "```\n\n";
    }
    if ($plan['crontab']) {
        $md .= "### config/cron/crontab (SERVICES=cron)\n\n```cron\n" . implode("\n", $plan['crontab']) . "\n\n```\n\n";
    }
    if ($plan['registry_draft']) {
        $md .= "### database/table-registry.txt draft (tables setup's PrestaShop registry does not know)\n\n"
            . "Suggestions only: `make schema-resolve` asks about each table; keep volatile tables `skip`.\n\n```text\n";
        foreach ($plan['registry_draft'] as $line) {
            $md .= $line['line'] . ($line['bytes'] !== null ? '   # ' . sp_human($line['bytes']) : '') . "\n";
        }
        $md .= "```\n\n";
    }

    $md .= "## 3. Details\n\n### PHP\n\n";
    $data = sp_setup_data();
    $rows = array();
    foreach (array_merge(array_keys($data['env_ini']), array_keys(sp_core_ini())) as $key) {
        $setup = isset($data['env_ini'][$key]) ? 'env ' . $data['env_ini'][$key] : sp_core_ini_value($key, $ext['target_branch']);
        $rows[] = array($key, $php['ini'][$key], $setup);
    }
    foreach (array('display_errors', 'log_errors', 'error_reporting', 'open_basedir', 'auto_prepend_file', 'session.save_path', 'opcache.jit', 'xdebug.mode') as $key) {
        $rows[] = array($key, $php['ini'][$key], '');
    }
    $md .= sp_md_table(array('Setting', 'LIVE', 'setup'), $rows) . "\n";
    $md .= 'Source: ' . $php['ini_source'] . '. Loaded ini: `' . sp_md_cell($php['loaded_ini_file']) . '`; scanned: ' . sp_md_cell($php['scanned_ini_files']) . ".\n\n";
    $md .= 'disable_functions: ' . ($php['disable_functions'] ? '`' . implode(', ', $php['disable_functions']) . '`' : 'none') . "\n\n";

    $md .= "### Extensions\n\n";
    $md .= '- LIVE: ' . implode(', ', $php['extensions']) . "\n";
    $md .= '- setup PHP ' . $ext['target_branch'] . ' CORE image: ' . implode(', ', $ext['core']) . "\n";
    $md .= '- Compiled in setup, enable with config/php/cache.ini: redis, memcached' . ($ext['available_not_enabled'] ? ' (LIVE loads ' . implode(', ', $ext['available_not_enabled']) . ')' : '') . "\n";
    $md .= '- Host-specific on LIVE (not needed): ' . ($ext['host_specific'] ? implode(', ', $ext['host_specific']) : 'none') . "\n";
    $md .= '- Required by the application: ' . ($ext['required'] ? implode(', ', $ext['required']) : 'none known') . "\n";
    $md .= '- In setup but not on LIVE (harmless): ' . ($ext['core_but_not_live'] ? implode(', ', $ext['core_but_not_live']) : 'none') . "\n\n";

    $md .= "### Web server\n\n";
    $rows = array(
        array('Type', $web['type']), array('SERVER_SOFTWARE', $web['server_software']), array('PHP handler', $web['php_handler']),
        array('Apache modules', $web['apache_modules'] === null ? 'not readable (not mod_php)' : $web['apache_modules']),
        array('HTTPS', $web['https']), array('Proxy/CDN hints', $web['proxy_hints'] ? $web['proxy_hints'] : 'none'),
        array('Document root', $web['document_root']), array('Application vs document root', $web['document_root_relation']),
        array('Hosting panel', $web['panel'] ? $web['panel'] : 'unknown'),
    );
    $htaccess = $web['htaccess'];
    if (sp_get($htaccess, 'present')) {
        $rows[] = array('.htaccess', $htaccess['bytes'] . ' bytes, ' . $htaccess['lines'] . ' lines' . ($htaccess['prestashop_section'] ? ', PrestaShop section' : '')
            . ($htaccess['rewrite_base'] ? ', RewriteBase ' . $htaccess['rewrite_base'] : ''));
        foreach ($htaccess['php_values'] as $value) {
            $rows[] = array('.htaccess line ' . $value['line'], $value['directive'] . ' ' . $value['name'] . ' ' . $value['value'] . ($value['guarded'] ? ' (inside IfModule)' : ''));
        }
    } else {
        $rows[] = array('.htaccess', 'none in the application root');
    }
    foreach ($web['user_ini'] as $user_ini) {
        $pairs = array();
        foreach ($user_ini['values'] as $key => $value) {
            $pairs[] = $key . '=' . $value;
        }
        $rows[] = array($user_ini['file'], $pairs);
    }
    $md .= sp_md_table(array('Item', 'LIVE'), $rows) . "\n";

    $md .= "### Database\n\n";
    if ($database['available']) {
        $v = $database['variables'];
        $rows = array(array('Engine / version', $database['flavour'] . ' ' . $database['version'] . ' (' . $database['version_comment'] . ')'),
            array('Config source', $database['config_source']), array('Host / port', $database['host'] . ($database['port'] !== '' ? ':' . $database['port'] : '')),
            array('Database / prefix', $database['name'] . ' / ' . ($database['prefix'] !== '' ? $database['prefix'] : '-')),
            array('Schema charset / collation', $database['schema_charset'] . ' / ' . $database['schema_collation']),
            array('Tables / views', $database['tables']['count'] . ' / ' . $database['tables']['views']),
            array('Data / index', sp_human($database['tables']['data_bytes']) . ' / ' . sp_human($database['tables']['index_bytes'])),
            array('Engines', sp_md_pairs($database['tables']['engines'])), array('Collations', sp_md_pairs($database['tables']['collations'])));
        foreach (sp_db_variable_names() as $name) {
            if (isset($v[$name]) && !in_array($name, array('version', 'version_comment'), true)) {
                $value = $v[$name];
                if (in_array($name, array('innodb_buffer_pool_size', 'max_allowed_packet', 'tmp_table_size', 'max_heap_table_size', 'innodb_log_file_size',
                                           'innodb_redo_log_capacity', 'query_cache_size', 'sort_buffer_size', 'join_buffer_size'), true)) {
                    $value .= ' (' . sp_human((int) $value) . ')';
                }
                $rows[] = array($name, $value);
            }
        }
        $md .= sp_md_table(array('Item', 'LIVE'), $rows) . "\n";
        $skip = array_flip(sp_ps_registry_skip());
        $known = array_flip(sp_ps_registry_known());
        $rows = array();
        foreach ($database['tables']['largest'] as $table) {
            $short = $database['prefix'] !== '' && strpos($table['name'], $database['prefix']) === 0 ? substr($table['name'], strlen($database['prefix'])) : null;
            $registry = !sp_is_ps($platform) ? 'classify (generic)'
                : ($short !== null && isset($skip[$short]) ? 'skip (structure only)' : ($short !== null && isset($known[$short]) ? 'import' : 'not in setup registry'));
            $rows[] = array($table['name'], $table['approx_rows'], sp_human($table['bytes']), $table['engine'], $registry);
        }
        $md .= "Largest tables (approximate rows from information_schema):\n\n" . sp_md_table(array('Table', 'Rows ~', 'Size', 'Engine', 'setup registry'), $rows) . "\n";
        if ($database['stored_objects']) {
            $rows = array();
            foreach ($database['stored_objects'] as $object) {
                $rows[] = array($object['kind'], $object['name'], $object['definer'], $object['security']);
            }
            $md .= "Views, triggers, routines, events:\n\n" . sp_md_table(array('Kind', 'Name', 'Definer', 'Security'), $rows) . "\n";
        }
        if ($database['other_visible_schemas']) {
            $md .= 'Other schemas visible to the application user: ' . implode(', ', $database['other_visible_schemas']) . "\n\n";
        }
    } else {
        $md .= 'Not reachable: ' . sp_md_cell(sp_get($database, 'error', 'no configuration')) . ' (config source: ' . sp_md_cell($database['config_source']) . ")\n\n";
    }

    if (is_array($ps)) {
        $md .= "### PrestaShop\n\n";
        $rows = array(array('Version', $ps['version'] . ' (' . $ps['version_source'] . ')'), array('Config file', $ps['config_file']));
        $keys = array();
        foreach ($ps['required_keys'] as $key) {
            $presence = sp_get($ps['key_presence'], $key);
            $keys[] = $key . (is_array($presence) ? ' (' . $presence['file_key'] . '): ' . ($presence['present'] ? 'present' : 'MISSING') : ': MISSING');
        }
        $rows[] = array('Keys (presence only)', $keys);
        $rows[] = array('Keys go to', 'env/local.env (private) locally, Bitbucket Deployment variables on stage/prod - copy values from LIVE ' . $ps['config_file']);
        $rows[] = array('Debug mode', $ps['debug']['mode_dev'] === null ? 'unknown' : ($ps['debug']['mode_dev'] ? 'ON' : 'off') . ' (' . $ps['debug']['source'] . ')');
        $rows[] = array('Cache', ($ps['cache']['enabled'] ? 'on' : 'off') . ($ps['cache']['system'] ? ', ' . $ps['cache']['system'] : ''));
        foreach ($ps['parameters'] as $key => $value) {
            if (!in_array($key, array('_PS_CACHE_ENABLED_', '_PS_CACHING_SYSTEM_', 'ps_caching', 'ps_cache_enable'), true)) {
                $rows[] = array($key, is_bool($value) ? ($value ? 'true' : 'false') : $value);
            }
        }
        $rows[] = array('Themes (directories)', $ps['theme_directories']);
        $rows[] = array('Admin directory', $ps['admin_directory']);
        $rows[] = array('Overrides', $ps['overrides']['count'] . ' file(s)');
        $rows[] = array('Module directories', $ps['module_directories']);
        $rows[] = array('vendor/ present', $ps['composer_vendor']);
        if (isset($ps['configuration'])) {
            foreach ($ps['configuration'] as $name => $value) {
                $rows[] = array($name, $value);
            }
            if ($ps['configuration_per_shop']) {
                $rows[] = array('Per-shop overrides', array_keys($ps['configuration_per_shop']));
            }
            $rows[] = array('Languages', sp_md_langs($ps['languages']));
            $rows[] = array('Cron modules', $ps['cron_modules'] ? $ps['cron_modules'] : 'none');
            if (isset($ps['cronjobs_tasks'])) {
                $rows[] = array('cronjobs tasks', $ps['cronjobs_tasks']);
            }
            if (isset($ps['memcached_servers'])) {
                $servers = array();
                foreach ($ps['memcached_servers'] as $server) {
                    $servers[] = $server['ip'] . ':' . $server['port'];
                }
                $rows[] = array('memcached_servers', $servers);
            }
        } else {
            $rows[] = array('Database details', $ps['database']);
        }
        $md .= sp_md_table(array('Item', 'LIVE'), $rows) . "\n";
        if (!empty($ps['shops'])) {
            $rows = array();
            foreach ($ps['shops'] as $shop) {
                $rows[] = array($shop['id_shop'], $shop['name'], $shop['active'], $shop['theme'], $shop['domain'], $shop['domain_ssl'],
                                $shop['physical_uri'] . $shop['virtual_uri'], $shop['main']);
            }
            $md .= "Shops and URLs:\n\n" . sp_md_table(array('Shop', 'Name', 'Active', 'Theme', 'Domain', 'SSL domain', 'URI', 'Main'), $rows) . "\n";
        }
        if (!empty($ps['modules'])) {
            $list = array();
            foreach ($ps['modules'] as $module) {
                $list[] = $module['name'] . ' ' . $module['version'] . ((string) $module['active'] === '1' ? '' : ' (inactive)');
            }
            $md .= 'Installed modules (' . count($list) . "): " . implode(', ', $list) . "\n\n";
        }
        if ($ps['overrides']['files']) {
            $md .= 'Override files: ' . implode(', ', $ps['overrides']['files']) . "\n\n";
        }
    }
    if ($r['composer']['present']) {
        $composer = $r['composer'];
        $md .= "### Composer\n\n" . sp_md_table(array('Item', 'Value'), array(
            array('name', $composer['name']), array('require php', $composer['php']), array('config.platform.php', $composer['platform_php']),
            array('ext-* (composer.json)', $composer['extensions']), array('ext-* (composer.lock packages)', $composer['lock_extensions']),
            array('frameworks', sp_md_pairs($composer['frameworks'])))) . "\n";
    }
    if (!sp_is_ps($platform) && $platform['config']['file'] !== null) {
        $md .= "### Application configuration\n\n" . sp_md_table(array('Item', 'Value'), array(
            array('file', $platform['config']['file']), array('names (values not shown)', $platform['config']['env_names']),
            array('values', sp_md_pairs($platform['config']['values'])))) . "\n";
    }

    $md .= "### Host\n\n";
    $rows = array(array('OS', sp_get($host, 'os_release', $host['os']) . ' / ' . $host['uname']), array('Hostname', $host['hostname']),
        array('Time zone', 'PHP ' . $host['timezone']['php_default'] . ', /etc/timezone ' . ($host['timezone']['etc_timezone'] !== '' ? $host['timezone']['etc_timezone'] : '-')
            . ', TZ ' . sp_md_cell($host['timezone']['env_tz'])),
        array('Locale', 'LC_ALL ' . sp_md_cell($host['locale']['lc_all']) . ', LANG ' . sp_md_cell($host['locale']['lang'])),
        array('Disk of the root', 'free ' . sp_human($host['disk']['free_bytes']) . ' of ' . sp_human($host['disk']['total_bytes'])),
        array('Memory', isset($host['memory_total_bytes']) ? sp_human($host['memory_total_bytes']) . ' total, ' . sp_human(sp_get($host, 'memory_available_bytes')) . ' available' : 'not readable'),
        array('CPUs', sp_get($host, 'cpu_count', 'not readable')), array('Load', sp_get($host, 'load_average')),
        array('uid / gid', $host['uid'] . ' / ' . $host['gid'] . ' (' . $host['uid_source'] . ')'), array('open_basedir', $host['open_basedir']));
    foreach (array('redis', 'memcached') as $service) {
        $state = $host['services'][$service];
        $rows[] = array($service, 'extension ' . ($state['extension'] ? 'loaded' : 'not loaded') . ', 127.0.0.1:' . $state['localhost_port'] . ' '
            . ($state['reachable'] === null ? 'not tested' : ($state['reachable'] ? 'answers' : 'closed')));
    }
    foreach ($host['directories']['entries'] as $dir => $usage) {
        $rows[] = array($dir . '/', sp_human($usage['bytes']) . ', ' . $usage['files'] . ' files' . ($usage['complete'] ? '' : ' (scan stopped: at least)'));
    }
    $md .= sp_md_table(array('Item', 'LIVE'), $rows) . "\n";

    $md .= "### Cron\n\n";
    if (!empty($r['cron']['entries'])) {
        $rows = array();
        foreach ($r['cron']['entries'] as $entry) {
            $rows[] = array($entry['schedule'], $entry['command'], !$entry['relevant'] ? 'does not name the application root (another site?)'
                : ($entry['setup']['line'] !== null ? $entry['setup']['line'] : $entry['setup']['note']));
        }
        $md .= sp_md_table(array('Schedule', 'Command (redacted)', 'setup'), $rows) . "\n";
    } else {
        $md .= sp_md_cell(sp_get($r['cron'], 'reason', 'no entries')) . "\n\n";
    }

    $md .= "## 4. Mismatches and risks\n\n";
    if ($r['risks']) {
        $rows = array();
        foreach ($r['risks'] as $risk) {
            $rows[] = array(strtoupper($risk['level']), $risk['title'], $risk['fix']);
        }
        $md .= sp_md_table(array('Level', 'Finding', 'What to do'), $rows) . "\n";
    } else {
        $md .= "None found.\n\n";
    }
    if ($meta['warnings']) {
        $md .= "Probe notes: " . implode('; ', array_map('sp_md_cell', $meta['warnings'])) . "\n\n";
    }

    $md .= sp_llm_instructions($r);
    $md .= "\n## 6. Removal\n\n" . $meta['reminder'] . "\n";
    return $md;
}

function sp_md_pairs($pairs)
{
    $out = array();
    foreach ((array) $pairs as $key => $value) {
        $out[] = $key . ': ' . (is_scalar($value) || $value === null ? sp_md_cell($value) : json_encode($value));
    }
    return $out ? implode(', ', $out) : '-';
}

function sp_md_langs($languages)
{
    $out = array();
    foreach ((array) $languages as $language) {
        $out[] = $language['iso_code'] . ((string) $language['active'] === '1' ? '' : ' (inactive)');
    }
    return $out;
}

function sp_llm_instructions($r)
{
    $is_ps = sp_is_ps($r['application']);
    $directory = $r['setup']['database_cnf_directory'];
    $text = "## 5. Instructions for an LLM\n\n"
        . "You prepare the environment repository of one project for **setup v2** (shared checkout `~/Projects/setup`, "
        . "github.com/BigSant/DockerDevelopmentEnvironment). Use only this report; it holds no secret values.\n\n"
        . "Repository layout (the project directory `app/` is the environment repository):\n\n"
        . "- `app/public/` is the application code (" . ($is_ps ? 'the shop' : 'the application') . "), usually a separate Git repository. "
        . "Change it only where a risk above says so (e.g. `.htaccess` php_value lines) and say so explicitly.\n"
        . "- `env/common.env`: shared settings, committed, never secrets. `env/<env>.env` (`local`, `test`, `stage`, `prod`): private, never committed; "
        . "commit `env/<env>.env.example` copies without secrets. Env files are dotenv (no shell): quote values with spaces, use single quotes for values with `$`.\n"
        . "- Only settings listed in setup's `docker/project-settings.yaml` (and keys Compose uses directly, e.g. `PHP_MEMORY_LIMIT`, `DOCUMENT_ROOT`) are read. "
        . "Do not invent settings. Application settings reach php-fpm/cron by name with `APP_ENVIRONMENT=\"NAME1 NAME2\"` in common.env; values stay in env files or Deployment variables.\n"
        . "- Drop-ins (add only, never copy CORE files): `config/php/*.ini` (+ `config/php/<env>/`), `" . $directory . "/*.cnf` "
        . "(+ `init.d/` for first DB creation), `config/apache/*.conf`, `config/apache/vhost.d/*.conf`, `config/nginx-proxy/{server.d,location.d}/*.conf`, "
        . "`config/cron/crontab` (+ `crontab.<env>`, with `SERVICES=cron`), `config/startup/*.sh`.\n"
        . "- Never put `date.timezone` in ini (use `TZ`), nor memory/time/upload limits (use `PHP_MEMORY_LIMIT`, `PHP_MAX_EXECUTION_TIME`, "
        . "`PHP_UPLOAD_MAX_FILESIZE`, `PHP_POST_MAX_SIZE`), nor opcache/APCu switches (use `PHP_OPCACHE`, `PHP_APCU`, `CACHE_MODE`).\n"
        . "- Missing PHP extensions: `app/Dockerfile` whose first lines are exactly `ARG SETUP_PHP_IMAGE` and `FROM \${SETUP_PHP_IMAGE}`, "
        . "plus `app/.dockerignore` with `**` and `!Dockerfile`. `redis`/`memcached` need only `config/php/cache.ini` (`extension=redis.so`).\n"
        . "- After-import SQL: `database/after-import/common/` (every env, including prod imports), `database/after-import/<env>/`, "
        . "`database/after-import/integrations/<name>.sql` (integration switches, off outside prod). Files `NNN-name.sql`; placeholders `\${DOMAIN}`"
        . ($is_ps ? ", `\${DB_PREFIX}`, `\${PS_SSL}`" : '') . " only.\n"
        . "- Database as code: `database/schema/` (snapshot), `database/table-registry.txt` (every table: `name group full|slice|faker|empty|drop import|skip`"
        . ($is_ps ? ', `{p}` = table prefix; setup already classifies the standard PrestaShop tables' : '') . "), `database/migrations/`.\n";
    if ($is_ps) {
        $text .= "- PrestaShop: `PROFILE=prestashop` and `PS_VERSION` in common.env; setup generates the configuration file from env. "
            . "The shop keys listed in the secrets block go to `env/local.env` (private) and to the stage/prod Bitbucket Deployment variables - "
            . "the user copies the values from the LIVE configuration file, you only write the names.\n";
    }
    $text .= "- Stage/prod need `PHP_MEMORY`, `PHP_CPUS`, `DATABASE_MEMORY`, `DATABASE_CPUS`, `WEBSERVER_MEMORY`, `WEBSERVER_CPUS`, `PROXY_MEMORY`, `PROXY_CPUS`, "
        . "and for releases `APP_UID`, `APP_GID`, `DEPLOY_PATH` (`DEPLOY_CONTEXT`, `DEPLOY_SSH` for a remote server). Values marked GUESS must be sized for the target server.\n\n"
        . "Steps:\n\n"
        . "1. Write `env/common.env` and `env/prod.env.example` from section 2 (keep the existing `PROJECT_NAME`; drop GUESS lines you cannot justify, keep HINT lines commented unless decided).\n"
        . "2. Add the drop-ins, Dockerfile, after-import SQL and crontab drafted in section 2 that the risks justify; fix each HIGH risk or state why not.\n"
        . "3. List for the user what they must do by hand: secrets (section 2), `.htaccess`/shop changes, decisions marked HINT.\n"
        . "4. Commands for the user: `make check` -> `make setup` -> `make db-import-plan file=<LIVE dump>` -> `make db-import file=<LIVE dump>` -> "
        . "`make schema-export` -> `make schema-resolve` -> commit `database/schema` and `database/table-registry.txt` (`make setup` installs the schema hook). "
        . "After drop-in changes `make restart service=<service>`; after Dockerfile or PHP_VERSION changes `make build` and `make up`. "
        . "Servers: `make release ENV=prod`, `make deploy-images ENV=prod`, `make deploy ENV=prod ref=<tag>`.\n"
        . "5. Verify: `make doctor`; `make shell` then `php -m`, `php -r 'echo ini_get(\"max_input_vars\");'`; in the DB `SHOW VARIABLES LIKE 'sql_mode'`.\n"
        . "6. Never commit `env/*.env`, `.generated/`, dumps or keys. References: setup `doc/lt/03-nustatymai.md`, `05-servisai.md`, `07-duomenu-baze.md`, "
        . "`09-aplinkos.md`, `doc/RELEASES.md` (Migrating a v1 project).\n";
    return $text;
}
