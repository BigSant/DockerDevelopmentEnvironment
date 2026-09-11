# 3. Nustatymų žinynas

[Turinys](README.md) · [Atgal](02-pradzia.md) · [Toliau: Compose](04-compose-ir-dockerfile.md)

## Kur rašyti reikšmę

Pavyzdys: visur reikia PHP 8.1, bet DB slaptažodis vietoje ir prod skirtingas.

`env/common.env`:

```dotenv
PROJECT_NAME=demo
PHP_VERSION=8.1
PROFILE=prestashop
```

`env/local.env`:

```dotenv
DOMAIN=demo.localhost
LOCALHOST_PORT=31820
LOCALHOST_PORT_SSL=31821
DATABASE_NAME=demo
DATABASE_USER=demo
DATABASE_PASSWORD='vietinis-pavyzdys-pakeisk'
```

`env/prod.env`:

```dotenv
DOMAIN=parduotuve.example.com
LOCALHOST_PORT=80
LOCALHOST_PORT_SSL=443
DATABASE_NAME=demo_prod
DATABASE_USER=demo_prod
DATABASE_PASSWORD='atskiras-prod-pavyzdys-pakeisk'
```

Čia pateikta reikšmių atskyrimo iliustracija. Vien `prod.env` neparuošia gamybinio serverio, TLS, DB teisių ir aplikacijos – [prod paruošimas aprašytas atskirai](09-aplinkos.md).

`ENV=test` neskaito `local.env`. Eiliškumas yra:

```text
setup/docker/.env → projekto env/common.env → projekto env/<ENV>.env
```

Vėlesnė reikšmė pakeičia ankstesnę. Runner išvalo iš shell tuos raktus, kurie deklaruoti skaitomuose env failuose, ir pats nustato techninius kelius. Todėl projekto nustatymus laikyk failuose. `make PHP_VERSION=8.3 up` nėra dokumentuotas PHP versijos keitimo būdas; keisk env ir perstatyk atvaizdą.

Naujam projektui `COMPOSE_PROFILES` nereikalingas. Servisus aprašyk YAML: servisas be `profiles:` paleidžiamas, kai jo YAML įtrauktas. Aplinkos papildymus laikyk `compose/local.yaml` ar `compose/prod.yaml`.

Env nėra shell scriptas. Nerašyk ten `$(komanda)`, nenaudok `source env/local.env`. Slaptažodžiams su `$` saugiau naudoti viengubas kabutes. Savus sudėtingus simbolius patikrink per `make check`; paslapčių nekopijuok iš išplėsto Compose į pokalbius. Interpoliavimo taisyklės: [Docker dotenv žinynas](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

## Tapatybė, profilis ir servisai

Lentelių „numatyta“ reiškia dabartinį bendrą setup, jeigu projektas nieko nepakeitė. „Tuščia“ nereiškia, kad reikšmė visada neprivaloma.

| Raktas | Numatyta / leidžiama | Ką daro ir kur naudoti |
| --- | --- | --- |
| `PROJECT_NAME` | Bendrame faile tuščias; būtinas. Mažosios `a-z`, skaitmenys, `_`, `-`, pirmas simbolis raidė/skaitmuo | Projekto konteinerių, tinklo ir atvaizdų vardų pagrindas. Pvz. `forsena`; laikyk `common.env`. Pakeitus atsiras kitas Compose projektas. |
| `PROFILE` | Tuščias; bendras PHP | `prestashop` įjungia PS veiksmus. `akeneo` pasirenka Akeneo atvaizdų papildymus. Tuščias – bendras PHP. Kitas vardas savaime nesukuria integracijos. |
| `COMPOSE_PROFILES` | Tuščias; pasirenkama Compose galimybė | Kableliais atskirti pasirenkami servisų profiliai: `redis,mailpit`. Gali būti tuščias. Neprideda YAML apraše nesančio serviso. |
| `PROJECT_COMPOSE_FILES` | Tuščias; grouped šablone `compose/qa.yaml compose/doctrine.yaml compose/redis.yaml` | Papildomų YAML sąrašas per tarpus, skaitomas iš kairės į dešinę. Keliai nuo Makefile katalogo, turi likti jame. |

`PROFILE=prestashop` nusako **aplikacijos rūšį**, `ENV=test` – **aplinką**, o `PROFILES=redis` – **papildomus servisus**. Tai trys atskiri pasirinkimai.

## Adresai, HTTP ir patikros

| Raktas | Numatyta | Ką keisti ir konkretus pavyzdys |
| --- | --- | --- |
| `DOMAIN` | Tuščias, HTTP aplinkai būtinas | Hostname be `http://`, be porto ir kelio: `demo.localhost`. Bootstrap tikrina, ar jis veda į šį kompiuterį. |
| `LOCALHOST_PORT` | `0` | Kompiuterio HTTP portas, praktiškai rinkis laisvą `1..65535`; bootstrap `0` pakeičia laisvu. Pvz. `31820`. |
| `LOCALHOST_PORT_SSL` | `0` | Kompiuterio HTTPS portas. Pvz. `31821`; turi nesikirsti su kitais projektais. |
| `DOCUMENT_ROOT` | `/` | Poaplankis **aplikacijos checkout viduje**. `public` reiškia `/var/www/html/public`, o ne kitą host katalogą. |
| `WEBSERVICE_TIMEOUT` | `90` sekundžių | Nginx proxy užklausos timeout, pvz. `300` ilgam importui per HTTP. Tai ne `make up` laukimo laikas. |
| `WHITELISTED_IP` | `allow all;` | Perduodamas Nginx inicializatoriui. Esamame proxy pagrindiniame šablone nėra veikiančio bendro IP filtro vietos: vien šios reikšmės nelaikyk prieigos ribojimu. Ribojimą dėk į konkretų Nginx server/location config. |
| `SQL_DOMAIN` | `DOMAIN`, jei kintamasis nenustatytas | Tik SQL `${DOMAIN}` pakeitimui. Gali būti `demo.test.localhost:31830`, nors `DOMAIN` turi likti be porto. Tuščia reikšmė SQL vykdyme taip pat grįžta prie `DOMAIN`. |

`make up` laukia Docker sveikatos patikrų, bet HTTP užklausų į aplikaciją nesiunčia.

## DB prisijungimai ir failų savininkai

| Raktas | Numatyta | Paskirtis / atnaujinimas |
| --- | --- | --- |
| `DATABASE_NAME` | Tuščias, DB komandoms būtinas | Pvz. `demo`; laikyk aplinkos faile. Turi sutapti su veikiančio DB konteinerio `MYSQL_DATABASE`. |
| `DATABASE_USER` | Tuščias | Aplikacijos DB vartotojas. Pirmą kartą kuriamas inicializuojant tuščią DB. |
| `DATABASE_PASSWORD` | Tuščias | Aplikacijos DB slaptažodis. Bendras dev Compose tą pačią reikšmę naudoja ir `MYSQL_ROOT_PASSWORD`. Esamos DB slaptažodžio pakeitimas env faile jo serverio viduje nepakeičia. |
| `DATABASE_HOST` | `127.0.0.1` | Istorinis DB konteinerio nustatymas: MySQL gauna `MYSQL_HOST`, MariaDB – `MYSQL_ROOT_HOST`. Tai **ne** įprastas PHP → DB adresas. |
| `APP_DATABASE_HOST` | `database` | PHP runtime gauna kaip `DATABASE_HOST`. Keisk tik jei aplikacija sąmoningai jungiasi kitur; PS testinė aplinka reikalauja `database`. |
| `APP_DATABASE_PORT` | `3306` | PHP runtime `DATABASE_PORT`. Tai DB vidinis portas, ne naršyklės ar hosto persiųstas portas. PS tikrina `1..65535`. |
| `DATABASE_UID` | Dabartinio vartotojo UID | Bendras **MySQL** servisas leidžia parinkti pastovų duomenų savininką. Pvz. `1001`. Katalogo teisės turi atitikti. |
| `DATABASE_GID` | Dabartinio vartotojo GID | MySQL grupė. MariaDB šių dviejų override nenaudoja; jos `user` keičiamas Compose. |

Bendri pirmojo DB starto scriptai suteikia dev aplikacijos vartotojui plačias teises, kad veiktų vietinės migracijos. `ENV=prod` pats jų nesusiaurina. Gamybiniam naudojimui atskirai paruošk DB paskyras ir savo inicializavimo politiką.

## PrestaShop debug

Šie laukai veikia tik su `PROFILE=prestashop`. Bendras PHP servisas juos jau perduoda konteineriui; papildomo Compose aprašo nereikia.

| Env raktas | Numatytoji reikšmė | Ką rašyti ir kas įvyksta |
| --- | --- | --- |
| `PS_DEBUG_MODE` | Runner palieka tuščią, naujas grouped šablonas nustato `off` | `off` išjungia debug; `on` įjungia visoms užklausoms ir CLI; `ip` įjungia tik leidžiamiems HTTP klientų IP, CLI išjungia. Tuščias arba nepateiktas raktas palieka esamą `defines.inc.php` nevaldomą ir **neatšaukia ankstesnio pakeitimo**. |
| `PS_DEBUG_IPS` | Tuščias | Kableliais atskirti tikslūs IPv4 / IPv6 adresai, pvz. `192.0.2.10,2001:db8::10`. Su `ip` būtinas bent vienas. Tarpai aplink adresus leidžiami; domenai, portai, CIDR (`/24`) ir tušti sąrašo elementai atmetami. Su `off` / `on` sąrašas tik patikrinamas, prieigos neriboja; su tuščiu režimu ignoruojamas. |

Režimo vardai rašomi mažosiomis raidėmis, be papildomų tarpų. `true`, `false`, `1`, `0` nėra režimų vardai. Netinkama konfigūracija sustabdo PS paruošimą prieš DB prisijungimų failo pakeitimą.

Nustatymus taikyk su `make up` (kitai aplinkai – `make up ENV=prod`). Jie keičiami paleidžiant PHP, ne kuriant image. Visas paruošimas, realios situacijos ir proxy paaiškinimas yra [aplikacijos skyriuje](06-aplikacija.md#f-prestashop-debug-iš-env).

## Programų versijos

Šiuos raktus paprastai laikyk `common.env`. Keitimo seka: `make check` → `make build` → `make up`. DB variklio versijos keitimas papildomai reikalauja suderinamo duomenų perkėlimo; nepakanka perstatyti konteinerį.

| Raktas | Dabartinis default | Ką pasirenka |
| --- | --- | --- |
| `PHP_VERSION` | `8.1` | PHP Dockerfile katalogą. Šaltiniuose yra `5.6`, `7.0`–`7.4`, `8.0`–`8.5`; tai negarantuoja, kad senas receptas šiandien susikurs be papildomų pataisų. |
| `XDEBUG_VERSION` | `3.1.5` | Xdebug plėtinio versiją. Turi derėti su pasirinktu PHP; PS 1.6 nereikia automatiškai priskirti šio default. |
| `COMPOSER_VERSION` | `2.7.6` | Composer atvaizdo/programos versiją. Taip pat turi derėti su PHP. |
| `NODE_VERSION` | `22.23.1` | Per nvm į PHP bazinį atvaizdą diegiamą Node versiją. |
| `APCU_VERSION` | `5.1.28` | APCu versiją tuose PHP receptuose, kurie ją naudoja; neįjungia papildomo serviso. |
| `PHPSTAN_PHAR_VERSION` | `1.12.33` | Į PHP atvaizdą kepamo PHPStan PHAR versiją. Tai ne atskiro QA serviso versija. |
| `PHP_PHPSTAN_VERSION` | `2.2.2-php8.1` | Atskiro `php-phpstan` įrankio bazinio atvaizdo tag. |
| `PHP_CS_VERSION` | `3.95.10-php8.1` | `php-cs` / PHP-CS-Fixer atvaizdo tag. |
| `DOCTRINE_MIGRATIONS_VERSION` | `3.8` | Dockerfile naudoja Composer apribojimą `^3.8`, todėl tai ne užfiksuotas patch leidimas. |
| `NGINX_VERSION` | `1.26.0` | Nginx proxy bazinio atvaizdo versiją. |
| `APACHE_VERSION` | `2.4.59` | Apache bazinio atvaizdo versiją. |
| `MYSQL_VERSION` | `8.4.0` | MySQL versiją, kai pagrinde pasirinktas MySQL aprašas. |
| `MARIADB_VERSION` | `11.3.2` | MariaDB versiją, kai pagrinde pasirinktas MariaDB aprašas. Vien raktas MySQL nepakeičia MariaDB. |
| `PMA_VERSION` | `5.2.3` | Pasirenkamo phpMyAdmin versiją. |
| `MAILPIT_VERSION` | `1.18.3` | Pasirenkamo Mailpit versiją. |
| `PLAYWRIGHT_VERSION` | `1.61.1` | Naršyklių atvaizdo ir į jį diegiamo `@playwright/test` versiją. Jos turi sutapti. |
| `REDIS_VERSION` | Bendrame setup nėra; grouped šablone `7.4` | Pasirenkamo `redis:<versija>-alpine` atvaizdo tag. Jis atsisiunčiamas su `make pull`, ne gaminamas bendru Dockerfile. |

Čia užrašytos repozitorijos reikšmės, o ne rekomendacija kiekvienam projektui visada rinktis būtent jas. CI patikrintos konfigūracijos ir DB komandų versijos aprašytos [atnaujinimų skyriuje](10-kasdienis-darbas.md).

## PHP HTTP ribos

| Raktas | Default | Pavyzdys ir matavimo vienetas |
| --- | --- | --- |
| `PHP_MEMORY_LIMIT` | `512M` | `1024M`: vieno HTTP PHP vykdymo atminties riba. |
| `PHP_MAX_EXECUTION_TIME` | `30` | `300`: sekundės HTTP PHP vykdymui. |
| `PHP_UPLOAD_MAX_FILESIZE` | `20M` | `64M`: vieno įkeliamo failo riba. |
| `PHP_POST_MAX_SIZE` | `20M` | `70M`: visos POST užklausos riba; turi tilpti failas ir kiti formos duomenys. |

Šios reikšmės naudojamos FPM pool `php_admin_value`. Vien `.ini` pakeitimas gali nepakeisti HTTP ribos, nes pool ją nustato atskirai. CLI `php -i` gali rodyti kitą reikšmę nei HTTP. [Servisų skyriuje](05-servisai.md) parodyta, kaip valdyti abu.

## Projekto keliai ir QA komandos

| Raktas | Default | Reikšmės pavyzdys / paaiškinimas |
| --- | --- | --- |
| `APP_SOURCE_DIRECTORY` | Vietoje `app/public`; testui ir kitoms aplinkoms atskiri default | Kelias nuo `<projektas>/`, pvz. `app/kodas`. Turi likti projekte; testui – `.generated/test` viduje. |
| `DATA_DIRECTORY` | Vietoje `data`; kitoms aplinkoms atskiri default | Kelias nuo `<projektas>/`, pvz. `data/local-v2`. Nekeisk jo kaip slapto DB „atnaujinimo“: kitas kelias reiškia kitus failus. |
| `POST_IMPORT_SQL_DIRECTORY` | Tuščias; būtinas `db-import` | `app/database/after-import`; tušti common/aplinkos katalogai leidžiami. |
| `SCHEMA_DIRECTORY` | Tuščias; būtinas schema komandoms | `app/database/schema`; hook veikia repozitorijoje, kuriai priklauso šis katalogas. |
| `FIXTURES_DIRECTORY` | Tuščias; būtinas fixture komandoms | `app/database/fixtures`; rinkinys parenkamas komandos argumentu. |
| `PHPSTAN_BASELINE_FILE` | Tuščias; grouped šablone `/tmp/phpstan/baselines/phpstan.neon` | **Konteinerio absoliutus kelias**, į kurį `make phpstan-baseline` rašo baseline. Reikia atitinkamo writable mount. |
| `PLAYWRIGHT_COMMAND` | `npx playwright test`; grouped turi ir `--config=/e2e/config/playwright.config.cjs` | Argumentų eilutė, ne shell scriptas. Su `&&` dviejų komandų nepaleisi. |

Prisimink du skirtingus atskaitos taškus: `SCHEMA_DIRECTORY=app/database/schema` skaičiuojamas nuo `forsena/`, o `PROJECT_COMPOSE_FILES=compose/redis.yaml` – nuo `forsena/app/`.

## Techniniai kintamieji, kuriuos paruošia runner

Šiuos vardus naudok Compose `${...}` išraiškose. Paprastai jų nerašyk į env ir nebandyk nukreipti vieno projekto į kito duomenis.

| Kintamasis | Reikšmė `forsena/app`, `ENV=local` atveju |
| --- | --- |
| `ROOT_DIRECTORY` | `/home/tomas/Projects/setup/docker` – bendrų Docker šaltinių vieta. |
| `PROJECT_DIRECTORY` | `/home/tomas/Projects/forsena` – projekto šaknis. |
| `PROJECT_APP_DIRECTORY` | `…/forsena/app`. |
| `PROJECT_DOCKER_DIRECTORY` | Makefile katalogas, čia taip pat `…/forsena/app`. |
| `PROJECT_CONFIG_DIRECTORY` | `…/forsena/app/config`; istoriniame `root` išdėstyme gali skirtis nuo Docker config vietos. |
| `PROJECT_WEB_DIRECTORY` | Pasirinktos aplikacijos kelias; vietoje `…/app/public`. |
| `PROJECT_DATA_DIRECTORY` | Pasirinktos aplinkos duomenų bazinis kelias; vietoje `…/forsena/data`. |
| `DOCKERFILE_DIRECTORY` | Projekto Makefile katalogas, jei jame yra `Dockerfile`; kitu atveju bendras `setup/docker`. |
| `ENV` | Make pasirinkimas: `local`, `test`, `stage`, `prod`. |
| `BUILD_ENV` | `test` atveju `local`; kitu atveju sutampa su `ENV`. |
| `IMAGE_ENV` | Tokia pati taisyklė kaip `BUILD_ENV`; testas gali naudoti vietinius atvaizdus. |
| `HOST_UID`, `HOST_GID` | Komandą vykdančio vartotojo skaitiniai ID. |
| `SETUP_IMAGE_SUFFIX` | Tuščias arba `-s` ir kontrolinės sumos dalis. |
| `PHP_INI_SCAN_DIR` | PHP/cron servisų environment nurodyti papildomi ini paieškos katalogai. Savavališkas jo pakeitimas gali išjungti projekto `.ini` skaitymą. |
| `COMPOSE_DISABLE_ENV_FILE` | `true`; atsitiktinis shell vietos `.env` netampa papildomu šaltiniu. |
| `BUILDX_NO_DEFAULT_ATTESTATIONS` | `1`; runner nustatyta build parinktis. |
| `BUILDX_METADATA_PROVENANCE` | `disabled`; runner nustatyta build parinktis. |

`BASE_IMAGE` yra projekto Dockerfile build argumentas, kurį grouped `compose/common.yaml` surenka iš PHP bazinio atvaizdo vardo. `PROFILES` ir `EXTRA_COMPOSE_FILES` yra vidiniai `project-settings.yaml` laukai, atitinkantys pasirenkamus `COMPOSE_PROFILES` ir `PROJECT_COMPOSE_FILES`.

Runner pašalina iš shell `COMPOSE_PROJECT_NAME`, `COMPOSE_FILE`, `COMPOSE_ENV_FILES` ir `ENV_FILE`, kad svetimo projekto eksportai nepasirinktų kitos aplinkos. [Make ir CLI argumentai](11-komandos-ir-klaidos.md) aprašyti atskirai.

## Savas parametras tik vienam projektui

Situacija: tavo aplikacijai reikia `INTEGRATION_URL`.

1. Į `env/local.env` įrašyk `INTEGRATION_URL=https://sandbox.example.com`.
2. Į esamą `compose/common.yaml` PHP servisą įdėk:

```yaml
services:
  php-fpm:
    environment:
      INTEGRATION_URL: ${INTEGRATION_URL:?Įrašyk INTEGRATION_URL}
```

3. Aplikacijoje arba jos paruošimo scripte skaityk `getenv('INTEGRATION_URL')`.
4. Vykdyk `make check` ir `make up`.

Vien env eilutė nėra automatinis perdavimas PHP. Šis aiškus prijungimas leidžia vienam projektui turėti savą konfigūraciją nekeičiant bendro setup.

## Cache, resursų ir hibridinių aplinkų nustatymai

Visi papildomi runtime jungikliai, jų reikšmės ir pirmenybė aprašyti [12 skyriuje](12-cache-ir-hibridines-aplinkos.md#visi-šio-sluoksnio-nustatymai). Aplinkos vardas ir cache režimas parenkami nepriklausomai.

`make bootstrap` automatiškai paruošia kompiuterio Nginx maršrutą iš `http://<DOMAIN>/` ir HTTPS į projekto HTTP portą. Atskiro env jungiklio nėra; pats projekto failų kūrimas host konfigūracijos nekeičia.

`PROJECT_DISPLAY_NAME`: neprivalomas PhpStorm vardo pakeitimas per env. Generatorius
šio lauko nekuria. Be jo `make ide-init` ir `make bootstrap` išsaugo esamą
`.idea/.name`; jei IDE vardo nėra, naudoja `PROJECT_NAME`.
`create-project MelgaMCP` į env įrašo `PROJECT_NAME=melga-mcp`, o į `.idea/.name` –
`MelgaMCP`. Docker naudoja `melga-mcp`, DB vardas ir vartotojas – `melga_mcp`.

Naujai kuriamo projekto `env/common.env` yra tik `PROJECT_NAME`. Privačiame env iš pradžių yra tik
`DOMAIN` ir trys DB prisijungimų reikšmės. Portus įrašo `make bootstrap`.
Atvaizdų žymos pagal build konfigūraciją apskaičiuojamos automatiškai.
