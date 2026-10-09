<?php
// PrestaShop configuration file from a template plus runtime env (PHP 5.6 compatible).
// PS_VERSION selects the layout: 1.6 -> config/settings.inc.php, 1.7/8/9 -> app/config/parameters.php.
// Shop keys come from PS_* env; a key missing from env keeps the value already in the file.

function psConfiguredVersion()
{
    $version = getenv('PS_VERSION');
    if ($version === false || $version === '') return null;
    if (!preg_match('/^1\.6(\.\d+)*$|^1\.7(\.\d+)*$|^[89](\.\d+)*$/D', $version)) {
        throw new PrestashopSetupException('PS_VERSION must be a PrestaShop version such as 1.6.1.24, 1.7.8.11, 8.2.1 or 9.0.2.');
    }
    return $version;
}

function psLayoutForVersion($version)
{
    return strpos($version, '1.6') === 0 ? 'legacy' : 'modern';
}

function psCodeVersion($root)
{
    $settings = "$root/config/settings.inc.php";
    if (is_file($settings) && preg_match('/[\'"]_PS_VERSION_[\'"]\s*,\s*[\'"]([0-9.]+)[\'"]/', file_get_contents($settings), $m)) return $m[1];
    foreach (array("$root/src/Core/Version.php", "$root/app/AppKernel.php") as $file) {
        if (is_file($file) && preg_match('/const\s+VERSION\s*=\s*[\'"]([0-9.]+)[\'"]/', file_get_contents($file), $m)) return $m[1];
    }
    return null;
}

function psCheckVersion($root, $version)
{
    $code = psCodeVersion($root);
    if ($code === null) return;
    if ($code !== $version && strpos($code, $version . '.') !== 0) {
        throw new PrestashopSetupException("PS_VERSION=$version does not match the shop code ($code); update env or the code.");
    }
}

// env name => [parameters.php key, settings.inc.php constant, required from version]
function psKeyMap()
{
    return array(
        'PS_COOKIE_KEY' => array('cookie_key', '_COOKIE_KEY_', '1.6'),
        'PS_COOKIE_IV' => array('cookie_iv', '_COOKIE_IV_', '1.6'),
        'PS_RIJNDAEL_KEY' => array(null, '_RIJNDAEL_KEY_', '1.6'),
        'PS_RIJNDAEL_IV' => array(null, '_RIJNDAEL_IV_', '1.6'),
        'PS_NEW_COOKIE_KEY' => array('new_cookie_key', null, '1.7'),
        'PS_SECRET' => array('secret', null, '1.7'),
        'PS_API_PRIVATE_KEY' => array('api_private_key', null, '9'),
        'PS_API_PUBLIC_KEY' => array('api_public_key', null, '9'),
    );
}

function psKeysFor($version)
{
    $layout = psLayoutForVersion($version);
    $major = $layout === 'legacy' ? '1.6' : (strpos($version, '1.7') === 0 ? '1.7' : substr($version, 0, 1));
    $order = array('1.6' => 0, '1.7' => 1, '8' => 2, '9' => 3);
    $keys = array();
    foreach (psKeyMap() as $env => $spec) {
        $name = $layout === 'legacy' ? $spec[1] : $spec[0];
        if ($name === null || $order[$spec[2]] > $order[$major]) continue;
        $value = getenv($env);
        if ($value !== false && $value !== '' && strpos($env, 'PS_API_') === 0) {
            $decoded = base64_decode($value, true);
            if ($decoded === false || strpos($decoded, '-----BEGIN') === false) {
                throw new PrestashopSetupException("$env must be a base64 encoded PEM key.");
            }
            $value = $decoded;
        }
        $keys[$name] = array($env, $value === false || $value === '' ? null : $value);
    }
    return $keys;
}

function psRandom($length)
{
    $alphabet = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    $out = '';
    for ($i = 0; $i < $length; $i++) $out .= $alphabet[ord(openssl_random_pseudo_bytes(1)) % strlen($alphabet)];
    return $out;
}

function psGenerateConfig($root, $version)
{
    $db = psConnectionValues();
    $keys = psKeysFor($version);
    $missing = array();
    foreach ($keys as $name => $entry) if ($entry[1] === null) $missing[] = $entry[0];
    if ($missing) {
        throw new PrestashopSetupException('No shop configuration file yet and no keys to create it: set ' . implode(', ', $missing) . ' (make bootstrap generates them locally).');
    }
    $prefix = getenv('PS_DATABASE_PREFIX') ? getenv('PS_DATABASE_PREFIX') : 'ps_';
    if (psLayoutForVersion($version) === 'legacy') {
        $file = "$root/config/settings.inc.php";
        $defines = array(
            '_DB_SERVER_' => $db['HOST'] . ($db['PORT'] === '3306' ? '' : ':' . $db['PORT']),
            '_DB_NAME_' => $db['NAME'], '_DB_USER_' => $db['USER'], '_DB_PASSWD_' => $db['PASSWORD'],
            '_DB_PREFIX_' => $prefix, '_MYSQL_ENGINE_' => 'InnoDB', '_PS_CACHING_SYSTEM_' => 'CacheMemcache',
            '_PS_CACHE_ENABLED_' => '0', '_PS_CREATION_DATE_' => date('Y-m-d'), '_PS_VERSION_' => $version,
        );
        foreach ($keys as $name => $entry) $defines[$name] = $entry[1];
        $contents = "<?php\n";
        foreach ($defines as $name => $value) $contents .= 'define(' . var_export($name, true) . ', ' . var_export($value, true) . ");\n";
    } else {
        $file = "$root/app/config/parameters.php";
        $parameters = array(
            'database_host' => $db['HOST'], 'database_port' => $db['PORT'], 'database_name' => $db['NAME'],
            'database_user' => $db['USER'], 'database_password' => $db['PASSWORD'], 'database_prefix' => $prefix,
            'database_engine' => 'InnoDB', 'mailer_transport' => 'smtp', 'mailer_host' => '127.0.0.1',
            'mailer_user' => null, 'mailer_password' => null, 'ps_caching' => 'CacheMemcache',
            'ps_cache_enable' => false, 'ps_creation_date' => date('Y-m-d'),
            'locale' => getenv('PS_LOCALE') ? getenv('PS_LOCALE') : 'en-US', 'use_debug_toolbar' => true,
        );
        foreach ($keys as $name => $entry) $parameters[$name] = $entry[1];
        $contents = "<?php\nreturn " . var_export(array('parameters' => psEscapeParameters($parameters)), true) . ";\n";
    }
    foreach (array(dirname(dirname($file)), dirname($file)) as $path) {
        if (is_link($path)) throw new PrestashopSetupException('Refusing a symlink in a managed PrestaShop path.');
    }
    if (!is_dir(dirname($file)) && !mkdir(dirname($file), 0755, true)) {
        throw new PrestashopSetupException('Cannot create the shop configuration directory.');
    }
    if (file_put_contents($file, $contents, LOCK_EX) !== strlen($contents) || !chmod($file, 0600)) {
        throw new PrestashopSetupException('Cannot write the shop configuration file.');
    }
    echo "Created the PrestaShop configuration from the template and runtime env.\n";
}

// Values from env that replace the file's keys; null means "keep the file value".
function psKeyOverrides($version, $existing)
{
    $values = array();
    foreach (psKeysFor($version) as $name => $entry) {
        if ($entry[1] !== null) {
            $values[$name] = $entry[1];
        } elseif (!array_key_exists($name, $existing) || $existing[$name] === '' || $existing[$name] === null) {
            throw new PrestashopSetupException("Missing {$entry[0]}: the shop configuration has no $name either.");
        }
    }
    return $values;
}
