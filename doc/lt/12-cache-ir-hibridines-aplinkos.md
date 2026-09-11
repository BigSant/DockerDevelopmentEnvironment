# 12. Cache, hibridinės aplinkos ir bendros konteinerių taisyklės

[Turinys](README.md) · [Komandos](11-komandos-ir-klaidos.md)

## Trys nepriklausomi pasirinkimai

Įsivaizduok tris jungiklius. Vienas pasirenka, **su kuriuo projektu ir duomenimis dirbi**. Antras – **ar naudoti cache**. Trečias – **kokius papildomus servisus paleisti**. Pasukus vieną, kiti neturi persijungti.

| Pasirinkimas | Paskirtis | Pavyzdys |
| --- | --- | --- |
| `ENV` Make komandoje | Env failas, aplikacijos/DB katalogai, domenai, konteinerių vardai ir aplinkos SQL rinkinys. | `ENV=local`, `ENV=test`. |
| `CACHE_MODE` env faile | Bendri PHP, web serverio ir palaikomo PS cache nustatymai. | `CACHE_MODE=on` veikia ir `local`. |
| `PROFILES` Make komandoje arba `COMPOSE_PROFILES` env faile | Pasirenkami servisai, kurių aprašai jau prijungti projekte. | `PROFILES=redis`. |

`PROFILE=prestashop` yra dar kitas dalykas: **aplikacijos rūšis**, pagal kurią parenkamas PS konfigūracijos adapteris. Symfony/Laravel/savas PHP gali naudoti tuščią profilį ir projekto hook.

## Kas veikia be papildomų nustatymų

| Nustatymas | Local | Test | Stage | Prod |
| --- | --- | --- | --- | --- |
| `CACHE_MODE=auto` rezultatas | `off` | `off` | `on` | `on` |
| PHP OPcache / APCu | Išjungti | Išjungti | Įjungti, jei plėtiniai įdiegti | Įjungti, jei plėtiniai įdiegti |
| PS Smarty cache | Išjungtas | Išjungtas | Įjungtas | Įjungtas |
| PS šablonų kompiliavimas | Tikrinti failų pakeitimus | Tikrinti failų pakeitimus | Netikrinti kiekvieną užklausą | Netikrinti kiekvieną užklausą |
| PS CSS/JS optimizacijų cache | Išjungtas | Išjungtas | Įjungtas | Įjungtas |
| PS objektų cache | Išjungtas | Išjungtas | Išsaugomas parduotuvės pasirinkimas | Išsaugomas parduotuvės pasirinkimas |
| `MAIL_MODE=auto` rezultatas | `off` | `off` | `off` | `preserve` |
| `HTTP_FORCE_HTTPS=auto` rezultatas | `off` | `off` | `on` | `on` |

Local tikslas – matyti pakeistą kodą ir šablonus be rankinio įprastų cache valymo. Būtinas Symfony konteinerio kompiliavimas, duomenų bazės atminties buferiai ir modulio savas cache nėra automatiškai išjungiami. Keičiant servisų aprašus ar modulio savą cache vis tiek gali reikėti `make cache-clear` arba projekto komandos.

Debug yra atskiras pasirinkimas: `PS_DEBUG_MODE=off|on|ip`. Cache įjungimas jo nekeičia. Xdebug taip pat lieka atskirai valdomas esama PHP konfigūracija; vietiniame atvaizde derinimas pradedamas gavus trigger.

## Situacija A: įprastai programuoji Forseną

`env/common.env` jau yra:

```dotenv
CACHE_MODE=auto
MAIL_MODE=auto
HTTP_FORCE_HTTPS=auto
```

```bash
make runtime-info
make check
make up
```

`runtime-info` parodo išspręstus bendrus jungiklius, nerodydamas DB slaptažodžių. Local rezultatas bus cache išjungtas, PS laiškai išjungti. Po pirmo bendro setup atnaujinimo reikia `make build`, nes pasikeitė web serverio šablonai ir konteinerių aprašai. Vėliau vien dėl cache reikšmių pakeitimo buildinti nereikia.

## Situacija B: lokaliai tikrini produkcinį cache

Tik `env/local.env` pridėk arba pakeisk:

```dotenv
CACHE_MODE=on
MAIL_MODE=off
```

```bash
make runtime-info
make up
```

Lieki `local`: tas pats `public/`, vietinė DB, domenas, local SQL ir vietiniai atvaizdai. PS Smarty ir CSS/JS cache įsijungs; laiškų pasirinkimas liks `off`. Local OPcache numatytai tikrina PHP failų pasikeitimus net šiame hibridiniame variante.

Jei nori tikrinti būtent produkcinį OPcache ir sąmoningai priimti tai, kad failų pakeitimai nebus pastebimi iki PHP perkrovimo:

```dotenv
CACHE_MODE=on
PHP_OPCACHE_VALIDATE_TIMESTAMPS=off
MAIL_MODE=off
```

Po kodo pakeitimo arba bandymo:

```bash
make cache-clear
```

Ši komanda PS atveju išvalo palaikomą aplikacijos cache ir perkrauna PHP, todėl išvalomas ir FPM procesų OPcache/APCu. Ji nevalo Redis DB ir neliečia kitų projektų konteinerių.

Grįžimui prie įprasto darbo nustatyk `CACHE_MODE=off` ir `PHP_OPCACHE_VALIDATE_TIMESTAMPS=auto`, tada `make up`. Vien komentaro pridėjimas prie vienos eilutės gali palikti kitus konkrečius override veikti.

## Situacija C: nori tik vieno cache sluoksnio

Pavyzdžiui, tikrini APCu, tačiau PHP ir šablonų pakeitimus nori matyti iškart:

```dotenv
CACHE_MODE=off
PHP_APCU=on
```

Arba nori Smarty cache, bet šablonų failai turi būti tikrinami:

```dotenv
CACHE_MODE=off
PS_SMARTY_CACHE=on
PS_SMARTY_COMPILE=check
```

Konkretaus sluoksnio jungiklis turi pirmenybę prieš `CACHE_MODE` numatytą reikšmę. Išsaugok env ir vykdyk `make up`.

## Situacija D: vietinis cache ir Redis integracijos testas

Forsenoje `compose/redis.yaml` jau prijungtas per `PROJECT_COMPOSE_FILES`, o `config/redis/` turi bendrą ir kiekvienos aplinkos konfigūraciją. Įprastai Redis **nepaleidžiamas**.

```bash
make init
make pull PROFILES=redis
make up PROFILES=redis
make ps PROFILES=redis
```

Jeigu nori nuolat paleisti Redis, `env/local.env` nustatyk:

```dotenv
COMPOSE_PROFILES=redis
CACHE_MODE=on
MAIL_MODE=off
```

Redis vidinis adresas yra `redis:6379`, hosto portas automatiškai neatveriamas. Local duomenų vieta – `data/redis`; testinės aplinkos – `.generated/test/data/redis`. Env pakeitimas ar `CACHE_MODE=off` Redis duomenų neištrina. Kai nebereikia serviso, prieš pašalindamas profilį gali sustabdyti pasirinktą aplinką su `make down PROFILES=redis`, tada paleisti pagrindinius servisus su `make up PROFILES=`.

Redis serverio paleidimas pats savaime nepakeičia aplikacijos cache backend. Tavo moduliui reikia jo palaikomo PHP kliento ir projekto konfigūracijos. PHP Redis plėtinys šiuo pakeitimu automatiškai neįdiegiamas į visas PHP versijas. Naudok aplikacijos turimą klientą arba aiškiai pridėk suderinamą plėtinį/projekto priklausomybę.

Jei PS modulis jau turi savo Redis backend, jo klasę/adresą nurodyk to projekto `config/prestashop/parameters.override.php` bei Compose perduodamuose env. Tik tuomet rinkis `PS_OBJECT_CACHE=on`. Setup pats neatspėja modulio klasės ir nekeičia `_PS_CACHING_SYSTEM_` / `ps_caching`.

Kiti projektai gali prijungti bendro šablono `compose/redis.yaml` ir `config/redis/` failus. Vien `PROFILES=redis` be serviso aprašo Redis nesukuria.

## Visi šio sluoksnio nustatymai

| Raktas | Reikšmės ir numatytoji reikšmė |
| --- | --- |
| `CACHE_MODE` | `auto`, `off`, `on`; numatytai `auto`, aplinkų lentelė aukščiau. |
| `PHP_OPCACHE`, `PHP_APCU` | `auto`, `off`, `on`; `auto` paveldi išspręstą `CACHE_MODE`. |
| `PHP_OPCACHE_VALIDATE_TIMESTAMPS` | `auto`, `off`, `on`; `auto` yra `on` local/test ir `off` stage/prod, nepriklausomai nuo cache įjungimo. |
| `PHP_OPCACHE_REVALIDATE_FREQ` | Sveikas skaičius `0..1000000`, sekundės; numatytai `0`. Aktualu tik įjungus laiko žymų tikrinimą. |
| `PS_SMARTY_CACHE`, `PS_ASSET_CACHE` | `auto`, `off`, `on`; `auto` paveldi `CACHE_MODE`. Veikia tik PS. |
| `PS_SMARTY_COMPILE` | `auto`, `never`, `check`, `always`; su `auto` išjungtas cache duoda `check`, įjungtas – `never`. `always` perkompiliuoja kiekvieną kartą ir yra lėtesnis. |
| `PS_OBJECT_CACHE` | `auto`, `off`, `on`, `preserve`; `auto` su cache išjungimu duoda `off`, su įjungimu – `preserve`. Backend nepakeičiamas. `preserve` negrąžina ankstesnės reikšmės, jei ją jau pakeitei. |
| `MAIL_MODE` | `auto`, `off`, `preserve`; `auto` tik prod duoda `preserve`, kitur `off`. `preserve` reiškia nekeisti esamos reikšmės, ne automatiškai įjungti laiškus. |
| `HTTP_FORCE_HTTPS` | `auto`, `off`, `on`; `auto` duoda `off` local/test, `on` stage/prod. Bendro Apache peradresavimo taisyklė tikrina ir `X-Forwarded-Proto`. |
| `PHP_CONTAINER_MEMORY` | Numatyta `1g`; PHP-FPM, cron, phpMyAdmin konteinerio RAM riba. |
| `DB_CONTAINER_MEMORY` | Numatyta `2g`; MySQL/MariaDB konteinerio RAM riba. |
| `WEB_CONTAINER_MEMORY` | Numatyta `256m`; bendrų web/proxy ir Mailpit konteinerių RAM riba. |
| `REDIS_CONTAINER_MEMORY` | Redis šablone numatyta `512m`; pats Redis dar turi savo `maxmemory` konfigūracijoje. |
| `CONTAINER_CPUS` | Numatyta `2`; teigiamas skaičius iki `1024`, leidžiamos trupmenos, pvz. `1.5`. Riba taikoma kiekvienam atitinkamam konteineriui atskirai. |
| `CONTAINER_LOG_MAX_SIZE` | Numatyta `10m`; Docker `json-file` vieno žurnalo failo riba. |
| `CONTAINER_LOG_MAX_FILES` | Numatyta `3`; sveikas skaičius `1..1000000`, kiek failų laikyti vienam konteineriui. |
| `MIN_FREE_DISK_MB` | Numatyta `1024`; laisvos disko vietos rezervas MiB, sveikas skaičius `0..1000000`. `0` panaikina rezervą, bet ne operacijos dydžio įvertinimą. |
| `BACKUP_KEEP_LAST` | Numatyta `5`; teigiamas sveikas skaičius iki `1000000`. Kiek naujausių automatiškai pavadintų tos aplinkos DB kopijų palikti valymo metu. |
| `CACHE_CLEAR_COMMAND` | Tuščia; kitoms aplikacijoms komanda PHP konteineryje, pvz. `php bin/console cache:clear`. Skaidoma į argumentus, shell operatoriai nevykdomi. |

Dydžius bendriems validuojamiems RAM/žurnalų raktams rašyk teigiamu sveiku skaičiumi ir mažąja `k`, `m` arba `g`, pvz. `512m`. QA įrankiai turi savo esamas RAM ribas; jas gali perrašyti projekto Compose faile.

`SETUP_ENABLE_PMA` ir `SETUP_ENABLE_MAILPIT` yra techniniai runner parenkami `0/1` laukai pagal realiai pasirinktus servisus. Jų nerašyk į env. Proxy nebekuria neegzistuojančių servisų virtualių hostų.

PHP nustatymų eilė: image bazė/profilis → bendras runtime `php-policy/90-cache.ini` → projekto `config/php/*.ini` → `config/php/<ENV>/*.ini`. Todėl tavo aiškus `.ini` override vis tiek gali pakeisti env numatytą elgseną. `runtime-info` rodo išspręstus env, o ne galutinį `ini_get()` po visų aplikacijos pakeitimų.

## Kas tiksliai keičiama PrestaShop

PHP startas paruošia prisijungimus ir debug, tada per PDO tiesiogiai sutvarko pasirinktas parduotuvės konfigūracijos eilutes. Aplikacijos moduliai ir laiškų siuntimo funkcijos tam nepaleidžiami. DB palaukiama iki 60 sekundžių; jei ji neparuošta, PHP startas sustoja.

- `PS_SMARTY_CACHE` keičia to paties vardo DB raktą.
- `PS_SMARTY_COMPILE` verčiamas į `PS_SMARTY_FORCE_COMPILE`: `never=0`, `check=1`, `always=2`.
- `PS_ASSET_CACHE` keičia esamus `PS_CSS_THEME_CACHE`, `PS_JS_THEME_CACHE`, `PS_HTML_THEME_COMPRESSION`, `PS_JS_HTML_THEME_COMPRESSION` raktus. Konkrečioje PS versijoje nesančių papildomų raktų nekuria.
- `MAIL_MODE=off` nustato `PS_MAIL_METHOD=3`. Tai standartinės PS laiškų sistemos išjungimas.
- `PS_OBJECT_CACHE` keičia modernų `ps_cache_enable` arba PS 1.6 `_PS_CACHE_ENABLED_`. Backend ir kiti parduotuvės raktai išsaugomi.

Pritaikomos visos esamos pasirinkto rakto eilutės, įskaitant multishop reikšmes. Kitų integracijų adresai ir parametrai neliečiami. Nepasikeitusios DB reikšmės neperrašomos. Privalomų Smarty/mail raktų nebuvimas rodo, kad reikia įkelti įdiegtos parduotuvės DB.

Kai `make db-import` naudojamas jau veikiančiame PS projekte, po SQL vėl pritaikoma veikiančio PHP konteinerio cache/mail politika, išvalomas aplikacijos cache ir perkraunamas PHP. Jei PHP sustabdytas, tai atliks kitas jo startas. Po env pakeitimų pirmiau taikyk `make up`, kad konteineris gautų naujas reikšmes. SQL importas ir cache/mail pakeitimai nėra viena viską atšaukianti operacija.

## Kitos aplikacijos ir laiškų ribos

OPcache/APCu, konteinerių ribos ir bendro web serverio cache taisyklės galioja visiems šiuos bendrus servisus naudojantiems projektams. PS konfigūracijos adapteris vykdomas tik `ps` / `prestashop` profiliui.

Kitai aplikacijai `CACHE_MODE` ir `MAIL_MODE` jau perduodami į PHP konteinerį. Jos `config/startup/*.sh` / projekto konfigūracijos generatorius turi juos išversti į konkretaus framework nustatymus. Pavyzdžiui, į savo `runtime.php` įrašyti `cache_enabled` pagal `getenv('CACHE_MODE') === 'on'` ir `mail_enabled` pagal projekto siuntimo taisykles.

`MAIL_MODE=off` nėra viso konteinerio išorinio tinklo blokavimas. Savas modulis, siuntimas per išorinį HTTP API, mokėjimai ir tiekėjų užduotys turi savo projekto sandbox/išjungimo nustatymus. Cache jungiklis jų ir `COMPOSE_PROFILES` nekeičia; įjungtas cron lieka įjungtas pagal atskirai pasirinktą profilį.

## Kasdienės komandos ir preflight

```bash
make restart service=php-fpm
make logs service=php-fpm follow=1 tail=100
make composer cmd="install"
make cache-clear
make runtime-info
make db-backup-prune
make db-backup-prune apply=1
```

`restart` priverstinai perkuria pasirinktą servisą su dabartiniais env, nebuildindamas image. Be `service` perkuria pasirinktus aplinkos servisus. `logs` numatytai rodo paskutines 100 eilučių; `follow=1` seka naujas. `composer` be `cmd` parodo versiją. Komandos vykdomos PHP konteinerio aplikacijos darbo kataloge.

`make check` tikrina Compose, bendrų env reikšmių tinkamumą, PS failų buvimą ir projekto deklaruotus env reikalavimus. `make up` / `make restart` PS atveju papildomai paleidžia konfigūracijos patikrą vienkartiniame PHP konteineryje **prieš** perkuriant esamus servisus. Tam reikia jau pastatyto PHP image. Ši patikra DB nekeičia. Tikras DB ryšys ir schemos buvimas tikrinami paleidimo metu.

Savo aplikacijos reikalavimus laikyk pasirenkamame `config/environment.json`:

```json
{
  "php-fpm": {
    "SUPPLIER_API_URL": {"required": true, "type": "url"},
    "SUPPLIER_ENABLED": {"required": true, "type": "boolean"},
    "WORKER_COUNT": {"type": "integer"},
    "APPLICATION_MODE": {"required": true, "choices": ["sandbox", "live"]}
  }
}
```

Tikrinami realiai servisui Compose perduoti env. Deklaracija jų pati neperduoda. `required` numatytai `false`; `type` numatytai `string`, taip pat leidžiami `integer`, `boolean` (`0/1/true/false`) ir HTTP(S) `url`. `choices` yra leidžiamų tekstinių reikšmių sąrašas. Neįjungto pasirenkamo serviso reikalavimai tikrinami tada, kai jį pasirenki. Klaidos rodo rakto vardą, ne jo slaptą reikšmę.

## Diskas, žurnalai ir kopijų valymas

`doctor` tikrina laisvos vietos rezervą. DB importas papildomai įvertina išskleisto SQL dydį, o testinės kopijos kūrimas prieš kopijavimą gauna rsync numatomą perkeliamų failų dydį. Tai preliminarūs įvertinimai: DB indeksų, laikinų failų ir kitų procesų poreikiai gali būti didesni. Backup taip pat tikrina rezervą, o neužbaigta kopija nepaskelbiama kaip sėkminga.

`db-backup-prune` be `apply=1` tik parodo planą. Valymas apima tik `.generated/backups/<šis-projektas>-<ši-aplinka>-<laiko-žyma>.sql.gz`. Kitų projektų, aplinkų, tavo paties pavadintų failų ir symlink nešalina. Automatinio Docker image/volume ar DB duomenų trynimo nėra.

Docker žurnalų rotacija riboja konteinerio stdout/stderr failus. Aplikacijos atskirai į `public/var/logs` ar kitus katalogus rašomiems failams reikia atskiros aplikacijos rotacijos. Naujos Docker ribos pritaikomos perkuriant konteinerį.

Šaltiniai: [PHP OPcache parametrai](https://www.php.net/manual/en/opcache.configuration.php), [Docker servisų nustatymai](https://docs.docker.com/reference/compose-file/services/), [Docker žurnalų rotacija](https://docs.docker.com/engine/logging/configure/), [PrestaShop konfigūracija](https://devdocs.prestashop-project.org/8/development/configuration/configuring-prestashop/). Šio skyriaus env vardai yra bendro setup sąsaja, ne universalūs framework standartai.
