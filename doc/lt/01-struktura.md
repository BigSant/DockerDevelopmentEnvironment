# 1. Struktūra: kas kur gyvena

[Turinys](README.md) · [Toliau: pirmas paleidimas](02-pradzia.md)

## Penkios sąvokos

| Žodis | Paprastai | Pavyzdys |
| --- | --- | --- |
| Aplikacija | Tavo svetainės programa | PrestaShop failai `app/public/`. |
| Servisas | Vienas aplinkos darbas | `database` saugo DB, `php-fpm` vykdo PHP. |
| Atvaizdas, arba image | Paruošta programų dėžutė | PHP su reikalingais plėtiniais. |
| Konteineris | Paleista tos dėžutės kopija | Veikiantis Forsenos PHP procesas. |
| Bind mount | Tas pats katalogas matomas ir kompiuteryje, ir konteineryje | Kompiuterio `app/public` konteineryje matomas kaip `/var/www/html`. |

`Dockerfile` yra atvaizdo gaminimo receptas. Compose YAML pasako, kokius konteinerius paleisti ir ką jiems prijungti. Env failai pateikia reikšmes, pavyzdžiui, PHP versiją arba DB vardą.

Dar keli žodžiai, kuriuos sutiksi: **CLI** – programa terminale; **PHP-FPM** – PHP procesai, kuriems serveris perduoda naršyklės užklausas; **cache** – laikini, iš naujo atkuriami rezultatai; **override** – tavo pasirinktas ankstesnės reikšmės pakeitimas; **hook** – scriptas, kurį konkretus įvykis, pvz. PHP startas arba Git commitas, iškviečia automatiškai. **Default** reiškia numatytąją reikšmę, kai pats kitos nepasirenki.

## Bendras setup ir konkretus projektas

```text
~/Projects/
├── setup/                       # Viena bendra Git repozitorija
│   ├── VERSION                  # Setup versija
│   ├── prepare_project.py       # Sukuria trūkstamus projekto šablono failus
│   ├── docker/
│   │   ├── .env                 # Bendros numatytosios reikšmės
│   │   ├── project.mk           # Bendros make komandos
│   │   ├── project.py           # Surenka konkretaus projekto aplinką
│   │   ├── docker/             # Servisų Dockerfile, Compose ir baziniai config
│   │   ├── profile/            # Bendri PrestaShop / Akeneo pakeitimai
│   │   └── runtime/            # Aplikacijos paruošimas prieš PHP paleidimą
│   ├── templates/grouped/      # Pilnas pasirenkamų dalių šablonas
│   ├── tests/                  # Paties setup testai
│   └── doc/lt/                 # Šis vadovas
└── forsena/
    ├── app/                    # Čia atidarai PhpStorm ir vykdai make
    └── data/                   # Vietiniai DB ir kitų servisų duomenys
```

`setup` nėra submodulis kiekvienoje aplikacijoje. Kiekvienas projekto `Makefile` suranda bendrą katalogą. Projekte lieka jo savi nustatymai, o komandų logika laikoma vienoje vietoje.

Naršyklės užklausa įprastoje aplinkoje keliauja taip:

```text
naršyklė → nginx-proxy → webserver (Apache) → php-fpm → database
```

Konteineryje `localhost` reiškia **tą patį konteinerį**. PHP jungiasi į DB vardu `database`, į Redis – vardu `redis`. Kompiuterio naršyklė naudoja projekto domeną ir išorinį portą.

## Pilnas galimų projekto vietų žemėlapis

Tai galimybių žemėlapis, ne sąrašas katalogų, kuriuos privaloma iš karto sukurti.

```text
forsena/
├── app/
│   ├── Makefile
│   ├── README.md
│   ├── .gitignore
│   ├── .dockerignore                  # Reikia turint projekto Dockerfile
│   ├── Dockerfile                     # Pasirenkamas projekto PHP receptas
│   ├── dockerfiles/                   # Savi papildomų atvaizdų receptai
│   ├── env/
│   │   ├── common.env                 # Vieši bendri projekto nustatymai
│   │   ├── local.env                  # Privatūs vietiniai nustatymai
│   │   ├── test.env                   # Privatūs testinės aplinkos nustatymai
│   │   ├── stage.env                  # Privatūs tarpinės aplinkos nustatymai
│   │   ├── prod.env                   # Privatūs gamybinės aplinkos nustatymai
│   │   └── *.env.example              # Pildymo pavyzdžiai be tikrų paslapčių
│   ├── compose/
│   │   ├── base.yaml                  # Servisų ir tinklo pagrindas
│   │   ├── common.yaml                # Visoms aplinkoms bendri pakeitimai
│   │   ├── local.yaml                 # Tik vietiniai pakeitimai
│   │   ├── test.yaml                  # Tik testiniai pakeitimai
│   │   ├── stage.yaml                 # Tik tarpinės aplinkos pakeitimai
│   │   ├── prod.yaml                  # Tik gamybiniai pakeitimai
│   │   ├── redis.yaml                 # Pasirenkamas Redis
│   │   ├── qa.yaml                    # Kodo tikrinimo įrankių prijungimas
│   │   ├── doctrine.yaml              # Doctrine prijungimas
│   │   └── papildomas-servisas.yaml   # Savas Compose komponentas
│   ├── config/
│   │   ├── php/                       # *.ini; local/test/stage/prod poaplankiai
│   │   ├── php-fpm/                   # Savas pool config; reikia Compose mount
│   │   ├── mysql/                     # *.cnf; aplinkų poaplankiai
│   │   ├── mariadb/                   # *.cnf; aplinkų poaplankiai
│   │   ├── apache/                    # *.conf; aplinkų poaplankiai
│   │   ├── nginx-proxy/               # *.conf; aplinkų poaplankiai
│   │   ├── redis/                     # redis.conf ir aplinkų redis.conf
│   │   ├── cron/                      # crontab ir crontab.<aplinka>
│   │   ├── prestashop/                # parameters.override.php / settings.override.php
│   │   ├── startup/                   # Numeruoti *.sh prieš PHP-FPM startą
│   │   └── mano-programa/             # Savi failai; skaitymą turi prijungti pats
│   ├── database/
│   │   ├── schema/                    # Sugeneruotos lentelių apibrėžtys
│   │   ├── after-import/
│   │   │   ├── common/                # SQL po kiekvieno importo
│   │   │   └── local|test|stage|prod/  # SQL po atitinkamos aplinkos importo
│   │   ├── fixtures/
│   │   │   ├── common/                # Duomenys kiekvienam pasirinktam rinkiniui
│   │   │   ├── local/                 # Vietiniam darbui skirti duomenys
│   │   │   ├── test/                  # Testavimo duomenys
│   │   │   └── pristatymas/           # Galima sukurti ir savo rinkinį
│   │   └── doctrine/
│   │       ├── connection.php         # DB ryšys migracijų įrankiui
│   │       ├── migrations.php         # Kur rasti ir registruoti migracijas
│   │       └── versions/              # Konkrečios PHP migracijos
│   ├── qa/
│   │   ├── baselines/                 # PHPStan ir kitų įrankių išimčių failai
│   │   ├── phpstan/phpstan.neon        # PHPStan taisyklės ir tikrinami katalogai
│   │   ├── php-cs/.php-cs-fixer.php    # PHP formatavimo taisyklės
│   │   ├── playwright/
│   │   │   ├── playwright.config.cjs  # Naršyklės testų nustatymai
│   │   │   └── tests/                 # *.spec.cjs testų scenarijai
│   │   └── psalm/                     # Savas įrankis; automatiškai neprijungtas
│   ├── public/                        # Vietinės aplikacijos kodas
│   ├── stage/public/                  # Numatytoji stage aplikacijos vieta
│   ├── prod/public/                   # Numatytoji prod aplikacijos vieta
│   ├── tiekejai/                      # Forsenos savi failai, setup jų nevaldo
│   ├── scripts/                       # Savo ranka paleidžiami pagalbiniai scriptai
│   ├── docs/                          # Tik šiam projektui skirta dokumentacija
│   ├── .idea/                         # PhpStorm projekto nustatymai
│   └── .generated/                    # Privatūs sugeneruoti failai; ne į Git
│       ├── compose.local.yaml         # make config rezultatas
│       ├── phpstorm-compose.local.yaml
│       ├── phpstorm-backups/          # IDE failų kopijos prieš pakeitimus
│       ├── backups/                   # make db-backup rezultatai
│       ├── db-import.local.lock       # Importo/fixtures vykdymo užraktas
│       └── test/
│           ├── initialized           # Žyma, kad kopiją valdo test-init
│           ├── app/                   # Nepriklausoma testinės aplikacijos kopija
│           └── data/                  # Testinė DB, TLS ir įrankių rezultatai
└── data/
    ├── mysql/ arba mariadb/           # Tik vieno pasirinkto DB variklio failai
    ├── ssl/                           # domain.crt ir domain.key
    ├── profiling/                     # PHP profiliavimo rezultatai
    ├── mailpit/                       # Sugauti laiškai
    ├── phpstan/                       # Cache ir ataskaitos
    ├── php-cs/                        # Formatavimo įrankio cache
    ├── playwright/                    # Ataskaitos, trace, test-results
    ├── doctrine-migrations/           # Įrankio darbiniai duomenys
    ├── local/redis/                   # Dabartinio Redis šablono vietinė vieta
    ├── stage/                         # Stage duomenų bazinis katalogas
    └── prod/                          # Prod duomenų bazinis katalogas
```

`local|test|stage|prod` žemėlapyje reiškia keturis galimus katalogų vardus. Nekurk vieno katalogo su vertikaliais brūkšniais.

## Kas perskaitoma automatiškai

| Vieta | Kas ten saugoma | Kada perskaitoma | Ar į Git? |
| --- | --- | --- | --- |
| `env/common.env` | Projekto bendros reikšmės | Kiekviena runner komanda | Taip, be paslapčių |
| `env/<ENV>.env` | Aplinkos prisijungimai ir skirtumai | Pasirinkus `ENV` | Ne |
| `compose/base.yaml` | Servisų pagrindas | Visada | Taip |
| `compose/common.yaml` | Bendri pakeitimai | Jei failas yra | Taip |
| `compose/<ENV>.yaml` | Aplinkos pakeitimai | Jei failas yra | Taip |
| Kiti `compose/*.yaml` | Papildomi servisai ir prijungimai | Tik įrašius į `PROJECT_COMPOSE_FILES` arba `include` | Taip |
| `config/php/*.ini` ir `config/php/<ENV>/*.ini` | PHP direktyvos | Bendro PHP serviso paleidimo metu | Taip, jei nėra paslapčių |
| `config/mysql/*.cnf`, `config/mariadb/*.cnf` ir aplinkų failai | Pasirinkto DB serverio nustatymai | To variklio paleidimo metu | Taip |
| `config/apache/*.conf`, `config/nginx-proxy/*.conf` ir aplinkų failai | HTTP serverių direktyvos | Atitinkamo serviso paleidimo metu | Taip |
| `config/startup/*.sh` | Projekto paruošimo veiksmai | Kiekvieną bendro PHP serviso startą | Taip |
| `config/prestashop/*.override.php` | Papildomi PS parametrai | Tik PS profiliui ir tik atpažintam formatui | Taip, paslaptis skaityti iš env |
| `config/cron/crontab`, `crontab.<ENV>` | Suplanuotos užduotys | Įjungus cron servisą | Taip |
| `database/after-import` | DB taisymo SQL | Tik `db-import` ir nustačius kelią | Taip |
| `database/fixtures` | Paruošti duomenys | Tik aiškiai pasirinkus rinkinį | Taip, be tikrų klientų duomenų |
| `database/schema` | Lentelių SQL ir manifestas | `schema-export` / `schema-check` | Taip |
| `database/doctrine`, `qa/*`, `config/redis` | Pasirenkami įrankiai | Tik su jų Compose prijungimu | Taip |
| `.generated`, `../data` | Veikiančios aplinkos būsena | Sukuria komandos / servisai | Ne |
| `scripts`, `docs`, `tiekejai`, savi katalogai | Projekto pasirinktas turinys | Jokios automatikos vien dėl vardo | Sprendžia projektas |

`config/php/test/runtime.ini` skaitomas testinėje aplinkoje, tačiau `config/startup/test/010-task.sh` **automatiškai nevykdomas**. Startup ieško tik tiesioginių `config/startup/*.sh` failų. Jei reikia skirtingo elgesio, naudok env ir sąlygą scripte.

DB konfigūracija ir SQL turi skirtingą darbą. `config/mysql/local/runtime.cnf` pasako, **kaip veikia MySQL**, o `database/after-import/local/010-domain.sql` keičia **įrašus tavo DB**.

## Aplikacijos public ir interneto public nėra tas pats

Šiame setup `app/public` reiškia visą aplikacijos checkout. PrestaShop atveju ten yra `index.php`, `modules`, `app`, `config` ir kiti failai.

Symfony tipo projektas gali turėti dar vieną savo `public`:

```text
demo/app/public/             # Visas Git checkout
├── composer.json
├── src/
└── public/index.php         # Tik ši dalis turi būti pasiekiama naršyklei
```

Tada `DOCUMENT_ROOT=public`. Aplikacijos checkout vieta nesikeičia; pasikeičia Apache aptarnaujamas poaplankis.

## Kiti palaikomi išdėstymai

| Generatoriaus variantas | Kur Makefile / env / compose | Kur vietinės aplikacijos kodas |
| --- | --- | --- |
| `--layout app` | `<projektas>/app` | `<projektas>/app/public` |
| `--layout root` | `<projektas>/docker` | `<projektas>/app/public` |
| `--layout legacy` | `<projektas>/app/docker` | `<projektas>/app/public` |

Naujam projektui generatoriaus automatinis pasirinkimas yra `root`. Norėdamas Forsenos išdėstymo, aiškiai naudok `--layout app`.

Taip pat yra du failų grupavimo variantai: `grouped` naudoja `env/` ir `compose/`, o `flat` naudoja `.env`, `.env.local`, `compose.yaml`, `compose.override.yaml`, `compose.local.override.yaml` šalia Makefile. Vienoje vietoje jų nemaišyk. Šis vadovas toliau remiasi `grouped` ir `--layout app`.

Senesni QA prijungimai gali rodyti į `app/config/phpstan`, `app/config/php-cs`, `app/config/playwright`, `app/tests/playwright` ir `app/config/doctrine-migrations`. `compose/qa.yaml` bei `compose/doctrine.yaml` juos pakeičia į šiame vadove aprašytus `qa/` ir `database/doctrine/` kelius. Vien perkėlus failus prijungimai nepasikeičia.
