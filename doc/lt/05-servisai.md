# 5. Servisų konfigūracija su situacijomis

[Turinys](README.md) · [Atgal](04-compose-ir-dockerfile.md) · [Toliau: aplikacija](06-aplikacija.md)

Failų pavadinimus, pvz. `20-runtime.ini`, gali pasirinkti pats. Svarbūs yra katalogas, plėtinys ir programos skaitomos direktyvos. Bendro katalogo failai skaitomi prieš pasirinktos aplinkos failus. PHP/MySQL nustatymams tai leidžia aplinkoje pakeisti reikšmę; Nginx ir Apache dar svarbus direktyvos kontekstas – ne visus įrašus galima pakartoti.


## Neprivalomi konfigūracijos katalogai

Bendri `config/php`, `config/mysql`, `config/mariadb`, `config/apache` ir
`config/nginx-proxy` katalogai bei jų aplinkos pakatalogiai prijungiami tik jeigu
jie egzistuoja. Vien dėl standartinės konfigūracijos jų kurti nereikia.
Tas pats galioja PHP `config` prijungimui, per kurį pasiekiami startup scriptai
ir PrestaShop papildomi parametrai.

Pavyzdys: reikia PHP nustatymo tik lokaliai. Sukurk
`config/php/local/custom.ini`, įrašyk nustatymą ir paleisk `make up`.
Runner iš naujo aptinka katalogą. Jei katalogas jau prijungtas ir keitei tik failo
turinį, PHP perkrauk su `make restart service=php-fpm`.

Savo Compose faile nurodytas kitas config failas ar katalogas lieka privalomas.
DB duomenų, aplikacijos ir TLS prijungimai taip pat nėra praleidžiami.
Runner paruošia techninį prijungimų papildymą `.generated` kataloge;
jo redaguoti ar saugoti Git nereikia. PhpStorm atnaujinimas per `make ide-refresh`
naudoja tą pačią galutinę konfigūraciją.

## PHP-FPM procesų skaičius

`PHP_MEMORY_LIMIT` yra vienos PHP užklausos atminties riba.
`PHP_CONTAINER_MEMORY` apriboja visą konteinerį. Kad užklausos nekonkuruotų dėl
visos atminties, FPM vienu metu leidžiamų procesų skaičius konfigūruojamas atskirai.
Numatyta: daugiausia 4 procesai, startuoja 1, laikomi 1–2 laisvi procesai,
kiekvienas pakeičiamas nauju po 500 užklausų.

Pavyzdys didesnei aplinkai, kurios apkrova ir atminties sąnaudos jau išmatuotos:

```dotenv
PHP_FPM_MAX_CHILDREN=8
PHP_FPM_START_SERVERS=2
PHP_FPM_MIN_SPARE_SERVERS=1
PHP_FPM_MAX_SPARE_SERVERS=3
PHP_FPM_MAX_REQUESTS=500
```

Laikyk `env/common.env` arba konkrečios aplinkos env. Nebūtina įrašyti numatytųjų
reikšmių. Turi galioti `MIN_SPARE_SERVERS <= START_SERVERS <= MAX_SPARE_SERVERS <= MAX_CHILDREN`.
`MAX_REQUESTS=0` išjungia periodinį procesų pakeitimą. Pakeitus env užtenka
`make up`; pirmą kartą pereinant nuo senų atvaizdų reikia `make build`.
Keturi procesai nėra garantija, kad visos užklausos tilps į 1 GB: dideliems importams
ir sunkesniems moduliams reikia matuoti atmintį ir derinti abi ribas.
[PHP-FPM nustatymų žinynas](https://www.php.net/manual/en/install.fpm.configuration.php).


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

Į `env/common.env` įrašyk `DOCUMENT_ROOT=public`. Bendro PHP aplikacijai `PROFILE` įrašo nereikia. Apache viduje bus aptarnaujamas `/var/www/html/public`, nors visas kodas prijungtas į `/var/www/html`.

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
4. Nukopijuotame Redis YAML pašalink `profiles: [redis]`, kad servisas įsijungtų kartu su tuo failu. Jei Redis reikia tik lokaliai, jo aprašą dėk į `compose/local.yaml`.

Pilnas savas `compose/redis.yaml` variantas su paprastu duomenų keliu:

```yaml
services:
  redis:
    image: redis:${REDIS_VERSION}-alpine
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
protected-mode no
port 6379
daemonize no
logfile ""
dir /data
include /usr/local/etc/redis/environment/redis.conf
```

Šiame pavyzdyje Redis prieinamas projekto `network_app` konteineriams, o `ports` nėra publikuojami į host. `protected-mode no` leidžia PHP konteineriui prisijungti be slaptažodžio, kaip [oficialaus Docker Redis atvaizdo numatytoje konfigūracijoje](https://hub.docker.com/_/redis). Tai nėra autentifikacija: prieš suteikdamas prieigą platesniam tinklui, prod/stage konfigūracijoje paruošk projekto ACL/slaptažodžius ir atitinkamus aplikacijos bei healthcheck prisijungimus. Žr. [Redis prieigos valdymą](https://redis.io/docs/latest/operate/oss_and_stack/management/security/).

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

Grouped Redis šablonas ir aukščiau pateiktas variantas naudoja `${PROJECT_DATA_DIRECTORY}/redis`: vietoje tai `data/redis`, kitoms aplinkoms runner jau parenka atskirą duomenų katalogą. Jei tavo senesnėje kopijoje yra papildomas `${ENV}`, prieš keisdamas kelią patikrink, kur saugomi esami duomenys.

`ENV=test` reikės ir `config/redis/test/redis.conf`; vien `local` failo neužtenka, nes `include` ieško pasirinktos aplinkos failo.

## Mailpit ir phpMyAdmin: papildyti minimalų projektą

Situacija: PMA reikia visose aplinkose, o laiškus gaudantis Mailpit reikalingas tik lokaliai.

Į `compose/common.yaml` pridėk:

```yaml
include:
  - ${ROOT_DIRECTORY}/docker/pma/docker-compose.yml

services:
  nginx-proxy:
    environment:
      PMA_ALLOWED_IPS: ${PMA_ALLOWED_IPS:-}
      PMA_TRUSTED_PROXIES: ${PMA_TRUSTED_PROXIES:-}
```

IP reikšmes rašyk į `env/common.env`, jei jos bendros visoms aplinkoms:

```dotenv
PMA_ALLOWED_IPS="203.0.113.10 198.51.100.0/24 2001:db8::10"
```

Pavyzdinius IP pakeisk savo biuro, namų arba VPN išoriniais IP. Jei dar nežinai, įrašyk `PMA_ALLOWED_IPS=` – STAGE ir LIVE prieigos neturės niekas, o lokaliai PMA veiks. `PMA_TRUSTED_PROXIES` į env pridėk tik jei naudoji tarpinį proxy. YAML `${PMA_ALLOWED_IPS:-}` reiškia „paimk reikšmę iš env, o jei jos nėra – perduok tuščią“. Naujai kuriamiems projektams PMA laukai nepridedami.

PMA ir Mailpit bendruose aprašuose `profiles` nėra. Įtraukei serviso YAML – jis įjungtas toje aplinkoje. Papildomų profilio pasirinkimo ar `!override` eilučių nereikia.

Jei senesnis projektas įtraukia visą bendrą `docker/docker-compose.yml`, jame jau yra PMA ir Mailpit, todėl abu bus aktyvūs. Norėdamas juos atskirti pagal aplinkas, naudok atskirų servisų `include`, kaip šiame pavyzdyje. To paties serviso antrą kartą neįtrauk.

Į `compose/local.yaml` pridėk:

```yaml
include:
  - ${ROOT_DIRECTORY}/docker/mailpit/docker-compose.yml
```

Rezultatas: lokaliai veiks abu servisai; STAGE ir LIVE veiks tik PMA. Bendras Nginx pagal įjungtus servisus paruošia `pma.<DOMAIN>` ir `mailpit.<DOMAIN>` adresus. Lokaliam projektui `melga` tai `http://pma.melga.local/` ir `http://mailpit.melga.local/`. Domenus bei sertifikatą paruošia `make bootstrap`.

### PMA IP taisyklės

| Parametras | Kam skirtas |
| --- | --- |
| `PMA_ALLOWED_IPS` | Leidžiami klientų IP arba CIDR tinklai. Tarpai, kableliai ir naujos eilutės atskiria įrašus. Galima naudoti IPv4 ir IPv6. STAGE/LIVE tuščia arba nepateikta reikšmė uždraudžia visus klientus. |
| `PMA_TRUSTED_PROXIES` | Tik tavo patikimų tarpinių proxy IP arba tinklai. Be šio nustatymo kliento atsiųsta `X-Forwarded-For` antraštė nesuteikia prieigos. |
| `SETUP_ENVIRONMENT` | Automatiškai perduoda bendras setup pagal `ENV`. Jo projekto YAML nekeisk. `local` ir `test` neribojami; `stage` ir `prod` ribojami. |

LIVE šiame setup vadinasi `prod`: naudojami `ENV=prod`, `env/prod.env` ir `compose/prod.yaml`.

IP sąrašas iš env failo lokaliai ignoruojamas. Taigi dirbdamas lokaliai gali testuoti cache ir Redis su kitokiais runtime nustatymais – tai savaime neįjungs PMA IP ribojimo. Sprendžia tik aplinka, ne cache režimas ar atvaizdo build etapas.

Taisyklės galioja **HTTP ir HTTPS**, adresams `pma.<DOMAIN>` ir `www.pma.<DOMAIN>`. Pats projekto puslapis dėl jų neužblokuojamas. Neįtrauktas klientas gauna `403 Forbidden`. Neteisingas IP ar CIDR sustabdo Nginx paleidimą, užuot atvėręs prieigą.

Jei STAGE ir LIVE sąrašai skiriasi, reikšmes rašyk į `env/stage.env` ir `env/prod.env`. Konkrečios aplinkos reikšmė pakeičia `env/common.env` reikšmę. Pavyzdžiui, `env/stage.env`:

```dotenv
PMA_ALLOWED_IPS="198.51.100.25 2001:db8::20"
```

`PMA_ALLOWED_IPS=` aplinkos faile išvalo paveldėtą sąrašą ir uždraudžia prieigą. Jei eilutės nėra, paveldimas bendras sąrašas. Tavo projekte naudojami failai kataloge `env/`; papildomo `.env` projekto šaknyje kurti nereikia.

### Kai prieš konteinerį yra dar vienas Nginx

Situacija: išorinis proxy yra `10.20.0.5`, o tavo biuro išorinis IP – `203.0.113.10`:

Į atitinkamą env failą įrašyk:

```dotenv
PMA_ALLOWED_IPS=203.0.113.10
PMA_TRUSTED_PROXIES=10.20.0.5
```

Išorinis proxy turi į `X-Forwarded-For` įrašyti tikrą besijungiančio kliento IP arba jį pridėti grandinės gale. Nurodyk proxy IP tokį, kokį mato konteineris: dėl Docker/NAT jis gali skirtis nuo serverio viešo IP. Nginx pasitikės šia antrašte tik iš nurodyto proxy ir grandinėje parinks paskutinį nepatikimą adresą.

Į patikimų proxy sąrašą nedėk viso interneto (`0.0.0.0/0`, `::/0`) ar tinklo su nepatikimais klientais. Proxy IP nedėk į leidžiamų **klientų** sąrašą vien tam, kad dingtų `403`: taip įleistum visus už jo esančius lankytojus. Plačiau: [Nginx real-IP](https://nginx.org/en/docs/http/ngx_http_realip_module.html) ir [IP prieigos taisyklės](https://nginx.org/en/docs/http/ngx_http_access_module.html).

PMA serviso `ports` STAGE/LIVE aplinkose neviešink ir išorinio proxy nenukreipk tiesiai į PMA: toks kelias apeitų Nginx IP taisykles. Bendras PMA servisas host porto nepublikuoja. Jis gauna DB vartotoją ir slaptažodį iš runtime konfigūracijos, todėl ribojame prieigą prie pačios DB administravimo sąsajos.

### Pritaikyti pakeitimus

Atnaujinus bendrą setup pirmą kartą reikia naujo Nginx atvaizdo. Projekto kataloge:

```bash
make check
make build
make up
```

STAGE naudok tas pačias komandas su `ENV=stage`, LIVE – su `ENV=prod`, pagal įprastą to serverio diegimo tvarką. Aplinkos env, aplikacijos kodas, DB ir sertifikatas jau turi būti paruošti. Vėliau pakeitus vien IP sąrašą env faile pakaks `make up ENV=stage` (ar `prod`): Compose perkurs proxy su naujomis reikšmėmis.

Jei PMA viešas adresas naudoja HTTPS, aplinkos YAML nurodyk jo adresą:

```yaml
services:
  pma:
    environment:
      PMA_ABSOLUTE_URI: https://pma.${DOMAIN}/
```

Atverk PMA iš leidžiamo tinklo ir iš kito tinklo. Pastarasis per HTTP ir HTTPS turi gauti `403`. Jei uždrausti visi klientai, tikrink Nginx loguose matomą IP ir patikimo proxy nustatymą.

Aplikacijos SMTP nustatyk `mailpit`, portą `1025`, be TLS ir be būtinos autentifikacijos. Forsenos after-import SQL išjungia laiškų siuntimą; norėdamas matyti laiškus Mailpit, aplikacijoje taip pat parink SMTP režimą. UI portas `8025` nėra SMTP portas `1025`.

Mailpit duomenys laikomi `${PROJECT_DATA_DIRECTORY}/mailpit`, jo `MP_DATABASE` numatytai `/data/mailpit.db`, `TZ=Europe/Vilnius`, `MP_MAX_MESSAGES=5000`. Vietiniame Mailpit nustatyta `MP_SMTP_AUTH_ACCEPT_ANY=1` ir `MP_SMTP_AUTH_ALLOW_INSECURE=1`. phpMyAdmin naudoja `PMA_HOST=database`, `PMA_USER` ir `PMA_PASSWORD` iš runtime DB nustatymų.

## Cron: periodiškai importuoti kainas

Situacija: vietoje kas penkias minutes nori paleisti aplikacijos `bin/import-prices.php`.

Jei cron nėra pagrinde, į esamą `include` pridėk bendrą `docker/cron/docker-compose.yml`. Įjunk jį tik norimos aplinkos YAML, pvz. `compose/local.yaml`:

```yaml
services:
  cron:
    profiles: !reset []
```

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
