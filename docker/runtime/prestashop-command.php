<?php
ini_set('display_errors', '0');
ini_set('log_errors', '0');
require __DIR__ . '/prestashop.php';
try {
    $profile = getenv('PROFILE');
    if ($profile !== 'ps' && $profile !== 'prestashop') { echo "PrestaShop checks skipped for this profile.\n"; exit(0); }
    $root = isset($argv[2]) ? rtrim($argv[2], '/') : '/var/www/html';
    // No checkout yet: write nothing (a generated config would block git clone into the directory).
    if (!is_dir($root) || !(new FilesystemIterator($root))->valid()) {
        echo "No shop code in $root yet: PrestaShop preparation skipped; clone the shop repository there and run make up again.\n";
        exit(0);
    }
    if (isset($argv[1]) && $argv[1] === 'check') checkPrestashop($root);
    elseif (isset($argv[1]) && $argv[1] === 'validate') validatePrestashop($root, isset($argv[3]) ? $argv[3] : '/opt/setup/project');
    elseif (isset($argv[1]) && $argv[1] === 'policy') applyPrestashopPolicy($root);
    elseif (isset($argv[1]) && $argv[1] === 'cache-clear') clearPrestashopCache($root);
    else preparePrestashop($root, isset($argv[3]) ? $argv[3] : '/opt/setup/project');
} catch (Exception $error) {
    fwrite(STDERR, 'PrestaShop setup: ' . ($error instanceof PrestashopSetupException ? $error->getMessage() : 'Configuration or database check failed; verify shop files and credentials.') . "\n");
    exit(1);
} catch (Throwable $error) {
    fwrite(STDERR, "PrestaShop setup: cannot parse or update the shop configuration.\n");
    exit(1);
}
