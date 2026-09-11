<?php

declare(strict_types=1);

return [
    'driver' => 'pdo_mysql',
    'host' => getenv('DATABASE_HOST') ?: 'database',
    'user' => getenv('DATABASE_USER'),
    'password' => getenv('DATABASE_PASSWORD'),
    'dbname' => getenv('DATABASE_NAME'),
    'charset' => 'utf8mb4',
];
