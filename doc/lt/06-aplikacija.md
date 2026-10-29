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

**Profilio taisyklė.** Kiekvienas setup žingsnis, skaitantis ar keičiantis aplikacijos dalykus, žiūri į `PROFILE`. Ne `prestashop` profilis (tuščias, `generic`, `akeneo`) PrestaShop dalykų neskaito ir neliečia:

| Žingsnis | Generic projekte |
| --- | --- |
| `setup` / `bootstrap` | PS raktai negeneruojami, `app-prepare` nieko nedaro. |
| `PS_RELEASE=on`, `PS_RELEASE_PATHS`, `PS_ADMIN_DIR` | Atmetama: `… belongs to PrestaShop; set PROFILE=prestashop …`. PS release į image'ą nepatenka. |
| PHP startas, `up`, `restart`, `check`, `doctor` | PS konfigūracijos failas netikrinamas ir nekuriamas. |
| `db-import`, `db-load`, `test-prepare` | PS saugūs numatytieji ir parduotuvės politika nevykdomi; projekto after-import SQL vykdomi. `${DB_PREFIX}` ir `${PS_SSL}` SQL'e atmetami. |
| Lentelių registras, rinkinio receptas | `{p}` (PS prefiksas) atmetamas – lentelės rašomos pilnais vardais. `@products` reikia tik tada, kai yra `slice` lentelių. |
| `test-prepare` kopija | `var/cache`, `var/logs`, `cache` kopijuojami kaip kiti failai (PS juos kuria tuščius). |
| `phpunit`, IDE | `modules/` – paprastas katalogas: rinkiniai tik `app` (aplikacijos šaknis) ir `project` (`tests/`). |
| `runtime-info` | `PS_*` jungiklių nerodo. |

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

## PrestaShop .htaccess

Parduotuvių repozitorijos `.htaccess` dažniausiai nelaiko, o be jo draugiški URL grąžina 404. Setup jį sugeneruoja PrestaShop `Tools::generateHtaccess()` (tas pats, ką daro BO *Shop Parameters → Traffic & SEO → Save*) veikiančiame `php-fpm`:

- `make up`, `make setup`, `make restart` – jei `.htaccess` nėra;
- `make db-import`, `make db-load` ne prod – visada iš naujo: after-import parduotuvėms davė vietinius domenus, o senas `.htaccess` perrašinėja senus.

Kas parašyta už PrestaShop `~~start~~`/`~~end~~` žymių, lieka. Nepavykus (pvz. DB nepasiekiama) setup tik įspėja.

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

- `PS_RELEASE=on` kuriant PHP image'ą įdeda `PS_VERSION` release į `/opt/prestashop/release/<versija>`. PS 1.7+ release imamas iš oficialaus `prestashop/prestashop` image'o, PS 1.6 – iš GitHub zip (`PS_RELEASE_SHA256` gali užfiksuoti jo kontrolinę sumą). Release'as paruošiamas atskirame build etape ir į image'ą įdedamas vienu sluoksniu (apie 0,5 GB).
- `make setup` žingsnis `app-prepare` nukopijuoja **tik** `PS_RELEASE_PATHS` katalogus. Kiekvienas katalogas sinchronizuojamas pilnai: senas turinys pašalinamas ir nukopijuojamas release turinys.
- `admin` kopijuojant pervadinamas į `PS_ADMIN_DIR`, jei jis nurodytas.
- Saugomi katalogai (`var/`, `img/`, `upload/`, `download/`, `cache/`, `config/themes/`, `app/Resources/translations/`, `translations/export/`) iš release'o gauna **tik trūkstamus** failus: `cache/.htaccess`, apsauginius `index.php`, numatytuosius `img/` failus. Esamas failas niekada neperrašomas, todėl turinys ir runtime failai lieka. Parduotuvės repo šių failų laikyti nereikia.
- `install/` niekada nekopijuojamas. Katalogas, kuriame yra parduotuvės Git sekamų failų, atmetamas: release niekada neperrašo sekamo kodo.
- Kopijuojama tik `make setup` metu lokaliai ir build serveryje ruošiant deploy'aus release'ą, niekada konteinerio starto metu. Todėl pašalinti moduliai „neprisikelia“, o `install/` negrįžta.

Pakeitus `PS_RELEASE` ar `PS_VERSION`, paleisk `make setup`.

### Savas composer.json

Jei projektas keičia branduolio priklausomybes, jo `composer.json` ir `composer.lock` laikomi aplinkos repozitorijoje `config/composer/`. `make setup` juos nukopijuoja į parduotuvę ir paleidžia `composer install` (stage/prod – be dev paketų). Tada release `vendor/` nekopijuojamas, net jei jis nurodytas `PS_RELEASE_PATHS`.

## Parduotuvės .gitignore

Parduotuvės repozitorija laiko **kodą**, ir tik jį. Viskas, ką git ignoruoja, priklauso vienai iš kelių grupių ir yra
išvardinta aiškiu keliu nuo parduotuvės šaknies. Viskas kita sekama. Sprendimai ir jų priežastys:
[ADR 2](../adr/0002-the-shop-repository-holds-code-only.md).

**Taisyklės**

- **Vienas `.gitignore`, šaknyje.** Modulių ir temų `.gitignore` (jų autorių) nelaikomi: jų taisyklės perkeliamos į
  šaknies failą, keliais nuo šaknies (`data/*.xml` faile `modules/x/.gitignore` → `/modules/x/data/*.xml`), tai, ką
  šaknies failas jau dengia, išmetama. Savo `.gitignore` palieka tik composer paketai `vendor/` kataloguose. Tai
  tikrina commit hook'as ([F skyrius](08-kodo-kokybe.md#f-patikros-prieš-commitą-ir-pushą-git-hookai)).
- **Jokių taisyklių pagal plėtinį ar katalogo vardą bet kur** (`*.png`, `*.svg`, `cache/`, `log/`, `.htaccess`,
  `composer.lock`, `tests`, `vendor/`, `bin/*`): jos tyliai paslepia modulių kodą. Leidžiamos tik įrankių būsenos
  taisyklės, kurios bet kur reiškia tą patį (`.idea/`, `.vscode/`, `node_modules/`, `.phpunit.result.cache`,
  `.DS_Store`, `.asana-memory-session`).
- **Komentarai paprasti, po vieną eilutę**, prasideda grupės numeriu: `# 3. Content: …`, `# 5c. PrestaRock modules:
  content (as group 3)`. Taisyklė, kuri dėl eilės stovi kitos grupės viduje, gauna savo grupės komentarą.
- **Grupės:**

| Grupė | Kas | Pavyzdžiai |
| --- | --- | --- |
| 1. Release | Nepakeistas PS branduolys iš release'o: `PS_RELEASE_PATHS` ir šaknies `composer.json`/`composer.lock` | `/vendor/`, `/bin/`, `/docs/`, `/translations/cldr/`, `/translations/default/`, `/config/defines.inc.php`, `/themes/classic/`, admin katalogas (`PS_ADMIN_DIR`) |
| 2. Generated | Ką rašo setup, PrestaShop ar build'as | `/app/config/parameters.php`, `/.htaccess`, `/robots.txt`, `/*sitemap*.xml`, `/var/`, `/config/themes/`, `/config/xml/*.xml`, `/modules/*/config*.xml`, `/modules/*/logs/`, temos build'o rezultatas (`/themes/x/assets/js/`, `/themes/x/assets/css/`) |
| 3. Content | LIVE failai, kurių deploy'us neliečia | `/img/`, `/upload/`, `/download/`, `/cache/` (apsaugas `index.php`/`.htaccess` atneša setup iš release'o), `/invoices/`, `/translations/*-*/` (kalbų paketai), `/translations/export/`, `/modules/*/translations/`, `/mails/*/lang.php` (laiškų temos), `/modules/*/cache/`, `/modules/*/var/` |
| 4. Tools | Redaktorių, AI ir build įrankių būsena | `.idea/`, `.vscode/`, `.claude/`, `node_modules/`, `.phpunit.result.cache`, `/deploy/` |
| 5. Atskiri moduliai | Vieno modulio keliai, sugrupuoti pagal kūrėją: PrestaShop (5a), trečiųjų šalių, PrestaRock; kiekvienoje – generated ir content | `/modules/ps_banner/img/`, `/modules/prpigu/data/pigu_product_export.xml`, `/modules/prproductlogistics/config/credentials.json` |

**Kas yra kodas (sekama):** modulių `vendor/` (įskaitant `psr/log`, `psr/cache`), testai ir jų fixture'ai, laiškų
šablonai `mails/` (`*.html`, `*.txt`; ir modulių bei temos `mails/`), modulių `composer.lock`, modulių katalogų apsaugos (`.htaccess`,
`index.php`, kurių joks release neatneša, pvz. `modules/prvarlefeed/var/.htaccess`), temų ir modulių paveikslėliai bei
šriftai, `override/`, `admin-api/`, `js/`, `pdf/`.

**Kas ne kodas, nors atrodo:** modulių `config.xml` ir `config_<iso>.xml` (juos rašo PrestaShop,
`Module::_generateConfigXml()`), laiškų temos `mails/<iso>/lang.php` (rašo BO vertimai; programuotojo temos keliauja
[vertimų migracija](09-aplinkos.md#vertimai-translations)), kalbų paketai `translations/<locale>/` (rašo paketų diegimas BO ir vertimo moduliai,
pvz. `ets_translate`), build'o rezultatas (`assets/`, `views/js/front/` – jį kuria
[`config/deploy/build.sh`](13-php-ir-build-irankiai.md) kiekviename release'e), modulio paslaptys
(`config/credentials.json`).

**Kai keiti `.gitignore`**

1. Sąrašą, ką pakeitimas atidengia ar paslepia, pasitikrink su LIVE kopija (pvz. FTP), ne tik su lokaliu medžiu:
   lokali kopija dažnai senesnė (pvz. LIVE kalbų paketai turėjo 44 failais daugiau). `git status --ignored` prieš ir po
   pakeitimo turi skirtis tik tuo, ką norėjai pakeisti.
2. Failą, kuris jau sekamas, `.gitignore` neišima: jis lieka sekamas. Išimti – `git rm --cached` (diske lieka).
3. **Kol serveris kodą gauna kitaip nei setup deploy'umi** (pvz. webhook'as `git checkout -f master`), `git rm --cached`
   po merge'o **ištrina tą failą serveryje**. Setup deploy'us saugomų kelių netrina niekada, todėl tokius pakeitimus
   merge'ink tik serveriui perėjus į setup deploy'ų. Tas pats su naujai sekamais failais: `checkout -f` perrašo
   serverio kopiją repo versija, todėl prieš merge'ą palygink juos su LIVE (hash'ais).
4. Failas, kurio niekas negeneruoja ir neatneša (modulio apsauga, testas), iš git neišimamas, net jei taisyklė jį laiko
   ignoruotinu: pataisyk taisyklę (`/modules/*/var/*` + `!/modules/*/var/.htaccess`, ne `/modules/*/var/`).

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

`PS_DEBUG_IPS` – tikslūs IPv4/IPv6 adresai per kablelį, be portų ir CIDR. Su `ip` sąrašas privalomas. PHP lygina `REMOTE_ADDR`, kurį Apache gauna iš patikimo proxy (žr. [tikras kliento IP](05-servisai.md#ip-apribojimai)). Išjungimui visada rašyk `off` – vien eilutės ištrynimas gali palikti paveldėtą reikšmę. `defines_custom.inc.php` neturi iš anksto apibrėžti `_PS_MODE_DEV_`. `PS_DEBUG_MODE` nustatytas be `config/defines.inc.php` yra klaida. Išimtis – tuščias ar dar neegzistuojantis `app/public`: tada `make up` parašo `No shop code in … yet`, praleidžia visus PrestaShop žingsnius ir nieko ten neįrašo, kad `git clone` į tą katalogą veiktų.

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
