<?php
// Compatibility entry point for projects using the original explicit mount.
$library = dirname(dirname(__DIR__)) . '/runtime/prestashop.php';
if (!is_file($library)) $library = '/opt/setup/runtime/prestashop.php';
require $library;
ini_set('display_errors', '0');
ini_set('log_errors', '0');
try {
    preparePrestashop(isset($argv[1]) ? rtrim($argv[1], '/') : '/var/www/html', isset($argv[2]) ? $argv[2] : '/opt/setup/project');
} catch (Exception $error) {
    fwrite(STDERR, 'PrestaShop setup: ' . ($error instanceof PrestashopSetupException ? $error->getMessage() : 'Cannot load or update the shop configuration.') . "\n");
    exit(1);
} catch (Throwable $error) {
    fwrite(STDERR, "PrestaShop setup: cannot parse or update the shop configuration.\n");
    exit(1);
}
