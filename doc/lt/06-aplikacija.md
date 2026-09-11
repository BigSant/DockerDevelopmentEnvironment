# 6. Aplikacijos konfigūracija: PS ir kiti projektai

[Turinys](README.md) · [Atgal](05-servisai.md) · [Toliau: DB](07-duomenu-baze.md)

## Kas vyksta paleidžiant PHP

```text
Konteineris gauna env
    ↓
Jei PROFILE=ps / prestashop: atpažįstamas ir atnaujinamas PS config
    ↓
Vykdomi projekto config/startup/*.sh abėcėlės tvarka
    ↓
Paleidžiamas PHP-FPM
```

Šie veiksmai atliekami **paleidimo metu**, nes prisijungimai skiriasi tarp local, test ir prod. Jų nekepame į Dockerfile. Hook klaida sustabdo PHP paleidimą, todėl neteisingai paruošta aplikacija nepradeda priimti užklausų.

## Kaip pasirinkti aplikaciją

| Situacija | `env/common.env` | Kas bus automatiškai daroma |
| --- | --- | --- |
| PrestaShop | `PROFILE=ps` | PS konfigūracijos paruošimas ir PS DB patikra. |
| Tas pats senu vardu | `PROFILE=prestashop` | Toks pats PS veikimas. |
| Akeneo | `PROFILE=akeneo` | Bendri Akeneo atvaizdo papildymai; PS failai neliečiami. Pilnas Akeneo įdiegimas nėra automatinis. |
| Symfony, Laravel, savas PHP | `PROFILE=` | PS veiksmų nėra. Aplikacija pati skaito env arba naudoja projekto hook. |

`SMOKE_URL` gali veikti visiems profiliams. Tik PS profiliui papildomai perskaitomas jo konfigūracijos failas ir tikrinamas PDO prisijungimas.

## A. Naujesnis PS ir parameters.php

Situacija: atkuriama esama parduotuvė, kurios konfigūracija yra `public/app/config/parameters.php`.

1. Turėk originalų tos parduotuvės `parameters.php`, įskaitant jos `secret`, cookie raktus, lentelių prefiksą ir kitus reikalingus laukus.
2. Pasirink `PROFILE=ps`.
3. Aplinkos env užpildyk `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`. Bendras PHP prisijungimo adresas – `database:3306`.
4. Paleisk `make up` po DB paruošimo/importo.

Setup esamame `parameters` masyve keičia tik šiuos prisijungimo raktus ir tavo aiškiai nurodytus papildymus:

| PHP raktas | Iš kur ateina |
| --- | --- |
| `database_host` | PHP runtime `DATABASE_HOST`, numatytai `APP_DATABASE_HOST=database`. |
| `database_port` | PHP runtime `DATABASE_PORT`, numatytai `APP_DATABASE_PORT=3306`. |
| `database_name` | `DATABASE_NAME`. |
| `database_user` | `DATABASE_USER`. |
| `database_password` | `DATABASE_PASSWORD`. |

Kiti esami laukai išsaugomi. Pasikeitus turiniui failas pakeičiamas atominiu pervadinimu, prieiga nustatoma `0600`, išvalomi `var/cache/dev` ir `var/cache/prod`. Jei reikšmės tos pačios, failas ir cache be reikalo neperrašomi. Nestandartinius ar kitur esančius cache tvarko pats projektas.

Symfony tipo parametrų `%` simboliai automatiškai escape'inami į `%%`. Override faile rašyk tikrą norimą tekstą su vienu `%`; antrą escape atlieka updater.

## B. Tik vienam PS projektui reikia papildomo parametro

Situacija: tavo modulis skaito `supplier_api_url` ir `supplier_import_enabled` iš PS parametrų.

`env/local.env`:

```dotenv
SUPPLIER_API_URL=https://sandbox.example.com/api
SUPPLIER_IMPORT_ENABLED=1
```

`compose/common.yaml` papildymas:

```yaml
services:
  php-fpm:
    environment:
      SUPPLIER_API_URL: ${SUPPLIER_API_URL:?Įrašyk tiekėjo API adresą}
      SUPPLIER_IMPORT_ENABLED: ${SUPPLIER_IMPORT_ENABLED:-0}
```

`config/prestashop/parameters.override.php`:

```php
<?php
return array(
    'supplier_api_url' => getenv('SUPPLIER_API_URL'),
    'supplier_import_enabled' => getenv('SUPPLIER_IMPORT_ENABLED') === '1',
);
```

```bash
make check
make up
make smoke
```

Patikra: aplikacijos modulis turi matyti savo aplinkai parinktą adresą ir jungiklį. Setup neprideda modulio, kuris šiuos naujus vardus skaitytų – vardai turi sutapti su tavo aplikacijos logika.

Override gali pridėti arba pakeisti papildomus raktus. Raktų vardai turi prasidėti raide ar `_`, toliau leidžiamos raidės, skaitmenys ir `_`; reikšmėms naudok tekstą, skaičių, boolean, `null` ar paprastus masyvus. DB prisijungimo raktai rezervuoti env ir override faile atmetami.

Failo pašalinimas **neištrina** anksčiau į `parameters.php` įrašyto papildomo rakto. Jei funkciją išjungi, aiškiai nustatyk `false`; jei reikia visai pašalinti raktą, atlik projekto konfigūracijos migraciją.

Nėra automatinio `parameters.local.override.php` pasirinkimo. Skirtingas reikšmes perduok per env, kaip pavyzdyje, arba sąmoningai prijunk kitą failą aplinkos Compose apraše.

## C. PS 1.6 ir settings.inc.php

Situacija: senoje parduotuvėje nėra `app/config/parameters.php`, jos prisijungimai yra `public/config/settings.inc.php`.

Setup atpažįsta seną formatą ir keičia:

| Konstanta | Reikšmė |
| --- | --- |
| `_DB_SERVER_` | DB hostas; jei portas ne 3306, prijungiamas `:portas`. |
| `_DB_NAME_` | DB vardas. |
| `_DB_USER_` | DB vartotojas. |
| `_DB_PASSWD_` | DB slaptažodis. |

Išsaugomi kiti aprašymai, komentarai, `_DB_PREFIX_`, cookie ir šifravimo raktai. Parseris ieško `define(...)`, o ne aklai keičia panašų tekstą komentaruose. Pasikartojantis valdomas `define` laikomas klaida. Trūkstamus valdomus `define` updater gali pridėti į esamą failą.

Papildomos tik to projekto konstantos – `config/prestashop/settings.override.php`:

```php
<?php
return array(
    '_SUPPLIER_IMPORT_ENABLED_' => getenv('SUPPLIER_IMPORT_ENABLED') === '1',
    '_SUPPLIER_API_URL_' => getenv('SUPPLIER_API_URL'),
);
```

Env perdavimas toks pats kaip ankstesniame pavyzdyje. PS 1.6 override naudok skaliarines reikšmes, ne masyvus. Atskiro modernios konfigūracijos failo setup nekuria. Keičiantis senam config, išvalomi klasės ir palaikomi Smarty/cachefs cache; apsauginiai `index.php` ir `.htaccess` paliekami.

**Konfigūracijos palaikymas nėra pilno PS 1.6 atvaizdo garantija.** CI su PHP 5.6 patikrina failo atnaujinimą ir hook veikimą. Esamame PHP 5.6 Dockerfile dalis plėtinių diegimo komandų pakomentuota; jo sukūrimą, reikiamus plėtinius, Composer/Xdebug ir DB suderinamumą reikia patikrinti konkrečiai parduotuvei. Vien `PHP_VERSION=5.6` šiame leidime nėra patikrintas pilnas naujos PS 1.6 parduotuvės įdiegimas.

## D. Ankstyva PS versija turi parameters.yml

Jei parduotuvė turi `app/config/parameters.yml`, bet neturi `parameters.php`, updater sustoja su aiškia žinute. Jis neperrašo YAML ir nelaiko modernaus `settings.inc.php` suderinamumo failo PS 1.6 konfigūracija.

Sprendimas: su tos aplikacijos įrankiais konvertuoti konfigūraciją į palaikomą formatą arba projektui sąmoningai parašyti savą paruošimą. Neišjunk PS profilio vien tam, kad paslėptum neparuoštos konfigūracijos klaidą, ir nepakeisk jos tuščiu pavyzdžiu be parduotuvės raktų.

## E. Kita aplikacija nori savo sugeneruoto runtime.php

Situacija: tavo PHP aplikacija skaito `config/runtime.php`; ji nėra PS. Norime jį sugeneruoti iš env kiekvieną startą.

Common env turi `PROFILE=`. `compose/common.yaml`:

```yaml
services:
  php-fpm:
    environment:
      APP_MODE: ${ENV}
      INTEGRATION_URL: ${INTEGRATION_URL:?Įrašyk integracijos adresą}
```

`config/startup/010-runtime.sh`:

```sh
#!/bin/sh
set -eu
php /opt/setup/project/myapp/prepare.php
```

`config/myapp/prepare.php`:

```php
<?php
$directory = '/var/www/html/config';
if (!is_dir($directory) && !mkdir($directory, 0755, true)) {
    throw new RuntimeException('Nepavyko paruošti aplikacijos config katalogo.');
}
$values = array(
    'mode' => getenv('APP_MODE'),
    'integration_url' => getenv('INTEGRATION_URL'),
);
$contents = "<?php\nreturn " . var_export($values, true) . ";\n";
$target = $directory . '/runtime.php';
if (is_file($target) && file_get_contents($target) === $contents) {
    exit(0);
}
$temporary = tempnam($directory, '.runtime-');
if ($temporary === false) {
    throw new RuntimeException('Nepavyko sukurti laikino config failo.');
}
try {
    if (!chmod($temporary, 0600)
        || file_put_contents($temporary, $contents) !== strlen($contents)
        || !rename($temporary, $target)) {
        throw new RuntimeException('Nepavyko išsaugoti runtime config.');
    }
} finally {
    if (is_file($temporary)) {
        unlink($temporary);
    }
}
```

Tai naujam tavo valdomam config failui skirtas pavyzdys, ne savavališko esamo framework config perrašymo receptas. Aplikacijoje `require` grąžins masyvą. Sugeneruotą `config/runtime.php` ignoruok **aplikacijos Git repozitorijoje**, o `config/myapp/prepare.php` ir hook versijuok aplinkos repozitorijoje.

Vykdyk `make check`, `make up` ir patikrink aplikacijos funkciją. Tuščias PS profilis užtikrina, kad PS failų nėra ieškoma. `APP_MODE` perduodame aiškiai – vien runner `ENV` vardas savaime nėra kiekvieno konteinerio env.

## Hook taisyklės

- Tiesioginiai `config/startup/*.sh` vykdomi failų vardų tvarka. Naudok `010-…`, `020-…`, `100-…`.
- Scriptai paleidžiami su `sh`, todėl jiems nereikia executable bito. Bash sintaksė, pvz. `[[ ... ]]`, netinka be atskiro Bash kvietimo.
- Hook gali būti vykdomas daug kartų. Nekurk naujo vartotojo ar nedubliuok DB įrašo kiekvieną startą.
- SQL dump ir fixtures turi atskiras Make komandas. Jų nekrauk iš hook kiekvieną PHP restartą.
- Konfigūracijos mount `/opt/setup/project` yra read-only. Sugeneruotą aplikacijos failą rašyk į jos writable vietą, ne į šaltinio hook katalogą.
- Nuorodos į symlink hook failus atmetamos. PS updater taip pat saugo valdomus konfigūracijos/cache kelius nuo symlink.
- Pakeitęs tik hook turinį, perkrauk PHP: paprastas nepakeisto Compose `up` nebūtinai sukelia naują startą.
- Jei projekte perrašytas PHP `entrypoint`, įsitikink, kad jis iškviečia bendrą `/opt/setup/runtime/start.sh` arba sąmoningai turi visą reikalingą savo paruošimą.
