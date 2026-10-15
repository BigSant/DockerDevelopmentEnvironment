# 3. Nustatymų žinynas

[Turinys](README.md) · [Atgal](02-pradzia.md) · [Toliau: Compose ir Dockerfile](04-compose-ir-dockerfile.md)

## Kur rašyti reikšmę

Reikšmės skaitomos šia tvarka, vėlesnė pakeičia ankstesnę:

```text
setup/docker/.env → projekto env/common.env → projekto env/<ENV>.env
```

- `env/common.env` – bendri projekto nustatymai visoms aplinkoms. Commit'inamas, slaptažodžių jame nėra.
- `env/<ENV>.env` – vienos aplinkos reikšmės ir slaptažodžiai (`local.env`, `test.env`, `stage.env`, `prod.env`). Į Git nededami. Vietoj jų commit'inami `*.env.example` be slaptažodžių.
- `ENV=test` skaito `test.env`, o ne `local.env`.
- Kai setup pats įrašo reikšmę (`create-project`, `make init`, `make bootstrap`, `make test-init`), ją deda po sekcijos antrašte (`# Project`, `# Versions`, `# PrestaShop`, `# Services`, `# Address`, `# Access`, `# Database`, `# PrestaShop keys`, `# Database as code`, `# Runtime`, `# Tools`), sekcijas – šia tvarka; trūkstamą sekciją sukuria. Sekcija baigiasi tuščia eilute. Esama eilutė keičiama vietoje, nežinomi raktai rašomi failo gale. Sąrašas: [env_layout.py](../../docker/env_layout.py).

Pavyzdys: visur PHP 8.4, bet DB slaptažodis kiekvienoje aplinkoje savas.

`env/common.env`:

```dotenv
PROJECT_NAME=demo
PROFILE=prestashop
PS_VERSION=9.0.2
PHP_VERSION=8.4
SERVICES="pma"
```

`env/local.env`:

```dotenv
DOMAIN=demo.local
DATABASE_NAME=demo
DATABASE_USER=demo
DATABASE_PASSWORD='vietinis-pavyzdys-pakeisk'
SERVICES="pma mailpit"
```

Runner'is iš terminalo aplinkos pašalina raktus, kurie paminėti env failuose, todėl `make PHP_VERSION=8.3 up` versijos nepakeis – keisk failą. Taip ankstesnio projekto `export` neperjungia šio projekto. Raktas, kurio env failuose **nėra**, gali ateiti iš proceso aplinkos. Slaptažodžiams (`DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD`, `DATABASE_MONITOR_PASSWORD`, `DBMONITOR_TOKEN`, `METRICS_TOKEN`, PS raktai) yra išimtis: jei env faile jie tušti (`DATABASE_PASSWORD=`), naudojama proceso aplinkos reikšmė. Taip Bitbucket Pipelines ir GitHub Actions perduoda slaptažodžius (žr. [deploy](09-aplinkos.md#bitbucket-pipelines)). Jei slaptažodis įrašytas env faile, laimi failas. Tas pats galioja [`APP_ENVIRONMENT`](#savi-aplikacijos-parametrai-app_environment) vardams, tik be išimties tuščiai reikšmei: env faile paminėtas vardas (net tuščias) proceso aplinkos reikšmę uždengia.

Env failas nėra shell scriptas: nerašyk `$(komanda)` ir nenaudok `source env/local.env`. Slaptažodžiams su `$` naudok viengubas kabutes. Reikšmes su tarpais (pvz. `SERVICES`) rašyk kabutėse. Taisyklės: [Docker dotenv interpoliacija](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

Runner'is skaito tik raktus, išvardytus [project-settings.yaml](../../docker/project-settings.yaml), ir raktus, kuriuos Compose aprašai naudoja tiesiogiai. Visi jie surašyti žemiau.

## Tapatybė, profilis ir servisai

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `PROJECT_NAME` | Privalomas | Konteinerių, tinklo ir image'ų vardų pagrindas. Mažosios raidės, skaitmenys, `_`, `-`. Laikyk `common.env`. |
| `PROJECT_DISPLAY_NAME` | Tuščias | PhpStorm projekto pavadinimas. Be jo išsaugomas `.idea/.name` arba naudojamas `PROJECT_NAME`. |
| `PROFILE` | Tuščias (bendras PHP) | `prestashop` įjungia PS veiksmus, `akeneo` – Akeneo image'o papildymus. Kitas vardas veikia kaip bendras PHP. |
| `SERVICES` | Tuščias | Papildomi CORE servisai per tarpus: `pma mailpit cron dbmonitor backup metrics memcached redis playwright php-phpstan php-cs php-doctrine-migrations`. Nežinomas vardas – klaida. Žr. [servisai](05-servisai.md#servisų-katalogas-services). |
| `COMPOSE_PROFILES` | Tuščias | Compose profiliai savo apraše turimiems servisams. Kasdieniam darbui dažniausiai nereikia. |
| `PROJECT_COMPOSE_FILES` | Tuščias | Papildomi projekto YAML failai (keliai nuo `app/`), skaitomi po `compose/common.yaml` ir prieš `compose/<ENV>.yaml`. |
| `APP_ENVIRONMENT` | Tuščias | Aplikacijos savų env kintamųjų **vardai** per tarpus (be reikšmių), kuriuos setup perduoda `php-fpm` ir `cron`. Laikyk `common.env`. Žr. [Savi aplikacijos parametrai](#savi-aplikacijos-parametrai-app_environment). |
| `SETUP_MIN_VERSION` | Tuščias | Seniausia setup versija, su kuria projektas veikia, pvz. `2.3.0`. Kiekviena `make` komanda pirmiausia palygina ją su setup `VERSION` ir su senesniu setup sustoja, nieko nepakeitusi. Laikyk `common.env`. Jį tikrina setup versijos po 2.2.0; 2.2.0 ir senesnės jį ignoruoja. |

`PROFILE=prestashop` nusako **aplikacijos rūšį**, `ENV=test` – **aplinką**, `SERVICES` – **papildomus servisus**. Tai trys atskiri pasirinkimai.

## Adresai ir prieiga

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `DOMAIN` | Tuščias | Hostname be `http://`, porto ir kelio: `demo.local`. Lokaliai jį parenka `make bootstrap`, testui – `<vardas>.test.local`. Gali būti ir keli hostname'ai, žr. [Keli domenai](#keli-domenai). |
| `LOCALHOST_PORT`, `LOCALHOST_PORT_SSL` | `0` | Projekto HTTP ir HTTPS portai kompiuteryje. `0` – `make bootstrap` parenka laisvą porą. Naršyklėje porto rašyti nereikia. |
| `PUBLISH_ADDRESS` | `127.0.0.1` | Adresas, ant kurio nginx-proxy publikuoja abu portus. `127.0.0.1` (ar `::1`) – projektas pasiekiamas tik per kompiuterio Nginx arba DirectAdmin. Kitas šio serverio adresas (pvz. VPN sąsajos `10.8.0.14`) – klientai tame tinkle jungiasi tiesiai, be DirectAdmin; tada nginx-proxy jų `X-Forwarded-*` antraščių nepriima. Tik vienas IPv4/IPv6 adresas: be porto, CIDR ir zonos; `0.0.0.0` ir `::` atmetami (portai būtų visose sąsajose). Žr. [Tikras kliento IP](05-servisai.md#ip-apribojimai). |
| `DOCUMENT_ROOT` | `/` | Poaplankis aplikacijos viduje, kurį aptarnauja Apache, pvz. `public`. |
| `WEBSERVICE_TIMEOUT` | `90` | Nginx proxy užklausos laikas sekundėmis. |
| `HTTP_FORCE_HTTPS` | `auto` | `auto` = `off` local/test, `on` stage/prod. `on` peradresuoja HTTP į HTTPS, jei užklausa neatėjo per HTTPS. |

### Keli domenai

Kai svetainė atsako keliais hostname'ais (pvz. kiekviena kalba ar multistore parduotuvė savo domene), `DOMAIN` išvardija juos per tarpus. Įrašas yra hostname (mažosiomis raidėmis) arba `DOMAIN_<VARDAS>` kintamojo vardas, kurį apibrėži bet kuriame env faile:

```dotenv
# env/local.env
DOMAIN_LT=demo.local
DOMAIN_LV=lv.demo.local
DOMAIN_EE=ee.demo.local
DOMAIN="DOMAIN_LT DOMAIN_LV DOMAIN_EE"

# env/prod.env
DOMAIN_LT=demo.lt
DOMAIN_LV=demo.lv
DOMAIN_EE=demo.ee
DOMAIN="DOMAIN_LT DOMAIN_LV DOMAIN_EE"
```

- **Vardai, ne `${…}`.** Setup env failų neinterpoliuoja, Compose interpoliuoja: `${DOMAIN_LV}` jiems reikštų skirtingus dalykus. Didžiosios raidės – kintamojo vardas, mažosios – hostname, todėl galima ir maišyti: `DOMAIN="demo.local DOMAIN_LV"`.
- **Pirmas hostname – pagrindinis.** Visur, kur setup naudoja `DOMAIN`, lieka jis vienas: `pma.`, `mailpit.`, `cron.` ir kiti servisų subdomenai, Apache `ServerName`, `.template` failai, after-import `${DOMAIN}`, numatytosios parduotuvės saugus numatytasis po importo.
- **Visi hostname'ai** gauna užklausas: nginx-proxy svetainės `server_name` (su `www.`), Docker tinklo alias'ai, numatytieji `METRICS_URLS`, o local/test – `/etc/hosts`, kompiuterio Nginx maršrutas ir mkcert sertifikatas.
- **DB setup nekeičia.** Kurį hostname gauna kuri parduotuvė, nusprendi pats: after-import SQL gali naudoti `${DOMAIN_LV}` ir kitus `DOMAIN` išvardytus kintamuosius ([after-import](07-duomenu-baze.md)).
- Vienas domenas su keliais (`demo.local/latvija/`) papildomų nustatymų nereikalauja: kelias nustatomas tik `ps_shop_url`.
- Nežinomas ar tuščias `DOMAIN_<VARDAS>`, URL ar hostname su portu sąraše – klaida. Vienas hostname, kaip iki šiol, nesikeičia.
- Pakeitus sąrašą: `make bootstrap` (local/test), tada `make up`. Kompiuterio Nginx maršrutą rašo `host_proxy.py`, todėl po setup atnaujinimo kartą paleisk `setup/install-host`.
| `ALLOWED_IPS` | Tuščias | Bendras leidžiamų IP/CIDR sąrašas **visiems** web įėjimams, įskaitant svetainę. |
| `SITE_ALLOWED_IPS`, `PMA_ALLOWED_IPS`, `MAILPIT_ALLOWED_IPS`, `CRON_ALLOWED_IPS`, `DBMONITOR_ALLOWED_IPS`, `BACKUP_ALLOWED_IPS`, `METRICS_ALLOWED_IPS` | Tuščias | Vieno įėjimo sąrašas. Jei nurodytas, pakeičia `ALLOWED_IPS` tam įėjimui. |
| `TRUSTED_PROXIES` | Tuščias | Iš kurių adresų nginx-proxy priima `X-Forwarded-For` ir `X-Forwarded-Proto`. Tuščias: su `PUBLISH_ADDRESS` ant loopback – tik `127.0.0.1`, `::1` ir projekto Docker tinklo gateway (taip ateina kompiuterio Nginx / DirectAdmin); su kitu adresu – niekas. Nurodytas sąrašas su loopback galioja iškart, su kitu adresu – tik kartu su `TRUSTED_PROXIES_DIRECT=on`, kitaip klaida. |
| `TRUSTED_PROXIES_DIRECT` | `off` | `on` – leisti `TRUSTED_PROXIES`, kai `PUBLISH_ADDRESS` nėra loopback ir klientai jungiasi tiesiai. Nurodyk tik kai sąraše vien tikri proxy, kurių klientai negali naudoti ([Tikras kliento IP](05-servisai.md#ip-apribojimai)). |

IP sąrašų taisyklės ir pavyzdžiai: [IP apribojimai](05-servisai.md#ip-apribojimai).

## Duomenų bazė

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD` | Tuščias | Aplikacijos DB ir jos vartotojas. Vartotojas gauna teises **tik į savo DB** (ir `DATABASE_EXTRA_SCHEMAS`). Sukuriami pirmą kartą paleidžiant tuščią DB. |
| `DATABASE_EXTRA_SCHEMAS` | Tuščias | Papildomos projekto schemos per tarpą, pvz. `"asana_graph asana_git"`. Sukuriamos kartu su DB, vartotojas gauna teises ir į jas. Kopija, importas, `db-restrict-grants`, registras, snapshot'as (kiekviena schema – savo kataloge), migracijos ir after-import (`<grupė>/<schema>/`) apima visas schemas; bazinis rinkinys, `db-load` ir Doctrine diff – tik `DATABASE_NAME`. Žr. [papildomos schemos](07-duomenu-baze.md#papildomos-schemos). |
| `DATABASE_TEST_SCHEMAS` | Tuščias | Aplikacijos testų schemos per tarpą, vardai baigiasi `_test`, `*` – bet kokie simboliai, pvz. `"asana*_test"`. Vartotojas gauna teises (pirmas paleidimas, `db-restrict-grants`), bet setup jų nekuria ir jos nepatenka į snapshot'ą, kopiją, importą ar registrą. Tik local/test. Žr. [testų schemos](07-duomenu-baze.md#testų-schemos). |
| `DATABASE_ROOT_PASSWORD` | Privalomas | Atskiras root slaptažodis, turi skirtis nuo `DATABASE_PASSWORD`. Local/test sugeneruoja `make init` / `make bootstrap`. |
| `APP_DATABASE_HOST`, `APP_DATABASE_PORT` | `database`, `3306` | Kur jungiasi PHP. Keisk tik jei aplikacija sąmoningai jungiasi kitur. |
| `DATABASE_HOST` | `127.0.0.1` (bendrame `.env`) | DB konteinerio vartotojo host'o nustatymas (MySQL `MYSQL_HOST`, MariaDB `MYSQL_ROOT_HOST`). Tai ne PHP adresas. |
| `DATABASE_UID`, `DATABASE_GID` | Tavo vartotojo ID | Tik MySQL: DB failų savininkas. |
| `DATABASE_BUFFER_POOL` | Apskaičiuojama | InnoDB buffer pool, žr. [resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui). |
| `DATABASE_PERFORMANCE_SCHEMA` | `off` | `on` įjungia MySQL `performance_schema` (DB monitoriui). Kainuoja kelis šimtus MB atminties. |
| `DATABASE_MONITOR_USER` | `<DATABASE_NAME>_monitor` | DB monitoriaus vartotojas, žr. [DB monitorius](05-servisai.md#db-monitorius). |
| `DATABASE_MONITOR_PASSWORD` | Tuščias | Jo slaptažodis, bent 16 simbolių. |
| `DBMONITOR_TOKEN` | Tuščias | Neprivalomas Bearer token'as DB monitoriaus endpoint'ui, bent 24 simboliai. Tuščias – prieigą saugo tik `DBMONITOR_ALLOWED_IPS`. |

## Metrikos (Prometheus)

Galioja su `SERVICES=metrics`, žr. [Metrikos](05-servisai.md#metrikos-prometheus).

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `METRICS_TOKEN` | Tuščias | Neprivalomas Bearer token'as `/metrics`, bent 24 simboliai. Tuščias – prieigą saugo tik `METRICS_ALLOWED_IPS`. |
| `METRICS_HTPASSWD` | Tuščias | Neprivalomas Basic-auth (htpasswd eilutės) `metrics.<domain>` puslapiams, išskyrus `/metrics`. Slaptas kaip token'as. Žr. [slaptažodžiai įrankiams](05-servisai.md#slaptažodžiai-įrankiams-htpasswd). |
| `METRICS_ALLOWED_IPS` | Tuščias | Kas pasiekia `metrics.<domain>`: Prometheus serverio adresas (IP/CIDR) ir, jei reikia, komandos adresai. Be jo ar `ALLOWED_IPS` stage/prod servisas nepasileidžia. |
| `METRICS_URLS` | `https://<DOMAIN>/` | Tikrinami URL per tarpus (seno scripto `--urls`): atsakymo laikas ir būsena. |
| `METRICS_PROBE_ADDRESS` | Tuščias | IP, į kurį jungiasi URL patikra vietoj DNS (kaip `curl --connect-to`), pvz. VPN adresas. |
| `METRICS_NETWORK_INTERFACE` | Numatytojo maršruto sąsaja | Serverio tinklo sąsaja `network_*` metrikoms (seno scripto `network_interface`). |
| `METRICS_SUPPORT_LEVEL` | `0` | Projekto palaikymo lygis 0–9 (`support_level`), pagal jį alert'ai parenka, ką pranešti. |
| `METRICS_DISK_WRITE_MB` | `100` | Disko rašymo testo dydis (`disk_write_speed`), kartą per valandą. `0` išjungia. |
| `METRICS_HTTP_WINDOW_MINUTES` | `1` | Kelių paskutinių pilnų minučių HTTP log'ai skaičiuojami (1–60). Lygus Prometheus `scrape_interval`. |
| `METRICS_DOCKER_INTERVAL` | `15` | Kas kiek sekundžių (5–300) Docker kolektorius nuskaito projekto konteinerius. Senesnė nei trys intervalai (+30 s) nuotrauka – `setup_metrics_group_up{group="containers"} 0`. Žr. [Konteineriai, cron ir procesai](05-servisai.md#konteineriai-cron-ir-procesai). |
| `LOGS_RETENTION_DAYS` | `14` | Kiek dienų (1–366) laikyti konteinerių log'ų failus, žr. [Log'ų istorija](05-servisai.md#logų-istorija). |
| `LOGS_MAX_MB` | `2048` | Visų log'ų failų riba MB (64–1048576): viršijus pirmi trinami seniausi, šiandienos – niekada. |

## Laikas ir derinimas

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `TZ` | `Europe/Vilnius` | Viena laiko zona visiems konteineriams, PHP `date.timezone`, DB ir cron. |
| `XDEBUG_MODE` | `debug` (local/test) | `off` arba sąrašas per kablelį iš `develop`, `coverage`, `debug`, `gcstats`, `profile`, `trace`. Pritaikoma su `make restart`. Stage/prod šis raktas atmetamas. |
| `PS_DEBUG_MODE` | Tuščias | Tik PS: `off`, `on`, `ip`. Žr. [PS debug](06-aplikacija.md#prestashop-debug). |
| `PS_DEBUG_IPS` | Tuščias | Tikslūs IPv4/IPv6 adresai per kablelį, kai `PS_DEBUG_MODE=ip`. |

## PrestaShop

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `PS_VERSION` | Privalomas PS profiliui | Parduotuvės kodo versija, pvz. `9.0.2`. Pagal ją parenkamas konfigūracijos failo formatas. Nesutapimas su kodu – klaida. |
| `PS_COOKIE_KEY`, `PS_COOKIE_IV` | Tuščias | Parduotuvės raktai, visoms versijoms. |
| `PS_RIJNDAEL_KEY`, `PS_RIJNDAEL_IV` | Tuščias | Tik PS 1.6. |
| `PS_NEW_COOKIE_KEY`, `PS_SECRET` | Tuščias | PS 1.7, 8, 9. |
| `PS_API_PRIVATE_KEY`, `PS_API_PUBLIC_KEY` | Tuščias | Tik PS 9, PEM raktai base64 forma. |
| `PS_DATABASE_PREFIX` | `ps_` | Lentelių prefiksas. |
| `PS_LOCALE` | `en-US` | Kuriamo `parameters.php` `locale` reikšmė. |
| `PS_RELEASE` | `off` | `on` – oficialus PS release saugomas PHP image'e. Tik `PROFILE=prestashop`: generic projektas su `on`, `PS_RELEASE_PATHS` ar `PS_ADMIN_DIR` sustoja klaida. |
| `PS_RELEASE_PATHS` | Tuščias | Katalogai per tarpus, kopijuojami iš release į parduotuvę, pvz. `vendor` arba `admin`. Reikia `PS_RELEASE=on`. |
| `PS_RELEASE_SHA256` | Tuščias | PS 1.6 release zip kontrolinė suma. |
| `PS_ADMIN_DIR` | Tuščias | Kuo pervadinti release `admin` katalogą kopijuojant. |
| `PS_SSL` | `on` | Ką after-import įrašo į `PS_SSL_ENABLED` ir `PS_SSL_ENABLED_EVERYWHERE` ne prod aplinkose. |
| `SETUP_COMPATIBILITY_CHECK` | Įjungta | `off` išjungia PS ir PHP versijų patikrą (fork'ams, pvz. thirty bees). |

Raktai local/test aplinkoje įrašomi į privatų env failą automatiškai. Stage/prod raktai ateina iš serverio env, Bitbucket Deployment variables arba GitHub environment secrets. Plačiau: [aplikacijos skyrius](06-aplikacija.md).

## Duomenų bazė kaip kodas ir importas

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `DATABASE_AS_CODE` | `on` | `off` išjungia schemą, registrą, migracijas, rinkinius ir schemos commit hook'ą (žr. [DB kaip kodas](07-duomenu-baze.md)). |
| `DATABASE_MIGRATIONS` | `on` | `off`: projektas migruoja DB pats (pvz. Doctrine). Setup migracijų negeneruoja, nereikalauja ir nevykdo; snapshot'as, registras ir commit hook'as lieka (žr. [kai projektas migruoja pats](07-duomenu-baze.md#kai-projektas-migruoja-pats-doctrine)). |
| `SCHEMA_DIRECTORY` | `app/database/schema` | Kitas schemos snapshot'o katalogas nuo projekto šaknies. Su `DATABASE_AS_CODE=off` nenaudojamas. |
| `POST_IMPORT_SQL_DIRECTORY` | `database/after-import` šalia Makefile | Kitas after-import katalogas nuo projekto šaknies. |
| `FIXTURES_DIRECTORY` | Tuščias | Fixtures katalogas nuo projekto šaknies, pvz. `app/database/fixtures`. |
| `BACKUP_SSH` | Kompiuterio atsakymas | `make db-pull`: setup-server paskyra VPN adresu, pvz. `setup@10.8.0.14`. Paprastai nenurodomas: pirmas `make db-pull` paklausia ir išsaugo setup'o `docker/.env.backup` visiems projektams. Projekte – tik jei jo kopijos kitame serveryje. |
| `BACKUP_SERVER_PROJECT` | git vardas, tada `PROJECT_NAME` | Projekto katalogo vardas serveryje (`projects/<vardas>`). Paprastai nereikia: pirmiausia bandomas aplinkos repo vardas (`origin`, pvz. `asana_tracker`), tada `PROJECT_NAME`; serveriui nežinant nė vieno, `make db-pull` paklausia ir įrašo į `env/local.env`. |
| `BACKUP_SSH_KEY` | Kompiuterio atsakymas | Kitas privatus SSH raktas `make db-pull` (pvz. `~/.ssh/kopijoms_ed25519`); paklausiama kartu su `BACKUP_SSH`. |
| `INTEGRATIONS_ENABLED` | Tuščias | Integracijos, kurias po užkėlimo palikti įjungtas, per tarpus. |
| `MAIL_MODE` | `auto` | `auto`, `off`, `mailpit`, `preserve`. Žr. [paštas](12-cache-ir-hibridines-aplinkos.md#paštas-mail_mode). |

Keliai `SCHEMA_DIRECTORY`, `POST_IMPORT_SQL_DIRECTORY`, `FIXTURES_DIRECTORY` skaičiuojami nuo projekto šaknies (`demo/`), o `PROJECT_COMPOSE_FILES` – nuo `demo/app/`.

## Testai ir kodo kokybė

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `TEST_COPY_EXCLUDE` | Tuščias | Keliai parduotuvėje per tarpus, kurių nekopijuoti į `ENV=test` kopiją, pvz. `/modules/prgenericsync/cache`. PrestaShop kopija visada praleidžia `var/cache`, `var/logs`, `cache`; generic aplikacijos savo cache katalogus išvardina čia. |
| `PHPSTAN_BASELINE_FILE` | Tuščias | Konteinerio kelias, kur `make phpstan-baseline` rašo baseline. |
| `PLAYWRIGHT_COMMAND` | `npx playwright test` | `make e2e` komanda. |
| `DOCTRINE_PHP_VERSION` | `8.3` | Doctrine migracijų įrankio PHP versija. |

## Serveriai, release ir deploy

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `APP_UID`, `APP_GID` | Tuščias | Stage/prod: hosting'o (DirectAdmin) vartotojo skaitiniai ID. Konteineriai veikia šiuo vartotoju. Release'ui privalomi. Su setup-server – iš build katalogo `target.env`, ne iš projekto env failų. |
| `DEPLOY_CONTEXT` | Tuščias | Docker context'o vardas taikinio serveriui. Su juo stage/prod konteineriai valdomi serveryje. |
| `DEPLOY_SSH` | Tuščias | `vartotojas@host`, kur `rsync` siunčia kodą. Tuščias – vietinis kelias. |
| `DEPLOY_PATH` | Tuščias | Absoliutus projekto šaknies kelias taikinyje (katalogas su `app/` ir `data/`). `DEPLOY_CONTEXT`, `DEPLOY_SSH` ir `DEPLOY_PATH` su setup-server ateina iš build katalogo `target.env`; projekto `env/*.env.example` jų nerašo. |
| `DEPLOY_PROTECTED_PATHS` | Tuščias | Papildomi kodo medžio keliai (nuo kodo šaknies, per tarpą), kurių deploy'us niekada neperrašo ir netrina, pvz. `"var/data/ var/log/ config/local.php"`. Katalogas baigiasi `/`, leidžiamas `*`. Papildo profilio sąrašą ([kodo deploy'us](09-aplinkos.md#kodo-deployus)). |
| `DEPLOY_PHP_RELOAD` | `on` | `off` – deploy'aus pabaigoje neperkrauti php-fpm (`USR2`). Be perkrovimo prod OPcache gali rodyti seną kodą ([kodo deploy'us](09-aplinkos.md#kodo-deployus)). |
| `DEPLOY_REQUIRED_VARIABLES` | Tuščias | Aplikacijos Deployment variables (Bitbucket) ar environment secrets (GitHub) **vardai** (per tarpus), be kurių deploy'us nepradedamas, pvz. `"SEARCH_MASTER_KEY HTPASSWD_WEB"`. Papildo setup'o privalomus (DB slaptažodžiai, PS raktai). Žr. [Deployment variables](09-aplinkos.md#deployment-variables). |
| `DEPLOY_OPTIONAL_VARIABLES` | Tuščias | Neprivalomi Deployment variables vardai, kurių nėra `APP_ENVIRONMENT` (pvz. tik Compose naudojamas raktas). Tušti – tik įspėjimas. `APP_ENVIRONMENT` vardų čia kartoti nereikia. |
| `DEPLOY_PLAIN_VARIABLES` | Tuščias | Sąrašų vardai, kuriuos `make deploy-variables` saugo ne Secured (Bitbucket) arba kaip aplinkos variables, ne secrets (GitHub), pvz. URL, ID. Vardas, kurio nėra jokiame sąraše, – klaida. |
| `CI_PROVIDER` | Pagal repozitoriją | `bitbucket` arba `github`: kurio tiekėjo kintamuosius tikrina `make deploy-variables`. Įrašo `create-project`. Be jo – vienintelis `DEPLOY_*_REPOSITORY`, tada `origin` remote'as (`github.com` – GitHub), kitaip `bitbucket`. `make pipeline-deploy` tiekėją atpažįsta pats. Žr. [GitHub Actions](09-aplinkos.md#github-actions). |
| `DEPLOY_BITBUCKET_REPOSITORY` | `origin` remote'as | `workspace/repo`, kurio Deployment variables tikrina `make deploy-variables`. Reikia, kai `origin` nėra `bitbucket.org`. |
| `DEPLOY_GITHUB_REPOSITORY` | `origin` remote'as | `savininkas/repo`, kurio aplinkos secrets ir variables tikrina `make deploy-variables`. Reikia, kai `origin` nėra `github.com`. |

Stage/prod dar privalomi keturių pagrindinių servisų limitai, žr. [resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui).

## Kodo ir duomenų keliai

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `APP_SOURCE_DIRECTORY` | `app/public`; test – `app/.generated/test/app`; stage/prod – `app/<aplinka>/public` | Aplikacijos kodo vieta nuo projekto šaknies. Turi likti projekte; testui – `.generated/test` viduje. |
| `DATA_DIRECTORY` | `data`; test – `app/.generated/test/data`; stage/prod – `data/<aplinka>` | Duomenų katalogas nuo projekto šaknies. Kitas kelias reiškia kitus DB failus. |

## Cache, resursai ir log'ai

Šių raktų reikšmės ir pirmenybė aprašytos [12 skyriuje](12-cache-ir-hibridines-aplinkos.md): `CACHE_MODE`, `PHP_OPCACHE`, `PHP_APCU`, `PHP_OPCACHE_VALIDATE_TIMESTAMPS`, `PHP_OPCACHE_REVALIDATE_FREQ`, `PS_SMARTY_CACHE`, `PS_SMARTY_COMPILE`, `PS_ASSET_CACHE`, `PS_OBJECT_CACHE`, `CACHE_CLEAR_COMMAND`, `<SERVISAS>_MEMORY`, `<SERVISAS>_CPUS`, `PHP_FPM_*`, `MEMCACHED_CACHE_MB`, `REDIS_MAXMEMORY_MB`, `CONTAINER_LOG_MAX_SIZE`, `CONTAINER_LOG_MAX_FILES`, `MIN_FREE_DISK_MB`, `BACKUP_KEEP_LAST`.

## PHP užklausų ribos

| Raktas | Numatyta | Pavyzdys |
| --- | --- | --- |
| `PHP_MEMORY_LIMIT` | `512M` | `1024M`: vienos PHP užklausos atmintis. |
| `PHP_MAX_EXECUTION_TIME` | `30` | `300`: sekundės vienai HTTP užklausai. |
| `PHP_UPLOAD_MAX_FILESIZE` | `20M` | `64M`: vieno įkeliamo failo dydis. |
| `PHP_POST_MAX_SIZE` | `20M` | `70M`: visos POST užklausos dydis. |

Šios reikšmės nustatomos FPM pool'e, todėl vien `.ini` pakeitimas HTTP ribos gali nepakeisti. CLI `php -i` gali rodyti kitą reikšmę.

## Cron

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `CRON_HISTORY_KEEP` | `100` | Kiek paskutinių kiekvienos užduoties paleidimų log'ų laikyti. |
| `CRON_SCHEDULE` | `on` | `off` – cron servisas ir jo sąsaja veikia, bet nė viena užduotis nevykdoma pagal grafiką; „Run now“ veikia ir įrašomas į istoriją. Leidžiama bet kurioje aplinkoje. Pritaikoma su `make restart service=cron`. Žr. [cron](05-servisai.md#cron). |

## Programų versijos

Paprastai laikomos bendrame setup `.env`. Projektas gali perrašyti bet kurią `common.env` faile. Keitimo seka: `make check` → `make build` → `make up`.

| Raktas | Numatyta | Ką pasirenka |
| --- | --- | --- |
| `PHP_VERSION` | `8.1` | PHP Dockerfile katalogą: `5.6`, `7.0`–`7.4`, `8.0`–`8.5`. Rašyk šaką (`8.4`), ne patch versiją. |
| `XDEBUG_VERSION` | `3.1.5` | Xdebug versiją. Turi derėti su PHP. |
| `COMPOSER_VERSION` | `2.7.6` | Composer versiją PHP image'e. |
| `APCU_VERSION` | `5.1.28` | APCu plėtinio versiją. |
| `NODE_VERSION`, `NPM_VERSION` | `22.23.1`, `10.9.8` | Node ir npm PHP 8.x image'e. |
| `WEBPACK_VERSION`, `WEBPACK_CLI_VERSION` | `5.94.0`, `5.1.4` | Globalūs Webpack įrankiai. |
| `NVM_VERSION`, `NVM_INSTALL_SHA256` | `v0.40.4`, žr. `.env` | Keičiami kartu. |
| `SUPERCRONIC_VERSION`, `SUPERCRONIC_SHA1SUM` | `0.2.29`, žr. `.env` | Cron vykdyklė, keičiami kartu. |
| `PHPSTAN_PHAR_VERSION` | `1.12.33` | PHPStan PHAR PHP image'e. |
| `PHP_PHPSTAN_VERSION` | `2.2.2-php8.1` | `php-phpstan` serviso image'o tag'as. |
| `PHP_CS_VERSION` | `3.95.10-php8.1` | `php-cs` serviso image'o tag'as. |
| `DOCTRINE_MIGRATIONS_VERSION` | `3.8` | Doctrine migracijų įrankis. |
| `NGINX_VERSION` | `1.30.5` | nginx-proxy. |
| `APACHE_VERSION` | `2.4.59` | Apache. |
| `DATABASE_ENGINE` | `mysql` | DB variklis: `mysql` arba `mariadb`. |
| `MYSQL_VERSION` | `8.4.0` | MySQL versija, kai `DATABASE_ENGINE=mysql`. |
| `MARIADB_VERSION` | `11.3.2` | MariaDB versija, kai `DATABASE_ENGINE=mariadb`. |
| `PMA_VERSION` | `5.2.3` | phpMyAdmin. |
| `MAILPIT_VERSION` | `1.18.3` | Mailpit. |
| `MEMCACHED_VERSION` | `1.6.45` | Memcached. |
| `REDIS_VERSION` | `7.4` | Redis. |
| `PLAYWRIGHT_VERSION` | `1.61.1` | Playwright image'as ir `@playwright/test`. |
| `NPM_CONFIG_CACHE`, `COMPOSER_HOME` | `/tmp/setup-npm-cache`, `/tmp/setup-composer` | npm podėlio ir Composer namų katalogai PHP konteineryje. |

DB variklio versijos keitimui neužtenka perkurti konteinerio: reikia suderinamo duomenų perkėlimo (dump ir importas į naują tuščią katalogą).

## Kintamieji, kuriuos nustato runner'is

Šiuos vardus gali naudoti Compose `${...}` išraiškose. Į env failus jų nerašyk.

| Kintamasis | Reikšmė projektui `demo`, `ENV=local` |
| --- | --- |
| `ROOT_DIRECTORY` | `…/setup/docker` – bendrų Docker šaltinių vieta. |
| `PROJECT_DIRECTORY` | `…/demo` – projekto šaknis. |
| `PROJECT_APP_DIRECTORY`, `PROJECT_DOCKER_DIRECTORY` | `…/demo/app`. |
| `PROJECT_CONFIG_DIRECTORY` | `…/demo/app/config`. |
| `PROJECT_WEB_DIRECTORY` | Pasirinktos aplinkos kodas, čia `…/demo/app/public`. |
| `PROJECT_DATA_DIRECTORY` | Pasirinktos aplinkos duomenys, čia `…/demo/data`. |
| `ENV` | `local`, `test`, `stage` arba `prod`. |
| `BUILD_ENV`, `IMAGE_ENV` | `local`, kai `ENV=test`; kitaip sutampa su `ENV`. |
| `HOST_UID`, `HOST_GID` | Tavo vartotojo ID; stage/prod – `APP_UID`/`APP_GID`. |
| `SETUP_IMAGE_SUFFIX_<SERVISAS>` | Kiekvieno image'o fingerprint'as. |

Drop-in `.template` failuose kiekviename CORE konteineryje dar prieinami `DOMAIN`, `PROJECT_NAME` ir `SETUP_ENV`, DB konteineryje – ir `DATABASE_NAME`, `DATABASE_USER`. Žr. [drop-in failai](05-servisai.md#template-failai).

## Savi aplikacijos parametrai (APP_ENVIRONMENT)

Env failo eilutė į konteinerį pati nepatenka: ji tik užpildo Compose `${...}` išraiškas. Aplikacijos savus parametrus (API raktus, integracijų adresus, jungiklius) setup perduoda pagal vardų sąrašą `APP_ENVIRONMENT`.

Situacija: aplikacija skaito `SUPPLIER_URL` ir `SUPPLIER_TOKEN` su `getenv()`.

1. `env/common.env` išvardyk **vardus** ir bendras ne slaptas reikšmes:

```dotenv
APP_ENVIRONMENT="SUPPLIER_URL SUPPLIER_TOKEN"
SUPPLIER_URL=https://supplier.example.com
```

2. Slaptą reikšmę įrašyk `env/local.env` (`SUPPLIER_TOKEN='...'`), o stage/prod – į Bitbucket Deployment variables arba GitHub environment secrets. Serverio env faile to vardo nerašyk, net tuščio: paminėtas vardas uždengia proceso aplinkos reikšmę.
3. Paleisk `make check` ir `make restart service=php-fpm` (ir `service=cron`, jei jis įjungtas).

Kas vyksta:

- Kiekvienas vardas tampa `VARDAS: ${VARDAS-}` servisuose `php-fpm` (ten veikia ir `make phpunit`) ir `cron` (kai jis yra `SERVICES`). Visose aplinkose: local, test, stage, prod.
- Vardai rašomi į sugeneruotą `.generated/app-environment-<santrauka>.yaml`. Jame tik nuorodos, **reikšmių nėra**; reikšmes įstato Compose. Image'ai dėl šio nustatymo neperkuriami.
- Neįrašyta reikšmė konteineryje yra tuščia eilutė (`getenv()` grąžina `''`, ne `false`). Jei aplikacijai tuščia reikšmė reiškia ką kita nei „nėra“, įrašyk numatytąją reikšmę į `common.env`.
- Ilgą sąrašą galima laužyti per kelias eilutes kabučių viduje. Komentarų (`#`) kabutėse nerašyk – jie taptų vardais.
- Vardas turi atitikti `^[A-Z][A-Z0-9_]*$`. Setup'o vardai atmetami su klaida, nes kopija tyliai pakeistų setup'o reikšmę: visi šio skyriaus nustatymai (pvz. `DOMAIN`, `SERVICES`, `PROFILE`), runner'io kintamieji (`ENV`, `HOST_UID`…) ir šeimos `DATABASE_*`, `APP_DATABASE_*`, `MYSQL_*`, `MARIADB_*`, `PS_*`, `PHP_*`, `XDEBUG_*`, `CRON_*`, `PROJECT_*`, `SETUP_*`, `COMPOSE_*`, `DOCKER_*`. DB prisijungimą, `DOMAIN`, `PROJECT_NAME`, `TZ` `php-fpm` ir `cron` jau gauna iš setup.

Compose ranka vis dar reikia, kai aplikacija kintamąjį vadina kitaip nei env failas, kai reikia Compose numatytosios reikšmės (`${X:-...}`, `${X:?...}`) arba kai parametras skirtas tik vienam servisui:

```yaml
services:
  php-fpm:
    environment:
      SEARCH_KEY: ${MEILISEARCH_MASTER_KEY-}
```

Projekto Compose failai skaitomi po `APP_ENVIRONMENT`, todėl jų aprašas laimi: projektas, kuris kintamuosius dar išvardija pats, veikia kaip anksčiau. Savo servisams (žr. [savas servisas](04-compose-ir-dockerfile.md#savas-servisas)) `APP_ENVIRONMENT` netaikomas – jų `environment` rašyk ranka.

Ar reikšmė tikrai perduota, gali patikrinti [config/environment.json](12-cache-ir-hibridines-aplinkos.md#aplikacijos-env-reikalavimai).
