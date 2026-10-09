# 1. Struktūra: kas kur gyvena

[Turinys](README.md) · [Toliau: pirmas paleidimas](02-pradzia.md)

## Kelios sąvokos

| Žodis | Paprastai | Pavyzdys |
| --- | --- | --- |
| Aplikacija | Tavo svetainės programa | PrestaShop failai `app/public/`. |
| Servisas | Vienas aplinkos darbas | `database` saugo DB, `php-fpm` vykdo PHP. |
| Image'as | Paruošta programų „dėžutė“ | PHP su reikalingais plėtiniais. |
| Konteineris | Paleista image'o kopija | Veikiantis projekto PHP procesas. |
| Bind mount | Tas pats katalogas matomas kompiuteryje ir konteineryje | `app/public` konteineryje matomas kaip `/var/www/html`. |
| CORE | Bendro setup konfigūracija ir image'ai | `setup/docker/docker/apache/conf/sites.conf`. |
| Drop-in | Projekto failas, kuris **papildo** CORE konfigūraciją | `config/php/20-runtime.ini`. |

`Dockerfile` yra image'o gaminimo receptas. Compose YAML pasako, kokius konteinerius paleisti ir ką jiems prijungti. Env failai duoda reikšmes, pvz. PHP versiją ar DB vardą.

Dar keli žodžiai: **CLI** – programa terminale; **PHP-FPM** – PHP procesai, kuriems web serveris perduoda užklausas; **hook** – scriptas, kurį automatiškai iškviečia įvykis (PHP startas, `git commit`); **default** – numatytoji reikšmė, kai nieko nepasirenki.

## Pagrindiniai principai

- **Viskas valdoma per env.** Tas pats CORE veikia local, test, stage ir prod aplinkose, skiriasi tik env reikšmės.
- **Projektas tik papildo CORE.** Drop-in failai įtraukiami po CORE failų (paskutinis laimi). Viso CORE failo perdengti negalima.
- **Papildomų servisų pagal nutylėjimą nėra.** Visada veikia tik keturi: `nginx-proxy`, `webserver` (Apache), `php-fpm`, `database`. Kiti įjungiami `SERVICES=` sąrašu.
- **Viena komanda – vienas veiksmas.** `make up` tik paleidžia, `make doctor` tik tikrina. `make setup` tik iškviečia kelias vieno veiksmo komandas iš eilės.
- **DB niekada neliečiama netyčia.** `make setup`, `make up`, `make bootstrap` ir testai tavo DB nekeičia. Duomenys keičiami tik aiškiomis `db-*` komandomis.

## Bendras setup ir konkretus projektas

```text
~/Projects/
├── setup/                         # Viena bendra Git repozitorija visiems projektams
│   ├── install-host               # Vienkartinis kompiuterio paruošimas
│   ├── create-project             # Naujo projekto failų kūrimas (wizard'as)
│   ├── VERSION
│   ├── docker/
│   │   ├── .env                   # Bendros numatytosios versijos ir reikšmės
│   │   ├── project.mk             # Bendros make komandos
│   │   ├── project.py             # Runner'is: surenka konkretaus projekto aplinką
│   │   ├── project-settings.yaml  # Visų projekto nustatymų sąrašas
│   │   ├── compatibility.json     # PS versija → tinkamos PHP / MySQL / MariaDB versijos
│   │   ├── docker/<servisas>/     # Kiekvieno serviso Dockerfile ir docker-compose.yml
│   │   ├── database-profile/      # PS lentelių registras, rinkinio receptas, saugūs after-import SQL
│   │   └── runtime/               # Scriptai konteinerių viduje (startas, .template, cron, DB monitorius)
│   ├── templates/                 # Naujo projekto šablonai
│   ├── tests/                     # Paties setup testai
│   └── doc/lt/                    # Šis vadovas
└── demo/
    ├── app/                       # Aplinkos repozitorija: čia atidarai PhpStorm ir vykdai make
    │   └── public/                # Aplikacijos kodas (PrestaShop atveju – atskira repozitorija)
    └── data/                      # Veikiančios aplinkos duomenys (DB failai, TLS, Mailpit, cron istorija)
```

`setup` nėra submodulis. Projekto `Makefile` pats suranda bendrą katalogą (`../../setup`), todėl komandų logika laikoma vienoje vietoje.

Naršyklės užklausa keliauja taip:

```text
naršyklė → kompiuterio Nginx (demo.local) → nginx-proxy → webserver (Apache) → php-fpm → database
```

Konteineryje `localhost` reiškia **tą patį konteinerį**. PHP jungiasi į DB vardu `database`, į Redis – `redis`, į Memcached – `memcached`.

## Projekto katalogų žemėlapis

Tai galimybių žemėlapis. Katalogus kurk tik tada, kai dedi į juos failą: be jų viskas veikia, o tušti katalogai nekuriami.

```text
demo/
├── app/
│   ├── Makefile                       # Vienodas visiems projektams
│   ├── .gitignore
│   ├── Dockerfile                     # Neprivalomas: tik papildo CORE PHP image'ą
│   ├── env/
│   │   ├── common.env                 # Bendri projekto nustatymai (Git)
│   │   ├── local.env                  # Privatūs vietiniai nustatymai (ne Git)
│   │   ├── test.env                   # Privatūs ENV=test nustatymai (sukuria komandos)
│   │   ├── stage.env, prod.env        # Serverių nustatymai (ne Git)
│   │   └── *.env.example              # Pavyzdžiai be slaptažodžių (Git)
│   ├── compose/
│   │   ├── base.yaml                  # Neprivalomi projekto servisai (pagrindinius prijungia setup)
│   │   ├── common.yaml                # Neprivalomi Compose skirtumai visoms aplinkoms
│   │   └── local.yaml, prod.yaml …    # Neprivalomi Compose skirtumai vienai aplinkai
│   ├── config/
│   │   ├── php/                       # *.ini drop-in; php/<aplinka>/ – tik tai aplinkai
│   │   ├── apache/                    # *.conf globaliai, vhost.d/*.conf – svetainės VirtualHost
│   │   ├── nginx-proxy/               # *.conf (http), server.d/*.conf, location.d/*.conf
│   │   ├── mysql/ arba mariadb/       # *.cnf ir init.d/ (SQL pirmą kartą kuriant DB)
│   │   ├── redis/                     # *.conf, kai SERVICES turi redis
│   │   ├── cron/                      # crontab ir crontab.<aplinka>
│   │   ├── prestashop/                # parameters.override.php / settings.override.php
│   │   ├── composer/                  # Savas parduotuvės composer.json / composer.lock
│   │   ├── startup/                   # *.sh, vykdomi prieš PHP-FPM startą
│   │   ├── deploy/build.sh            # Asset'ų surinkimas deploy'aus metu
│   │   └── environment.json           # Neprivalomi aplikacijos env reikalavimai
│   ├── database/
│   │   ├── schema/                    # Lentelių snapshot'as (commit hook'as jį atnaujina)
│   │   ├── table-registry.txt         # Privalomas lentelių registras
│   │   ├── migrations/                # Apsaugotos SQL migracijos
│   │   ├── after-import/              # common/, <aplinka>/, integrations/ SQL po užkėlimo
│   │   ├── data/<rinkinys>/           # Bazinis duomenų rinkinys (pvz. dev) ir recipe.txt
│   │   └── fixtures/                  # Papildomi SQL rinkiniai
│   ├── public/                        # Aplikacijos kodas (local)
│   ├── stage/public/, prod/public/    # Aplikacijos kodas stage/prod aplinkoms serveryje
│   ├── .idea/                         # PhpStorm nustatymai
│   └── .generated/                    # Sugeneruoti privatūs failai (ne Git)
│       ├── compose.<ENV>.yaml         # make config rezultatas
│       ├── backups/                   # make db-backup kopijos
│       ├── release/<aplinka>/         # images.lock.json, deploy'ų istorija, release'ai
│       └── test/app, test/data        # Izoliuota ENV=test kodo kopija ir duomenys
└── data/
    ├── mysql/ arba mariadb/           # Pasirinkto DB variklio failai
    ├── ssl/                           # domain.crt ir domain.key
    ├── profiling/                     # Xdebug profiliai ir trace
    ├── mailpit/, cron/, redis/        # Įjungtų servisų duomenys
    └── stage/, prod/                  # Serverių aplinkų duomenys
```

`<aplinka>` reiškia vieną iš `local`, `test`, `stage`, `prod`.

## Kas perskaitoma automatiškai

| Vieta | Kas ten saugoma | Kada perskaitoma | Ar į Git? |
| --- | --- | --- | --- |
| `env/common.env` | Bendri projekto nustatymai | Kiekviena komanda | Taip, be slaptažodžių |
| `env/<ENV>.env` | Aplinkos prisijungimai ir skirtumai | Pasirinkus `ENV` | Ne |
| `compose/base.yaml` | Projekto papildomi servisai | Jei failas yra | Taip |
| `compose/common.yaml`, `compose/<ENV>.yaml` | Compose skirtumai | Jei failas yra | Taip |
| `SERVICES=` sąraše esantys servisai | CORE servisų aprašai | Kiekviena komanda | Taip (sąrašas env faile) |
| `config/<servisas>/…` | Drop-in failai | To serviso starto metu | Taip, slaptažodžius imk iš env per `.template` |
| `config/mysql/init.d/` | SQL ir scriptai | Tik pirmą kartą kuriant DB | Taip |
| `config/startup/*.sh` | Projekto paruošimo veiksmai | Kiekvieną PHP startą | Taip |
| `config/cron/crontab*` | Cron užduotys | Kai `SERVICES` turi `cron` | Taip |
| `database/after-import/` | SQL po DB užkėlimo | `db-import`, `db-load`, `test-prepare` | Taip |
| `database/schema/`, `table-registry.txt`, `migrations/` | DB kaip kodas | Commit hook'as, `db-*` komandos | Taip |
| `database/data/<rinkinys>/` | Anonimizuotas bazinis rinkinys | `db-load`, `test-prepare` | Taip |
| `.generated/`, `../data/` | Veikiančios aplinkos būsena | Sukuria komandos | Ne |

`config/php/local/20-runtime.ini` skaitomas tik `ENV=local`, o `config/php/20-runtime.ini` – visose aplinkose. Aplinkos failas skaitomas po bendro, todėl jo reikšmė laimi.

DB konfigūracija ir SQL turi skirtingą darbą: `config/mysql/20-runtime.cnf` pasako, **kaip veikia MySQL**, o `database/after-import/local/010-domain.sql` keičia **įrašus tavo DB**.

## Aplikacijos public ir interneto public nėra tas pats

`app/public` reiškia visą aplikacijos checkout'ą. PrestaShop atveju ten yra `index.php`, `modules`, `app`, `config` ir kiti failai.

Symfony tipo projektas gali turėti dar vieną savo `public`:

```text
demo/app/public/             # Visas Git checkout'as
├── composer.json
├── src/
└── public/index.php         # Tik ši dalis turi būti pasiekiama naršyklei
```

Tada `env/common.env` įrašyk `DOCUMENT_ROOT=public`. Kodo vieta nesikeičia, pasikeičia tik Apache aptarnaujamas poaplankis.

## Dvi repozitorijos viename projekte

Forsenoje aplinkos repozitorija yra `forsena/app` (env, compose, config, database), o parduotuvės repozitorija – `forsena/app/public`. `git status` kataloge `app/` ir `git -C public status` rodo skirtingus darbus. Daugiau: [kasdienis darbas](10-kasdienis-darbas.md#dvi-git-repozitorijos).
