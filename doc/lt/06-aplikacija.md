# 6. Aplikacija: PrestaShop ir kiti projektai

[Turinys](README.md) · [Atgal](05-servisai.md) · [Toliau: duomenų bazė](07-duomenu-baze.md)

## Kas vyksta paleidžiant PHP

```text
PHP drop-in failai (.template) paverčiami
    ↓
Jei PROFILE=prestashop: patikrinama PS_VERSION, paruošiamas konfigūracijos failas,
pritaikomi debug, cache ir pašto nustatymai
    ↓
Vykdomi config/startup/*.sh pagal vardą
    ↓
Paleidžiamas PHP-FPM
```

Šie veiksmai vyksta kiekvieno PHP starto metu, nes prisijungimai skiriasi tarp aplinkų. Klaida sustabdo startą, todėl blogai paruošta aplikacija užklausų nepriima. `make up` ir `make restart` PrestaShop konfigūraciją dar patikrina vienkartiniame konteineryje prieš perkurdami veikiančius servisus.

## Aplikacijos rūšis

| Aplikacija | `env/common.env` | Kas daroma automatiškai |
| --- | --- | --- |
| PrestaShop 1.6–9 | `PROFILE=prestashop` | Konfigūracijos failas iš env, versijos patikra, debug, cache ir pašto politika, `PS_RELEASE_PATHS`, DB saugūs numatytieji po importo. |
| Akeneo | `PROFILE=akeneo` | Akeneo PHP image'o papildymai. Pilnas Akeneo įdiegimas neautomatizuotas. |
| Symfony, Laravel, savas PHP | `PROFILE=` (arba `generic`) | PS veiksmų nėra. Aplikacija skaito env pati arba per startup scriptą. |

## PrestaShop konfigūracijos failas

PrestaShop konfigūracijos failas generuojamas iš šablono ir env. Jo nereikia gauti iš kolegos.

| `PS_VERSION` | Failas | Raktai env faile |
| --- | --- | --- |
| `1.6.x` | `config/settings.inc.php` | `PS_COOKIE_KEY`, `PS_COOKIE_IV`, `PS_RIJNDAEL_KEY`, `PS_RIJNDAEL_IV` |
| `1.7.x`, `8.x` | `app/config/parameters.php` | `PS_COOKIE_KEY`, `PS_COOKIE_IV`, `PS_NEW_COOKIE_KEY`, `PS_SECRET` |
| `9.x` | `app/config/parameters.php` | Kaip 1.7/8, plius `PS_API_PRIVATE_KEY`, `PS_API_PUBLIC_KEY` (PEM, base64) |

`env/common.env`:

```dotenv
PROFILE=prestashop
PS_VERSION=9.0.2
```

Kaip tai veikia:

- **`PS_VERSION` turi sutapti su kodu.** Jei kode yra kita versija, PHP nepasileidžia: `PS_VERSION=… does not match the shop code`. Taip pastebi pamirštą atnaujinimą.
- **Failo nėra** – jis sukuriamas iš šablono su DB prisijungimu (`DATABASE_*`), raktais (`PS_*`), `PS_DATABASE_PREFIX` (numatyta `ps_`) ir `PS_LOCALE`. Jei trūksta privalomo rakto, `make check` sustoja: `No app/config/parameters.php yet and no keys to create it: set …`.
- **Failas yra** – DB prisijungimas ir raktai jame atnaujinami iš env. Raktas, kurio env nėra, lieka toks, koks faile. Kiti laukai išsaugomi.
- PHP prie DB jungiasi `database:3306` (`APP_DATABASE_HOST`, `APP_DATABASE_PORT`).

**Iš kur raktai:**

- **local/test:** `make init` / `make bootstrap` (taigi ir `make setup`) įrašo trūkstamus raktus į privatų env failą. Jei parduotuvėje jau yra konfigūracijos failas, raktai nukopijuojami iš jo, kitaip sugeneruojami taip, kaip juos generuoja PS diegimo programa. Švari aplinka veikia be LIVE raktų.
- **stage/prod:** raktai niekada negeneruojami. Jie ateina iš serverio env arba Bitbucket Deployment variables. Prod naudoja esamus LIVE raktus: be jų, pvz., Forsenoje neišsišifruotų mokėjimo modulio duomenys.
- **PS 1.6:** visi slaptažodžiai priklauso nuo `PS_COOKIE_KEY`. Užkeliant LIVE dump'ą lokaliai reikia LIVE `PS_COOKIE_KEY` privačiame `local.env`.

## PrestaShop ir PHP suderinamumas

Setup turi suderinamumo lentelę [compatibility.json](../../docker/compatibility.json): PS šaka → tinkamos PHP, MySQL ir MariaDB versijos. Ją naudoja `./create-project` (siūlo tik tinkamas versijas) ir `make check`.

| PS šaka | PHP |
| --- | --- |
| 1.6, 1.7.0–1.7.4 | 5.6, 7.0, 7.1 |
| 1.7.5–1.7.6 | 5.6–7.2 |
| 1.7.7 | 7.1–7.3 |
| 1.7.8 | 7.1–7.4 |
| 8.0–8.2 | 7.2–8.1 |
| 9.0 | 8.1–8.4 |
| 9.1 | 8.1–8.5 |

```bash
make check
```

Nesuderinama PHP versija yra klaida, nesuderinama DB versija – tik įspėjimas. Fork'ams (pvz. thirty bees) patikrą išjungia `SETUP_COMPATIBILITY_CHECK=off`.

## Oficialus PrestaShop release

Švariame parduotuvės clone'e gali trūkti failų, kurių Git neseka, pvz. `vendor/` ar `admin` katalogo. Juos gali paimti iš oficialaus release:

```dotenv
PS_RELEASE=on
PS_RELEASE_PATHS=vendor
```

- `PS_RELEASE=on` kuriant PHP image'ą įdeda `PS_VERSION` release į `/opt/prestashop/release/<versija>`. PS 1.7+ release imamas iš oficialaus `prestashop/prestashop` image'o, PS 1.6 – iš GitHub zip (`PS_RELEASE_SHA256` gali užfiksuoti jo kontrolinę sumą).
- `make setup` žingsnis `app-prepare` nukopijuoja **tik** `PS_RELEASE_PATHS` katalogus. Kiekvienas katalogas sinchronizuojamas pilnai: senas turinys pašalinamas ir nukopijuojamas release turinys.
- `admin` kopijuojant pervadinamas į `PS_ADMIN_DIR`, jei jis nurodytas.
- `install/` niekada nekopijuojamas. Katalogas, kuriame yra parduotuvės Git sekamų failų, atmetamas: release niekada neperrašo sekamo kodo.
- Kopijuojama tik `make setup` metu lokaliai ir build serveryje ruošiant deploy'aus release'ą, niekada konteinerio starto metu. Todėl pašalinti moduliai „neprisikelia“, o `install/` negrįžta.

Pakeitus `PS_RELEASE` ar `PS_VERSION`, paleisk `make setup`.

### Savas composer.json

Jei projektas keičia branduolio priklausomybes, jo `composer.json` ir `composer.lock` laikomi aplinkos repozitorijoje `config/composer/`. `make setup` juos nukopijuoja į parduotuvę ir paleidžia `composer install` (stage/prod – be dev paketų). Tada release `vendor/` nekopijuojamas, net jei jis nurodytas `PS_RELEASE_PATHS`.

## Papildomi PS parametrai

Situacija: tavo modulis skaito `supplier_api_url` iš PS parametrų.

`env/local.env`:

```dotenv
SUPPLIER_API_URL=https://sandbox.example.com/api
```

`compose/common.yaml`:

```yaml
services:
  php-fpm:
    environment:
      SUPPLIER_API_URL: ${SUPPLIER_API_URL:?Įrašyk tiekėjo API adresą}
```

`config/prestashop/parameters.override.php` (PS 1.7+):

```php
<?php
return array(
    'supplier_api_url' => getenv('SUPPLIER_API_URL'),
);
```

PS 1.6 naudoja `config/prestashop/settings.override.php` su konstantomis, pvz. `'_SUPPLIER_API_URL_' => getenv('SUPPLIER_API_URL')`, tik skaliarinės reikšmės.

```bash
make check
make restart service=php-fpm
```

DB prisijungimo raktų override faile rašyti negalima: jie imami tik iš env. Pašalinus override failą anksčiau įrašytas raktas iš konfigūracijos neištrinamas: funkcijai išjungti aiškiai nustatyk `false`.

## PrestaShop debug

Debug režimą valdo `_PS_MODE_DEV_` konstanta faile `public/config/defines.inc.php`. Setup ją keičia pagal env:

| `PS_DEBUG_MODE` | Kas vyksta |
| --- | --- |
| `off` | Debug išjungtas. |
| `on` | Debug įjungtas visiems lankytojams ir CLI. |
| `ip` | Debug tik užklausoms iš `PS_DEBUG_IPS` adresų, CLI – išjungtas. |
| tuščias | Setup failo neliečia ir ankstesnio pakeitimo neatšaukia. |

Bendras saugus pasirinkimas `env/common.env`:

```dotenv
PS_DEBUG_MODE=off
PS_DEBUG_IPS=
```

Tik savo kompiuteryje `env/local.env`:

```dotenv
PS_DEBUG_MODE=on
```

```bash
make restart service=php-fpm
```

Kad klaidas matytų tik konkretus žmogus (pvz. demonstracinėje aplinkoje):

```dotenv
PS_DEBUG_MODE=ip
PS_DEBUG_IPS=192.0.2.10,2001:db8::10
```

`PS_DEBUG_IPS` – tikslūs IPv4/IPv6 adresai per kablelį, be portų ir CIDR. Su `ip` sąrašas privalomas. PHP lygina `REMOTE_ADDR`, kurį Apache gauna iš patikimo proxy (žr. [tikras kliento IP](05-servisai.md#ip-apribojimai)). Išjungimui visada rašyk `off` – vien eilutės ištrynimas gali palikti paveldėtą reikšmę. `defines_custom.inc.php` neturi iš anksto apibrėžti `_PS_MODE_DEV_`. `PS_DEBUG_MODE` nustatytas be `config/defines.inc.php` yra klaida.

## Kitos aplikacijos ir startup scriptai

### Startup scriptai

`config/startup/*.sh` vykdomi kiekvieną PHP startą pagal failo vardą, prieš PHP-FPM:

- naudok numeraciją `010-…`, `020-…`;
- scriptai vykdomi su `sh` (ne Bash), executable bito nereikia;
- scriptas gali būti vykdomas daug kartų, todėl turi būti kartojamas saugiai;
- klaida sustabdo PHP startą;
- `config/` konteineryje matomas kaip `/opt/setup/project` ir yra tik skaitomas: generuojamus failus rašyk į aplikacijos katalogą;
- symlink scriptai atmetami;
- DB dump'ų ir fixtures iš startup scriptų nekrauk – tam yra `db-*` komandos.

### Pavyzdys: kita aplikacija sugeneruoja savo runtime.php

Situacija: tavo PHP aplikacija (ne PS) skaito `config/runtime.php`.

`compose/common.yaml`:

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
$values = array(
    'mode' => getenv('APP_MODE'),
    'integration_url' => getenv('INTEGRATION_URL'),
    'cache_enabled' => getenv('CACHE_MODE') === 'on',
);
$contents = "<?php\nreturn " . var_export($values, true) . ";\n";
$target = '/var/www/html/config/runtime.php';
if (!is_file($target) || file_get_contents($target) !== $contents) {
    file_put_contents($target, $contents, LOCK_EX);
}
```

```bash
make check
make restart service=php-fpm
```

Sugeneruotą `config/runtime.php` ignoruok **aplikacijos** Git repozitorijoje, o `config/startup/` ir `config/myapp/` versijuok aplinkos repozitorijoje. `CACHE_MODE` ir `MAIL_MODE` PHP konteineris jau gauna, todėl kitos aplikacijos adapteris gali juos išversti į savo nustatymus (žr. [cache ir paštas](12-cache-ir-hibridines-aplinkos.md)).
