# 5. Servisai ir drop-in failai

[Turinys](README.md) · [Atgal](04-compose-ir-dockerfile.md) · [Toliau: aplikacija](06-aplikacija.md)

## Servisų katalogas (SERVICES)

Visada veikia keturi pagrindiniai servisai: `nginx-proxy`, `webserver` (Apache), `php-fpm`, `database`. Visi kiti įjungiami env raktu `SERVICES`:

```dotenv
SERVICES="pma mailpit cron"
```

| Vardas | Kas tai | Kaip pasiekiamas |
| --- | --- | --- |
| `pma` | phpMyAdmin | `https://pma.demo.local/`, prisijungia aplikacijos DB vartotoju. |
| `mailpit` | Laiškų gaudyklė | UI `https://mailpit.demo.local/`, SMTP `mailpit:1025` projekto tinkle. |
| `cron` | Suplanuotos užduotys su sąsaja ir istorija | UI `https://cron.demo.local/`. Žr. [cron](#cron). |
| `dbmonitor` | DB našumo duomenys JSON forma | `https://dbmonitor.demo.local/` su token'u. Žr. [DB monitorius](#db-monitorius). |
| `backup` | Lentelių registras: kiekviena DB lentelė ir jos taisyklė kopijoms, importui ir dev rinkiniui | UI `https://backup.demo.local/`. Žr. [lentelių registras](#lentelių-registras-backup). |
| `metrics` | Serverio, svetainės, HTTP, saugumo, DB, procesų, konteinerių ir cron Prometheus metrikos esamiems Grafana dashboard'ams | `https://demo.local/metrics` su token'u. Žr. [Metrikos](#metrikos-prometheus). |
| `memcached` | Memcached cache | `memcached:11211` projekto tinkle. |
| `redis` | Redis cache | `redis:6379` projekto tinkle. |
| `playwright` | Naršyklės testų įrankis | `make e2e`. |
| `php-phpstan` | PHPStan įrankis | `make phpstan`. |
| `php-cs` | PHP-CS-Fixer įrankis | `make phpcs`. |
| `php-doctrine-migrations` | Doctrine migracijų įrankis | `make doctrine`. |

Įrankiai (`playwright`, `php-phpstan`, `php-cs`) su `make up` nepasileidžia: jie paleidžiami tik savo komanda. Kiti servisai pasileidžia su `make up`.

Sąrašą gali nurodyti `env/common.env` ir perrašyti aplinkos faile. Aplinkos faile nurodytas sąrašas pakeičia visą bendrą sąrašą, o ne prideda prie jo. Forsenos pavyzdys:

```dotenv
# env/common.env – visose aplinkose
SERVICES=pma
```

```dotenv
# env/local.env – lokaliai dar ir Mailpit
SERVICES="pma mailpit"
```

Įjungus naują servisą:

```bash
make build
make up
```

`make build` sukuria naujo serviso image'ą, `make up` jį paleidžia. Išjungus servisą, pašalink seną konteinerį: `make down`, tada `make up`.

Nežinomas vardas `SERVICES` sąraše yra klaida. Servisų adresai `pma.`, `mailpit.`, `cron.`, `dbmonitor.`, `backup.` veikia per kompiuterio Nginx, kurį paruošia `make bootstrap`. Stage/prod įrankiai `pma`, `mailpit`, `cron`, `dbmonitor`, `backup`, `metrics` be IP sąrašo nepasileidžia, žr. [IP apribojimai](#ip-apribojimai).

## Drop-in failai: papildyti CORE konfigūraciją

CORE failų projektas nekeičia. Vietoj to jis deda savo failus į `config/<servisas>/`, o CORE juos įtraukia **po** savo failų. Paskutinis failas laimi.

| Servisas | Projekto failai | Kur įtraukiama |
| --- | --- | --- |
| `php-fpm`, `cron` | `config/php/*.ini` | Po CORE PHP nustatymų. |
| `webserver` (Apache) | `config/apache/*.conf` | Globaliai, `httpd.conf` gale. |
| | `config/apache/vhost.d/*.conf` | Svetainės `VirtualHost` viduje. |
| `nginx-proxy` | `config/nginx-proxy/*.conf` | `http` lygyje. |
| | `config/nginx-proxy/server.d/*.conf` | Svetainės `server` bloke (HTTP ir HTTPS). |
| | `config/nginx-proxy/location.d/*.conf` | Svetainės `location /` bloke. |
| `database` | `config/mysql/*.cnf` arba `config/mariadb/*.cnf` | Po CORE MySQL nustatymų. |
| | `config/mysql/init.d/*` | Vieną kartą, kai DB kuriama pirmą kartą. |
| `redis` | `config/redis/*.conf` | Po CORE Redis nustatymų. |
| `cron` | `config/cron/crontab`, `crontab.<ENV>` | Cron užduotys. |
| `pma` | `config/pma/*.php` | phpMyAdmin `$cfg` nustatymai (be aplinkos poaplankio). |
| `php-fpm` | `config/startup/*.sh` | Prieš PHP-FPM startą, žr. [aplikacija](06-aplikacija.md#startup-scriptai). |

Kiekvienas katalogas gali turėti aplinkos poaplankį, pvz. `config/php/local/`, `config/nginx-proxy/prod/server.d/`, `config/mysql/test/init.d/`. Jo failai skaitomi tik toje aplinkoje ir po bendrų failų.

Mailpit ir Memcached konfigūracijos failų neskaito, todėl drop-in katalogų neturi: juos valdo env raktai ir Compose skirtumų failai.

**Katalogai neprivalomi.** Jei katalogo nėra, jis neprijungiamas ir niekas jo nekuria. Katalogą sukurk tik tada, kai dedi į jį failą. Pirmą kartą sukūrus katalogą paleisk `make up`, kad jis būtų prijungtas. Vėliau pakeitus failo turinį užtenka `make restart service=<servisas>`.

### .template failai

Failas su galūne `.template` konteinerio starto metu paverčiamas į failą be šios galūnės. Jame `${VARDAS}` pakeičiamas to konteinerio env reikšme:

- keičiamas tik `${VARDAS}` su riestiniais skliaustais, todėl Nginx `$uri`, `$host` ar PHP kintamieji nepaliečiami;
- nežinomas kintamasis yra klaida, ir konteineris nepasileidžia;
- reikšmės imamos tik iš to konteinerio env.

`php-fpm`, `cron`, `webserver`, `nginx-proxy` ir `database` konteineriuose prieinami `DOMAIN`, `PROJECT_NAME`, `SETUP_ENV` ir `TZ`, DB konteineryje dar `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_BUFFER_POOL`. Redis konteineryje yra tik `TZ` ir `REDIS_MAXMEMORY_MB`. Savo kintamąjį pirmiau perduok tam servisui: `php-fpm` ir `cron` – per [`APP_ENVIRONMENT`](03-nustatymai.md#savi-aplikacijos-parametrai-app_environment), kitiems – per Compose `environment`.

**Dėmesio:** `config/php/*.template` failus renderina ir cron konteineris. Jame nėra `php-fpm` FPM pool'o raktų (`PHP_FPM_*`, `PHP_MEMORY_LIMIT` ir pan.), `PS_SMARTY_*`, `PS_ASSET_CACHE`, `PS_OBJECT_CACHE` ir `PS_DEBUG_*`. Jei `SERVICES` turi `cron`, PHP šablonuose naudok tik abiem konteineriams bendrus kintamuosius, kitaip cron nepasileis.

Pavyzdys `config/apache/vhost.d/project-header.conf.template`:

```apache
Header always set X-Project "${PROJECT_NAME}-${SETUP_ENV}"
```

```bash
make restart service=webserver
```

### MySQL init.d

Failai `config/mysql/init.d/` (MariaDB – `config/mariadb/init.d/`) paleidžiami **vieną kartą**, kai DB duomenų katalogas kuriamas pirmą kartą. Pirma bendri, tada `config/mysql/<ENV>/init.d/`, abiejose grupėse pagal vardą:

- `*.sql` ir `*.sql.template` vykdomi kaip root projekto DB;
- `*.sh` vykdomi su `sh`.

Jau sukurtai DB šie failai nepaleidžiami. DB duomenims užkelti naudok [db komandas](07-duomenu-baze.md).

## PHP ir Xdebug

Situacija: lokaliai nori matyti klaidas ekrane.

`config/php/local/20-runtime.ini`:

```ini
display_errors = On
log_errors = On
```

```bash
make restart service=php-fpm
```

Laiko zonos `.ini` faile nerašyk: ją nustato `TZ` (numatyta `Europe/Vilnius`) visiems konteineriams iš karto. HTTP užklausų ribos keičiamos env raktais `PHP_MEMORY_LIMIT`, `PHP_MAX_EXECUTION_TIME`, `PHP_UPLOAD_MAX_FILESIZE`, `PHP_POST_MAX_SIZE` ([nustatymai](03-nustatymai.md#php-užklausų-ribos)). Patikra: `make shell`, tada `php --ini` ir `php -r 'echo ini_get("display_errors"), PHP_EOL;'`.

**Xdebug** yra tik local/test image'uose. Jį valdo `XDEBUG_MODE`:

| Reikšmė | Kas vyksta |
| --- | --- |
| tuščia arba `debug` | Derinimas su trigger'iu (numatyta local/test). |
| `off` | Xdebug išjungtas, PHP greitesnis. |
| `debug,profile` | Derinimas ir profiliavimas. Profiliai rašomi į `data/profiling`. |

```dotenv
# env/local.env
XDEBUG_MODE=debug,profile
```

```bash
make restart
```

Image'o perkurti nereikia. Derinimui PhpStorm įjunk klausymąsi (portas `9003`, IDE key `PHPSTORM`), uždėk breakpoint ir pridėk užklausai `XDEBUG_TRIGGER=1`, pvz. `https://demo.local/?XDEBUG_TRIGGER=1`. PhpStorm kelio `/var/www/html` atitikmuo turi būti tavo `public/`. Stage/prod `XDEBUG_MODE` atmetamas.

## MySQL ir MariaDB

Situacija: vietinis dump'as didelis, reikia didesnio paketo.

`config/mysql/local/20-runtime.cnf`:

```ini
[mysqld]
max_allowed_packet = 128M
```

```bash
make restart service=database
```

Prieš perkraudamas DB sustabdyk vykstantį importą. Patikra DB kliente: `SHOW VARIABLES LIKE 'max_allowed_packet';`.

InnoDB buffer pool CORE apskaičiuoja iš `DATABASE_MEMORY` (apie 60 %). Jei jį nurodai drop-in faile, tavo reikšmė laimi. Paprasčiau naudoti `DATABASE_BUFFER_POOL` env raktą ([resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui)). `performance_schema` įjungia `DATABASE_PERFORMANCE_SCHEMA=on`.

Aplikacijos DB vartotojas turi teises tik į savo DB, root turi atskirą slaptažodį `DATABASE_ROOT_PASSWORD`. Senai (prieš v2 sukurtai) DB teises vieną kartą susiaurina `make db-restrict-grants`, žr. [DB skyrių](07-duomenu-baze.md#senos-db-teisės).

Visi serverio kintamieji: [MySQL 8.4](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html), [MariaDB](https://mariadb.com/docs/server/server-management/variables-and-modes/server-system-variables).

## Apache

Situacija: checkout'as yra Symfony projektas, viešas failas `public/index.php`.

`env/common.env`:

```dotenv
DOCUMENT_ROOT=public
```

Papildomos direktyvos svetainės VirtualHost viduje – `config/apache/vhost.d/20-timeout.conf`:

```apache
Timeout 300
ProxyTimeout 300
```

```bash
make restart service=webserver
```

CORE Apache jau atsako `404` į paslėptus kelio segmentus (`.git`, `.env`, `.idea`; išskyrus `.well-known`), `*.sql`, `*.sql.gz`, `*.bak` ir `composer.json` / `composer.lock`. `HTTP_FORCE_HTTPS=on` (stage/prod numatyta) peradresuoja HTTP į HTTPS. Direktyvų žinynas: [Apache](https://httpd.apache.org/docs/2.4/mod/directives.html).

## Nginx proxy

Situacija: reikia įkelti didesnius nei 100 MB failus. CORE `server` bloke jau yra `client_max_body_size 100M`, todėl tą pačią direktyvą rašyk `location` lygyje.

`config/nginx-proxy/location.d/upload.conf`:

```nginx
client_max_body_size 200M;
```

```bash
make restart service=nginx-proxy
```

`config/nginx-proxy/*.conf` tinka `map`, `upstream` ar atskiram `server` blokui, `server.d/*.conf` – direktyvoms svetainės serveriui (pvz. peradresavimams), `location.d/*.conf` – pagrindinio `location` direktyvoms. Ta pati vienetinė direktyva tame pačiame kontekste du kartus yra klaida. Direktyvos: [Nginx](https://nginx.org/en/docs/dirindex.html).

## IP apribojimai

Kiekvienas web įėjimas (svetainė, PMA, Mailpit, cron UI, DB monitorius, `/metrics`) ribojamas nginx-proxy lygyje.

| Raktas | Kam galioja |
| --- | --- |
| `ALLOWED_IPS` | Visiems įėjimams, **įskaitant svetainę**. |
| `SITE_ALLOWED_IPS` | Tik svetainei. |
| `PMA_ALLOWED_IPS`, `MAILPIT_ALLOWED_IPS`, `CRON_ALLOWED_IPS`, `DBMONITOR_ALLOWED_IPS`, `BACKUP_ALLOWED_IPS`, `METRICS_ALLOWED_IPS` | Tik tam įrankiui. `METRICS_ALLOWED_IPS` galioja tik `/metrics` keliui, ne visai svetainei. |

Taisyklės:

- Sąraše rašyk IP arba CIDR (`203.0.113.10 198.51.100.0/24 2001:db8::10`), atskirk tarpais arba kableliais. Neteisingas įrašas sustabdo Nginx.
- Įėjimo sąrašas (`PMA_ALLOWED_IPS`) pakeičia bendrą `ALLOWED_IPS` tam įėjimui.
- Ne iš sąrašo atėjęs klientas gauna `403`.
- **Local/test** su tuščiu sąrašu viskas atvira.
- **Stage/prod** su tuščiu sąrašu svetainė vieša, o įrankiai uždrausti. Be to, runner'is atsisako paleisti `pma`, `mailpit`, `cron`, `dbmonitor`, `backup`, `metrics` be sąrašo: `pma on prod requires PMA_ALLOWED_IPS or ALLOWED_IPS`.

Viešai LIVE parduotuvei `ALLOWED_IPS` nenaudok, nes jis uždarytų ir svetainę. Rašyk įrankių sąrašus:

```dotenv
# env/prod.env
PMA_ALLOWED_IPS="203.0.113.10"
CRON_ALLOWED_IPS="203.0.113.10"
```

Testinei STAGE svetainei, kurią turi matyti tik komanda, tinka bendras sąrašas:

```dotenv
# env/stage.env
ALLOWED_IPS="203.0.113.10 198.51.100.0/24"
```

Pakeitus sąrašą: `make restart service=nginx-proxy` (stage/prod su `DEPLOY_CONTEXT` – per [deploy-images](09-aplinkos.md#imageai-serveryje)).

**Tikras kliento IP.** Kam nginx-proxy leidžia pasakyti kliento adresą (`X-Forwarded-For`) ir schemą (`X-Forwarded-Proto`), priklauso nuo [`PUBLISH_ADDRESS`](03-nustatymai.md#adresai-ir-prieiga):

- **`127.0.0.1` (numatyta, DirectAdmin ir kompiuteris).** Į projektą patenkama tik per kompiuterio Nginx arba DirectAdmin. Jie jungiasi per Docker portą ir konteineriui atrodo kaip projekto tinklo gateway, todėl nginx-proxy pasitiki tik juo ir `127.0.0.1`/`::1`. Kiti konteineriai ir tinklai kliento adreso perduoti negali.
- **Kitas adresas (pvz. VPN serveris be DirectAdmin).** Klientai jungiasi tiesiai, todėl jų atsiųstos `X-Forwarded-*` antraštės ignoruojamos: sąrašai tikrinami pagal tikrą jungties adresą, o schema yra ta, kuria atėjo užklausa. Be šito VPN klientas su suklastota `X-Forwarded-For` praeitų PMA, Mailpit, cron, DB monitoriaus ir `/metrics` sąrašus. Sąraše nurodyk VPN klientų adresus (pvz. `10.8.0.0/24`). Užklausos iš paties serverio ateina kaip Docker tinklo gateway.

`TRUSTED_PROXIES` nurodo pasitikimus proxy rankomis. Su `PUBLISH_ADDRESS` ant loopback jis veikia iškart. Su kitu adresu klientai jungiasi tiesiai, todėl klientas iš pasitikimo diapazono galėtų suklastoti `X-Forwarded-For` ir praeiti IP sąrašus. Tokį derinį runner'is atmeta. Jei sąraše tikrai tik proxy, per kurį klientai negali jungtis (pvz. VPN tinkle prieš projektą stovi kitas proxy), nustatyk ir `TRUSTED_PROXIES_DIRECT=on`. Toliau Apache gauna tik nginx-proxy nustatytą kliento adresą, ne kliento atsiųstą grandinę, todėl PHP (ir PrestaShop `getRemoteAddr`) mato tą patį IP. Į leidžiamų klientų sąrašą proxy adreso nedėk: taip įleistum visus už jo esančius lankytojus.

## Redis ir Memcached

Standartinio cache serviso nėra: Redis, Memcached ir APCu yra lygiaverčiai variantai, renkasi projektas.

```dotenv
SERVICES="redis"
```

- **Redis** pagal nutylėjimą yra cache: be persistencijos, `maxmemory` apie 90 % `REDIS_MEMORY` (arba `REDIS_MAXMEMORY_MB`), politika `allkeys-lru`. Adresas `redis:6379`.
- **Memcached** (`SERVICES="memcached"`) naudoja apie 90 % `MEMCACHED_MEMORY` (arba `MEMCACHED_CACHE_MB`). Adresas `memcached:11211`.
- Portai į kompiuterį nepublikuojami.

PHP plėtiniai `redis` ir `memcached` jau sukompiliuoti visuose PHP image'uose, bet neįjungti. Įjunk vieną drop-in failu `config/php/cache.ini`:

```ini
extension=redis.so
```

Arba `extension=memcached.so`. Tada:

```bash
make restart
```

Patikra: `make shell`, tada `php --ri redis`. `make restart` be serviso vardo perkrauna ir cron, kuris skaito tuos pačius `config/php` failus.

Redis persistenciją įjunk tik jei modulis Redis naudoja kaip saugyklą, ne kaip cache. `config/redis/persistence.conf`:

```conf
appendonly yes
```

Duomenys bus rašomi į `data/redis`. Paleidus servisą aplikacija dar turi būti nustatyta naudoti cache. Setup PrestaShop cache backend'o nekeičia, žr. [cache skyrių](12-cache-ir-hibridines-aplinkos.md).

## Mailpit ir phpMyAdmin

```dotenv
# env/local.env
SERVICES="pma mailpit"
```

```bash
make build
make up
```

- phpMyAdmin: `https://pma.demo.local/`. Jis jungiasi prie `database` aplikacijos DB vartotoju.
- Mailpit UI: `https://mailpit.demo.local/`, SMTP projekto tinkle `mailpit:1025` be TLS ir be būtinos autentifikacijos. Laiškai saugomi `data/mailpit`, daugiausia 5000.

Kai `SERVICES` turi `mailpit`, `MAIL_MODE=auto` pats nukreipia PrestaShop laiškus į Mailpit ([paštas](12-cache-ir-hibridines-aplinkos.md#paštas-mail_mode)). Stage/prod šiems įrankiams reikia IP sąrašo.

## Cron

Cron veikia su [supercronic](https://github.com/aptible/supercronic). Crontab'as laikomas Git ir yra vienintelis tiesos šaltinis.

```dotenv
SERVICES="cron"
```

`config/cron/crontab` (visose aplinkose):

```cron
*/5 * * * * cd /var/www/html && php bin/import-prices.php
```

`config/cron/crontab.local` – papildomos užduotys tik `ENV=local`. Aplinkos failas bendro nepakeičia, abu vykdomi kartu. Formatas: 5 laiko laukai ir komanda, be Linux vartotojo stulpelio. Leidžiami `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`. Failo gale palik tuščią eilutę.

```bash
make build
make up
```

Kas vyksta:

- Kiekvieno paleidimo output, exit code, trukmė ir būdas (pagal grafiką ar rankomis) įrašomi į `data/cron`. Laikomi paskutiniai `CRON_HISTORY_KEEP` (numatyta 100) kiekvienos užduoties paleidimai. Output'as matomas ir `make logs service=cron`.
- Jei užduotis dar vykdoma, kai ateina kitas jos laikas, naujas paleidimas praleidžiamas.
- Cron naudoja projekto PHP image'ą, tą patį kodą ir `config/php` nustatymus. Jis laukia, kol bus sveiki `php-fpm` ir `database`. `config/startup/*.sh` cron'e nevykdomi.
- Užduotys gauna tą patį DB prisijungimą kaip `php-fpm` (`DATABASE_HOST`, `DATABASE_PORT`, `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_PASSWORD`), `DOMAIN`, `PROJECT_NAME`, `SETUP_ENV` ir visus [`APP_ENVIRONMENT`](03-nustatymai.md#savi-aplikacijos-parametrai-app_environment) kintamuosius. Projekto Compose faile jų kartoti nereikia.

**Sąsaja** `https://cron.demo.local/`:

- rodo užduotis ir kitą jų paleidimo laiką (su `CRON_SCHEDULE=off` – „off“, žr. žemiau);
- leidžia paleisti užduotį dabar ir matyti kiekvieno paleidimo output'ą, exit code ir trukmę;
- local/test leidžia redaguoti crontab'ą: pakeitimas patikrinamas `supercronic -test`, išsaugomas `config/cron/` ir iš karto pritaikomas. Pakeitimą commit'ink;
- stage/prod crontab'ą tik rodo: serveryje jis keičiamas tik per Git.

Atskiro prisijungimo sąsaja neturi. Prieigą riboja `CRON_ALLOWED_IPS` / `ALLOWED_IPS`, o stage/prod be sąrašo cron nepasileidžia. Sąsaja atsako tik nginx-proxy: kiti projekto tinklo konteineriai (pvz. parduotuvė) gauna 403.

**Grafikas išjungtas.** Kai kopijoje yra tikri API token'ai, o crontab'as sinchronizuoja su gyvomis sistemomis, sąsają galima palikti, o grafiką išjungti. `crontab.<ENV>` užduotis tik prideda, todėl tam yra atskiras raktas, leidžiamas bet kurioje aplinkoje (pvz. `env/local.env` arba serveryje lygiagretaus veikimo metu):

```dotenv
CRON_SCHEDULE=off
```

```bash
make restart service=cron
```

- supercronic nepaleidžiamas: nė viena užduotis nevykdoma pagal grafiką. Crontab'as vis tiek patikrinamas `supercronic -test`, todėl su klaidingu crontab'u cron nepasileidžia, kaip ir su `on`.
- Sąsaja rodo užduotis ir pranešimą „Schedule off“. Stulpelyje „Next run“ vietoj laiko rodoma „off“, o po juo – kada užduotis būtų paleista.
- „Run now“ veikia per tą patį `cron-run.sh`: paleidimas įrašomas į istoriją kaip rankinis (`manual`), output'as matomas sąsajoje ir `make logs service=cron`.
- Local/test crontab'o redagavimas lieka su `supercronic -test` patikra. Išsaugotas pakeitimas iškart matomas sąsajoje, bet pagal grafiką nevykdomas.
- Su `metrics` servisu `setup_cron_schedule_enabled` yra `0`, o `setup_cron_job_expected_run_timestamp_seconds` neatiduodama, todėl trūkstamo paleidimo alert'as nesuveikia.

Numatyta `CRON_SCHEDULE=on`; kita reikšmė nei `on` / `off` – klaida.

## Lentelių registras (backup)

[Lentelių registrą](07-duomenu-baze.md#c-lentelių-registras) galima tvarkyti naršyklėje, kaip cron'ą:

```dotenv
SERVICES="cron backup"
```

```bash
make up
```

Sąsaja `https://backup.demo.local/`:

- rodo visas projekto DB lenteles, su [papildomomis schemomis](07-duomenu-baze.md#papildomos-schemos) – po skirtuką kiekvienai (šalia – kiek jos lentelių nėra registre): eilučių skaičių, dydį, taisyklę (`grupė rinkinys importas`) ir ar ji jau yra projekto registre. Standartinės PrestaShop lentelės, kurias klasifikuoja setup'o `prestashop` profilis, čia nerodomos ir per sąsają neperrašomos: jos keičiamos tik setup'e, visiems projektams (išimtį projektui galima įrašyti tik rankomis į failą). Pirmiausia rodomos lentelės, kurių registre **nėra** (jos stabdo commit'ą, `db-import` ir `db-dataset`); šalia – pasiūlymas, kaip `make schema-resolve`. Paspaudus stulpelio antraštę įrašai rikiuojami pagal jį, antras paspaudimas – atvirkščiai;
- local/test leidžia nustatyti lentelės taisyklę, pažymėti ją `drop` ir pašalinti projekto eilutę, o mygtukas „Save all suggestions“ įrašo visų lentelių be taisyklės pasiūlymus. Pakeitimas iškart rašomas į `app/database/table-registry.txt` (nauja eilutė – failo pradžioje, kad laimėtų prieš `*` šablonus; PrestaShop lentelės rašomos su `{p}`, papildomos schemos – `schema.lentelė`). „Save all suggestions“ taikomas pasirinktai schemai. Failą peržiūrėk ir commit'ink;
- stage/prod registrą tik rodo: serveryje jis keičiamas tik per Git;
- viršuje paprastai paaiškinta, kam skirtas kiekvienas laukas: Group (tik rūšiavimui), Dev copy (ką lentelė įdeda į mažą programuotojų DB kopiją, laikomą su kodu) ir Live copy (ką paimti kopijuojant LIVE DB į kompiuterį ar serverio kopiją), ir ką reiškia kiekviena reikšmė. Lentelėje kiekvienas laukas turi savo stulpelį; lentelės be sprendimo laukuose (punktyrinė riba) – pasiūlymas.

Servisas veikia tik su DB kaip kodu (be `DATABASE_AS_CODE=off`) ir skaito DB aplikacijos vartotoju. Kol nėra `app/database` (schema dar neeksportuota), sąsaja tai pasako ir nieko nerašo. Stage/prod be `BACKUP_ALLOWED_IPS` ar `ALLOWED_IPS` nepasileidžia. Sąsaja atsako tik nginx-proxy. Naujas `backup.` poddomenis atsiranda po `setup/install-host` ir `make bootstrap`.

## DB monitorius

Išorinė analizės sistema gauna DB našumo duomenis per JSON endpoint'ą, neprisijungdama prie DB. Atsakymo formatas kol kas yra juodraštis (`"format": "setup-dbmonitor/draft"`): serverio versija, pagrindiniai būsenos skaitikliai ir 20 sunkiausių užklausų šablonų.

1. `env/<ENV>.env`:

```dotenv
SERVICES="dbmonitor"
DATABASE_PERFORMANCE_SCHEMA=on
DATABASE_MONITOR_PASSWORD='bent-16-simboliu-slaptazodis'
DBMONITOR_TOKEN='bent-24-simboliu-atsitiktinis-tokenas'
DBMONITOR_ALLOWED_IPS="203.0.113.20"
```

2. Pritaikyk ir sukurk monitoriaus DB vartotoją:

```bash
make build
make restart
make db-monitor-user
```

`make db-monitor-user` sukuria (arba atnaujina) vartotoją `DATABASE_MONITOR_USER` (numatyta `<DATABASE_NAME>_monitor`), kuris turi tik `PROCESS` ir skaitymo teises į `performance_schema` bei `sys`. Slaptažodis perduodamas per stdin, ne komandinėje eilutėje.

3. Užklausa:

```bash
curl -H "Authorization: Bearer $DBMONITOR_TOKEN" https://dbmonitor.demo.local/
```

Be teisingo token'o atsakymas `401`. DB portas nepublikuojamas. Stage/prod be `DBMONITOR_ALLOWED_IPS` ar `ALLOWED_IPS` monitorius nepasileidžia. `performance_schema` kainuoja kelis šimtus MB DB atminties, todėl numatyta `off`.

## Metrikos (Prometheus)

Taikinys (serveris, kuriame veikia projekto konteineriai) atiduoda Prometheus `/metrics` **tais pačiais vardais, etiketėmis ir vienetais**, kaip senas stebėjimo scriptas su tarpine programa. Esami Grafana dashboard'ai ir alert'ai veikia nepakeisti, o setup v2 projektui tarpinės programos nebereikia. Atiduodami **visi** dashboard'ų ir alert'ų naudojami vardai (išskyrus parduotuvės `monitoring_*` – PrestaShop modulio, ir PageSpeed `pagespeed_insight_*` – stebėjimo programos), grupėmis:

- **sistema**: CPU (`cpu_usage_*{cpu="all"|"0"|…}`), RAM (`ram_*`), diskas ir inode'ai (`disk_*`, `disk_inodes_*`), I/O (`io_usage_*`, `iousage_usr`, `ip_usage_nice`), tinklas (`network_*`), `uptime` ir apkrova (`uptime_*`), `disk_write_speed`, `active_connections`;
- **svetainė**: `website_response_time{url}`, `website_response_status{url}`, `support_level`, naujas `website_ssl_expiry_timestamp_seconds{domain}` (sertifikato galiojimo pabaiga, Unix laikas);
- **HTTP** (paskutinių pilnų minučių log'ai, žr. [HTTP log'ai](#http-logai)): `http_response_codes{code}`, `http_bytes_transferred_total`, `http_request_ips{ip}`, `http_error_type{type}`, nauji `http_request_user_agents{user_agent}`, `http_requests_per_minute` ir `http_requests_per_minute_max`;
- **saugumas**: `ddos_most_connections_ip_count{ip}` – penki adresai su daugiausiai TCP jungčių į 80 ir 443 portus;
- **DB** (per [DB monitoriaus paskyrą](#db-metrikos)): buffer pool (`sql_*`), lėtos užklausos, lentelių skenavimai ir užklausos pagal bendrą laiką (`database_slow_query_*`, `database_table_scan_query_*`, `database_total_time_query_*`), lėčiausia užklausa, lentelių lock'ai ir I/O, vykdomi sakiniai, prisijungimai;
- **serveris** (`host`): daugiausiai CPU ir RAM naudojantys procesai (`processes_consuming_cpu`, `processes_consuming_memory` ir jų `_total`), nauji I/O wait skaitikliai ir `setup_server_info` (OS, branduolys, Docker, DB versijos);
- **konteineriai** ir **cron** (naujos): kiekvieno projekto konteinerio būsena, health, restart'ai ir OOM; kiekvienos cron užduoties paskutinis paleidimas, exit kodas ir trukmė. Žr. [Konteineriai, cron ir procesai](#konteineriai-cron-ir-procesai).

`setup_metrics_group_up{group}` yra `0`, jei kurios nors grupės dalies nepavyko surinkti (priežastis – serviso log'e).

Metrikų servisas yra PHP integruotas serveris su 4 worker'iais (`PHP_CLI_SERVER_WORKERS`, PHP 7.4+): lėtas svetainės ar DB patikrinimas neblokuoja kito scrape'o. Su senesniu PHP užklausos aptarnaujamos po vieną.

1. `env/<ENV>.env`:

```dotenv
SERVICES="metrics"
METRICS_TOKEN='bent-24-simboliu-atsitiktinis-tokenas'
METRICS_ALLOWED_IPS="10.8.0.20"            # Prometheus serverio adresas
METRICS_URLS="https://demo.lt/ https://demo.lt/kontaktai"
METRICS_SUPPORT_LEVEL=2
```

2. Pritaikyk (`nginx-proxy` image'as gauna `/metrics` kelią):

```bash
make build
make up
```

3. Patikra:

```bash
curl -H "Authorization: Bearer $METRICS_TOKEN" https://demo.lt/metrics
```

Be token'o ar su neteisingu – `401`, ne iš sąrašo – `403`, servisas be token'o – `503`. Stage/prod be `METRICS_ALLOWED_IPS` ar `ALLOWED_IPS` (`metrics on prod requires METRICS_ALLOWED_IPS or ALLOWED_IPS`) ir be 24+ simbolių `METRICS_TOKEN` servisas nepasileidžia.

**Kaip Prometheus skaito.** Kelias yra pagrindiniame domene, ne atskirame subdomene, todėl tinka abu taikiniai:

- **DirectAdmin** (viešas domenas): DirectAdmin baigia TLS ir perduoda užklausą į nginx-proxy, šis IP tikrina pagal `X-Forwarded-For`. Sąraše – tik Prometheus adresas.
- **VPN** (`PUBLISH_ADDRESS` – VPN sąsajos adresas, be DirectAdmin): Prometheus jungiasi per VPN `http://` ir domeno vardu (Prometheus serveryje vardas turi rodyti į VPN adresą, pvz. `/etc/hosts`).

```yaml
scrape_configs:
  - job_name: 'demo'                  # tas pats vardas, kurį dashboard'o $job kintamasis jau naudoja
    scheme: https                     # VPN taikiniui: http
    metrics_path: /metrics
    scrape_interval: 5m
    scrape_timeout: 1m
    authorization:
      credentials_file: /etc/prometheus/tokens/demo
    static_configs:
      - targets: ['demo.lt']
```

Viena užklausa trunka apie sekundę (CPU ir tinklas matuojami 1 s, kaip `mpstat 1 1` / `sar 1 1`) plius kiekvieno `METRICS_URLS` adreso patikra (iki 20 s), todėl `scrape_timeout` palik didesnį už numatytą 10 s.

**Serverio, ne konteinerio skaičiai.** Servisas naudoja projekto PHP image'ą, veikia projekto vartotoju ir turi tris papildomus prijungimus, visus tik skaitymui:

- serverio `/proc` → `/host/proc`: CPU, RAM, apkrova ir uptime `/proc` yra viso serverio; tinklo skaitikliai skaitomi iš `/host/proc/1/net/dev` (serverio tinklo vardų erdvė, ne konteinerio). Release'as šį prijungimą palieka, nes `/proc` yra kiekviename Linux taikinyje;
- projekto `data/` katalogas → `/host/disk`: tik failų sistemos dydžiui (`df -k`, `df -i`), failai neskaitomi;
- HTTP log'ų tomas `<PROJECT_NAME>-<ENV>-http-logs` → `/host/http-logs`, žr. [HTTP log'ai](#http-logai).

Su `cron` servisu prisijungia dar du katalogai, taip pat tik skaitymui: cron istorija (`data/cron`) ir crontab (`app/config/cron`, jei yra). Docker socket'o metrikų servisas negauna – konteinerius skaito atskiras kolektorius, žr. [Konteineriai, cron ir procesai](#konteineriai-cron-ir-procesai).

Portų servisas nepublikuoja ir projekto tinkle nėra: jo atskirame tinkle yra tik nginx-proxy (jis kreipiasi į `/metrics`) ir DB (DB grupei).

**Prasmė ir vienetai** (kaip seno scripto, įskaitant jo keistenybes, kad dashboard'ai rodytų tą patį):

| Metrikos | Šaltinis | Vienetai, pastabos |
| --- | --- | --- |
| `cpu_usage_*` | `mpstat -P ALL 1 1` | %, `cpu="all"` ir kiekvienas branduolys |
| `ram_*` | `free` | KiB; `ram_used` = total − free − buff/cache |
| `disk_*`, `disk_inodes_*` | `df -k`, `df -i` projekto duomenų disko | KiB ir vnt.; `*_available_percentage` yra `Use%` (užimta dalis), kaip anksčiau |
| `io_usage_*`, `iousage_usr`, `ip_usage_nice` | `iostat -x` avg-cpu | % nuo serverio paleidimo; vardai su klaidomis palikti, nes juos naudoja dashboard'as |
| `network_*` | `sar -n DEV 1 1` | paketai/s, KiB/s; sąsaja – `METRICS_NETWORK_INTERFACE`, kitaip numatytojo maršruto |
| `uptime`, `uptime_average_load_*` | `uptime` | s; `uptime_average_load_10min` yra 5 min apkrova (seno parserio vardas) |
| `disk_write_speed` | `dd … conv=fdatasync` | kaip seno parserio (`MB/s × 1024`); `METRICS_DISK_WRITE_MB` konteinerio laikiname kataloge (Docker diske), ne dažniau nei kas valandą |
| `website_response_*` | `curl -L` | s ir HTTP kodas; `0` – neatsakė ar netinkamas sertifikatas |
| `http_response_codes`, `http_bytes_transferred_total`, `http_request_ips` | prieigos log'as per langą | vnt. ir baitai (`$body_bytes_sent`); IP – 10 aktyviausių (`head -10`); be užklausų kodų ir IP eilučių nėra, baitai `0` |
| `http_request_user_agents` | prieigos log'as | nauja: 10 dažniausių user agent'ų (seno scripto „HTTP top User agents“, kurių parseris neatidavė) |
| `http_requests_per_minute`, `http_requests_per_minute_max` | prieigos log'as | nauja: vidutinis ir didžiausias užklausų skaičius per minutę lange (seno scripto „requests per minute“) |
| `http_error_type` | svetainės klaidų log'as per langą | visada šeši tipai su seno parserio etiketėmis; įrašas su keliais tos pačios rūšies pranešimais skaičiuojamas kartą (kaip `awk`); `Connection reset` – nepriklausomai nuo raidžių dydžio |
| `ddos_most_connections_ip_count` | `/proc/net/tcp*` (kaip `netstat -an`) | 5 adresai; visos būsenos, išskyrus klausymą (taip pat `TIME_WAIT`); 80 **ir 443** portai, tik įeinančios jungtys; be loopback |
| `active_connections` | `netstat -an \| grep ESTABLISHED \| wc -l` | serverio tinklo vardų erdvės TCP `ESTABLISHED` (`/host/proc/1/net/tcp`, `tcp6`); konteinerių vidiniai ryšiai (pvz. PHP → DB) savo erdvėse neįskaičiuojami, VPN taikinyje – ir jungtys į nginx-proxy publikuotus portus (jos nginx-proxy erdvėje) |
| `processes_consuming_cpu`, `processes_consuming_memory` `{process}` | `ps -eo %mem,%cpu,cmd`, sumuota pagal komandą | %, kaip `ps`: CPU – procesoriaus laikas per visą proceso gyvavimą (ne šios akimirkos), RAM – rezidentinė dalis; po 10 kiekvieno. `process` – komanda **be argumentų** (`argv[0]`, branduolio gija – `[vardas]`): argumentuose būna slaptažodžių (pvz. runner'io OAuth), senas scriptas rodė visą eilutę |
| `processes_consuming_cpu_total`, `processes_consuming_memory_total` `{process}` | ten pat | tos komandos procesų skaičius |

**Svetainės patikra** eina iš metrikų konteinerio per jo atskirą tinklą: projekto tinkle domenas rodo tiesiai į nginx-proxy, o patikra turi praeiti tą patį kelią kaip lankytojas (DirectAdmin ir jo sertifikatas). Jei serveris negali pasiekti savo viešo adreso arba vardas yra tik VPN, nurodyk `METRICS_PROBE_ADDRESS` (pvz. `10.8.0.14`): jungiamasi ten, o `Host` ir SNI lieka iš URL. Sertifikato data nuskaitoma netikrinant jo, todėl pasibaigęs sertifikatas taip pat parodomas.

### HTTP log'ai

Senas scriptas kas 5 minutes skaitė DirectAdmin domeno prieigos ir klaidų log'us. Setup v2 tų pačių skaičių šaltiniai yra konteineriuose, todėl su `SERVICES=metrics` jie papildomai rašomi į bendrą Docker tomą `<PROJECT_NAME>-<ENV>-http-logs`; metrikų servisas jį prijungia tik skaitymui:

| Failas tome | Kas rašo | Kodėl šis |
| --- | --- | --- |
| `access.log` (+ `access.log.1`) | nginx-proxy, pagrindinės svetainės HTTP ir HTTPS serveriai, `combined` formatas | nginx-proxy mato tikrą kliento IP: priekinio web serverio `X-Forwarded-For` (tik iš patikimų, žr. [IP apribojimai](#ip-apribojimai)) arba tiesioginio kliento adresą VPN taikinyje; Apache už jo mato tik proxy adresą |
| `error.log` (+ `error.log.1`) | Apache per `rotatelogs` (svetainės `ErrorLog`) | PHP pranešimai ateina per FastCGI: su `metrics` php-fpm paleidžiamas su `fastcgi.logging=1` |
| `proxy-net/` | nginx-proxy kas 10 s (`/proc/net/tcp`, `tcp6`, `route`) | jungtys į publikuotus portus yra nginx-proxy tinklo vardų erdvėje, ne metrikų konteinerio |

- **`docker logs` nesikeičia**: nginx-proxy ir php-fpm toliau rašo viską į savo `docker logs` (su `CONTAINER_LOG_*` rotacija), tomas – tik papildoma kopija. `/metrics` užklausos lieka `docker logs`, bet į tomą nepatenka, kad Prometheus nebūtų skaičiuojamas kaip lankytojas.
- **Dydis ribotas**: kiekvienas log'as tome turi daugiausiai du failus po `METRICS_HTTP_LOG_MB` (numatyta 32): nginx-proxy perkelia `access.log` į `access.log.1` ir atidaro naują, Apache `rotatelogs -n 2` daro tą patį. Anksčiau Apache svetainės klaidų log'as augo konteinerio viduje be ribos.
- **Langas**: paskutinės `METRICS_HTTP_WINDOW_MINUTES` (numatyta 5) **pilnos** minutės iki einamosios, kaip seno scripto `5 minutes ago` … dabartinė minutė. Nustatyk lygų Prometheus `scrape_interval`, kad kiekviena užklausa būtų suskaičiuota vieną kartą (alert'ai sumuoja `sum_over_time(http_response_codes[30m])`).
- **Skaitymas**: log'ai skaitomi nuo galo dalimis ir tik kol pasiekiama eilutė, senesnė už langą (su 60 s atsarga vėliau įrašytoms eilutėms), todėl didelis log'as į atmintį nekraunamas. Eilutės be laiko (pvz. tęsinys) priskiriamos įrašui virš jų; nebaigta paskutinė eilutė ir ilgesnės nei 1 MiB praleidžiamos.
- **Laikas**: prieigos log'e laikas su zona, klaidų log'e – konteinerio `TZ` laikas (visi projekto konteineriai turi tą patį `TZ`).
- Kol Apache nieko neparašė, `error.log` nėra – tai reiškia nulį klaidų. Be `access.log` (tomas neprijungtas) HTTP grupės `setup_metrics_group_up` yra `0`.

**Saugumas** (`ddos_most_connections_ip_count`) skaičiuoja jungtis dviejose vietose: serverio tinklo vardų erdvėje (`/host/proc/1/net/tcp*`: DirectAdmin ar kitas priekinis web serveris priima klientus ten, kaip seno scripto `netstat`) ir nginx-proxy (`proxy-net/`: VPN taikinys, kur portai publikuoti tiesiai). nginx-proxy numatytojo maršruto vartai (Docker tinklo vartai – per juos ateina priekinio web serverio ir paties serverio persiųstos užklausos) nėra klientas. Jei nginx-proxy nuotrauka senesnė nei 2 min. ar jos nėra, serverio skaičiai vis tiek atiduodami, bet `setup_metrics_group_up{group="security"}` yra `0`.

### DB metrikos

DB grupė skaito tik statistiką per [DB monitoriaus](#db-monitorius) paskyrą (`PROCESS`, `performance_schema`, `sys`): skaitiklius ir užklausų šablonus, kuriuose reikšmės jau pakeistos `?`. Projekto lentelių eilučių ji skaityti negali (tai tikrina B testas), o vykdomų sakinių tekstas (`SQL_TEXT`, su reikšmėmis) nenaudojamas. Grupė generic – PrestaShop lentelių neliečia.

1. `env/<ENV>.env` (šalia `SERVICES="metrics"` ir token'o):

```dotenv
DATABASE_PERFORMANCE_SCHEMA=on
DATABASE_MONITOR_PASSWORD='bent-16-simboliu-slaptazodis'
```

2. Pritaikyk ir sukurk paskyrą (vieną kartą; kartoti saugu):

```bash
make up
make db-monitor-user
```

DB prisijungia prie metrikų serviso tinklo, todėl servisas DB randa vardu `database`, o pats lieka už projekto tinklo. Be `DATABASE_MONITOR_PASSWORD` ar nepasiekus DB – `setup_metrics_group_up{group="database"} 0`, kitos grupės veikia.

**`performance_schema` išjungta** (numatyta `off`) – tai būsena, ne klaida: `database_performance_schema 0`, grupė `1`, atiduodami tik buffer pool ir prisijungimai. Užklausų, lentelių ir vykdomų sakinių metrikų tada nėra.

| Metrikos | Šaltinis (seno scripto užklausa) | Vienetai, pastabos |
| --- | --- | --- |
| `sql_buffer_pool_size`, `sql_data_cached`, `sql_ram_free`, `sql_dirty_pages` | `INNODB_BUFFER_POOL_STATS`, `Innodb_buffer_pool_pages_dirty` | MB (puslapiai × `innodb_page_size`) |
| `sql_buffer_used` | ten pat | % (užimti puslapiai nuo viso pool'o) |
| `database_slow_query_total`, `_time_total`, `_time_average` `{query}` | `events_statements_summary_by_digest`, 10 lėčiausių pagal vidurkį | vykdymų sk., s, s |
| `database_table_scan_query_total`, `_total_row_examined`, `_total_row_sent` `{query}` | ten pat, 10 daugiausiai eilučių peržiūrėjusių | vnt. |
| `database_total_time_query_total`, `_time_total`, `_time_average` `{query}` | ten pat, 10 daugiausiai laiko užėmusių | vykdymų sk., s, s |
| `database_slowest_query_time{query}` (nauja) | ten pat, `MAX_TIMER_WAIT` | s; ilgiausias vienas vykdymas nuo DB statistikos pradžios (paleidimo) |
| `database_table_lock_total`, `database_table_lock_time_total` `{schema,table}` (naujos) | `table_lock_waits_summary_by_table`, 10 pagal laiką | counter nuo DB paleidimo; s |
| `database_table_io_read_total`, `_write_total`, `_read_time_total`, `_write_time_total` `{schema,table}` (naujos) | `table_io_waits_summary_by_table`, 10 daugiausiai skaitytų ir rašytų | counter nuo DB paleidimo; s |
| `database_running_statements`, `database_running_statement_time{query}` (naujos) | `events_statements_current` (be paties nuskaitymo) | vnt.; s, ilgiausiai vykdomas to šablono sakinys, 10 |
| `database_connections`, `database_connections_running`, `database_connections_max` (naujos) | `Threads_connected`, `Threads_running`, `max_connections` | vnt. |
| `database_performance_schema` (nauja) | `@@performance_schema` | `1` – įjungta, `0` – išjungta |

**Laiko langas.** Senas scriptas po kiekvieno paleidimo (kas 5 min.) išvalydavo `events_statements_summary_by_digest`, todėl `database_slow_query_*`, `database_table_scan_query_*` ir `database_total_time_query_*` rodė paskutinių 5 minučių užklausas. Monitoriaus paskyra lentelės valyti negali (ir neturi), todėl servisas pats skaičiuoja skirtumą nuo ankstesnio nuskaitymo: langas = Prometheus `scrape_interval` (5 min., kaip anksčiau). Pirmas nuskaitymas po metrikų serviso paleidimo apima laiką nuo DB paleidimo. Rankinis `curl` taip pat yra nuskaitymas – jis sutrumpina Prometheus langą. Vienas šablonas keliose schemose rodomas viena eilute (sumuojama).

### Lentelių registras

Su DB kaip kodu (numatyta) metrikų servisas tik skaitymui prijungia `data/<ENV>/table-registry/status.json`. Jį rašo kiekvienas `make db-backup` ir kopija prieš deploy'ų: kurios DB lentelės neturi eilutės registre.

| Metrika | Reikšmė |
| --- | --- |
| `setup_database_unregistered_tables` | kiek lentelių nėra registre (paskutinė patikra) |
| `setup_database_unregistered_table_info{table}` | kiekviena tokia lentelė (iki 100) |
| `setup_database_registry_check_timestamp_seconds` | kada patikrinta (kopijos laikas) |

Kol kopijos nebuvo, grupė nieko neatiduoda. Alert'o pavyzdys: `setup_database_unregistered_tables > 0`.

### Konteineriai, cron ir procesai

**Konteineriai.** Docker API (`/var/run/docker.sock`) yra root lygio prieiga, todėl web'ui atviras metrikų servisas jos negauna. Su `SERVICES=metrics` paleidžiamas antras konteineris `<PROJECT_NAME>-<ENV>-metrics-docker` (kolektorius): kas `METRICS_DOCKER_INTERVAL` sekundžių (numatyta 15) jis nuskaito **tik šio projekto** konteinerius (`com.docker.compose.project=<PROJECT_NAME>-<ENV>`) ir įrašo nedidelę nuotrauką į tomą `<PROJECT_NAME>-<ENV>-metrics-docker`; metrikų servisas tomą prijungia tik skaitymui. Kolektorius:

- neturi tinklo (`network_mode: none`), portų nepublikuoja, todėl iš išorės nepasiekiamas;
- siunčia tik `GET` užklausas (`/info`, `/containers/json`, `/containers/{id}/json`, `/events`), aplinkos kintamųjų ir kitų konteinerio duomenų nekopijuoja;
- veikia be capabilities (`cap_drop: ALL`, `no-new-privileges`) su tik skaitomu root failų sistema; root naudotojas reikalingas tik root'ui priklausančiam socket'ui atidaryti.

Socket'as prijungtas su `:ro`, bet tai **neriboja Docker API**: per jį vis tiek galima siųsti bet kokią užklausą, ir socket'as lieka root lygio prieiga. `:ro` tik neleidžia konteineriui pakeisti paties socket failo. Apsauga yra aukščiau išvardinta: kolektorius neturi tinklo ir capabilities, o jo kodas siunčia tik `GET`.

Kodėl ne socket proxy (pvz. `docker-socket-proxy`): jam reikia to paties socket'o ir jis pasiekiamas tinklu, o čia socket'ą turintis procesas tinklo neturi visai. Kodėl ne serverio scriptas: DirectAdmin taikiniuose setup v2 nieko į serverį nediegia. Release'as socket'o prijungimą palieka (kaip `/proc`), nes jis yra kiekviename Docker taikinyje.

| Metrikos | Šaltinis | Vienetai, pastabos |
| --- | --- | --- |
| `setup_container_info{project,service,container,image,state,health}` | `State.Status`, `State.Health.Status` | visada `1`; `state` – `running`, `restarting`, `exited`, …; `health` – `healthy`, `unhealthy`, `starting`, `none` |
| `setup_container_running{project,service,container}` | `State.Status` | `1` – veikia, `0` – ne (taip pat `restarting`) |
| `setup_container_healthy{…}` | `State.Health.Status` | `1` – `healthy`, `0` – `unhealthy` ar `starting`; tik konteineriams su healthcheck |
| `setup_container_restarts_total{…}` | `RestartCount` | counter: restart policy perkrovimai nuo konteinerio sukūrimo (`make restart` ir perkūrimas jo nedidina) |
| `setup_container_oom_killed{…}`, `setup_container_exit_code{…}` | `State.OOMKilled`, `State.ExitCode` | paskutinio išėjimo priežastis ir kodas; po perkrovimo Docker `OOMKilled` išvalo |
| `setup_container_oom_kills_total{project,service}` | Docker `oom` įvykiai | counter: OOM nužudymai nuo kolektoriaus pirmo paleidimo (skaičius laikomas nuotraukoje, todėl išlieka per kolektoriaus perkrovimą) |
| `setup_container_started_timestamp_seconds{…}`, `setup_containers_collected_timestamp_seconds` | `State.StartedAt`, nuotraukos laikas | Unix laikas |

Jei nuotraukos nėra ar ji senesnė nei trys intervalai (+30 s), konteinerių metrikų nėra, o `setup_metrics_group_up{group="containers"} 0`.

**Cron** (su `SERVICES="cron metrics"`): metrikų servisas tik skaitymui prijungia crontab (`app/config/cron`, jei yra) ir cron serviso paleidimų istoriją (`data/cron/history.jsonl`, rašo `cron-run.sh`). Kiekvienai šios aplinkos užduočiai (`crontab` ir `crontab.<ENV>`):

| Metrikos | Pastabos |
| --- | --- |
| `setup_cron_job_info{job,source,line,schedule}` | `job` – užduoties id, kaip cron UI (pagal komandą); `source:line` – crontab failas ir eilutė. **Komanda etiketėse nerodoma**: joje būna token'ų (pvz. cron URL su `?token=`) |
| `setup_cron_job_last_run_timestamp_seconds{job}`, `setup_cron_job_last_exit_code{job}`, `setup_cron_job_last_duration_seconds{job}` | paskutinis užbaigtas paleidimas (planinis ar rankinis): pradžia, exit kodas (`0` – sėkmė), trukmė s |
| `setup_cron_job_last_success_timestamp_seconds{job}` | paskutinio sėkmingo paleidimo pradžia |
| `setup_cron_job_expected_run_timestamp_seconds{job}` | pirmas planinis laikas po paskutinio paleidimo (be paleidimų – po dabar); jei jis praeityje, paleidimo trūksta (neįvyko, praleistas, nes ankstesnis dar vyko, ar dar vyksta). Su `CRON_SCHEDULE=off` neatiduodama |
| `setup_cron_schedule_enabled` | `1` – užduotys vykdomos pagal grafiką, `0` – [grafikas išjungtas](#cron) (`CRON_SCHEDULE=off`) |

Ta pati komanda `crontab` ir `crontab.<ENV>` turi vieną id ir vieną istoriją (kaip cron UI), todėl paleidimo metrikos jai atiduodamos kartą. Alert'ų pavyzdžiai: `setup_cron_job_last_exit_code != 0`; `time() - setup_cron_job_expected_run_timestamp_seconds > 600`. Be cron istorijos katalogo – `setup_metrics_group_up{group="cron"} 0`; be `cron` serviso grupė nieko neatiduoda.

**Procesai, I/O wait, versijos** (`host` grupė, iš serverio `/proc`):

| Metrikos | Pastabos |
| --- | --- |
| `processes_consuming_*` | seno scripto vardais, žr. lentelę [aukščiau](#metrikos-prometheus) |
| `setup_cpu_iowait_seconds_total` | counter: CPU laikas, laukiant I/O (`/proc/stat` `iowait`, visi branduoliai); Grafana – `rate()`. Seni `cpu_usage_iowait` (1 s mėginys) ir `io_usage_iowait` (nuo paleidimo) lieka |
| `setup_processes_blocked` | procesai, dabar laukiantys I/O (`procs_blocked`) |
| `setup_disk_io_time_seconds_total{device}` | counter: laikas, kai diskas vykdė I/O (`/proc/diskstats`); tik visi diskai, be skaidinių, `loop`, `nbd` ir RAM įrenginių |
| `setup_server_info{os,kernel,docker,database}` | visada `1`: OS ir Docker versija iš kolektoriaus (Docker `/info`), branduolys iš `/proc`, DB – `VERSION()` per monitoriaus paskyrą; nežinoma – tuščia etiketė |
