<?php
// The table registry as docker/database_registry.py reads it, for the backup UI: the project registry
// (app/database/table-registry.txt) first, then setup's profile defaults; the first matching line wins.
// Lines: `table group base live`, table with * wildcards and {p} for the PrestaShop table prefix. A table of an
// extra schema is schema.table: a line with a dot is about the extra schemas only, one without about the main database.

const TABLES_BASE_RULES = array('full', 'slice', 'faker', 'empty', 'drop');
const TABLES_LIVE_RULES = array('import', 'skip', '-');
// Setup's own bookkeeping (applied migrations) needs no line.
const TABLES_SETUP_TABLES = array('setup_migrations');
const TABLES_HEADER = "# Project table registry: table  group  base-rule(full|slice|faker|empty|drop)  live-import(import|skip)\n"
    . "# Read before the profile defaults; every table must be classified (make schema-resolve).\n"
    . "# A table of an extra schema (DATABASE_EXTRA_SCHEMAS) is schema.table.\n";

// The PrestaShop table prefix behind {p}; null for a generic project, whose tables are named in full.
function tablesPrefix()
{
    $profile = getenv('PROFILE') ?: '';
    if (!in_array($profile, array('ps', 'prestashop'), true)) return null;
    return getenv('PS_DATABASE_PREFIX') ?: 'ps_';
}

// [[pattern, group, base, live, line number], ...]; a bad line is an exception naming file and line.
function tablesParse($text, $source, $prefix)
{
    $entries = array();
    foreach (preg_split('/\r?\n/', $text) as $index => $raw) {
        $line = trim(explode('#', $raw, 2)[0]);
        if ($line === '') continue;
        $fields = preg_split('/\s+/', $line);
        if (count($fields) !== 4 || !in_array($fields[2], TABLES_BASE_RULES, true) || !in_array($fields[3], TABLES_LIVE_RULES, true)) {
            throw new RuntimeException($source . ':' . ($index + 1) . ': expected "table group base live"');
        }
        if (strpos($fields[0], '{p}') !== false) {
            if ($prefix === null) throw new RuntimeException($source . ':' . ($index + 1) . ': {p} belongs to PrestaShop');
            $fields[0] = str_replace('{p}', $prefix, $fields[0]);
        }
        $entries[] = array($fields[0], $fields[1], $fields[2], $fields[3], $index + 1);
    }
    return $entries;
}

function tablesProfileFile()
{
    $profile = getenv('PROFILE') ?: '';
    if ($profile === '') return null;
    if ($profile === 'ps') $profile = 'prestashop';
    $file = (getenv('BACKUP_PROFILE_DIRECTORY') ?: '/opt/setup/database-profile') . '/' . basename($profile) . '/table-registry.txt';
    return is_file($file) ? $file : null;
}

// ['project' => entries, 'profile' => entries]
function tablesLoad($projectFile)
{
    $prefix = tablesPrefix();
    $project = is_file($projectFile) ? tablesParse((string) file_get_contents($projectFile), basename($projectFile), $prefix) : array();
    $profileFile = tablesProfileFile();
    $profile = $profileFile ? tablesParse((string) file_get_contents($profileFile), 'profile registry', $prefix) : array();
    return array('project' => $project, 'profile' => $profile);
}

// [source, pattern, group, base, live] of the first matching line, or null when the table has none.
function tablesMatch($registry, $table)
{
    foreach (array('project', 'profile') as $source) {
        foreach ($registry[$source] as $entry) {
            if ((strpos($entry[0], '.') !== false) !== (strpos($table, '.') !== false)) continue;
            if (fnmatch($entry[0], $table)) return array($source, $entry[0], $entry[1], $entry[2], $entry[3]);
        }
    }
    return null;
}

// A starting classification for a table without a line (database_registry.guess); the person decides.
function tablesGuess($table, $columns)
{
    if (preg_match('/(^tmp_|^zz_|_bak_|_backup|_old$|_copy$)/', $table)) return array('review', 'empty', 'skip');
    if (preg_match('/(cache|_log$|_logs$|session|_stats?$|_index$|api_response)/', $table)) return array('cache', 'empty', 'skip');
    if (in_array('id_order', $columns, true) || in_array('id_cart', $columns, true)) return array('orders', 'faker', 'import');
    if (in_array('id_customer', $columns, true) || in_array('email', $columns, true)) return array('customers', 'faker', 'import');
    if (in_array('id_product', $columns, true)) return array('catalog', 'slice', 'import');
    return array('config', 'full', 'import');
}

function tablesValidRule($group, $base, $live)
{
    return preg_match('/^[A-Za-z0-9_.-]{1,40}$/', $group) && in_array($base, TABLES_BASE_RULES, true)
        && in_array($live, TABLES_LIVE_RULES, true) && (($base === 'drop') === ($live === '-'));
}

// The registry name of a table: {p}rest for a PrestaShop table, so the line survives another prefix.
function tablesRegistryName($table)
{
    $prefix = tablesPrefix();
    if (strpos($table, '.') !== false) return $table;  // an extra schema's table: schema.table, never {p}
    return ($prefix !== null && strpos($table, $prefix) === 0) ? '{p}' . substr($table, strlen($prefix)) : $table;
}

function tablesLine($table, $group, $base, $live)
{
    return sprintf("%-48s %-12s %-6s %s", tablesRegistryName($table), $group, $base, $live);
}

// The project registry with this table's own line set (rules) or removed (rules null). The table's line
// is the one naming exactly it; a new line goes before every other line, so it wins over wildcards.
function tablesWrite($projectFile, $changes)
{
    $prefix = tablesPrefix();
    $lines = is_file($projectFile) ? preg_split('/\r?\n/', rtrim((string) file_get_contents($projectFile), "\n")) : explode("\n", rtrim(TABLES_HEADER, "\n"));
    $own = function ($line) use ($prefix) {
        $fields = preg_split('/\s+/', trim(explode('#', $line, 2)[0]));
        if (count($fields) !== 4) return null;
        return $prefix === null ? $fields[0] : str_replace('{p}', $prefix, $fields[0]);
    };
    $first = count($lines);
    foreach ($lines as $index => $line) {
        if ($own($line) !== null) { $first = $index; break; }
    }
    $added = array();
    foreach ($changes as $table => $rules) {
        $found = false;
        foreach ($lines as $index => $line) {
            if ($own($line) !== $table) continue;
            if ($rules === null) { unset($lines[$index]); } else { $lines[$index] = tablesLine($table, $rules[0], $rules[1], $rules[2]); }
            $found = true;
        }
        if (!$found && $rules !== null) $added[] = tablesLine($table, $rules[0], $rules[1], $rules[2]);
    }
    $lines = array_values($lines);
    if ($added) {
        $first = min($first, count($lines));
        array_splice($lines, $first, 0, $added);
    }
    $text = implode("\n", $lines) . "\n";
    tablesParse($text, basename($projectFile), $prefix);  // never write a registry setup cannot read
    $temporary = $projectFile . '.tmp-' . bin2hex(random_bytes(4));
    if (file_put_contents($temporary, $text) === false || !rename($temporary, $projectFile)) {
        @unlink($temporary);
        throw new RuntimeException('Cannot write ' . basename($projectFile));
    }
}
