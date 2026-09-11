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

## F. PrestaShop debug iš env

Debug režimas parodo daugiau klaidų informacijos. Jį valdo PS konstanta `_PS_MODE_DEV_`, esanti aplikacijos `config/defines.inc.php`. Mūsų `app/` struktūroje visas kelias yra **`public/config/defines.inc.php`**. Tai kitas katalogas nei aplinkos `config/php/`, kuriame laikomi PHP `.ini` failai.

Tas pats bendras updater veikia PS 1.6 ir naujesniems PS projektams su palaikoma DB konfigūracija. Pasirink `PROFILE=ps` arba `PROFILE=prestashop`, turėk originalų tos parduotuvės `defines.inc.php` ir jos prisijungimų failą. Kitų profilių failai neliečiami.

### Situacija 1: vietoje kuri modulį ir nori matyti klaidas

`env/common.env` bendrą saugų pasirinkimą palik:

```dotenv
PROFILE=ps
PS_DEBUG_MODE=off
PS_DEBUG_IPS=
```

Tik savo `env/local.env` pridėk:

```dotenv
PS_DEBUG_MODE=on
```

Iš aplinkos katalogo paleisk:

```bash
make up
make smoke
```

`on` reiškia, kad debug galioja **visiems** šios aplinkos lankytojams ir CLI komandoms. IP sąrašas šiame režime neriboja lankytojų. Tai PS klaidų režimas; Xdebug derintuvo ir `_PS_DEBUG_PROFILING_` jis neįjungia.

### Situacija 2: klaidas turi matyti tik konkretus žmogus

Pavyzdžiui, demonstracinę parduotuvę peržiūri klientas, o programuotojas klaidas turi matyti tik iš savo biuro. Tos aplinkos env faile:

```dotenv
PS_DEBUG_MODE=ip
PS_DEBUG_IPS=192.0.2.10,2001:db8::10
```

Adresai čia yra dokumentacijos pavyzdžiai: pakeisk juos tikrais konkrečių klientų adresais. Vietiniam tiesioginiam prisijungimui gali tikti `127.0.0.1,::1`, bet su Docker/proxy serveris gali matyti kitą adresą. Tuščias sąrašas neleidžiamas. Palaikomi tik tikslūs adresai, ne tinklų intervalai.

Paleisk `make up` pasirinktai aplinkai. PHP kiekvienai HTTP užklausai atskirai palygins serverio `REMOTE_ADDR` su sąrašu. Leidžiamam adresui debug bus įjungtas, kitiems išjungtas. Lygiaverčiai IPv6 užrašai sutampa; IPv4 `127.0.0.1` ir IPv4-mapped IPv6 `::ffff:127.0.0.1` laikomi atskirais adresais, todėl prireikus įrašyk abu. CLI ir phpdbg režimu `ip` visada išjungia debug, net jei procesui kas nors priskiria `REMOTE_ADDR`.

**Kai naudojamas proxy.** `REMOTE_ADDR` turi būti tikras kliento adresas, kurį paruošia patikimai sukonfigūruotas web serveris. PHP kodas pats neskaito `X-Forwarded-For` ar `X-Real-IP`. Bendrame Apache dabar įjungtas `mod_remoteip`, pasitikintis `172.16.0.0/12`; konkrečioje infrastruktūroje pasitikėjimą reikia apriboti tik tikrais proxy. Jeigu visi lankytojai atrodo kaip vienas Docker gateway ar proxy adresas, jo įtraukimas į leidžiamų adresų sąrašą leistų debug visiems už jo esantiems žmonėms. Pirmiausia sutvarkyk tikro kliento adreso perdavimą per visą proxy grandinę.

Patikra: iš leidžiamo ir neleidžiamo kliento patikrink `_PS_MODE_DEV_` bei klaidų rodymą savo bandymų aplinkoje. Vien CLI patikra `ip` režimo HTTP veikimo neparodo. Proxy patikrai iš neleidžiamo kliento papildomai siųsk suklastotą `X-Forwarded-For` su leidžiamu adresu: debug turi likti išjungtas. IP ribojimas nėra prisijungimo sistema; bendrą viešą IP naudojantys žmonės turės vienodą rezultatą.

### Situacija 3: užbaigei derinimą arba ruoši prod

`env/prod.env` arba kitos derinamos aplinkos faile aiškiai nustatyk:

```dotenv
PS_DEBUG_MODE=off
PS_DEBUG_IPS=
```

```bash
make up ENV=prod
make smoke ENV=prod
```

Local atveju `ENV=prod` nerašyk. Vien eilutės ištrynimas ne visada išjungia debug: gali būti paveldėtas common pasirinkimas. Jei režimas galiausiai tuščias, updater visai neliečia esamo failo, net jei anksčiau į jį įrašė `true`. Išjungimui visada naudok `off` ir paleisk `make up`.

### Kas konkrečiai pakeičiama ir kas išsaugoma

- Keičiamas tik esamo `define('_PS_MODE_DEV_', ...)` reikšmės argumentas. `off` įrašo `false`, `on` – `true`, o `ip` – kiekvienos užklausos metu įvertinamą IP patikrą. Likusios konstantos ir aplinkinis failo turinys išsaugomi.
- Failas pakeičiamas atominiu pervadinimu, išsaugant jo prieigos teises. Nepasikeitęs rezultatas neperrašomas; vien debug pakeitimas DB parametrų ir aplikacijos cache nevalo. PS pats pasirenka debug/prod veikimą, o PHP-FPM startas atnaujina jo procesus.
- Reikia vieno įprasto, vardą tekstu nurodančio `define`. Trūkstamas, pasikartojantis aprašas arba symlink yra klaida. Updater nekuria naujo PS sisteminio failo nuo nulio ir neprideda debug konstantos failo gale, kai PS jau būtų ją panaudojęs.
- `defines_custom.inc.php` ar kitas ankstesnis aplikacijos kodas neturi iš anksto apibrėžti `_PS_MODE_DEV_`: PHP jau apibrėžtos konstantos nepakeičia. Tokį ankstesnį debug valdymą pašalink prieš pereidamas prie env.
- Administravimo skydelis ar PS atnaujinimas gali vėl pakeisti `defines.inc.php`. Env yra mūsų paleidimo konfigūracija: naujas PHP startas pritaiko ją iš naujo. Jei env nepasikeitė, konteinerį perkrauk Docker/PhpStorm įrankiu. `docker restart` tinka tik jau konteineryje esantiems env, o po **env failo pakeitimo** reikia `make up`.
- Failas yra aplikacijos prijungtame kataloge. Jeigu jis versijuojamas aplikacijos Git, matysi vietinį pakeitimą. Aplinkai sugeneruotos IP taisyklės nekelk kaip bendro aplikacijos pakeitimo. Aplinkos env pavyzdžius versijuok, tikrą `local.env` / `prod.env` laikyk privačiai.
- Tą patį aplikacijos katalogą naudojantys procesai skaito tą patį failą. Skirtingiems local/test/prod režimams reikia atskirų aplikacijos kopijų; šiam setup `test-init` jas atskiria. Read-only produkcinio kodo atveju šis startup updater netinka be atskiro writable konfigūracijos sprendimo.

Elgsenos šaltiniai: [PS 1.6 debug konstantos ir klaidų valdymas](https://github.com/PrestaShop/PrestaShop/blob/1.6.1.24/config/defines.inc.php), [PHP serverio kintamieji](https://www.php.net/manual/en/reserved.variables.server.php). Mūsų env vardai ir jų vertimas aprašyti pagal šio setup kodą, tai nėra pačio PrestaShop standartiniai env vardai.

## Hook taisyklės

- Tiesioginiai `config/startup/*.sh` vykdomi failų vardų tvarka. Naudok `010-…`, `020-…`, `100-…`.
- Scriptai paleidžiami su `sh`, todėl jiems nereikia executable bito. Bash sintaksė, pvz. `[[ ... ]]`, netinka be atskiro Bash kvietimo.
- Hook gali būti vykdomas daug kartų. Nekurk naujo vartotojo ar nedubliuok DB įrašo kiekvieną startą.
- SQL dump ir fixtures turi atskiras Make komandas. Jų nekrauk iš hook kiekvieną PHP restartą.
- Konfigūracijos mount `/opt/setup/project` yra read-only. Sugeneruotą aplikacijos failą rašyk į jos writable vietą, ne į šaltinio hook katalogą.
- Nuorodos į symlink hook failus atmetamos. PS updater taip pat saugo valdomus konfigūracijos/cache kelius nuo symlink.
- Pakeitęs tik hook turinį, perkrauk PHP: paprastas nepakeisto Compose `up` nebūtinai sukelia naują startą.
- Jei projekte perrašytas PHP `entrypoint`, įsitikink, kad jis iškviečia bendrą `/opt/setup/runtime/start.sh` arba sąmoningai turi visą reikalingą savo paruošimą.

## Cache ir laiškų politika

Dabartinis setup taip pat tvarko PS objektų cache parametrą bei pasirinktus Smarty/CSS/JS ir laiškų DB nustatymus. Visos taisyklės ir jų pirmenybė aprašyti [12 skyriuje](12-cache-ir-hibridines-aplinkos.md#kas-tiksliai-keičiama-prestashop). Kito framework aplikacijos cache išjungimą turi įgyvendinti projekto adapteris.
