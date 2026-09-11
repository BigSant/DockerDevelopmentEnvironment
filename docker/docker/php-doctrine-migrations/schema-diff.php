<?php

declare(strict_types=1);

// This process can reach only the disposable database container, never the project DB.
require '/opt/doctrine/vendor/autoload.php';

use Doctrine\DBAL\DriverManager;
use Doctrine\DBAL\Connection;

function definitions(Connection $connection): array
{
    $result = [];
    $connection->executeStatement("SET SESSION sql_mode = ''");
    foreach ($connection->createSchemaManager()->listTableNames() as $name) {
        $row = $connection->fetchNumeric('SHOW CREATE TABLE ' . $connection->quoteIdentifier($name));
        $result[$name] = preg_replace('/(?m)^(\) ENGINE=\S+) AUTO_INCREMENT=\d+\b/', '$1', $row[1]);
    }
    ksort($result);
    return $result;
}

$phase = 'reading input';
try {
    $input = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
    $configuration = require '/input/config/migrations.php';
    $paths = $configuration['migrations_paths'] ?? [];
    if (count($paths) !== 1) {
        throw new RuntimeException('Configure exactly one migration namespace for automatic generation.');
    }
    $namespace = array_key_first($paths);
    if (!preg_match('/^[A-Za-z_][A-Za-z0-9_]*(?:\\\\[A-Za-z_][A-Za-z0-9_]*)*$/D', $namespace)) {
        throw new RuntimeException('Invalid migration namespace.');
    }
    $path = $paths[$namespace];
    if (!str_starts_with($path, '/input/config/') || str_contains($path, '..')) {
        throw new RuntimeException('Migration files must be inside the Doctrine configuration directory.');
    }
    $relative = substr($path, strlen('/input/config/'));
    $metadata = $configuration['table_storage']['table_name'] ?? 'doctrine_migration_versions';
    foreach (['before', 'after'] as $side) {
        unset($input[$side][$metadata]);
    }
    foreach (['charset', 'collation'] as $key) {
        if (!preg_match('/^[a-zA-Z0-9_]+$/D', $input[$key])) {
            throw new RuntimeException('Invalid database character settings.');
        }
    }
    if (!preg_match('/^Version[0-9]+$/D', $input['class']) || !preg_match('/^[0-9a-f]{64}$/D', $input['fingerprint'])) {
        throw new RuntimeException('Invalid generation identifier.');
    }
    $params = ['driver' => 'pdo_mysql', 'host' => '127.0.0.1', 'user' => 'root',
               'password' => getenv('MYSQL_ROOT_PASSWORD'), 'charset' => 'utf8mb4'];
    $phase = 'waiting for the disposable database';
    $deadline = microtime(true) + 90;
    while (true) {
        try {
            $admin = DriverManager::getConnection($params);
            $admin->executeQuery('SELECT 1');
            break;
        } catch (Throwable $error) {
            if (microtime(true) >= $deadline) {
                throw $error;
            }
            usleep(500000);
        }
    }
    $connections = [];
    foreach (['before', 'after'] as $side) {
        $phase = 'restoring ' . $side . ' schema';
        $database = 'setup_diff_' . $side;
        $admin->executeStatement('CREATE DATABASE ' . $database . ' CHARACTER SET ' . $input['charset'] . ' COLLATE ' . $input['collation']);
        $connection = DriverManager::getConnection($params + ['dbname' => $database]);
        $connection->executeStatement("SET SESSION sql_mode = ''");
        $connection->executeStatement('SET FOREIGN_KEY_CHECKS=0');
        foreach ($input[$side] as $sql) {
            $connection->executeStatement($sql);
        }
        $connection->executeStatement('SET FOREIGN_KEY_CHECKS=1');
        $connections[$side] = $connection;
    }
    $before = $connections['before'];
    $after = $connections['after'];
    $expected = definitions($after);
    $phase = 'comparing schemas with Doctrine DBAL';
    $manager = $before->createSchemaManager();
    $comparator = $manager->createComparator();
    $platform = $before->getDatabasePlatform();
    $diff = $comparator->compareSchemas($manager->introspectSchema(), $after->createSchemaManager()->introspectSchema());
    $sql = $platform->getAlterSchemaSQL($diff);
    // Never silently omit changes that DBAL cannot represent (engine/options/types etc.).
    $phase = 'verifying generated SQL against the target schema';
    foreach ($sql as $statement) {
        $before->executeStatement($statement);
    }
    if (definitions($before) !== $expected) {
        throw new RuntimeException('The generated SQL does not reproduce the target schema; write this migration manually.');
    }
    if (!$sql) {
        echo json_encode(['sql_count' => 0, 'directory' => $relative], JSON_THROW_ON_ERROR);
        exit(0);
    }
    $platformClass = '\\' . get_class($platform);
    $body = '        $this->abortIf(get_class($this->connection->getDatabasePlatform()) !== ' .
        $platformClass . "::class, 'Migration requires the database platform used for generation.');\n";
    foreach ($sql as $statement) {
        $body .= '        $this->addSql(' . var_export($statement, true) . ");\n";
    }
    $class = $input['class'];
    $fingerprint = $input['fingerprint'];
    $code = <<<CODE
<?php

declare(strict_types=1);

namespace {$namespace};

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

// Schema diff: {$fingerprint}
// Generated from a Git schema and the local database. Review SQL and data effects.
final class {$class} extends AbstractMigration
{
    public function getDescription(): string
    {
        return 'Apply reviewed schema changes';
    }

    public function isTransactional(): bool
    {
        return false; // MySQL/MariaDB DDL can commit implicitly.
    }

    public function up(Schema \$schema): void
    {
{$body}    }

    public function down(Schema \$schema): void
    {
        \$this->throwIrreversibleMigrationException('Write and test a data-safe rollback explicitly.');
    }
}

CODE;
    echo json_encode(['sql_count' => count($sql), 'directory' => $relative, 'code' => $code], JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    // DBAL diagnostics can contain full DDL, including private defaults. Keep them out of logs.
    fwrite(STDERR, 'Schema diff failed while ' . $phase . '. Check schema compatibility and Doctrine configuration; no project DB was modified.' . PHP_EOL);
    exit(1);
}
