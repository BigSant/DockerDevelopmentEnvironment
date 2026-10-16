<?php
// The project's traffic classes: config/metrics/traffic.php in the project's configuration (PROJECT_CONFIG_DIRECTORY,
// mounted read-only at /host/metrics-config), a PHP file that returns
//   array(
//       'classes' => array(
//           'payment' => array('label' => 'Payment callbacks', 'match' => array(
//               array('path' => '#^/module/(paysera|montonio)/(callback|validation)#'))),
//           'monitor' => array('match' => array(array('ip' => array('10.8.0.0/24'), 'path' => '#^/health$#'))),
//           'api' => array('extend' => true, 'match' => array(array('agent' => '#^MyErpSync/#'))),  // or 'api' => false
//           'special' => array('match' => function (array $request) { return ...; }),
//       ),
//       'bots' => array('ShopCrawler/1.0'),  // crawlers beyond setup's list, like METRICS_BOTS
//   );
// A class matches when one of its rules does; a rule matches when every field does. Fields: path (regex on the path
// without the query), url (regex on the path with it), agent (regex), method, ip (addresses and CIDRs), status and host
// (one value or a list each). A closure gets the request as an array of those fields. The order: attack, the
// project's classes in file order (the first that matches), bot, asset, api, human. 'api' is the only built-in class
// a project changes: extend it (its agents or the project's rules), replace its rules, or turn it off.
// Without the file the built-in classes apply; an invalid file is refused whole (traffic_rules says why) and the
// built-in classes apply too, so a mistake never stops the analysis.
// Usage: php traffic-config.php [file]   checks the file (exit 0 and the classes, or 3 and the errors)
require_once __DIR__ . '/metrics.php';

const TRAFFIC_BUILTIN_CLASSES = array('human', 'api', 'bot', 'asset', 'attack');
const TRAFFIC_BUILTIN_LABELS = array('human' => 'People', 'api' => 'Scripts & API', 'bot' => 'Bots', 'asset' => 'Static files',
    'attack' => 'Attacks');
// A project's own classes: each is a label of setup_http_class_requests, so their number stays small.
const TRAFFIC_CLASS_LIMIT = 10;
const TRAFFIC_FIELDS = array('path', 'url', 'agent', 'method', 'ip', 'status', 'host');
// Clients that are not browsers: HTTP libraries and command-line tools, or no agent at all ("-" as nginx logs it).
const TRAFFIC_API_AGENTS = '#^(-?$|curl/|Wget/|python-requests/|python-httpx/|Python-urllib/|Python/\S+ aiohttp/|aiohttp/|GuzzleHttp/|okhttp/|Apache-HttpClient/|Java/|Go-http-client/|node-fetch|axios/|PostmanRuntime/|libwww-perl/)#i';
// The check's exit status for an invalid file (setup tells it from a failure of docker compose run).
const TRAFFIC_CONFIG_INVALID = 3;
// Changes when the built-in classification does: cached hours of an older one are built again.
const TRAFFIC_RULES_VERSION = '2';

function traffic_config_file()
{
    return metrics_setting('METRICS_TRAFFIC_CONFIG', '/host/metrics-config/traffic.php');
}

// The classification in effect, read once per process: array('classes' => name => array(label, rules or closure),
// 'api' => its matchers, each rules or a closure (the built-in one is array(array('agent' => TRAFFIC_API_AGENTS))), or
// null when it is off,
// 'bots' => extra bot words, 'labels' => every class's label in display order, 'hash' => what cached hours depend on,
// 'error' => why the project's file was refused, or null).
function traffic_rules()
{
    static $rules = null;
    if ($rules === null) $rules = traffic_rules_from(traffic_config_file());
    return $rules;
}

// $checked: traffic_config_check($file) when the caller has it (the file is included once: it may declare functions).
function traffic_rules_from($file, $checked = null)
{
    $config = array('classes' => array(), 'bots' => array());
    $builtin = array(array('agent' => TRAFFIC_API_AGENTS));
    $api = array($builtin);
    $error = null;
    $text = '';
    if (is_file($file)) {
        list($loaded, $errors) = $checked !== null ? $checked : traffic_config_check($file);
        if ($errors) {
            $error = 'config/metrics/traffic.php: ' . implode('; ', $errors) . '. The built-in classes apply until it is fixed.';
        } else {
            $config = $loaded;
            $text = (string) @file_get_contents($file);
            if (array_key_exists('api', $config['classes'])) {
                $spec = $config['classes']['api'];
                unset($config['classes']['api']);
                if ($spec === false) $api = null;
                elseif (!empty($spec['extend'])) $api = array($builtin, $spec['match']);
                else $api = array($spec['match']);
            }
        }
    }
    $labels = array('human' => TRAFFIC_BUILTIN_LABELS['human']);
    $classes = array();
    foreach ($config['classes'] as $name => $spec) {
        $classes[$name] = array(isset($spec['label']) ? $spec['label'] : ucfirst(str_replace('_', ' ', $name)), $spec['match']);
        $labels[$name] = $classes[$name][0];
    }
    if ($api !== null) $labels['api'] = TRAFFIC_BUILTIN_LABELS['api'];
    foreach (array('bot', 'asset', 'attack') as $name) $labels[$name] = TRAFFIC_BUILTIN_LABELS[$name];
    $bots = traffic_bot_words($config['bots']);
    return array('classes' => $classes, 'api' => $api, 'bots' => $bots, 'labels' => $labels, 'error' => $error,
        'hash' => md5(TRAFFIC_RULES_VERSION . "\0" . $text . "\0" . implode("\n", $bots)));
}

// setup's crawlers, METRICS_BOTS (comma-separated) and the project file's bots.
function traffic_bot_words($extra)
{
    $listed = preg_split('/\s*,\s*/', trim(metrics_setting('METRICS_BOTS', '')), -1, PREG_SPLIT_NO_EMPTY);
    return array_values(array_unique(array_merge(TRAFFIC_BOTS, $listed, $extra)));
}

// [config, errors] of a project file: the config with its rules prepared (ip ranges parsed) when there are no errors.
function traffic_config_check($file)
{
    $errors = array();
    if (is_link($file) || !is_file($file)) return array(null, array('not a regular file'));
    ob_start();
    try {
        $config = (function ($__file) { return include $__file; })($file);
    } catch (Throwable $error) {
        ob_end_clean();
        return array(null, array(get_class($error) . ' on line ' . $error->getLine() . ': ' . $error->getMessage()));
    }
    ob_end_clean();
    if (!is_array($config)) return array(null, array("it must return an array with 'classes' (and 'bots')"));
    foreach (array_diff(array_keys($config), array('classes', 'bots')) as $key) $errors[] = "unknown key '" . $key . "' (classes, bots)";
    $classes = isset($config['classes']) ? $config['classes'] : array();
    $bots = isset($config['bots']) ? $config['bots'] : array();
    if (!is_array($classes)) {
        $errors[] = "'classes' must be an array of name => class";
        $classes = array();
    }
    if (!is_array($bots) || array_filter($bots, function ($bot) { return !is_string($bot) || trim($bot) === ''; })) {
        $errors[] = "'bots' must be a list of user-agent words";
        $bots = array();
    }
    $own = 0;
    $prepared = array();
    foreach ($classes as $name => $spec) {
        $where = "class '" . $name . "'";
        if (!is_string($name) || !preg_match('/^[a-z][a-z0-9_]{0,31}$/', $name)) {
            $errors[] = $where . ': a name is a-z, 0-9 and _ (32 at most), starting with a letter';
            continue;
        }
        if ($name === 'api' && $spec === false) {
            $prepared[$name] = false;
            continue;
        }
        if ($name !== 'api' && in_array($name, TRAFFIC_BUILTIN_CLASSES, true)) {
            $errors[] = $where . ' is built in' . ($name === 'bot' ? " (add crawlers under 'bots')" : '') . '; only api can be changed';
            continue;
        }
        if ($name !== 'api') $own++;
        if (!is_array($spec)) {
            $errors[] = $where . ": must be an array with 'match'" . ($name === 'api' ? ', or false' : '');
            continue;
        }
        foreach (array_diff(array_keys($spec), $name === 'api' ? array('match', 'extend') : array('match', 'label')) as $key) {
            $errors[] = $where . ": unknown key '" . $key . "'";
        }
        if (isset($spec['label']) && (!is_string($spec['label']) || trim($spec['label']) === '' || strlen($spec['label']) > 40)) {
            $errors[] = $where . ': the label is a text of 40 characters at most';
        }
        if (isset($spec['extend']) && !is_bool($spec['extend'])) $errors[] = $where . ': extend is true or false';
        if (!isset($spec['match'])) {
            $errors[] = $where . ": no 'match'";
            continue;
        }
        $rules = traffic_config_rules($spec['match'], $where, $errors);
        if ($rules !== null) $prepared[$name] = array('match' => $rules) + $spec;
    }
    if ($own > TRAFFIC_CLASS_LIMIT) $errors[] = $own . ' classes; ' . TRAFFIC_CLASS_LIMIT . ' at most (each is a label of setup_http_class_requests)';
    if ($errors) return array(null, $errors);
    return array(array('classes' => $prepared, 'bots' => array_values($bots)), array());
}

// A class's rules checked and prepared (a closure as it is, ip lists as [binary address, prefix bits]); null when
// one is wrong (the reasons go to $errors).
function traffic_config_rules($match, $where, &$errors)
{
    if ($match instanceof Closure) return $match;
    if (!is_array($match) || !$match || array_keys($match) !== range(0, count($match) - 1)) {
        $errors[] = $where . ": 'match' is a list of rules (each an array of fields) or a function";
        return null;
    }
    $count = count($errors);
    $rules = array();
    foreach ($match as $index => $rule) {
        $at = $where . ' rule ' . ($index + 1);
        if (!is_array($rule) || !$rule) {
            $errors[] = $at . ': an array of fields (' . implode(', ', TRAFFIC_FIELDS) . ')';
            continue;
        }
        $prepared = array();
        foreach ($rule as $field => $value) {
            if (!in_array($field, TRAFFIC_FIELDS, true)) {
                $errors[] = $at . ": unknown field '" . $field . "' (" . implode(', ', TRAFFIC_FIELDS) . ')';
                continue;
            }
            if (in_array($field, array('path', 'url', 'agent'), true)) {
                if (!is_string($value) || @preg_match($value, '') === false) {
                    $errors[] = $at . ': ' . $field . ' is not a valid regular expression';
                } else {
                    $prepared[$field] = $value;
                }
                continue;
            }
            $values = is_array($value) ? $value : array($value);
            if (!$values) {
                $errors[] = $at . ': ' . $field . ' is empty';
                continue;
            }
            foreach ($values as $one) {
                if ($field === 'status' ? !(is_int($one) && $one >= 100 && $one <= 599) : !is_string($one) || $one === '') {
                    $errors[] = $at . ': ' . $field . ($field === 'status' ? ' is a status code or a list of them' : ' is a text or a list of texts');
                    continue 2;
                }
            }
            if ($field === 'ip') {
                $ranges = array();
                foreach ($values as $one) {
                    $range = traffic_ip_range($one);
                    if ($range === null) {
                        $errors[] = $at . ': ' . $one . ' is not an IP address or CIDR range';
                        continue 2;
                    }
                    $ranges[] = $range;
                }
                $prepared['ip'] = $ranges;
            } elseif ($field === 'method') {
                $prepared['method'] = array_map('strtoupper', $values);
            } elseif ($field === 'host') {
                $prepared['host'] = array_map('strtolower', $values);
            } else {
                $prepared['status'] = $values;
            }
        }
        $rules[] = $prepared;
    }
    return count($errors) === $count ? $rules : null;
}

// [binary address, prefix bits] of an address or a CIDR range, null when it is neither.
function traffic_ip_range($text)
{
    $parts = explode('/', $text, 2);
    $address = @inet_pton($parts[0]);
    if ($address === false) return null;
    $bits = strlen($address) * 8;
    if (isset($parts[1])) {
        if (!preg_match('/^\d{1,3}$/', $parts[1]) || (int) $parts[1] > $bits) return null;
        $bits = (int) $parts[1];
    }
    return array($address, $bits);
}

function traffic_ip_in($ip, $ranges)
{
    $address = @inet_pton($ip);
    if ($address === false) return false;
    foreach ($ranges as $range) {
        list($network, $bits) = $range;
        if (strlen($network) !== strlen($address)) continue;
        $bytes = intdiv($bits, 8);
        if (substr($address, 0, $bytes) !== substr($network, 0, $bytes)) continue;
        $rest = $bits % 8;
        if ($rest === 0) return true;
        $mask = (0xff << (8 - $rest)) & 0xff;
        if ((ord($address[$bytes]) & $mask) === (ord($network[$bytes]) & $mask)) return true;
    }
    return false;
}

// Whether a request (ip, method, path, url, status, agent, host) matches rules or a closure. A closure that fails
// does not match.
function traffic_matches($rules, $request)
{
    if ($rules instanceof Closure) {
        try {
            return (bool) $rules($request);
        } catch (Throwable $error) {
            return false;
        }
    }
    foreach ($rules as $rule) {
        if (isset($rule['path']) && !preg_match($rule['path'], $request['path'])) continue;
        if (isset($rule['url']) && !preg_match($rule['url'], $request['url'])) continue;
        if (isset($rule['agent']) && !preg_match($rule['agent'], $request['agent'])) continue;
        if (isset($rule['method']) && !in_array($request['method'], $rule['method'], true)) continue;
        if (isset($rule['status']) && !in_array($request['status'], $rule['status'], true)) continue;
        if (isset($rule['host']) && !in_array($request['host'], $rule['host'], true)) continue;
        if (isset($rule['ip']) && !traffic_ip_in($request['ip'], $rule['ip'])) continue;
        return true;
    }
    return false;
}

if (PHP_SAPI === 'cli' && isset($argv[0]) && realpath($argv[0]) === __FILE__) {
    require_once __DIR__ . '/traffic.php';
    $file = isset($argv[1]) ? $argv[1] : traffic_config_file();
    if (!is_file($file)) {
        echo 'No ' . $file . ': the built-in classes apply (' . implode(', ', TRAFFIC_BUILTIN_CLASSES) . ").\n";
        exit(0);
    }
    $checked = traffic_config_check($file);
    $errors = $checked[1];
    if ($errors) {
        fwrite(STDERR, $file . " is not valid:\n  " . implode("\n  ", $errors) . "\n");
        exit(TRAFFIC_CONFIG_INVALID);
    }
    $rules = traffic_rules_from($file, $checked);
    echo 'Traffic classes: ' . implode(', ', array_keys($rules['labels'])) . ($rules['api'] === null ? ' (api off)' : '') . "\n";
}
