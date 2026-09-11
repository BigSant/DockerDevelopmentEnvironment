<?php
// Run before PHP-FPM starts, with the existing application's config mounted writable.
// Shop-specific keys must come from the shop; debug is opt-in through runtime env.

class PrestashopSetupException extends RuntimeException
{
}

function removePrestashopCache($path)
{
    if (is_link($path)) {
        throw new PrestashopSetupException('Refusing a symlink in the PrestaShop cache path.');
    }
    if (!file_exists($path)) {
        return;
    }
    if (!is_dir($path)) {
        throw new PrestashopSetupException('Expected a PrestaShop cache directory.');
    }
    $entries = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::CHILD_FIRST
    );
    foreach ($entries as $entry) {
        // Unlink nested symlinks without following them outside the cache.
        $ok = $entry->isDir() && !$entry->isLink()
            ? rmdir($entry->getPathname()) : unlink($entry->getPathname());
        if (!$ok) {
            throw new PrestashopSetupException('Cannot clear the PrestaShop configuration cache.');
        }
    }
    if (!rmdir($path)) {
        throw new PrestashopSetupException('Cannot remove the PrestaShop cache directory.');
    }
}

function updatePrestashopParameters($root, $overrideFile = null)
{
    foreach (array($root, "$root/app", "$root/app/config", "$root/app/config/parameters.php",
                   "$root/var", "$root/var/cache", "$root/var/cache/dev", "$root/var/cache/prod") as $path) {
        if (is_link($path)) {
            throw new PrestashopSetupException('Refusing a symlink in a managed PrestaShop path.');
        }
    }
    $file = "$root/app/config/parameters.php";
    if (!is_file($file)) {
        throw new PrestashopSetupException('Restore app/config/parameters.php from this shop first; its encryption keys are required.');
    }
    $values = array();
    foreach (array('host', 'port', 'name', 'user', 'password') as $key) {
        $value = getenv('DATABASE_' . strtoupper($key));
        if ($value === false || $value === '') {
            throw new PrestashopSetupException('Missing runtime DATABASE_' . strtoupper($key) . '.');
        }
        // Symfony parameters escape %, and PrestaShop's bootstrap reverses it.
        $values['database_' . $key] = str_replace('%', '%%', $value);
    }
    if (!ctype_digit($values['database_port']) || (int) $values['database_port'] < 1 || (int) $values['database_port'] > 65535) {
        throw new PrestashopSetupException('DATABASE_PORT must be between 1 and 65535.');
    }
    $config = require $file;
    if (!is_array($config) || !isset($config['parameters']) || !is_array($config['parameters'])) {
        throw new PrestashopSetupException('Expected a parameters array in app/config/parameters.php.');
    }
    $overrides = psOverrides($overrideFile);
    foreach ($values as $key => $unused) {
        if (array_key_exists($key, $overrides)) {
            throw new PrestashopSetupException('Put database connection overrides in env, not parameters.override.php.');
        }
    }
    $values = array_merge($values, psEscapeParameters($overrides));
    $changed = false;
    foreach ($values as $key => $value) {
        if (!array_key_exists($key, $config['parameters']) || $config['parameters'][$key] !== $value) {
            $changed = true;
            $config['parameters'][$key] = $value;
        }
    }
    if (!$changed) {
        echo "PrestaShop DB parameters already match the runtime environment.\n";
        return;
    }
    $contents = "<?php\nreturn " . var_export($config, true) . ";\n";
    $temporary = tempnam(dirname($file), '.setup-parameters-');
    if ($temporary === false || dirname($temporary) !== dirname($file)) {
        if ($temporary !== false) {
            unlink($temporary);
        }
        throw new PrestashopSetupException('Cannot create a parameters file beside the original.');
    }
    try {
        if (!chmod($temporary, 0600) || file_put_contents($temporary, $contents) !== strlen($contents)) {
            throw new PrestashopSetupException('Cannot write the private parameters file.');
        }
        // Both the legacy appParameters.php and compiled Symfony containers cache
        // credentials. Clear before replacement, so a failure retries next startup.
        removePrestashopCache("$root/var/cache/dev");
        removePrestashopCache("$root/var/cache/prod");
        if (!rename($temporary, $file)) {
            throw new PrestashopSetupException('Cannot replace app/config/parameters.php.');
        }
    } finally {
        if (file_exists($temporary)) {
            unlink($temporary);
        }
    }
    echo "Updated PrestaShop parameters; cleared dev/prod cache.\n";
}


function psEscapeParameters($value)
{
    if (is_array($value)) {
        foreach ($value as $key => $item) $value[$key] = psEscapeParameters($item);
        return $value;
    }
    return is_string($value) ? str_replace('%', '%%', $value) : $value;
}

function psOverrides($file)
{
    if (!$file || !file_exists($file)) return array();
    if (is_link($file)) throw new PrestashopSetupException('Configuration override must not be a symlink.');
    $values = require $file;
    if (!is_array($values)) throw new PrestashopSetupException('Configuration override must return an array.');
    foreach ($values as $key => $value) {
        if (!is_string($key) || !preg_match('/^[A-Za-z_][A-Za-z0-9_]*$/D', $key)) {
            throw new PrestashopSetupException('Invalid configuration override key.');
        }
        if (is_object($value) || is_resource($value)) throw new PrestashopSetupException('Configuration values must be scalar or arrays.');
    }
    return $values;
}

function psLayout($root)
{
    $legacy = "$root/config/settings.inc.php";
    if (is_file($legacy) && preg_match('/[\'\"]_PS_VERSION_[\'\"]\s*,\s*[\'\"]1\.6\./', file_get_contents($legacy))) return 'legacy';
    if (is_file("$root/app/config/parameters.php")) return 'modern';
    if (is_file("$root/app/config/parameters.yml")) {
        throw new PrestashopSetupException('This shop uses parameters.yml; convert it with the application tooling before enabling automatic preparation.');
    }
    if (is_file($legacy)) return 'legacy';
    throw new PrestashopSetupException('Missing shop configuration: restore PS 1.6 config/settings.inc.php or newer app/config/parameters.php with the shop keys.');
}

function psConnectionValues()
{
    $values = array();
    foreach (array('HOST', 'PORT', 'NAME', 'USER', 'PASSWORD') as $key) {
        $value = getenv('DATABASE_' . $key);
        if ($value === false || $value === '') throw new PrestashopSetupException('Missing runtime DATABASE_' . $key . '.');
        $values[$key] = $value;
    }
    if (!ctype_digit($values['PORT']) || (int)$values['PORT'] < 1 || (int)$values['PORT'] > 65535) {
        throw new PrestashopSetupException('DATABASE_PORT must be between 1 and 65535.');
    }
    return $values;
}

function psPatchDefines($source, $values)
{
    $expressions = array();
    foreach ($values as $key => $value) $expressions[$key] = var_export($value, true);
    return psPatchDefineExpressions($source, $expressions);
}

// Expressions are generated by this updater, never accepted as PHP from env.
function psPatchDefineExpressions($source, $expressions, $requireExisting = false)
{
    $tokens = token_get_all($source);
    $flat = array();
    $offset = 0;
    foreach ($tokens as $token) {
        $text = is_array($token) ? $token[1] : $token;
        $id = is_array($token) ? $token[0] : null;
        if (!in_array($id, array(T_WHITESPACE, T_COMMENT, T_DOC_COMMENT), true)) {
            $flat[] = array($id, $text, $offset);
        }
        $offset += strlen($text);
    }
    $patches = array();
    $found = array();
    $appendAt = strlen($source);
    foreach ($flat as $i => $token) {
        if ($token[0] === T_CLOSE_TAG) $appendAt = min($appendAt, $token[2]);
        if ($token[0] !== T_STRING || strtolower($token[1]) !== 'define') continue;
        if (!isset($flat[$i+3]) || $flat[$i+1][1] !== '(' || $flat[$i+2][0] !== T_CONSTANT_ENCAPSED_STRING || $flat[$i+3][1] !== ',') continue;
        // A tokenizer-verified quoted literal, never arbitrary PHP expressions.
        $key = eval('return ' . $flat[$i+2][1] . ';');
        if (!array_key_exists($key, $expressions)) continue;
        if (isset($found[$key])) throw new PrestashopSetupException('Duplicate managed define: ' . $key . '.');
        $found[$key] = true;
        $depth = 0;
        $start = $flat[$i+3][2] + 1;
        for ($j=$i+4; $j<count($flat); $j++) {
            $text = $flat[$j][1];
            if ($depth === 0 && ($text === ')' || $text === ',')) {
                $patches[] = array($start, $flat[$j][2]-$start, ' ' . $expressions[$key]);
                break;
            }
            if (in_array($text, array('(', '[', '{'), true)) $depth++;
            if (in_array($text, array(')', ']', '}'), true)) $depth--;
        }
        if ($j === count($flat)) throw new PrestashopSetupException('Cannot parse managed define: ' . $key . '.');
    }
    $append = '';
    foreach ($expressions as $key => $expression) {
        if (!isset($found[$key])) {
            if ($requireExisting) throw new PrestashopSetupException('Missing managed define in config/defines.inc.php: ' . $key . '.');
            $append .= "\ndefine(" . var_export($key, true) . ', ' . $expression . ');';
        }
    }
    if ($append !== '') $patches[] = array($appendAt, 0, $append . "\n");
    usort($patches, function ($a, $b) { return $b[0] - $a[0]; });
    foreach ($patches as $patch) $source = substr_replace($source, $patch[2], $patch[0], $patch[1]);
    return $source;
}

function psDebugPlan($root)
{
    $mode = getenv('PS_DEBUG_MODE');
    // Existing projects keep their own debug behavior until they opt in.
    if ($mode === false || $mode === '') return null;
    if (!in_array($mode, array('off', 'on', 'ip'), true)) {
        throw new PrestashopSetupException('PS_DEBUG_MODE must be off, on or ip (or empty to leave the file unmanaged).');
    }
    $allowed = array();
    $ips = trim((string)getenv('PS_DEBUG_IPS'));
    if ($ips !== '') {
        foreach (explode(',', $ips) as $ip) {
            $ip = trim($ip);
            if (filter_var($ip, FILTER_VALIDATE_IP) === false) {
                throw new PrestashopSetupException('PS_DEBUG_IPS must contain comma-separated IPv4/IPv6 addresses, without ports or CIDR ranges.');
            }
            $allowed[] = bin2hex(inet_pton($ip));
        }
    }
    $allowed = array_values(array_unique($allowed));
    sort($allowed);
    if ($mode === 'ip' && !$allowed) throw new PrestashopSetupException('PS_DEBUG_MODE=ip requires at least one address in PS_DEBUG_IPS.');
    $expression = $mode === 'on' ? 'true' : 'false';
    if ($mode === 'ip') {
        // REMOTE_ADDR is supplied by the web server. Never read forwarded headers
        // here: only a server configured with trusted proxies may resolve those.
        $expression = "(!in_array(PHP_SAPI, array('cli', 'phpdbg'), true)"
            . " && isset(\$_SERVER['REMOTE_ADDR']) && is_string(\$_SERVER['REMOTE_ADDR'])"
            . " && in_array(bin2hex((string)@inet_pton(\$_SERVER['REMOTE_ADDR'])), "
            . var_export($allowed, true) . ', true))';
    }
    $file = "$root/config/defines.inc.php";
    foreach (array($root, "$root/config", $file) as $path) {
        if (is_link($path)) throw new PrestashopSetupException('Refusing a symlink in the PrestaShop debug configuration path.');
    }
    if (!is_file($file) || !is_readable($file)) {
        throw new PrestashopSetupException('Restore the shop config/defines.inc.php before setting PS_DEBUG_MODE.');
    }
    $source = file_get_contents($file);
    $updated = psPatchDefineExpressions($source, array('_PS_MODE_DEV_' => $expression), true);
    return array($file, $source, $updated);
}

function updatePrestashopDebug($plan)
{
    if ($plan === null) return;
    list($file, $source, $updated) = $plan;
    if ($source === $updated) {
        echo "PrestaShop debug configuration already matches the runtime environment.\n";
        return;
    }
    $temporary = tempnam(dirname($file), '.setup-defines-');
    if ($temporary === false || dirname($temporary) !== dirname($file)) {
        if ($temporary !== false) unlink($temporary);
        throw new PrestashopSetupException('Cannot write beside config/defines.inc.php.');
    }
    try {
        if (!chmod($temporary, fileperms($file) & 0777)
            || file_put_contents($temporary, $updated) !== strlen($updated)
            || !rename($temporary, $file)) {
            throw new PrestashopSetupException('Cannot replace config/defines.inc.php.');
        }
    } finally {
        if (file_exists($temporary)) unlink($temporary);
    }
    echo "Updated PrestaShop debug configuration from runtime env.\n";
}

function updateLegacyPrestashop($root, $overrideFile)
{
    $file = "$root/config/settings.inc.php";
    foreach (array($root, "$root/config", $file, "$root/cache", "$root/cache/smarty") as $path) {
        if (is_link($path)) throw new PrestashopSetupException('Refusing a symlink in a managed PrestaShop path.');
    }
    $db = psConnectionValues();
    $values = array('_DB_SERVER_' => $db['HOST'] . ($db['PORT'] === '3306' ? '' : ':' . $db['PORT']),
                    '_DB_NAME_' => $db['NAME'], '_DB_USER_' => $db['USER'], '_DB_PASSWD_' => $db['PASSWORD']);
    $overrides = psOverrides($overrideFile);
    foreach ($overrides as $value) {
        if (is_array($value)) throw new PrestashopSetupException('Legacy configuration constants must be scalar values.');
    }
    foreach ($values as $key => $unused) {
        if (array_key_exists($key, $overrides)) throw new PrestashopSetupException('Put database connection overrides in env, not settings.override.php.');
    }
    $source = file_get_contents($file);
    $updated = psPatchDefines($source, array_merge($values, $overrides));
    if ($updated === $source) { echo "PrestaShop 1.6 settings already match.\n"; return; }
    $temporary = tempnam(dirname($file), '.setup-settings-');
    if ($temporary === false || dirname($temporary) !== dirname($file)) throw new PrestashopSetupException('Cannot write beside settings.inc.php.');
    try {
        if (!chmod($temporary, 0600) || file_put_contents($temporary, $updated) !== strlen($updated)) throw new PrestashopSetupException('Cannot write private settings.inc.php.');
        $index = "$root/cache/class_index.php";
        if (file_exists($index) && !unlink($index)) throw new PrestashopSetupException('Cannot clear legacy class cache.');
        foreach (array('cache/smarty/cache', 'cache/smarty/compile', 'cache/cachefs') as $relative) {
            $path = "$root/$relative";
            if (is_link($path)) throw new PrestashopSetupException('Refusing a symlinked legacy cache root.');
            if (!is_dir($path)) continue;
            $entries = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($path, FilesystemIterator::SKIP_DOTS), RecursiveIteratorIterator::CHILD_FIRST);
            foreach ($entries as $entry) {
                if (in_array($entry->getFilename(), array('index.php', '.htaccess'), true)) continue;
                if ($entry->isDir() && !$entry->isLink()) { @rmdir($entry->getPathname()); }
                elseif (!unlink($entry->getPathname())) throw new PrestashopSetupException('Cannot clear legacy cache.');
            }
        }
        if (!rename($temporary, $file)) throw new PrestashopSetupException('Cannot replace settings.inc.php.');
    } finally {
        if (file_exists($temporary)) unlink($temporary);
    }
    echo "Updated PrestaShop 1.6 settings; shop keys preserved.\n";
}

function preparePrestashop($root, $config)
{
    $profile = getenv('PROFILE');
    if ($profile !== 'ps' && $profile !== 'prestashop') return;
    // Reject an invalid debug policy before changing the shop's DB config.
    $debug = psDebugPlan($root);
    if (psLayout($root) === 'legacy') updateLegacyPrestashop($root, "$config/prestashop/settings.override.php");
    else updatePrestashopParameters($root, "$config/prestashop/parameters.override.php");
    updatePrestashopDebug($debug);
}

function checkPrestashop($root)
{
    if (psLayout($root) === 'legacy') {
        require "$root/config/settings.inc.php";
        $host = _DB_SERVER_; $port = '3306';
        if (preg_match('/^(.*):([0-9]+)$/D', $host, $matches)) { $host=$matches[1]; $port=$matches[2]; }
        $name=_DB_NAME_; $user=_DB_USER_; $password=_DB_PASSWD_;
    } else {
        $config = require "$root/app/config/parameters.php";
        $p = $config['parameters'];
        foreach ($p as $key => $value) if (is_string($value)) $p[$key]=str_replace('%%', '%', $value);
        $host=$p['database_host']; $port=empty($p['database_port']) ? '3306' : $p['database_port'];
        $name=$p['database_name']; $user=$p['database_user']; $password=$p['database_password'];
    }
    $db = new PDO("mysql:host=$host;port=$port;dbname=$name", $user, $password, array(PDO::ATTR_TIMEOUT=>5, PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION));
    if ((int)$db->query('SELECT 1')->fetchColumn() !== 1) throw new PrestashopSetupException('PrestaShop database probe failed.');
    echo "PrestaShop configuration and database connection: OK\n";
}
