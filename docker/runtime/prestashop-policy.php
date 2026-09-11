<?php
// Profile adapter: no framework bootstrap, no module hooks or mail dispatch.
function psPolicyValues()
{
    $values = array();
    foreach (array('PS_SMARTY_CACHE' => array('PS_SMARTY_CACHE'),
                   'PS_ASSET_CACHE' => array('PS_CSS_THEME_CACHE', 'PS_JS_THEME_CACHE', 'PS_HTML_THEME_COMPRESSION', 'PS_JS_HTML_THEME_COMPRESSION')) as $env => $keys) {
        $value = getenv($env);
        if ($value === false || $value === '') continue;
        if ($value !== 'on' && $value !== 'off') throw new PrestashopSetupException($env . ' must be on or off.');
        foreach ($keys as $key) $values[$key] = $value === 'on' ? '1' : '0';
    }
    $compile = getenv('PS_SMARTY_COMPILE');
    if ($compile !== false && $compile !== '') {
        $modes = array('never' => '0', 'check' => '1', 'always' => '2');
        if (!isset($modes[$compile])) throw new PrestashopSetupException('PS_SMARTY_COMPILE must be never, check or always.');
        $values['PS_SMARTY_FORCE_COMPILE'] = $modes[$compile];
    }
    $mail = getenv('MAIL_MODE');
    if ($mail !== false && $mail !== '' && $mail !== 'preserve') {
        if ($mail !== 'off') throw new PrestashopSetupException('MAIL_MODE must be off or preserve.');
        $values['PS_MAIL_METHOD'] = '3';
    }
    return $values;
}

function psObjectCacheValue()
{
    $value = getenv('PS_OBJECT_CACHE');
    if ($value === false || $value === '' || $value === 'preserve') return null;
    if ($value !== 'on' && $value !== 'off') throw new PrestashopSetupException('PS_OBJECT_CACHE must be on, off or preserve.');
    return $value === 'on';
}

function psPolicyConnection($root)
{
    if (psLayout($root) === 'legacy') {
        require "$root/config/settings.inc.php";
        $prefix = _DB_PREFIX_;
    } else {
        $config = require "$root/app/config/parameters.php";
        $prefix = isset($config['parameters']['database_prefix']) ? $config['parameters']['database_prefix'] : null;
    }
    if (!is_string($prefix) || !preg_match('/^[A-Za-z0-9_]*$/D', $prefix)) {
        throw new PrestashopSetupException('Restore a valid shop database prefix before applying cache/mail policy.');
    }
    $values = psConnectionValues();
    $deadline = time() + 60;
    do {
        try {
            $db = new PDO('mysql:host=' . $values['HOST'] . ';port=' . $values['PORT'] . ';dbname=' . $values['NAME'],
                $values['USER'], $values['PASSWORD'], array(PDO::ATTR_TIMEOUT => 2, PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION));
            return array($db, $prefix . 'configuration');
        } catch (PDOException $error) {
            if (time() >= $deadline) throw new PrestashopSetupException('Database unavailable for PS cache/mail policy; prepare/import the shop database first.');
            usleep(250000);
        }
    } while (true);
}

function applyPrestashopPolicy($root)
{
    $values = psPolicyValues();
    if (!$values) return;
    list($db, $table) = psPolicyConnection($root);
    // Verify every key first. Do not invent rows or shop scopes for unknown PS versions.
    $select = $db->prepare("SELECT id_configuration, value FROM `$table` WHERE name = ?");
    $changes = array();
    foreach ($values as $name => $value) {
        $select->execute(array($name));
        $rows = $select->fetchAll(PDO::FETCH_ASSOC);
        if (!$rows && in_array($name, array('PS_SMARTY_CACHE', 'PS_SMARTY_FORCE_COMPILE', 'PS_MAIL_METHOD'), true)) {
            throw new PrestashopSetupException('Missing PS configuration key ' . $name . '; import an installed shop database first.');
        }
        foreach ($rows as $row) {
            if ((string)$row['value'] !== $value) $changes[] = array($value, $row['id_configuration']);
        }
    }
    if (!$changes) { echo "PrestaShop cache/mail policy already matches.\n"; return; }
    // Only the chosen config keys, including existing multishop overrides, are touched.
    $db->beginTransaction();
    try {
        $update = $db->prepare("UPDATE `$table` SET value = ?, date_upd = NOW() WHERE id_configuration = ?");
        foreach ($changes as $change) $update->execute($change);
        $db->commit();
    } catch (Exception $error) {
        if ($db->inTransaction()) $db->rollBack();
        throw $error;
    }
    echo "Updated PrestaShop cache/mail policy; unrelated integration settings preserved.\n";
}
