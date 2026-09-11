<?php
// Run before PHP-FPM starts, with the existing application's config mounted writable.
// Only connection settings are managed; shop-specific keys must come from the shop.

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

function updatePrestashopParameters($root)
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
    echo "Updated PrestaShop DB parameters from the runtime environment; cleared dev/prod cache.\n";
}

// PHP errors can include source values. Startup errors intentionally print no config.
ini_set('display_errors', '0');
ini_set('log_errors', '0');
try {
    updatePrestashopParameters(isset($argv[1]) ? rtrim($argv[1], '/') : '/var/www/html');
} catch (Throwable $error) {
    $message = $error instanceof PrestashopSetupException ? $error->getMessage() : 'Cannot load or update the existing parameters file.';
    fwrite(STDERR, "PrestaShop setup: $message\n");
    exit(1);
}
