# 13. PHP suderinamumas ir JS/CSS surinkimas

[Turinys](README.md)

## Kur yra įrankiai

PHP 8.x konteineryje yra PHP, Composer, Node, npm, npx ir Webpack. Jiems nereikia
atskirų servisų ar naujų katalogų projekte. Xdebug diegiamas tik `local` surinkimo
etape; `stage` ir `prod` jo neturi. Seni PHP 5.x / 7.x receptai neturi tokio paties
Node įrankių komplekto.

## Versijos yra env failuose

Bendri pasirinkimai saugomi `setup/docker/.env`. Projekto `env/common.env` juos
pakeičia visoms aplinkoms, o `env/local.env`, `env/stage.env` arba `env/prod.env` –
tik pasirinktai aplinkai. Versijos nesirenkamos pagal aplikacijos pavadinimą.

Pavyzdys PHP 8.4 projektui, kurio tema naudoja Node 22:

```dotenv
PHP_VERSION=8.4
XDEBUG_VERSION=3.5.3
COMPOSER_VERSION=2.10.3
APCU_VERSION=5.1.28
NODE_VERSION=22.23.1
NPM_VERSION=10.9.8
WEBPACK_VERSION=5.94.0
WEBPACK_CLI_VERSION=5.1.4
PHPSTAN_PHAR_VERSION=1.12.33
SUPERCRONIC_VERSION=0.2.29
NVM_VERSION=v0.40.4
```

Servisams atskirai naudojami `NGINX_VERSION`, `APACHE_VERSION`, `MYSQL_VERSION`,
`PMA_VERSION`, `MAILPIT_VERSION`; pasirenkamų servisų versijos surašytos
[nustatymų žinyne](03-nustatymai.md). Vien versijos įrašymas serviso neprideda.
Keičiant NVM ar Supercronic leidimą reikia pakeisti ir patikros sumą:
`NVM_INSTALL_SHA256` arba `SUPERCRONIC_SHA1SUM`. Jos turi būti iš patikrinto leidimo.

`PHP_VERSION` šiuo metu parenka ir Dockerfile katalogą, todėl rašyk `8.4`, o ne
`8.4.12`. Tai PHP šakos pasirinkimas, ne nekintantis konkretaus atvaizdo digest.
Aplikacijos Composer ir npm bibliotekos lieka `composer.lock` ir
`package-lock.json`. `WEBPACK_VERSION` valdo konteinerio globalų Webpack;
`npm run build` pirmiausia naudoja temos įdiegtą Webpack iš jos priklausomybių.

Pakeitus surinkimo versijas:

```bash
make check
make build
make up
```

`build` surenka atvaizdus, `up` pakeičia veikiančius konteinerius. Vien env failo
redagavimas jau veikiančio PHP nepakeičia. Prieš keičiant PHP patikrink konkrečios
aplikacijos ir modulių suderinamumą. Xdebug versija taip pat turi
[derėti su PHP](https://xdebug.org/docs/compat).

## Pavyzdys: Forsenos temos surinkimas

Komandas vykdyk `forsena/app` kataloge. `dir` yra kelias **nuo `public`**:

```bash
make composer
make npm
make npm dir=themes/framework/_dev cmd=ci
make npm dir=themes/framework/_dev cmd='run build'
make npm dir=themes/framework/_dev cmd='run watch'
```

Pirmos dvi komandos parodo įrankių versijas. `ci` įdiegia priklausomybes pagal
`package-lock.json`. `build` sukuria temos JS/CSS; `watch` stebi pakeitimus ir
sustabdomas su Ctrl+C. Nerašyk `dir=public/themes/...`, nes `public` konteineryje
jau yra `/var/www/html`.

Composer veikia analogiškai: `make composer dir=themes/framework cmd=validate`.
`install` vykdyk tik kataloge, kuriame yra `composer.json`. Forsenos aplikacijos
šaknyje atkurtas PrestaShop 9.0.2 `composer.json`. Iš jo pašalintos projekte
nesančios `classic` ir `hummingbird` temos; HTMLPurifier atnaujintas į 4.19.0
PHP 8.4 palaikymui. Kitų bibliotekų PHP reikalavimai tikrinami prieš perjungiant
aplinką; `composer.lock` sutikrintas. `composer install` gali vykdyti aplikacijos pluginus ir keisti
modulių failus; jo nereikia vien Docker aplinkai perjungti.

PHP servisas vykdo komandas su host vartotojo UID, kad nesukurtų root priklausančių
failų. Npm podėlis pagal nutylėjimą yra `/tmp/setup-npm-cache`, Composer namų
katalogas – `/tmp/setup-composer`. Juos galima pakeisti per `NPM_CONFIG_CACHE` ir
`COMPOSER_HOME` projekto env, jei naujas kelias pasiekiamas ir rašomas konteineryje.
`/tmp` duomenys neišlieka pakeitus konteinerį.

## PrestaShop 9 patikra

Reikalavimai sutikrinti 2026-09-12 su
[oficialia PrestaShop dokumentacija](https://devdocs.prestashop-project.org/9/basics/installation/system-requirements/).

| Tikrinama | Reikalavimas | Forsenos būsena patikros metu |
| --- | --- | --- |
| PHP | PS 9.0: 8.1–8.4; PS 9.1: 8.1–8.5 | Kode PS 9.0.2. Env pakeista į 8.4; veikiantis konteineris pradinės patikros metu dar naudojo 8.1.34. |
| Plėtiniai | curl, dom, fileinfo, gd, iconv, intl, json, mbstring, openssl, PDO, pdo_mysql, SimpleXML, zip | Visi rasti veikiančiame konteineryje. |
| Atmintis | Rekomenduojama bent 512M | 512M; FPM riba valdoma `PHP_MEMORY_LIMIT`. |
| PHP URL nustatymai | `allow_url_fopen=On`, `allow_url_include=Off` | Atitinka. |
| Serveris ir DB | Apache 2.4+; MySQL 5.7+ arba MariaDB 10.2+ | Pasirinkti Apache 2.4.59 ir MySQL 8.4.0 atitinka minimalius reikalavimus. |
| Kodo surinkimas | Composer, Node 20.x, npm ir Webpack | Node 22 nėra dokumentacijoje nurodyta core surinkimo versija. Temos poreikius tikrink jos `package.json`. |

Forsenos temos kopijoje `npm ci` ir `npm run build` sėkmingai įvykdyti su Node
22.23.1 ir npm 10.9.8. Temos lockfile parinko Webpack 5.104.1. Surinkimas pateikė
29 įspėjimus apie temos priklausomybes ir išvestį; aplikacijos failai nekeisti.
Tai nepatvirtina viso PrestaShop core surinkimo su Node 22.

Tai infrastruktūros patikra, ne visos parduotuvės ar jos modulių veikimo garantija.
Node nereikalingas jau surinktos parduotuvės PHP užklausoms aptarnauti.

GD buvo įdiegtas, tačiau be JPEG, WebP ir FreeType. PHP 7.4–8.5 Dockerfile pataisyti,
kad įjungtų šiuos formatus pagal
[PHP GD surinkimo parinktis](https://www.php.net/manual/en/image.installation.php).
Tai aktualu prekių paveikslėlių apdorojimui. PHP 8.2 papildytas tuo pačiu Node
įrankių diegimu kaip kitos PHP 8.x versijos. PHP 8.5 receptas nebekompiliuoja
OPcache atskirai, nes jis
[įtrauktas į PHP dvejetainį failą](https://wiki.php.net/rfc/make_opcache_required).

Surinktą vietinį bazinį atvaizdą galima patikrinti neprijungiant aplikacijos ar DB:

```bash
SETUP_PHP_TEST_IMAGE=your-built-local-image python3 -m unittest discover -s tests -p test_php_image_integration.py -v
```

Ši komanda vykdoma `setup` kataloge. Ji tikrina privalomus plėtinius, JPEG/WebP
įrašymą ir nuskaitymą, FreeType, PHP/FPM nustatymus bei įrankių veikimą su UID 1000.
Kiekvienai papildomai PHP versijai reikia atskirai surinkti ir patikrinti atvaizdą.

PHP 8.4.25 ir 8.5.10 vietiniai atvaizdai surinkti su Xdebug 3.5.3 ir Composer
2.10.3. Kiekvieno atvaizdo keturi integraciniai testai praėjo, įskaitant FPM env
reikšmių veikimą. FPM bazinis pool failas įkeliamas po standartinio `www.conf`,
kad standartinės reikšmės neperrašytų mūsų nustatymų. Senesnių PHP receptų
GD pakeitimai peržiūrėti šaltiniuose, bet šioje patikroje atskirai nesurinkti.

CI `php-images` užduotis surenka mūsų PHP 8.4 ir 8.5 Dockerfile bei PrestaShop
profilio papildymus. Versijos saugomos `tests/fixtures/php-images/*.env`.
Tikrinami plėtiniai, GD, įrankiai ir FPM env pakeitimai. Tai atvaizdų, o ne
PrestaShop konkrečios versijos ar modulių suderinamumo testai.

Tą patį galima pakartoti iš `setup` katalogo:

```bash
python3 tests/build_php_image.py tests/fixtures/php-images/8.4.env
SETUP_PHP_TEST_IMAGE=setup-php-image-test python3 -m unittest discover -s tests -p test_php_image_integration.py -v
```

HTMLPurifier HTML valymo patikra su PHP 8.4 praėjo. Keistų priklausomybių ankstesni failai išsaugoti
projekto `.generated/dependency-backups`. HTMLPurifier PHP palaikymas aprašytas
[4.19.0 leidime](https://github.com/ezyang/htmlpurifier/releases/tag/v4.19.0).
