<?php
// Backup UI: every table of the project database and its extra schemas (DATABASE_EXTRA_SCHEMAS, one tab
// each) with its table registry line (project, profile default or none) and a suggestion; locally (ENV=local/test) it edits app/database/table-registry.txt, which the
// developer then commits. Servers only show it: there the registry changes through git.
// Reached only through nginx-proxy with the BACKUP_ALLOWED_IPS / ALLOWED_IPS allowlist.
// Self-contained on purpose: no web fonts, scripts or styles from elsewhere.
require __DIR__ . '/../registry.php';

$peer = isset($_SERVER['REMOTE_ADDR']) ? $_SERVER['REMOTE_ADDR'] : '';
$proxy = gethostbynamel(getenv('BACKUP_UI_PROXY') ?: 'nginx-proxy') ?: array();
if (!in_array($peer, array_merge(array('127.0.0.1', '::1'), $proxy), true)) {
    http_response_code(403);
    exit("Reach the backup UI through nginx-proxy.\n");
}

$environment = getenv('ENV') ?: 'local';
$directory = getenv('BACKUP_REGISTRY_DIRECTORY') ?: '/opt/setup/project-database';
$registryFile = $directory . '/table-registry.txt';
$present = is_dir($directory);
$editable = in_array($environment, array('local', 'test'), true) && $present && is_writable($directory);
$tokenFile = getenv('BACKUP_UI_TOKEN_FILE') ?: '/tmp/backup-ui-token';
if (!is_file($tokenFile)) file_put_contents($tokenFile, bin2hex(random_bytes(16)));
$token = trim(file_get_contents($tokenFile));

function h($value) { return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8'); }

function size($bytes)
{
    foreach (array('B', 'KB', 'MB', 'GB') as $unit) {
        if ($bytes < 1024 || $unit === 'GB') return ($unit === 'B' ? $bytes : number_format($bytes, $bytes < 10 ? 1 : 0, '.', '')) . ' ' . $unit;
        $bytes /= 1024;
    }
}

// The main database first, then the extra schemas; names are validated like setup does (letters, digits, _).
function databaseSchemas()
{
    $schemas = array((string) getenv('DATABASE_NAME'));
    foreach (preg_split('/\s+/', trim((string) getenv('DATABASE_EXTRA_SCHEMAS'))) as $name) {
        if (preg_match('/^[A-Za-z0-9_]{1,64}$/', $name) && !in_array($name, $schemas, true)) $schemas[] = $name;
    }
    return $schemas;
}

// The registry's name of a table: the main database's tables by name, an extra schema's as schema.table.
function registryName($schema, $table)
{
    return $schema === getenv('DATABASE_NAME') ? $table : $schema . '.' . $table;
}

// [schema => [registry name => [rows, bytes, columns, table]]], columns only for $selected (suggestions).
function databaseTables($schemas, $selected)
{
    $dsn = sprintf('mysql:host=%s;port=%s;dbname=%s;charset=utf8mb4', getenv('DATABASE_HOST') ?: 'database',
        getenv('DATABASE_PORT') ?: '3306', getenv('DATABASE_NAME'));
    $pdo = new PDO($dsn, getenv('DATABASE_USER'), getenv('DATABASE_PASSWORD'), array(PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION));
    $all = array_fill_keys($schemas, array());
    $listed = implode(',', array_map(function ($name) { return "'" . $name . "'"; }, $schemas));
    foreach ($pdo->query("SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, DATA_LENGTH + INDEX_LENGTH FROM information_schema.TABLES "
        . "WHERE TABLE_SCHEMA IN ($listed) AND TABLE_TYPE = 'BASE TABLE'") as $row) {
        if ($row[0] === $schemas[0] && in_array($row[1], TABLES_SETUP_TABLES, true)) continue;
        $all[$row[0]][registryName($row[0], $row[1])] = array((int) $row[2], (int) $row[3], array(), $row[1]);
    }
    $columns = $pdo->prepare('SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = ?');
    $columns->execute(array($selected));
    foreach ($columns as $row) {
        $name = registryName($selected, $row[0]);
        if (isset($all[$selected][$name])) $all[$selected][$name][2][] = $row[1];
    }
    foreach ($all as &$tables) ksort($tables);
    return $all;
}

$path = parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/', PHP_URL_PATH);
$method = isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : 'GET';
$schemas = databaseSchemas();
$asked = isset($_POST['schema']) ? (string) $_POST['schema'] : (isset($_GET['schema']) ? (string) $_GET['schema'] : '');
$schema = in_array($asked, $schemas, true) ? $asked : $schemas[0];
$view = isset($_GET['view']) ? (string) $_GET['view'] : 'unclassified';
$query = isset($_GET['q']) ? trim((string) $_GET['q']) : '';
// A click on a column header sorts by it; a second click reverses the order.
const SORTS = array('table', 'rows', 'size', 'source', 'rule', 'suggestion');
$sort = isset($_GET['sort']) && in_array($_GET['sort'], SORTS, true) ? (string) $_GET['sort'] : 'table';
$descending = isset($_GET['dir']) && $_GET['dir'] === 'desc';
$message = isset($_GET['saved']) ? (string) $_GET['saved'] : '';
$error = '';

try {
    $all = databaseTables($schemas, $schema);
    $tables = $all[$schema];
    $registry = tablesLoad($registryFile);
} catch (Exception $exception) {
    $all = array();
    $tables = array();
    $registry = array('project' => array(), 'profile' => array());
    $error = $exception instanceof PDOException ? 'Cannot read the database tables; check the database service and credentials.' : $exception->getMessage();
}

if ($method === 'POST') {
    if (!hash_equals($token, isset($_POST['token']) ? (string) $_POST['token'] : '')) {
        http_response_code(400);
        exit("The page expired; reload it.\n");
    }
    if (!$editable) {
        http_response_code(403);
        exit("The table registry changes through git on $environment.\n");
    }
    $changes = array();
    if ($path === '/accept-all') {
        foreach ($tables as $name => $table) {
            if (tablesMatch($registry, $name) === null) $changes[$name] = tablesGuess($name, $table[2]);
        }
    } else {
        $name = isset($_POST['table']) ? (string) $_POST['table'] : '';
        if (!isset($tables[$name])) { http_response_code(404); exit("Unknown table.\n"); }
        if ($path === '/remove') {
            $changes[$name] = null;
        } else {
            $base = isset($_POST['base']) ? (string) $_POST['base'] : '';
            $rules = $base === 'drop' ? array('drop', 'drop', '-')
                : array(trim(isset($_POST['group']) ? (string) $_POST['group'] : ''), $base, isset($_POST['live']) ? (string) $_POST['live'] : '');
            if (!tablesValidRule($rules[0], $rules[1], $rules[2])) { http_response_code(400); exit("Expected a group, a base rule and a LIVE rule.\n"); }
            $changes[$name] = $rules;
        }
    }
    try {
        if ($changes) tablesWrite($registryFile, $changes);
    } catch (Exception $exception) {
        http_response_code(500);
        exit(h($exception->getMessage()) . "\n");
    }
    $back = isset($_POST['back']) ? (string) $_POST['back'] : '/';
    if (!preg_match('#^/(\?[A-Za-z0-9_=&%.+-]*)?$#', $back)) $back = '/';
    header('Location: ' . $back . (strpos($back, '?') === false ? '?' : '&') . 'saved=' . count($changes), true, 303);
    exit;
}

// Tables without a registry line, per schema, for the tabs.
$missing = array();
foreach ($all as $name => $list) {
    $missing[$name] = 0;
    foreach ($list as $registered => $table) if (tablesMatch($registry, $registered) === null) $missing[$name]++;
}

$rows = array();
$counts = array('all' => 0, 'unclassified' => 0, 'project' => 0, 'profile' => 0, 'drop' => 0);
$bytes = 0;
foreach ($tables as $name => $table) {
    $match = tablesMatch($registry, $name);
    $source = $match ? $match[0] : 'unclassified';
    $counts['all']++;
    $counts[$source]++;
    if ($match && $match[3] === 'drop') $counts['drop']++;
    $bytes += $table[1];
    if ($view !== 'all' && $view !== $source) continue;
    if ($query !== '' && stripos($table[3], $query) === false) continue;
    $rows[] = array($name, $table, $match, tablesGuess($name, $table[2]));
}

$sortKey = function ($row) use ($sort) {
    list($name, $table, $match, $guess) = $row;
    switch ($sort) {
        case 'rows': return $table[0];
        case 'size': return $table[1];
        case 'source': return $match ? $match[0] : '';
        case 'rule': return $match ? implode(' ', array($match[2], $match[3], $match[4])) : '';
        case 'suggestion': return $match ? '' : implode(' ', $guess);
    }
    return $table[3];
};
usort($rows, function ($a, $b) use ($sortKey, $descending) {
    $x = $sortKey($a);
    $y = $sortKey($b);
    $order = is_int($x) ? ($x > $y) - ($x < $y) : strcmp($x, $y);
    if ($order === 0) $order = strcmp($a[0], $b[0]);  // ties by table name, always ascending
    return $descending ? -$order : $order;
});

// What every column and value means, shown as tooltips and in the help panel.
const HELP_BASE = array(
    'full' => 'Every row is copied into the dev data set. For settings and small lookup tables.',
    'slice' => 'Only the rows of the product sample (2000 active products by default, database/data/recipe.txt). For catalogue tables with id_product.',
    'faker' => 'Every row, with personal data (names, e-mails, phones, addresses) replaced by fixed dev values. For customers, orders, carts.',
    'empty' => 'Structure only, no rows. For caches, logs, statistics and search indexes.',
    'drop' => 'The table is not wanted: left out of the schema snapshot and removed with a guarded DROP migration (LIVE does not apply). For leftovers (*_bak_*, tmp_*, old copies).',
);
const HELP_LIVE = array(
    'import' => 'The rows of a LIVE dump are loaded (make db-import) and stage/prod backups keep them.',
    'skip' => 'Only the table is created, without its LIVE rows; stage/prod backups keep its structure only. For data that rebuilds itself or is not needed.',
);
const HELP_SOURCE = array(
    'project' => 'A line in app/database/table-registry.txt: change it here, or Remove it.',
    'profile' => "setup's default for a standard PrestaShop table; Save writes a project line that overrides it.",
    'unclassified' => 'No rule yet: it stops commits, make db-import and make db-dataset until you set one.',
);

$style = <<<'CSS'
:root{--bg:#0c0e0b;--panel:#13170f;--rule:#283022;--ink:#ece6d3;--dim:#8d917f;--faint:#5c6152;--amber:#ffb22e;--go:#4fe08c;--stop:#ff5b4f;--steel:#6f93c9;
--mono:ui-monospace,"JetBrains Mono","IBM Plex Mono","DejaVu Sans Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--amber);text-decoration:none}a:hover{text-decoration:underline}
.top{display:flex;gap:18px;align-items:center;flex-wrap:wrap;padding:14px 28px;border-bottom:1px solid var(--rule);background:#11150d}
.mark{font-weight:800;letter-spacing:.32em;color:var(--amber)}.mark small{display:block;font-size:10px;letter-spacing:.18em;color:var(--dim);font-weight:400}
.sign{padding:4px 12px;font-weight:800;letter-spacing:.24em;text-transform:uppercase;font-size:12px;color:#fff;border-radius:3px;background:var(--steel)}
.sign.stage{background:#b8740f}.sign.prod{background:#b3261e}.spacer{flex:1}
main{padding:22px 28px 60px;max-width:1500px;margin:0 auto}
.callout{border:1px solid var(--rule);border-left:4px solid var(--go);background:var(--panel);padding:10px 14px;margin:0 0 16px;border-radius:3px}
.callout.error{border-left-color:var(--stop)}.callout.wait{border-left-color:var(--amber)}
.stats{display:flex;gap:28px;flex-wrap:wrap;margin:0 0 18px}.stat b{display:block;font-size:22px}.stat span{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint)}
.stat.bad b{color:var(--stop)}.bad{color:var(--stop)}
.bar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 14px}
.btn,button,input,select{font:inherit;color:var(--ink);background:#191e14;border:1px solid var(--rule);padding:5px 10px;border-radius:3px}
.btn,button{cursor:pointer;text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.06em}
.btn.on{color:var(--amber);border-color:var(--amber)}button:hover,.btn:hover{border-color:var(--amber);color:var(--amber);text-decoration:none}
button.go{background:var(--amber);border-color:var(--amber);color:#1a1205}
table{width:100%;border-collapse:separate;border-spacing:0 4px}
th{text-align:left;font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint);font-weight:600;padding:0 10px}
th a{color:inherit}th a:hover{color:var(--amber);text-decoration:none}th a.on{color:var(--amber)}
td{background:var(--panel);padding:8px 10px;vertical-align:middle;border-top:1px solid var(--rule)}
td:first-child{border-left:3px solid var(--go);border-radius:4px 0 0 4px;word-break:break-all}
tr.unclassified td:first-child{border-left-color:var(--stop)}tr.drop td:first-child{border-left-color:var(--faint)}
td.num{text-align:right;color:var(--dim);white-space:nowrap}
.chip{display:inline-block;padding:1px 7px;border-radius:2px;font-size:10px;letter-spacing:.14em;text-transform:uppercase;font-weight:700;border:1px solid var(--rule);color:var(--dim)}
.chip.project{color:var(--go);border-color:rgba(79,224,140,.4)}.chip.unclassified{color:var(--stop);border-color:rgba(255,91,79,.45)}
.rule{color:var(--amber);white-space:nowrap}.hint{color:var(--dim);white-space:nowrap}
details.help{border:1px solid var(--rule);border-left:4px solid var(--steel);background:var(--panel);border-radius:3px;padding:10px 14px;margin:0 0 16px}
details.help summary{cursor:pointer;color:var(--amber);font-weight:700}details.help p{margin:8px 0}
.help table{border-spacing:0 2px;margin:6px 0}.help td{background:none;border:none;padding:3px 10px 3px 0;vertical-align:top}.help td:first-child{border:none;white-space:nowrap;color:var(--amber)}
th[title],[title]{cursor:help}
form.edit{display:flex;gap:4px;align-items:center;margin:0;flex-wrap:nowrap}form.edit input[name=group]{width:110px}
form.inline{display:inline;margin:0}
.empty{padding:40px;text-align:center;color:var(--dim);border:1px dashed var(--rule);border-radius:4px}
CSS;

header('Content-Type: text/html; charset=utf-8');
$sign = in_array($environment, array('stage', 'prod'), true) ? $environment : '';
echo '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    . '<title>Backup · ' . h(getenv('PROJECT_NAME')) . '</title><style>' . $style . '</style></head><body>'
    . '<div class="top"><div class="mark">BACKUP<small>' . h(getenv('PROJECT_NAME')) . ' · ' . h($schema) . '</small></div>'
    . '<span class="sign ' . h($sign) . '">' . h($environment) . '</span><span class="spacer"></span></div><main>';

if ($error !== '') echo '<div class="callout error">' . h($error) . '</div>';
if ($message !== '') echo '<div class="callout">Saved ' . h($message) . ' line(s) to app/database/table-registry.txt. Review and commit it.</div>';
if (!$present) {
    echo '<div class="callout wait">No app/database directory yet: run <code>make schema-export</code> first; the registry lives beside the schema.</div>';
} elseif (!$editable) {
    echo '<div class="callout wait">Read-only on ' . h($environment) . ': the table registry changes through git.</div>';
}

$valueRows = function ($help) {
    $html = '';
    foreach ($help as $value => $text) $html .= '<tr><td>' . h($value) . '</td><td>' . h($text) . '</td></tr>';
    return $html;
};
// Open while there is something to decide; one click away once every table has a rule.
echo '<details class="help"' . ($counts['unclassified'] ? ' open' : '') . '><summary>How the table registry is used</summary>'
    . '<p>For every table of the database, <code>app/database/table-registry.txt</code> says what happens to its data in three situations. '
    . 'Every table needs a rule: one without stops commits, <code>make db-import</code> and <code>make db-dataset</code>. '
    . 'A rule is three fields, set in the last column:</p><table>'
    . '<tr><td>group</td><td>A free label, e.g. catalog, customers, orders, config, cache. <code>make db-dataset</code> writes one file per group '
    . '(database/data/dev/&lt;group&gt;.sql); it does not change what happens to the data.</td></tr>'
    . '<tr><td>data set</td><td>Used by <code>make db-dataset</code> (builds the small anonymised dev data set from this local LIVE copy; it is committed) '
    . 'and <code>make db-load set=dev</code> (loads it).</td></tr>'
    . '<tr><td>LIVE</td><td>Used by <code>make db-import</code> of a LIVE dump and by stage/prod backups (<code>make db-backup</code>, the backup before a deploy).</td></tr>'
    . '</table><p>Data set values:</p><table>' . $valueRows(HELP_BASE) . '</table><p>LIVE values:</p><table>' . $valueRows(HELP_LIVE) . '</table>'
    . '<p>Source: <b>project</b> – ' . h(HELP_SOURCE['project']) . ' <b>profile</b> – ' . h(HELP_SOURCE['profile']) . ' <b>none</b> – ' . h(HELP_SOURCE['unclassified']) . '</p>'
    . '<p>Suggestion: a guess from the name and columns – cache/_log/_index in the name: cache empty skip; id_order or id_cart: orders faker import; '
    . 'id_customer or email: customers faker import; id_product: catalog slice import; otherwise config full import. Check the large tables (sort by Size).</p>'
    . '<p>Add/Save writes the line, Remove deletes the project line, Accept all suggestions writes the suggestion of every table of this schema without a rule. '
    . 'Then review and commit the file. The counts are the base tables of the schema (setup_migrations excluded): profile defaults cover the standard PrestaShop '
    . 'tables, the rest belong to modules and the project.</p></details>';

$at = '&schema=' . rawurlencode($schema);
$hidden = '<input type="hidden" name="schema" value="' . h($schema) . '">';
if (count($schemas) > 1) {
    // One tab per schema: the main database first, then DATABASE_EXTRA_SCHEMAS (schema.table in the registry).
    echo '<div class="bar">';
    foreach ($schemas as $index => $name) {
        echo '<a class="btn' . ($name === $schema ? ' on' : '') . '" href="/?schema=' . rawurlencode($name) . '&view=' . rawurlencode($view) . '">'
            . h($name) . ($index === 0 ? ' (main)' : '') . (empty($missing[$name]) ? '' : ' · <span class="bad">' . $missing[$name] . '</span>') . '</a>';
    }
    echo '</div>';
}

echo '<div class="stats">'
    . '<div class="stat"><b>' . $counts['all'] . '</b><span>tables</span></div>'
    . '<div class="stat' . ($counts['unclassified'] ? ' bad' : '') . '"><b>' . $counts['unclassified'] . '</b><span>not in the registry</span></div>'
    . '<div class="stat"><b>' . $counts['project'] . '</b><span>project lines</span></div>'
    . '<div class="stat"><b>' . $counts['profile'] . '</b><span>profile defaults</span></div>'
    . '<div class="stat"><b>' . $counts['drop'] . '</b><span>marked drop</span></div>'
    . '<div class="stat"><b>' . h(size($bytes)) . '</b><span>data + index</span></div></div>';

echo '<div class="bar">';
foreach (array('unclassified' => 'Not in the registry', 'project' => 'Project lines', 'profile' => 'Profile defaults', 'all' => 'All') as $key => $label) {
    echo '<a class="btn' . ($view === $key ? ' on' : '') . '" href="/?view=' . $key . $at . ($query !== '' ? '&q=' . rawurlencode($query) : '')
        . '&sort=' . $sort . ($descending ? '&dir=desc' : '') . '">' . h($label) . ' (' . $counts[$key] . ')</a>';
}
echo '<form method="get" class="inline">' . $hidden . '<input type="hidden" name="view" value="' . h($view) . '"><input type="hidden" name="sort" value="' . h($sort) . '">'
    . ($descending ? '<input type="hidden" name="dir" value="desc">' : '') . '<input name="q" value="' . h($query) . '" placeholder="filter by name"> <button>Filter</button></form>';
if ($editable && $counts['unclassified']) {
    echo '<span class="spacer"></span><form method="post" action="/accept-all" class="inline" onsubmit="return confirm(\'Write the suggestion of all '
        . $counts['unclassified'] . ' tables of ' . h($schema) . ' not in the registry?\')"><input type="hidden" name="token" value="' . h($token) . '">'
        . $hidden . '<input type="hidden" name="back" value="/?view=' . h($view) . h($at) . '"><button class="go">Accept all suggestions</button></form>';
}
echo '</div>';

$list = '/?view=' . rawurlencode($view) . $at . ($query !== '' ? '&q=' . rawurlencode($query) : '');
$back = $list . '&sort=' . $sort . ($descending ? '&dir=desc' : '');
$header = function ($column, $label, $title = '') use ($list, $sort, $descending) {
    $reverse = $column === $sort && !$descending;
    $arrow = $column === $sort ? ($descending ? ' ▼' : ' ▲') : '';
    return '<th' . ($title !== '' ? ' title="' . h($title) . '"' : '') . '><a' . ($column === $sort ? ' class="on"' : '') . ' href="' . h($list . '&sort=' . $column . ($reverse ? '&dir=desc' : '')) . '">'
        . h($label) . $arrow . '</a></th>';
};
if (!$rows) {
    echo '<div class="empty">' . ($view === 'unclassified' && $counts['all'] ? 'Every table is in the registry.' : 'No tables.') . '</div>';
} else {
    echo '<table><thead><tr>' . $header('table', 'Table') . $header('rows', 'Rows', 'Approximate row count (information_schema)')
        . $header('size', 'Size', 'Data + index on disk') . $header('source', 'Source', 'Where the rule comes from: project line, PrestaShop profile default or none')
        . $header('rule', 'Rule (group · data set · LIVE)', 'The rule in force: group, what the dev data set keeps, whether LIVE data is imported and backed up')
        . $header('suggestion', 'Suggestion', 'A guess from the table name and columns for a table without a rule')
        . ($editable ? '<th title="group: a free label · data set: rows kept by make db-dataset · LIVE: data imported and kept in server backups">Set: group · data set · LIVE</th>' : '')
        . '</tr></thead><tbody>';
    foreach ($rows as $row) {
        list($name, $table, $match, $guess) = $row;
        $source = $match ? $match[0] : 'unclassified';
        $rules = $match ? array($match[2], $match[3], $match[4]) : $guess;
        $class = $match ? ($match[3] === 'drop' ? 'drop' : '') : 'unclassified';
        echo '<tr class="' . $class . '"><td>' . h($table[3]) . '</td><td class="num">' . number_format($table[0], 0, '.', ' ') . '</td><td class="num">' . h(size($table[1])) . '</td>'
            . '<td><span class="chip ' . h($source) . '" title="' . h(HELP_SOURCE[$source]) . '">' . h($source === 'unclassified' ? 'none' : $source) . '</span>'
            . ($match && $match[1] !== $name ? '<br><span class="hint">' . h($match[1]) . '</span>' : '') . '</td>'
            . '<td class="rule">' . ($match ? h(implode(' ', $rules)) : '—') . '</td>'
            . '<td class="hint">' . ($match ? '' : h(implode(' ', $guess))) . '</td>';
        if ($editable) {
            echo '<td><form method="post" action="/set" class="edit"><input type="hidden" name="token" value="' . h($token) . '">' . $hidden
                . '<input type="hidden" name="table" value="' . h($name) . '"><input type="hidden" name="back" value="' . h($back) . '">'
                . '<input name="group" value="' . h($rules[0]) . '" aria-label="group" title="group: a free label; make db-dataset writes one file per group">'
                . '<select name="base" aria-label="data set rule" title="data set: what make db-dataset keeps of this table">';
            foreach (TABLES_BASE_RULES as $option) echo '<option title="' . h(HELP_BASE[$option]) . '"' . ($option === $rules[1] ? ' selected' : '') . '>' . $option . '</option>';
            echo '</select><select name="live" aria-label="LIVE rule" title="LIVE: whether make db-import loads its data and stage/prod backups keep it">';
            foreach (array('import', 'skip') as $option) echo '<option title="' . h(HELP_LIVE[$option]) . '"' . ($option === $rules[2] ? ' selected' : '') . '>' . $option . '</option>';
            echo '</select><button>' . ($match ? 'Save' : 'Add') . '</button></form>';
            if ($match && $match[0] === 'project' && $match[1] === $name) {
                echo '<form method="post" action="/remove" class="inline"><input type="hidden" name="token" value="' . h($token) . '">' . $hidden
                    . '<input type="hidden" name="table" value="' . h($name) . '"><input type="hidden" name="back" value="' . h($back) . '">'
                    . '<button title="Remove the project line">Remove</button></form>';
            }
            echo '</td>';
        }
        echo '</tr>';
    }
    echo '</tbody></table>';
}
echo '</main></body></html>';
