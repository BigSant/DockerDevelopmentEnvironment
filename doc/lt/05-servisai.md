# 5. Servisų konfigūracija su situacijomis

[Turinys](README.md) · [Atgal](04-compose-ir-dockerfile.md) · [Toliau: aplikacija](06-aplikacija.md)

Failų pavadinimus, pvz. `20-runtime.ini`, gali pasirinkti pats. Svarbūs yra katalogas, plėtinys ir programos skaitomos direktyvos. Bendro katalogo failai skaitomi prieš pasirinktos aplinkos failus. PHP/MySQL nustatymams tai leidžia aplinkoje pakeisti reikšmę; Nginx ir Apache dar svarbus direktyvos kontekstas – ne visus įrašus galima pakartoti.

## PHP: daugiau atminties ir didesnis įkeliamas failas

Situacija: moduliui reikia 1 GB atminties, o tiekėjo CSV failas yra 50 MB.

`env/local.env` HTTP vykdymui:

```dotenv
PHP_MEMORY_LIMIT=1024M
PHP_MAX_EXECUTION_TIME=300
PHP_UPLOAD_MAX_FILESIZE=64M
PHP_POST_MAX_SIZE=70M
WEBSERVICE_TIMEOUT=300
```

`config/php/local/20-runtime.ini` CLI ir bendriems PHP nustatymams:

```ini
memory_limit = 1024M
max_execution_time = 300
upload_max_filesize = 64M
post_max_size = 70M
date.timezone = Europe/Vilnius
display_errors = On
log_errors = On
```

`config/php/prod/20-runtime.ini` pavyzdys:

```ini
display_errors = Off
log_errors = On
opcache.validate_timestamps = 0
```

`opcache.validate_timestamps=0` tinka tik kai diegimo procedūra perkrauna PHP. Kitaip naujas kodas gali nesimatyti. Vietoje naudok `1` ir `opcache.revalidate_freq=0`.

Pritaikymas: `make up` perkurs PHP, jei pasikeitė env. Jei keitei tik `.ini` turinį, perkrauk PHP per [tiesioginę Compose komandą](11-komandos-ir-klaidos.md). Patikra: per `make shell` vykdyk `php --ini` ir `php -r 'echo ini_get("memory_limit"), PHP_EOL;'`. Tai CLI rezultatas; HTTP limitą FPM gali pakeisti per `php_admin_value`. Įkėlimo ribą galutinai tikrink tikroje aplikacijos formoje.

Nginx pagrindiniame serverio bloke šiuo metu turi 100 MB ribą. Šiam 50 MB pavyzdžiui jos pakanka. Didesniam nei 100 MB failui reikėtų pakeisti būtent serverio bloko config; vien globalaus `client_max_body_size` failas `config/nginx-proxy/` gali nepadėti.

Pilnas PHP direktyvų sąrašas ir jų taikymo vietos: [PHP ini žinynas](https://www.php.net/manual/en/ini.list.php).

### PHP-FPM procesų skaičius

Situacija: keli vienu metu vykstantys sunkūs importai sunaudoja per daug RAM. `config/php/*.ini` nekeičia FPM `pm.max_children`.

Sukurk savą `config/php-fpm/pool.conf`:

```ini
[www]
pm.max_children = 5
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
```

Prijunk `compose/local.yaml`:

```yaml
services:
  php-fpm:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/config/php-fpm/pool.conf:/usr/local/etc/php-fpm.d/zzzz-project.conf:ro
```

Tai **savas prijungimas**; `config/php-fpm` pagal vardą automatiškai neskaitomas. Patikrink konfigūraciją su `php-fpm -tt` konteineryje, tada perkrauk PHP. Derink procesų skaičių su vienos užklausos atminties riba.

## Xdebug: sustoti ties eilute PhpStorm

Situacija: nori matyti, kodėl modulis gauna neteisingą kainą.

PHP 8.1 vietinio atvaizdo Xdebug 3 pavyzdys `config/php/local/30-xdebug.ini`:

```ini
xdebug.mode = debug
xdebug.start_with_request = trigger
xdebug.client_host = host.docker.internal
xdebug.client_port = 9003
xdebug.idekey = PHPSTORM
```

Perkrauk PHP. PhpStorm įjunk klausymąsi, uždėk breakpoint ir užklausai pridėk `XDEBUG_TRIGGER=1`, pavyzdžiui `http://demo.localhost:31820/?XDEBUG_TRIGGER=1`. Patikrink, kad IDE kelio `/var/www/html` atitikmuo yra tavo aplikacijos `public/`.

Šios eilutės skirtos Xdebug 3. Senas Xdebug 2 naudoja kitus vardus; nekopijuok jų į PS 1.6 atvaizdą nežinodamas plėtinio versijos. Patikrink `php --ri xdebug`. Visi nustatymai: [Xdebug žinynas](https://xdebug.org/docs/all_settings).

## MySQL ir MariaDB: serverio nustatymai lieka config

Situacija: vietinis dump didelis, reikia didesnio paketo ir buffer pool.

MySQL failas `config/mysql/local/20-runtime.cnf`:

```ini
[mysqld]
max_allowed_packet = 128M
innodb_buffer_pool_size = 512M
```

MariaDB atveju toks failas būtų `config/mariadb/local/20-runtime.cnf`. Abu katalogus gali laikyti repozitorijoje, bet skaitomas tik pasirinkto variklio katalogas.

Prieš perkraunant DB sustabdyk tuo metu vykstantį importą/migraciją. Po perkrovimo DB klientu patikrink `SHOW VARIABLES LIKE 'max_allowed_packet';`. `make db-prepare` palaukia DB sveikatos, tačiau vien `.cnf` turinio pakeitimas nebūtinai privers Compose ją perkrauti.

MySQL pagrindiniai projekto `.cnf` prijungiami į `/etc/mysql/project.d`, pasirinktos aplinkos – į `/etc/mysql/project.d.env`. Tai katalogai `.cnf`, ne `.sql` failams.

### Vietinis lėčiau tikrinantis SQL režimas

Kai senas modulis neteisingai naudoja `GROUP BY`, pirmiausia taisyk užklausą. Jei reikia atkurti konkrečios senos parduotuvės aplinką, jos vietiniame `.cnf` galima aiškiai parinkti režimą:

```ini
[mysqld]
sql_mode = STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION
```

Tai pakeičia SQL tikrinimo taisykles, o ne pataiso modulį. Tą pasirinkimą užrašyk projekto README, kad kolegų aplinkos nesiskirtų nepastebimai.

Visi serverio kintamieji: [MySQL 8.4](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html), [MariaDB](https://mariadb.com/docs/server/server-management/variables-and-modes/server-system-variables). Pasirink savo variklio ir versijos dokumentaciją; jų direktyvos ne visada sutampa.

### Pakeisti MySQL į MariaDB

Situacija: naujas projektas turi naudoti MariaDB. `compose/base.yaml` pakeisk **vieną include**:

```yaml
include:
  - ${ROOT_DIRECTORY}/docker/nginx-proxy/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/apache/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/mariadb/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/php-fpm/docker-compose.yml
networks:
  network_app:
    name: ${PROJECT_NAME}-${ENV}-network
    driver: bridge
```

MySQL include tuo metu neturi likti: abu aprašai naudoja serviso vardą `database`. Į common env įrašyk pasirinktą `MARIADB_VERSION`, pvz. CI tikrinamą `11.4`. Failai bus rašomi į `${PROJECT_DATA_DIRECTORY}/mariadb`.

Esamam projektui pirmiausia išsaugok MySQL dump ir patikrintą grįžimo variantą, sustabdyk seną DB, tada paleisk MariaDB su **atskiru tuščiu** katalogu ir importuok loginį dump. MySQL `/var/lib/mysql` failų tiesioginis prijungimas MariaDB nėra šiame setup įgyvendinta migracija.

## Apache: kitas document root arba papildoma direktyva

Situacija: checkout yra Symfony projektas, jo viešas failas – `public/index.php`.

Į `env/common.env` įrašyk `DOCUMENT_ROOT=public`. Naudok `PROFILE=` savo pasirinktai bendro PHP aplikacijai. Apache viduje bus aptarnaujamas `/var/www/html/public`, nors visas kodas prijungtas į `/var/www/html`.

Papildomos serverio direktyvos gali būti faile `config/apache/local/20-runtime.conf`:

```apache
Timeout 300
ProxyTimeout 300
```

Patikrink su `httpd -t` Apache konteineryje ir perkrauk servisą. Projektiniai `.conf` prijungiami serverio kontekste **prieš** pagrindinį `sites.conf`; juose nesitikėk automatiškai perrašyti visų vėliau aprašyto VirtualHost nustatymų.

Jei reikia viso savo VirtualHost, prijunk projekto failą į `/usr/local/apache2/conf/sites.conf`, o standartinis init scriptas naudoja `sed`, taigi tas failas negali būti tiesiog read-only pakeistas nepakoregavus starto. Paprasčiau savam serveriui kartu parinkti starto komandą, kuri skaito paruoštą config ir paleidžia `httpd-foreground`. Tai jau sąmoningas viso serverio aprašo pakeitimas.

Visų direktyvų paskirtys ir leidžiami kontekstai: [Apache žinynas](https://httpd.apache.org/docs/2.4/mod/directives.html).

## Nginx proxy: papildomas sveikatos adresas

Situacija: reikia paprastos HTTP patikros, kuri patvirtintų paties proxy veikimą.

`config/nginx-proxy/local/20-status.conf`:

```nginx
server {
    listen 8081;
    server_name _;
    location = /health {
        default_type text/plain;
        return 200 "proxy veikia\n";
    }
}
```

`compose/local.yaml` papildyk:

```yaml
services:
  nginx-proxy:
    ports:
      - "127.0.0.1:31822:8081"
```

Po `make up` patikrink `http://127.0.0.1:31822/health`. Tai proxy patikra, o ne įrodymas, kad aplikacijos DB ar prisijungimas veikia.

`config/nginx-proxy/*.conf` įtraukiami `http` kontekste. Ten tinka papildomas `server`, `map`, `upstream`, bet ne atskiras `location` be serverio. Vienodo `client_max_body_size` pakartojimas tame pačiame kontekste gali būti klaida; kitame kontekste jau esantis serverio nustatymas gali turėti pirmenybę. Patikrink `nginx -t`. Pilnas sąrašas: [Nginx direktyvos](https://nginx.org/en/docs/dirindex.html).

Repozitorijoje esantis senas `docker/docker/nginx` ir `docker/config/nginx` yra atskiras istorinis variantas. Jis nėra automatiškai prijungtas į dabartinį Apache + nginx-proxy pagrindą ir jo keliai nėra sutapatinami su `config/nginx-proxy`.

## Redis: cache vienam projektui

Situacija: aplikacija moka naudoti Redis, bet kitiems projektams jo nereikia.

1. Turėk `compose/redis.yaml`, `config/redis/redis.conf` ir `config/redis/local/redis.conf`. Galima nukopijuoti juos iš [grouped šablono](../../templates/grouped).
2. Į `PROJECT_COMPOSE_FILES` sąrašą pridėk `compose/redis.yaml`, išsaugodamas jau reikalingus kitus failus.
3. Į common env įrašyk `REDIS_VERSION=7.4`.
4. Į local env įrašyk `COMPOSE_PROFILES=redis` arba papildyk esamą sąrašą.

Pilnas savas `compose/redis.yaml` variantas su paprastu duomenų keliu:

```yaml
services:
  redis:
    image: redis:${REDIS_VERSION}-alpine
    profiles: [redis]
    restart: unless-stopped
    command: [redis-server, /usr/local/etc/redis/redis.conf]
    networks: [network_app]
    volumes:
      - ${PROJECT_DATA_DIRECTORY}/redis:/data
      - ${PROJECT_DOCKER_DIRECTORY}/config/redis/redis.conf:/usr/local/etc/redis/redis.conf:ro
      - ${PROJECT_DOCKER_DIRECTORY}/config/redis/${ENV}:/usr/local/etc/redis/environment:ro
    healthcheck:
      test: [CMD, redis-cli, ping]
      interval: 5s
      timeout: 3s
      retries: 5
```

`config/redis/redis.conf`:

```conf
bind 0.0.0.0
protected-mode yes
port 6379
daemonize no
logfile ""
dir /data
include /usr/local/etc/redis/environment/redis.conf
```

`config/redis/local/redis.conf`, kai Redis yra atkuriamas cache:

```conf
appendonly no
save ""
maxmemory 128mb
maxmemory-policy allkeys-lru
```

`config/redis/prod/redis.conf` pavyzdys **cache** su disko žurnalu:

```conf
appendonly yes
appendfsync everysec
save ""
maxmemory 256mb
maxmemory-policy allkeys-lru
```

`allkeys-lru` gali šalinti raktus. Šio pavyzdžio nenaudok kaip patikimos užduočių eilės ar vienintelės svarbių duomenų saugyklos. Tokiam scenarijui reikia atskiro talpos, išlaikymo, prieigos ir atsarginių kopijų sprendimo. Konfigūravimas: [Redis dokumentacija](https://redis.io/docs/latest/operate/oss_and_stack/management/config/).

```bash
make init
make check
make pull
make up
```

`make pull` atsisiunčia Redis. Aplikacijoje atskirai nustatyk `redis:6379`, pavyzdžiui perdavęs jai `REDIS_HOST=redis` per Compose environment. Patikrink `redis-cli ping` Redis konteineryje; atsakymas turi būti `PONG`.

Jei naudoji nepakeistą grouped Redis šabloną, jo kelias yra `${PROJECT_DATA_DIRECTORY}/${ENV}/redis`, taigi vietoje `data/local/redis`. Aukščiau pateiktas savas variantas naudoja tiesiog `data/redis`. Vieno veikiančio projekto kelio nekeisk neperskaitęs jame esančių duomenų paskirties.

`ENV=test` reikės ir `config/redis/test/redis.conf`; vien `local` failo neužtenka, nes `include` ieško pasirinktos aplinkos failo.

## Mailpit ir phpMyAdmin: papildyti minimalų projektą

Situacija: nori pagauti laiškus ir DB naršyti per UI.

Minimalus Forsenos `base.yaml` šių servisų neturi. Į jo esamą `include` sąrašą pridėk:

```yaml
  - ${ROOT_DIRECTORY}/docker/mailpit/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/pma/docker-compose.yml
```

Į local env pridėk `COMPOSE_PROFILES=mailpit,pma`. Patogiausia minimaliame variante jų UI parodyti tiesioginiais portais `compose/local.yaml`:

```yaml
services:
  mailpit:
    ports:
      - "127.0.0.1:31825:8025"
    environment:
      MP_MAX_MESSAGES: "1000"
  pma:
    ports:
      - "127.0.0.1:31826:80"
    environment:
      PMA_ABSOLUTE_URI: http://127.0.0.1:31826/
```

Vykdyk `make init`, `make build`, `make up`. Mailpit atverk `http://127.0.0.1:31825`, phpMyAdmin – `http://127.0.0.1:31826`.

Aplikacijos SMTP nustatyk `mailpit`, portą `1025`, be TLS ir be būtinos autentifikacijos. Forsenos after-import SQL **išjungia** laiškų siuntimą; norėdamas juos matyti Mailpit, papildomai parink aplikacijoje SMTP režimą. SMTP UI portas `8025` nėra siuntimo portas `1025`.

Pilno šablono Nginx jau turi `mailpit.<DOMAIN>` ir `pma.<DOMAIN>` virtualius hostus. Jiems reikia veikiančių servisų ir atitinkamų domenų rezoliucijos. Minimalios Forsenos komanda šiuos virtualius hostus išvalo, todėl vien profilio įjungimas jos subdomenų neatkurs.

Mailpit duomenys laikomi `${PROJECT_DATA_DIRECTORY}/mailpit`, jo `MP_DATABASE` numatytai `/data/mailpit.db`, `TZ=Europe/Vilnius`, `MP_MAX_MESSAGES=5000`. phpMyAdmin naudoja `PMA_HOST=database`, `PMA_USER` ir `PMA_PASSWORD` iš runtime DB nustatymų. Šias servisų `environment` reikšmes galima keisti projekto Compose; vien tokio paties vardo nauja env eilutė jų nepakeičia, kol nėra `${...}` prijungimo.

Vietiniame Mailpit taip pat nustatyta `MP_SMTP_AUTH_ACCEPT_ANY=1` ir `MP_SMTP_AUTH_ALLOW_INSECURE=1`. Tai testinio laiškų gaudymo parinktys. phpMyAdmin `PMA_ABSOLUTE_URI` turi atitikti pasirinktą UI adresą, kaip tiesioginio porto pavyzdyje.

## Cron: periodiškai importuoti kainas

Situacija: vietoje kas penkias minutes nori paleisti aplikacijos `bin/import-prices.php`.

Jei cron nėra pagrinde, į esamą `include` pridėk bendrą `docker/cron/docker-compose.yml` ir įjunk `COMPOSE_PROFILES=cron` (ar papildyk sąrašą).

Failas `config/cron/crontab.local`:

```cron
*/5 * * * * cd /var/www/html && php bin/import-prices.php
```

Failo gale palik naują eilutę. Supercronic formato eilutėje nereikia Linux vartotojo stulpelio. Bendras `config/cron/crontab` vykdomas kartu su `crontab.<ENV>` – aplinkos failas bendro sąrašo nepakeičia.

```bash
make init
make up
```

Patikrink cron logus. Scriptas turi egzistuoti ir būti kartojamas saugiai. Cron naudoja tą patį PHP atvaizdą ir aplikacijos katalogą, bet savo `entrypoint`: bendri `config/startup/*.sh` jame iš naujo nevykdomi. PHP konfigūraciją jau paruošia `php-fpm`, kurio sveikatos cron laukia. Savus API env kintamuosius cron servisui perduok atskirai.

Norėdamas cron išjungti, pašalink profilį ir pašalink anksčiau paleistą cron konteinerį arba atlik `make down`, tada `make up`. Vien naujas `up` su mažesniu profilių sąrašu nebūtinai sustabdo seniau paleistą servisą.
