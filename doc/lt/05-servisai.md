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

Nežinomas vardas `SERVICES` sąraše yra klaida. Servisų adresai `pma.`, `mailpit.`, `cron.`, `dbmonitor.` veikia per kompiuterio Nginx, kurį paruošia `make bootstrap`. Stage/prod įrankiai `pma`, `mailpit`, `cron`, `dbmonitor` be IP sąrašo nepasileidžia, žr. [IP apribojimai](#ip-apribojimai).

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
| `php-fpm` | `config/startup/*.sh` | Prieš PHP-FPM startą, žr. [aplikacija](06-aplikacija.md#startup-scriptai). |

Kiekvienas katalogas gali turėti aplinkos poaplankį, pvz. `config/php/local/`, `config/nginx-proxy/prod/server.d/`, `config/mysql/test/init.d/`. Jo failai skaitomi tik toje aplinkoje ir po bendrų failų.

phpMyAdmin, Mailpit ir Memcached drop-in katalogų neturi: juos valdo env raktai ir Compose skirtumų failai.

**Katalogai neprivalomi.** Jei katalogo nėra, jis neprijungiamas ir niekas jo nekuria. Katalogą sukurk tik tada, kai dedi į jį failą. Pirmą kartą sukūrus katalogą paleisk `make up`, kad jis būtų prijungtas. Vėliau pakeitus failo turinį užtenka `make restart service=<servisas>`.

### .template failai

Failas su galūne `.template` konteinerio starto metu paverčiamas į failą be šios galūnės. Jame `${VARDAS}` pakeičiamas to konteinerio env reikšme:

- keičiamas tik `${VARDAS}` su riestiniais skliaustais, todėl Nginx `$uri`, `$host` ar PHP kintamieji nepaliečiami;
- nežinomas kintamasis yra klaida, ir konteineris nepasileidžia;
- reikšmės imamos tik iš to konteinerio env.

`php-fpm`, `webserver`, `nginx-proxy` ir `database` konteineriuose prieinami `DOMAIN`, `PROJECT_NAME`, `SETUP_ENV` ir `TZ`, DB konteineryje dar `DATABASE_NAME`, `DATABASE_USER`, `DATABASE_BUFFER_POOL`. Redis konteineryje yra tik `TZ` ir `REDIS_MAXMEMORY_MB`. Savo kintamąjį pirmiau perduok tam servisui per Compose `environment` ([pavyzdys](03-nustatymai.md#savas-parametras-tik-vienam-projektui)).

**Dėmesio:** `config/php/*.template` failus renderina ir cron konteineris, o jame `DOMAIN`, `PROJECT_NAME` ir `SETUP_ENV` nėra (yra `TZ`, `ENV`, `XDEBUG_MODE`, cache ir `PS_*` raktai). Jei `SERVICES` turi `cron`, PHP šablonuose naudok tik abiem konteineriams bendrus kintamuosius, kitaip cron nepasileis.

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

Kiekvienas web įėjimas (svetainė, PMA, Mailpit, cron UI, DB monitorius) ribojamas nginx-proxy lygyje.

| Raktas | Kam galioja |
| --- | --- |
| `ALLOWED_IPS` | Visiems įėjimams, **įskaitant svetainę**. |
| `SITE_ALLOWED_IPS` | Tik svetainei. |
| `PMA_ALLOWED_IPS`, `MAILPIT_ALLOWED_IPS`, `CRON_ALLOWED_IPS`, `DBMONITOR_ALLOWED_IPS` | Tik tam įrankiui. |

Taisyklės:

- Sąraše rašyk IP arba CIDR (`203.0.113.10 198.51.100.0/24 2001:db8::10`), atskirk tarpais arba kableliais. Neteisingas įrašas sustabdo Nginx.
- Įėjimo sąrašas (`PMA_ALLOWED_IPS`) pakeičia bendrą `ALLOWED_IPS` tam įėjimui.
- Ne iš sąrašo atėjęs klientas gauna `403`.
- **Local/test** su tuščiu sąrašu viskas atvira.
- **Stage/prod** su tuščiu sąrašu svetainė vieša, o įrankiai uždrausti. Be to, runner'is atsisako paleisti `pma`, `mailpit`, `cron`, `dbmonitor` be sąrašo: `pma on prod requires PMA_ALLOWED_IPS or ALLOWED_IPS`.

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

**Tikras kliento IP.** Konteinerio portai rišami tik prie `127.0.0.1`, todėl į projektą patenkama tik per kompiuterio Nginx arba serverio web serverį. nginx-proxy priima `X-Forwarded-For` tik iš `TRUSTED_PROXIES` adresų (numatyta: localhost ir privatūs tinklai), Apache `mod_remoteip` pasitiki tais pačiais tinklais. Todėl PHP mato tikrą kliento IP, o HTTPS žymą – iš `X-Forwarded-Proto`. Į leidžiamų klientų sąrašą proxy adreso nedėk: taip įleistum visus už jo esančius lankytojus.

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

**Sąsaja** `https://cron.demo.local/`:

- rodo užduotis ir kitą jų paleidimo laiką;
- leidžia paleisti užduotį dabar ir matyti kiekvieno paleidimo output'ą, exit code ir trukmę;
- local/test leidžia redaguoti crontab'ą: pakeitimas patikrinamas `supercronic -test`, išsaugomas `config/cron/` ir iš karto pritaikomas. Pakeitimą commit'ink;
- stage/prod crontab'ą tik rodo: serveryje jis keičiamas tik per Git.

Atskiro prisijungimo sąsaja neturi. Prieigą riboja `CRON_ALLOWED_IPS` / `ALLOWED_IPS`, o stage/prod be sąrašo cron nepasileidžia. Sąsaja atsako tik nginx-proxy: kiti projekto tinklo konteineriai (pvz. parduotuvė) gauna 403.

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
