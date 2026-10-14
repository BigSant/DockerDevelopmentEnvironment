# 12. Cache, paštas ir resursai

[Turinys](README.md) · [Atgal](11-komandos-ir-klaidos.md) · [Toliau: PHP ir build įrankiai](13-php-ir-build-irankiai.md)

## Trys nepriklausomi pasirinkimai

| Pasirinkimas | Ką lemia | Pavyzdys |
| --- | --- | --- |
| `ENV` make komandoje | Env failą, kodo ir DB katalogus, domeną, konteinerių vardus. | `ENV=local`, `ENV=test`. |
| `CACHE_MODE` env faile | PHP, web serverio ir PrestaShop cache. | `CACHE_MODE=on` veikia ir `local`. |
| `SERVICES` env faile | Papildomus servisus. | `SERVICES="redis"`. |

Pakeitus vieną, kiti nepersijungia. Pvz. lokaliai gali išbandyti produkcinį cache, likdamas su vietine DB ir domenu.

## Numatytoji elgsena

| Nustatymas | local | test | stage | prod |
| --- | --- | --- | --- | --- |
| `CACHE_MODE=auto` | `off` | `off` | `on` | `on` |
| PHP OPcache, APCu | išjungti | išjungti | įjungti | įjungti |
| OPcache tikrina failų pakeitimus | taip | taip | ne | ne |
| PS Smarty cache, CSS/JS cache | išjungti | išjungti | įjungti | įjungti |
| PS šablonų kompiliavimas | tikrinti pakeitimus | tikrinti pakeitimus | nekompiliuoti iš naujo | nekompiliuoti iš naujo |
| PS objektų cache | išjungtas | išjungtas | paliekamas parduotuvės pasirinkimas | paliekamas parduotuvės pasirinkimas |
| `MAIL_MODE=auto` | `mailpit` arba `off` | `mailpit` arba `off` | `mailpit` arba `off` | `preserve` |
| `HTTP_FORCE_HTTPS=auto` | `off` | `off` | `on` | `on` |

`mailpit` parenkamas, kai `SERVICES` turi `mailpit`, kitaip `off`.

Lokaliai tikslas – matyti pakeistą kodą ir šablonus be rankinio cache valymo. Modulių savas cache ir Symfony konteinerio kompiliavimas automatiškai neišjungiami: tada padeda `make cache-clear`.

Debug (`PS_DEBUG_MODE`) ir Xdebug (`XDEBUG_MODE`) yra atskiri pasirinkimai, cache jų nekeičia.

```bash
make runtime-info
```

Parodo visas išspręstas reikšmes be slaptažodžių.

## Situacija A: lokaliai tikrini produkcinį cache

`env/local.env`:

```dotenv
CACHE_MODE=on
```

```bash
make restart
```

Lieki `local`: tas pats kodas, DB ir domenas. PS Smarty ir CSS/JS cache įsijungia, OPcache vis tiek tikrina failų pakeitimus. Jei nori ir produkcinio OPcache (pakeitimai nematomi iki PHP perkrovimo):

```dotenv
CACHE_MODE=on
PHP_OPCACHE_VALIDATE_TIMESTAMPS=off
```

Po kodo pakeitimo paleisk `make cache-clear`. Grįžimui nustatyk `CACHE_MODE=off` ir `PHP_OPCACHE_VALIDATE_TIMESTAMPS=auto` (arba ištrink abi eilutes), tada `make restart`.

## Situacija B: tik vienas cache sluoksnis

APCu, bet kodo pakeitimus matyti iškart:

```dotenv
CACHE_MODE=off
PHP_APCU=on
```

Smarty cache, bet šablonai tikrinami:

```dotenv
CACHE_MODE=off
PS_SMARTY_CACHE=on
PS_SMARTY_COMPILE=check
```

Konkretaus sluoksnio raktas laimi prieš `CACHE_MODE`.

## Situacija C: Redis arba Memcached

`SERVICES="redis"` (arba `memcached`) ir PHP plėtinio įjungimas aprašyti [servisų skyriuje](05-servisai.md#redis-ir-memcached). Pats serviso paleidimas aplikacijos cache backend'o nepakeičia: setup nekeičia PrestaShop `_PS_CACHING_SYSTEM_` ar modulio nustatymų. Backend'o klasę nurodo projektas (pvz. per `config/prestashop/parameters.override.php`) ir tik tada įjungia `PS_OBJECT_CACHE=on`.

## Paštas (MAIL_MODE)

| Reikšmė | Kas vyksta PrestaShop |
| --- | --- |
| `auto` | prod – `preserve`; kitur – `mailpit`, jei `SERVICES` turi `mailpit`, kitaip `off`. |
| `mailpit` | SMTP į `mailpit:1025` be šifravimo ir be prisijungimo. Niekas neišeina iš kompiuterio, laiškai matomi `https://mailpit.demo.local/`. Reikia `mailpit` sąraše `SERVICES`. |
| `off` | `PS_MAIL_METHOD=3` – PS laiškų siuntimas išjungtas. |
| `preserve` | Nustatymai paliekami tokie, kokie DB. Tai nereiškia „įjungti“. |

Nustatymas pritaikomas kiekvieną PHP startą ir po kiekvieno DB užkėlimo, todėl LIVE dump'as lokaliai nesiunčia laiškų klientams. `MAIL_MODE` neblokuoja viso tinklo: modulių siuntimas per išorinį API, mokėjimai ir tiekėjai valdomi [integracijų jungikliais](07-duomenu-baze.md#d-live-dumpo-užkėlimas).

## Visi cache nustatymai

| Raktas | Reikšmės |
| --- | --- |
| `CACHE_MODE` | `auto`, `off`, `on`; numatyta `auto`. |
| `PHP_OPCACHE`, `PHP_APCU` | `auto`, `off`, `on`; `auto` paveldi `CACHE_MODE`. |
| `PHP_OPCACHE_VALIDATE_TIMESTAMPS` | `auto`, `off`, `on`; `auto` = `on` local/test, `off` stage/prod. |
| `PHP_OPCACHE_REVALIDATE_FREQ` | Sekundės `0..1000000`; numatyta `0`. |
| `PS_SMARTY_CACHE`, `PS_ASSET_CACHE` | `auto`, `off`, `on`; `auto` paveldi `CACHE_MODE`. Tik PS. |
| `PS_SMARTY_COMPILE` | `auto`, `never`, `check`, `always`; `auto` = `check`, kai cache išjungtas, `never`, kai įjungtas. |
| `PS_OBJECT_CACHE` | `auto`, `off`, `on`, `preserve`; `auto` = `off`, kai cache išjungtas, `preserve`, kai įjungtas. |
| `CACHE_CLEAR_COMMAND` | Ne PS aplikacijoms: komanda PHP konteineryje, pvz. `php bin/console cache:clear`. Shell operatoriai nevykdomi. |

## Kas keičiama PrestaShop

PHP startas per PDO tiesiogiai pakeičia šias parduotuvės konfigūracijos eilutes (moduliai nepaleidžiami, laiškai nesiunčiami):

- `PS_SMARTY_CACHE` – to paties vardo raktą;
- `PS_SMARTY_COMPILE` – `PS_SMARTY_FORCE_COMPILE` (`never=0`, `check=1`, `always=2`);
- `PS_ASSET_CACHE` – `PS_CSS_THEME_CACHE`, `PS_JS_THEME_CACHE`, `PS_HTML_THEME_COMPRESSION`, `PS_JS_HTML_THEME_COMPRESSION`;
- `MAIL_MODE` – `PS_MAIL_METHOD` ir (Mailpit atveju) SMTP serverį, portą, šifravimą, vartotoją;
- `PS_OBJECT_CACHE` – PS 1.7+ `ps_cache_enable` arba PS 1.6 `_PS_CACHE_ENABLED_`.

Keičiamos visos rakto eilutės, įskaitant multishop. DB palaukiama iki 60 sekundžių, kitaip PHP startas sustoja.

Kitos aplikacijos `CACHE_MODE` ir `MAIL_MODE` gauna kaip env ir išverčia pačios (žr. [startup scriptai](06-aplikacija.md#kitos-aplikacijos-ir-startup-scriptai)).

## Resursai kiekvienam servisui

Kiekvienas servisas turi savo atminties ir CPU ribą: `<SERVISAS>_MEMORY` ir `<SERVISAS>_CPUS`.

| Servisas | Raktai | Numatyta (local) |
| --- | --- | --- |
| php-fpm | `PHP_MEMORY`, `PHP_CPUS` | `1g`, `2` |
| database | `DATABASE_MEMORY`, `DATABASE_CPUS` | `2g`, `2` |
| webserver | `WEBSERVER_MEMORY`, `WEBSERVER_CPUS` | `256m`, `1` |
| nginx-proxy | `PROXY_MEMORY`, `PROXY_CPUS` | `128m`, `0.5` |
| cron | `CRON_MEMORY`, `CRON_CPUS` | `512m`, `1` |
| memcached | `MEMCACHED_MEMORY`, `MEMCACHED_CPUS` | `256m`, `0.5` |
| redis | `REDIS_MEMORY`, `REDIS_CPUS` | `256m`, `0.5` |
| pma | `PMA_MEMORY`, `PMA_CPUS` | `256m`, `0.5` |
| mailpit | `MAILPIT_MEMORY`, `MAILPIT_CPUS` | `256m`, `0.5` |
| dbmonitor | `DBMONITOR_MEMORY`, `DBMONITOR_CPUS` | `128m`, `0.25` |
| backup | `BACKUP_MEMORY`, `BACKUP_CPUS` | `128m`, `0.25` |
| metrics | `METRICS_MEMORY`, `METRICS_CPUS` | `128m`, `0.25` |

Atmintis rašoma `k`, `m` arba `g` (pvz. `512m`, `2g`), CPU – skaičius iki 1024, galima trupmena (`1.5`).

Iš limitų apskaičiuojami vidiniai nustatymai, jei jų nenurodai aiškiai:

| Vidinis nustatymas | Kaip apskaičiuojamas | Rankinis raktas |
| --- | --- | --- |
| InnoDB buffer pool | ~60 % `DATABASE_MEMORY` (ne mažiau 64M) | `DATABASE_BUFFER_POOL` (pvz. `1228M`) |
| FPM procesų skaičius | (`PHP_MEMORY` − 128 MB) / `PHP_FPM_PROCESS_MEMORY` (numatyta `128m`), nuo 2 iki 256 | `PHP_FPM_MAX_CHILDREN` |
| FPM laisvi procesai | start ir min = ¼ procesų, max = ½ procesų (ne mažiau 1) | `PHP_FPM_START_SERVERS`, `PHP_FPM_MIN_SPARE_SERVERS`, `PHP_FPM_MAX_SPARE_SERVERS` |
| FPM proceso pakeitimas | po 500 užklausų (`0` – niekada) | `PHP_FPM_MAX_REQUESTS` |
| Memcached atmintis | ~90 % `MEMCACHED_MEMORY` | `MEMCACHED_CACHE_MB` |
| Redis `maxmemory` | ~90 % `REDIS_MEMORY` | `REDIS_MAXMEMORY_MB` |

Pavyzdžiui, `PHP_MEMORY=1g` duoda 7 FPM procesus. FPM turi galioti `MIN_SPARE_SERVERS ≤ START_SERVERS ≤ MAX_SPARE_SERVERS ≤ MAX_CHILDREN`. Buffer pool gali perrašyti ir MySQL drop-in failu.

Limitų pakeitimas pritaikomas be image'o perkūrimo:

```bash
make restart
```

**Stage/prod** be keturių pagrindinių servisų (`PHP`, `DATABASE`, `WEBSERVER`, `PROXY`) limitų nepasileidžia. Klaidos pranešimas pasiūlo pradines reikšmes pagal Docker host'o RAM ir CPU, skaičiuodamas vienam projektui. Jei serveryje keli projektai, reikšmes padalink. Papildomiems servisams tinka numatytosios.

Seni pavadinimai dar veikia kaip atsarginės reikšmės: `PHP_CONTAINER_MEMORY` (php, cron, pma), `DB_CONTAINER_MEMORY`, `WEB_CONTAINER_MEMORY` (webserver, proxy, mailpit), `CONTAINER_CPUS`. Naujiems nustatymams naudok `<SERVISAS>_MEMORY/_CPUS`.

## Aplikacijos env reikalavimai

Neprivalomas `config/environment.json` leidžia `make check` patikrinti, ar servisui tikrai perduoti reikiami env:

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

`type`: `string` (numatyta), `integer`, `boolean` (`0/1/true/false`), `url` (http/https). `choices` – leidžiamos reikšmės. Tikrinami Compose perduoti env, deklaracija pati nieko neperduoda: kintamąjį perduoda [`APP_ENVIRONMENT`](03-nustatymai.md#savi-aplikacijos-parametrai-app_environment) (`php-fpm`, `cron`) arba Compose `environment`. Klaidos rodo rakto vardą, bet ne reikšmę.

## Log'ai, diskas ir kopijos

| Raktas | Numatyta | Ką daro |
| --- | --- | --- |
| `CONTAINER_LOG_MAX_SIZE` | `10m` | Vieno Docker log failo dydis. |
| `CONTAINER_LOG_MAX_FILES` | `3` | Kiek log failų laikyti konteineriui. |
| `MIN_FREE_DISK_MB` | `1024` | Laisvos vietos rezervas, kurį tikrina `doctor`, importas, kopija ir testinė kopija. |
| `BACKUP_KEEP_LAST` | `5` | Kiek naujausių automatiškai pavadintų kopijų palieka `make db-backup-prune`. |

Docker log'ų rotacija riboja tik konteinerio stdout/stderr. Aplikacijos rašomiems failams (`var/logs`) reikia atskiros rotacijos.

Šaltiniai: [PHP OPcache](https://www.php.net/manual/en/opcache.configuration.php), [Docker log'ų rotacija](https://docs.docker.com/engine/logging/configure/), [PrestaShop konfigūracija](https://devdocs.prestashop-project.org/8/development/configuration/configuring-prestashop/).
