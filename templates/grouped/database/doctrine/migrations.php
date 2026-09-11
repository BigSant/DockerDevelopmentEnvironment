<?php

declare(strict_types=1);

return [
    'table_storage' => ['table_name' => 'doctrine_migration_versions'],
    'migrations_paths' => ['DoctrineMigrations' => __DIR__ . '/versions'],
    // MySQL DDL commits implicitly; schema migrations below are non-transactional.
    'all_or_nothing' => false,
    'transactional' => false,
    'check_database_platform' => true,
];
