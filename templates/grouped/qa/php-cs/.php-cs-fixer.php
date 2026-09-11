<?php

$finder = PhpCsFixer\Finder::create()
    ->in('/var/www/html');

return (new PhpCsFixer\Config())
    ->setRules([
        '@PSR12' => true,
        '@PHP80Migration' => true,
    ])
    ->setFinder($finder)
    ->setCacheFile('/tmp/php-cs-fixer/cache/.php-cs-fixer.cache');
