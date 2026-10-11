<?php
// Backup UI: every table of the project database and its extra schemas (DATABASE_EXTRA_SCHEMAS, one tab
// each) that the project classifies: its registry line or none yet, with a suggestion. Standard PrestaShop tables
// follow setup's profile for every project and are not listed (they change in setup); locally (ENV=local/test) it edits app/database/table-registry.txt, which the
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
$view = isset($_GET['view']) && in_array($_GET['view'], array('unclassified', 'project', 'all'), true) ? (string) $_GET['view'] : 'unclassified';
$query = isset($_GET['q']) ? trim((string) $_GET['q']) : '';
// A click on a column header sorts by it; a second click reverses the order.
const SORTS = array('table', 'rows', 'size', 'source', 'group', 'base', 'live');
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
        $current = tablesMatch($registry, $name);
        if ($current && $current[0] === 'profile') {
            http_response_code(403);
            exit("A standard PrestaShop table follows setup's profile; change it in setup.\n");
        }
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
    // Standard PrestaShop tables follow setup's profile: counted, not listed.
    if ($source === 'profile') { $counts['profile']++; continue; }
    $counts['all']++;
    $counts[$source]++;
    if ($match && $match[3] === 'drop') $counts['drop']++;
    $bytes += $table[1];
    if ($view !== 'all' && $view !== $source) continue;
    if ($query !== '' && stripos($table[3], $query) === false) continue;
    $rows[] = array($name, $table, $match, tablesGuess($name, $table[2]));
}

// The rule a row shows: its registry line, or the suggestion while it has none.
$ruleOf = function ($row) {
    return $row[2] ? array($row[2][2], $row[2][3], $row[2][4]) : $row[3];
};
$sortKey = function ($row) use ($sort, $ruleOf) {
    $rule = $ruleOf($row);
    switch ($sort) {
        case 'rows': return $row[1][0];
        case 'size': return $row[1][1];
        case 'source': return $row[2] ? $row[2][0] : '';
        case 'group': return $rule[0];
        case 'base': return $rule[1];
        case 'live': return $rule[2];
    }
    return $row[1][3];
};
usort($rows, function ($a, $b) use ($sortKey, $descending) {
    $x = $sortKey($a);
    $y = $sortKey($b);
    $order = is_int($x) ? ($x > $y) - ($x < $y) : strcmp($x, $y);
    if ($order === 0) $order = strcmp($a[0], $b[0]);  // ties by table name, always ascending
    return $descending ? -$order : $order;
});

$style = <<<'CSS'
:root{--bg:#0c0e0b;--panel:#13170f;--rule:#283022;--line:#1c2217;--ink:#ece6d3;--dim:#8d917f;--faint:#5c6152;--amber:#ffb22e;--go:#4fe08c;--stop:#ff5b4f;--steel:#6f93c9;
--mono:ui-monospace,"JetBrains Mono","IBM Plex Mono","DejaVu Sans Mono",Menlo,Consolas,monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.5 var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--amber);text-decoration:none}code{color:var(--ink)}
.top{display:flex;gap:18px;align-items:center;flex-wrap:wrap;padding:14px 28px;border-bottom:1px solid var(--rule);background:#11150d}
.mark{font-weight:800;letter-spacing:.32em;color:var(--amber)}.mark small{display:block;font-size:10px;letter-spacing:.18em;color:var(--dim);font-weight:400}
.sign{padding:4px 12px;font-weight:800;letter-spacing:.24em;text-transform:uppercase;font-size:12px;color:#fff;border-radius:3px;background:var(--steel)}
.sign.stage{background:#b8740f}.sign.prod{background:#b3261e}.spacer{flex:1}
main{padding:22px 28px 60px;max-width:1400px;margin:0 auto}
.callout{border:1px solid var(--rule);border-left:4px solid var(--go);background:var(--panel);padding:10px 14px;margin:0 0 16px;border-radius:3px}
.callout.error{border-left-color:var(--stop)}.callout.wait{border-left-color:var(--amber)}
.help{border:1px solid var(--rule);border-left:4px solid var(--steel);background:var(--panel);border-radius:3px;padding:12px 16px;margin:0 0 18px}
.help p{margin:0 0 10px}.help .note{margin:10px 0 0;color:var(--dim)}
.help .cols{display:grid;grid-template-columns:1fr 1.6fr 1.2fr;gap:18px}
.help h3{margin:0 0 4px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--amber)}
.help h3 span{color:var(--faint);letter-spacing:0;text-transform:none;font-weight:400}
.help dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;margin:0}.help dt{color:var(--amber)}.help dd{margin:0;color:var(--dim)}
.help .cols>div>p{margin:0;color:var(--dim)}.help .cols>div>p+dl{margin-top:6px}
.stats{display:flex;gap:28px;flex-wrap:wrap;margin:0 0 16px}.stat b{display:block;font-size:20px}.stat span{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--faint)}
.stat.bad b,.bad{color:var(--stop)}
.bar{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:0 0 12px}
.btn,button,input,select{font:inherit;color:var(--ink);background:#191e14;border:1px solid var(--rule);padding:4px 8px;border-radius:3px}
.btn,button{cursor:pointer;text-transform:uppercase;font-size:11px;font-weight:700;letter-spacing:.06em;white-space:nowrap}
.btn.on{color:var(--amber);border-color:var(--amber)}button.go{background:var(--amber);border-color:var(--amber);color:#1a1205}
button.quiet{color:var(--dim)}
form.inline{display:inline;margin:0}
table.registry{width:100%;border-collapse:collapse}
.registry th{text-align:left;font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--faint);font-weight:600;padding:6px 8px;border-bottom:1px solid var(--rule);white-space:nowrap}
.registry th a{color:inherit}.registry th a.on{color:var(--amber)}
.registry th.num{text-align:right}
.registry td{padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:middle;white-space:nowrap}
.registry td.name{white-space:normal;word-break:break-all;border-left:3px solid var(--go);padding-left:10px}
.registry tr.unclassified td.name{border-left-color:var(--stop)}.registry tr.drop td.name{border-left-color:var(--faint);color:var(--dim)}
.registry td.num{text-align:right;color:var(--dim)}
.registry td.field{width:1%}.registry td.field input{width:120px}.registry td.field select{width:88px}
.registry tr.unclassified input,.registry tr.unclassified select{border-style:dashed;color:var(--dim)}
.registry td.actions{width:1%}
.registry .value{color:var(--amber)}
.chip{display:inline-block;padding:0 6px;border-radius:2px;font-size:10px;letter-spacing:.12em;text-transform:uppercase;font-weight:700;border:1px solid var(--rule);color:var(--dim)}
.chip.project{color:var(--go);border-color:rgba(79,224,140,.4)}.chip.unclassified{color:var(--stop);border-color:rgba(255,91,79,.45)}
.empty{padding:40px;text-align:center;color:var(--dim);border:1px dashed var(--rule);border-radius:4px}
@media (max-width:1000px){.help .cols{grid-template-columns:1fr}}
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

// How it works, once, at the top: what each field is for, in plain words.
echo '<div class="help"><p>The database gets copied in two ways: into a small <b>developer copy</b> that lets anyone run the shop '
    . 'without the live database, and from the <b>live shop</b> to a computer or into a server backup. For every table you decide '
    . 'what each copy takes from it.</p>'
    . '<div class="cols">'
    . '<div><h3>Group</h3><p>A name to sort tables, e.g. orders, catalog, cache. Only for order: it does not change what gets copied.</p></div>'
    . '<div><h3>Dev copy</h3><p>A small, safe copy of the database kept with the code, so a new developer starts the shop with one command. '
    . 'What this table puts in it:</p><dl>'
    . '<dt>full</dt><dd>all its rows</dd>'
    . '<dt>slice</dt><dd>only the rows of a sample of products, to keep the copy small</dd>'
    . '<dt>faker</dt><dd>all rows, but names, e-mails, phones and addresses are made up – no real customer data</dd>'
    . '<dt>empty</dt><dd>no rows, just the empty table</dd>'
    . '<dt>drop</dt><dd>the table is not needed at all and gets deleted</dd></dl></div>'
    . '<div><h3>Live copy</h3><p>When the live shop\'s database is copied to a computer or backed up on the server:</p><dl>'
    . '<dt>import</dt><dd>take its data</dd>'
    . '<dt>skip</dt><dd>take only the empty table – for data that rebuilds itself or is not needed (cache, logs, search indexes)</dd></dl></div>'
    . '</div><p class="note">Every table needs a decision. A red one has none yet – its fields hold a suggestion – and until it gets one, '
    . 'changes cannot be committed and no database can be loaded. Add or Save stores the decision in <code>app/database/table-registry.txt</code> '
    . '(commit it); Remove takes it back. '
    . ($counts['profile'] ? 'The ' . $counts['profile'] . ' standard PrestaShop tables are not listed: setup decides them for every shop.' : '')
    . '</p></div>';

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
    . '<div class="stat"><b>' . $counts['all'] . '</b><span>project tables</span></div>'
    . '<div class="stat' . ($counts['unclassified'] ? ' bad' : '') . '"><b>' . $counts['unclassified'] . '</b><span>without a decision</span></div>'
    . '<div class="stat"><b>' . $counts['project'] . '</b><span>project lines</span></div>'
    . '<div class="stat"><b>' . $counts['drop'] . '</b><span>marked drop</span></div>'
    . '<div class="stat"><b>' . h(size($bytes)) . '</b><span>data + index</span></div></div>';

echo '<div class="bar">';
foreach (array('unclassified' => 'Without a decision', 'project' => 'Project lines', 'all' => 'All') as $key => $label) {
    echo '<a class="btn' . ($view === $key ? ' on' : '') . '" href="/?view=' . $key . $at . ($query !== '' ? '&q=' . rawurlencode($query) : '')
        . '&sort=' . $sort . ($descending ? '&dir=desc' : '') . '">' . h($label) . ' (' . $counts[$key] . ')</a>';
}
echo '<form method="get" class="inline">' . $hidden . '<input type="hidden" name="view" value="' . h($view) . '"><input type="hidden" name="sort" value="' . h($sort) . '">'
    . ($descending ? '<input type="hidden" name="dir" value="desc">' : '') . '<input name="q" value="' . h($query) . '" placeholder="filter by name"> <button>Filter</button></form>';
if ($editable && $counts['unclassified']) {
    echo '<span class="spacer"></span><form method="post" action="/accept-all" class="inline" onsubmit="return confirm(\'Save the suggestion of all '
        . $counts['unclassified'] . ' tables of ' . h($schema) . ' without a decision?\')"><input type="hidden" name="token" value="' . h($token) . '">'
        . $hidden . '<input type="hidden" name="back" value="/?view=' . h($view) . h($at) . '"><button class="go">Save all suggestions</button></form>';
}
echo '</div>';

$list = '/?view=' . rawurlencode($view) . $at . ($query !== '' ? '&q=' . rawurlencode($query) : '');
$back = $list . '&sort=' . $sort . ($descending ? '&dir=desc' : '');
$header = function ($column, $label, $class = '') use ($list, $sort, $descending) {
    $reverse = $column === $sort && !$descending;
    $arrow = $column === $sort ? ($descending ? ' ▼' : ' ▲') : '';
    return '<th' . ($class !== '' ? ' class="' . $class . '"' : '') . '><a' . ($column === $sort ? ' class="on"' : '') . ' href="'
        . h($list . '&sort=' . $column . ($reverse ? '&dir=desc' : '')) . '">' . h($label) . $arrow . '</a></th>';
};
if (!$rows) {
    echo '<div class="empty">' . ($view === 'unclassified' && $counts['all'] ? 'Every table has a decision.' : 'No tables.') . '</div>';
} else {
    echo '<table class="registry"><thead><tr>' . $header('table', 'Table') . $header('rows', 'Rows', 'num') . $header('size', 'Size', 'num')
        . $header('source', 'Source') . $header('group', 'Group') . $header('base', 'Dev copy') . $header('live', 'Live copy')
        . ($editable ? '<th></th>' : '') . '</tr></thead><tbody>';
    foreach ($rows as $index => $row) {
        list($name, $table, $match, $guess) = $row;
        $source = $match ? $match[0] : 'unclassified';
        $rule = $ruleOf($row);
        $class = $match ? ($match[3] === 'drop' ? 'drop' : '') : 'unclassified';
        echo '<tr class="' . $class . '"><td class="name">' . h($table[3]) . '</td>'
            . '<td class="num">' . number_format($table[0], 0, '.', ' ') . '</td><td class="num">' . h(size($table[1])) . '</td>'
            . '<td><span class="chip ' . h($source) . '">' . h($source === 'unclassified' ? 'none' : $source) . '</span></td>';
        if (!$editable) {
            echo '<td class="value">' . h($rule[0]) . '</td><td class="value">' . h($rule[1]) . '</td><td class="value">' . h($rule[1] === 'drop' ? '–' : $rule[2]) . '</td></tr>';
            continue;
        }
        // The fields sit in their own cells and belong to the row's form (the HTML form attribute).
        $form = 'rule-' . $index;
        echo '<td class="field"><input form="' . $form . '" name="group" value="' . h($rule[0]) . '" aria-label="group"></td>'
            . '<td class="field"><select form="' . $form . '" name="base" aria-label="dev copy">';
        foreach (TABLES_BASE_RULES as $option) echo '<option' . ($option === $rule[1] ? ' selected' : '') . '>' . $option . '</option>';
        echo '</select></td><td class="field"><select form="' . $form . '" name="live" aria-label="live copy">';
        foreach (array('import', 'skip') as $option) echo '<option' . ($option === $rule[2] ? ' selected' : '') . '>' . $option . '</option>';
        echo '</select></td><td class="actions"><form id="' . $form . '" method="post" action="/set" class="inline"><input type="hidden" name="token" value="' . h($token) . '">'
            . $hidden . '<input type="hidden" name="table" value="' . h($name) . '"><input type="hidden" name="back" value="' . h($back) . '">'
            . '<button>' . ($match ? 'Save' : 'Add') . '</button></form>';
        if ($match && $match[0] === 'project' && $match[1] === $name) {
            echo ' <form method="post" action="/remove" class="inline"><input type="hidden" name="token" value="' . h($token) . '">' . $hidden
                . '<input type="hidden" name="table" value="' . h($name) . '"><input type="hidden" name="back" value="' . h($back) . '">'
                . '<button class="quiet">Remove</button></form>';
        }
        echo '</td></tr>';
    }
    echo '</tbody></table>';
}
echo '</main></body></html>';
