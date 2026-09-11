<?php
ini_set('display_errors', '0');
ini_set('log_errors', '0');
require __DIR__ . '/prestashop.php';
try {
    $profile = getenv('PROFILE');
    if ($profile !== 'ps' && $profile !== 'prestashop') { echo "PrestaShop checks skipped for this profile.\n"; exit(0); }
    $root = isset($argv[2]) ? rtrim($argv[2], '/') : '/var/www/html';
    if (isset($argv[1]) && $argv[1] === 'check') checkPrestashop($root);
    else preparePrestashop($root, isset($argv[3]) ? $argv[3] : '/opt/setup/project');
} catch (Exception $error) {
    fwrite(STDERR, 'PrestaShop setup: ' . ($error instanceof PrestashopSetupException ? $error->getMessage() : 'Configuration or database check failed; verify shop files and credentials.') . "\n");
    exit(1);
} catch (Throwable $error) {
    fwrite(STDERR, "PrestaShop setup: cannot parse or update the shop configuration.\n");
    exit(1);
}
