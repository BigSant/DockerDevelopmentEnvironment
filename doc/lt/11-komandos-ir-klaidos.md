# 11. Visos komandos ir klaidų sprendimai

[Turinys](README.md) · [Atgal](10-kasdienis-darbas.md)

Komandas vykdyk kataloge su projekto Makefile. Numatyta aplinka `local`. `make help` parodo trumpą priminimą.

Šis bendras runner skirtas PHP servisų rinkiniui, o DB komandos – MySQL/MariaDB. Savas PostgreSQL ar Python servisas gali būti aprašytas Compose, bet tai nesuteikia jam šių DB komandų ar PHP build veiksmų palaikymo. Tokiam išplėtimui reikia savų komandų.

## Visos viešos Make komandos

| Komanda | Ką padaro | Ko reikia / kas pasikeičia |
| --- | --- | --- |
| `make help` | Parodo komandų santrauką | Nieko nepaleidžia. |
| `make setup-info` | Setup versija, API, Git commit ir darbo pakeitimų požymis | Nereikia veikiančio stack ar užpildyto projekto env. |
| `make init` | Sukuria trūkstamą privatų env ir standartinius bind katalogus | Esamų failų ir aplikacijos neperrašo; sertifikatų negamina. |
| `make bootstrap` | Vietinis env/portų/domeno/TLS/IDE paruošimas | Tik local/test; aplikacijos checkout turi būti parūpintas. |
| `make check` | Patikrina išspręstą Compose visiems profiliams | Nereikia veikiančios aplikacijos; reikia teisingų env ir šaltinių. |
| `make config` | Parašo `.generated/compose.<ENV>.yaml` | Privatus rezultatas; gali turėti prisijungimų. |
| `make build` | Pirma gamina PHP bazę, paskui pasirinkto modelio build servisus | Keičiasi vietiniai Docker atvaizdai; neveikia kaip DB importas. |
| `make pull` | Atsisiunčia pasirinktus išorinius atvaizdus | Praleidžia vietinius build atvaizdus ir jų pakartotinį naudojimą, pvz. cron. |
| `make up` | Paleidžia/perkuria servisus, laukia sveikatos, atlieka smoke | Naudoja esamus atvaizdus: `--no-build --pull never`. |
| `make down` | Pašalina stack konteinerius/tinklą | Bind mount DB/kodo failai lieka. |
| `make ps` | Parodo pasirinktus konteinerius | Būsenos patikra. |
| `make logs` | Parodo Compose logus | Neprideda savo papildomų `--follow` argumentų. |
| `make shell` | Atidaro `sh` PHP konteineryje | PHP turi veikti; išėjimas `exit`. |
| `make doctor` | Tikrina Engine/Compose, atvaizdus, portus, mount, env/TLS; veikiančiam PS – DB/HTTP | Randa paruošimo klaidas; failų automatiškai netaiso. |
| `make smoke` | Veikiančių servisų ir aplikacijos patikra | PS skaito config ir jungiasi PDO; kitoms programoms HTTP pagal `SMOKE_URL`. |
| `make ide-init` | Sugeneruoja/atnaujina PhpStorm nustatymus | Esami savi komponentai saugomi; reikia IDE Docker ryšio. |
| `make ide-refresh` | Atnaujina tik privatų IDE Compose failą | Nekeičia IDE bendrų vartotojo parinkčių. |
| `make test-init` | Paruošia testinę kodo/duomenų vietą ir env | Vykdomas iš local konteksto; DB nekopijuoja. |
| `make db-prepare` | Paleidžia tik `database` ir laukia jos sveikatos | Prieš pirmą importą nereikia veikiančio PHP. |
| `make db-backup` | Sukuria naują loginę `.sql.gz` kopiją | Reikia veikiančios DB ir disko vietos. |
| `make db-import-plan file=…` | Parodo dump ir after-import/fixtures eiliškumą | Tikrina failus, SQL nevykdo. |
| `make db-import file=…` | Vykdo dump ir po-importo SQL | Keičia DB; pasibaigus klaida neanuliuoja jau atlikto SQL. |
| `make db-fixtures-plan set=…` | Parodo common + pasirinkto rinkinio failus | Duomenų nekeičia. |
| `make db-fixtures-load set=…` | Įkelia pasirinkto rinkinio duomenis | Reikia veikiančios DB ir tinkamos schemos. |
| `make schema-export` | Įrašo lentelių apibrėžtis ir manifestą | Keičia valdomus schema failus, DB duomenų neskaito/nekeičia. |
| `make schema-check` | Palygina DB su Git indeksu | Nestage'ina ir negeneruoja failų. |
| `make schema-hook-install` | Įdiegia opt-in pre-commit patikrą schemos repozitorijoje | Neperrašo svetimo hook. |
| `make phpstan` | Vykdo PHPStan užduotį | Reikia serviso aprašo, įrankio atvaizdo ir config. |
| `make phpstan-baseline` | Sugeneruoja baseline į nurodytą konteinerio kelią | Reikia writable mount ir vėlesnės Git peržiūros. |
| `make phpcs` | Pagal nutylėjimą tikrina formatavimą | `cmd=fix` ir `dir-fix` keičia aplikacijos failus. |
| `make e2e` | Vienkartiniame Playwright konteineryje vykdo testus | Reikia veikiančios/paruoštos aplikacijos ir, kai būtina, fixtures. |
| `make doctrine` | Pagal nutylėjimą rodo migracijų statusą | Reikia pasirinktinio Doctrine prijungimo; `cmd=migrate` keičia DB. |

## Make argumentai

| Argumentas | Kur veikia | Pavyzdys |
| --- | --- | --- |
| `ENV` | Runner komandos | `make up ENV=test`; leidžiama local/test/stage/prod. |
| `PROFILES` | Runner komandos | `make build PROFILES=phpstan,phpcs`; `PROFILES=` aiškiai pasirenka tik pagrindą. |
| `SETUP_DIRECTORY` | Bendro Makefile vieta | `make SETUP_DIRECTORY=/opt/setup check`. |
| `PROJECT_DIRECTORY` | Pažengusiems: projekto šaknies override | Paprastai apskaičiuojamas; keičiant kelius turi likti teisinga aplikacijos/DB izoliacija. |
| `PROJECT_DOCKER_DIRECTORY` | Nustato projekto Makefile | Katalogas, kuriame yra env/compose šaltiniai. |
| `PYTHON` | Runner interpretatorius | `make PYTHON=python3 check`. |
| `file` | `db-import`, `db-import-plan`, `db-backup` | `make db-import file=/kelias/dump.sql.gz`. Backup atveju – naujo rezultato kelias. |
| `backup` | Tik `db-import` vykdymas | `backup=1` prideda kopiją prieš importą; planas kopijos nesukuria. |
| `db-fixtures` | `db-import`, `db-import-plan` | `db-fixtures=test` prideda fixture planą po hooks. |
| `set` | `db-fixtures-plan/load` | `set=local`, `set=test`, `set=pristatymas`. |
| `refresh` | `test-init` | Tik `refresh=1` sustabdo ir atnaujina esamą testinę kodo kopiją. |
| `timeout` | Make lygiu tik `smoke` | `make smoke timeout=120`; sveikatos tikrinimo laukimo sekundės. |
| `cmd` | `phpstan`, `phpcs`, `e2e`, `doctrine` | `make doctrine cmd='migrate --dry-run'`. |
| `IDE_DOCKER_SERVER` | `ide-init` | Esamo PhpStorm Docker ryšio vardas. |
| `IDE_CONFIG_DIRECTORY` | `ide-init` | Nestandartinis PhpStorm config katalogas. |

`cmd` skaidomas į programos argumentus, ne vykdomas kaip shell. `cmd='foo && bar'` nesukuria dviejų procesų. Jeigu tikrai reikia shell, aiškiai paleisk shell arba savo scriptą per projekto Compose komandą.

### PHPStan vidinės užduotys

`cmd` gali būti `report` (default), `report-raw`, `report-html`, `dir-analysis DIR=…`, `git-analysis BASE_BRANCH=origin/master`, `git-action-analysis`, `bitbucket-analysis`, `baseline`. Pastarasis senas vidinis `baseline` rašo į įrankio cache; norint versijuojamo projekto baseline, naudok viešą `make phpstan-baseline`.

### PHP-CS-Fixer vidinės užduotys

`cmd` gali būti `check` (default), `fix`, `dir-check DIR=…`, `dir-fix DIR=…`, `git-check BASE_BRANCH=origin/master`, `git-fix BASE_BRANCH=origin/master`, `git-action-check`, `bitbucket-check`. Git užduotims aplikacijos Git istorija turi būti pasiekiama konteineryje. Testinė kopija sąmoningai neturi `.git`.

### Playwright ir Doctrine argumentai

`make e2e cmd='npx playwright test --config=/e2e/config/playwright.config.cjs --grep checkout'` pasirenka konkretų testą. Perduodamas visas naujas komandų argumentų sąrašas, todėl nepamiršk config kelio.

`make doctrine cmd=status`, `cmd=list`, `cmd=generate`, `cmd='migrate --dry-run'`, `cmd='migrate --no-interaction'` perduodami Doctrine programai. Pilnas jos subkomandų sąrašas priklauso nuo įdiegtos versijos; `cmd=list` parodo būtent ją.

## Tiesioginis runner CLI

Make naudoja `setup/docker/project.py`. Kai reikia, pavyzdžiui, ilgesnio `up` timeout, vykdyk iš projekto `app` katalogo:

```bash
python3 ../../setup/docker/project.py --docker-directory . --env local up --timeout 180
```

Visos CLI parinktys:

| Parinktis | Paskirtis |
| --- | --- |
| `--docker-directory` | Privaloma originalių projekto šaltinių vieta. |
| `--project-directory` | Pasirenkama projekto šaknis. |
| `--env` | local/test/stage/prod, default local. |
| `--profiles` | Aiškus pasirenkamų profilių sąrašas; gali būti tuščias. |
| `--command` | QA / Doctrine užduoties argumentai. |
| `--dump` | SQL arba SQL.GZ importui. |
| `--db-fixtures` | Fixture rinkinio vardas. |
| `--backup` | Kopija prieš importą. |
| `--output` | Naujas backup failas. |
| `--refresh-test` | Sustabdyti ir atnaujinti testinę aplikacijos kopiją. |
| `--timeout` | Teigiamas sekundžių skaičius; numatyta 90. Naudojamas up/db-prepare/smoke. |
| `--docker-server` | IDE Docker ryšio vardas. |
| `--ide-config-directory` | IDE config paieškos vieta. |

Veiksmų vardai sutampa su Make lentelės runner komandomis; `help` yra Make target, o CLI pagalba gaunama su `--help`. Vidiniam `doctor` PS smoke šiame leidime neperduodamas pasirinktas CLI timeout – atskirai kviesk `smoke`, jei reikia kito laiko.

## Kai reikia Compose komandos, kurios Make neturi

Situacija: nori perkrauti tik PHP po `.ini` pakeitimo arba žiūrėti tik DB logus. Sukurk šviežią privatų modelį:

```bash
make config
docker compose -f .generated/compose.local.yaml --profile '*' restart php-fpm
docker compose -f .generated/compose.local.yaml --profile '*' logs --tail=100 database
docker compose -f .generated/compose.local.yaml --profile '*' exec php-fpm php --ini
docker compose -f .generated/compose.local.yaml --profile '*' exec webserver httpd -t
docker compose -f .generated/compose.local.yaml --profile '*' exec nginx-proxy nginx -t
```

`--profile '*'` šioms konkrečiai įvardinto serviso `exec`, `restart`, `logs` komandoms leidžia matyti modelyje esančius profilius. **Nekopijuok jo į bendrą `up`**, nes tada paleistum ir pasirenkamus servisus.

Jeigu reikia tiesiog sustabdyti jau paleistą konkretų papildomą servisą:

```bash
docker compose -f .generated/compose.local.yaml --profile '*' stop cron
docker compose -f .generated/compose.local.yaml --profile '*' rm -f cron
```

Testinei aplinkai pirmiausia `make config ENV=test` ir tada naudok `.generated/compose.test.yaml`. Pirminį testinio modelio tikrinimą atlik runner komanda, o sugeneruoto failo netaisyk norėdamas apeiti izoliavimo taisykles. Tiesioginis Docker kvietimas runner apsaugų iš naujo nevykdo.

## Generatoriaus parinktys

`prepare_project.py` priima vieną arba daugiau projekto šaknų ir šias parinktis:

| Parinktis | Reikšmės / veikimas |
| --- | --- |
| `--layout` | `auto`, `root`, `app`, `legacy`. Naujai auto → root; esamam stengiasi išsaugoti aptiktą vietą. |
| `--sources` | `auto`, `grouped`, `flat`; esamo formato į kitą automatiškai nekonvertuoja. |
| `--check` | Tik planas, failų nerašo. |
| `--from-legacy` | Peržiūrimas seno `app/docker` nustatymų kopijavimas; custom Dockerfile/overlay gali reikalauti rankinio perkėlimo. |

Pasirengimo komanda kuria trūkstamus failus, o ne sinchronizuoja visus šablonus perrašydama tavo darbą. Senas `new_host.sh <domain>` yra kito workflow hosto paruošėjas; jo nemaišyk su jau konsoliduoto projekto `make bootstrap`.

## Dažniausios klaidos: situacija → patikra → sprendimas

| Požymis | Ką patikrinti | Sprendimas |
| --- | --- | --- |
| `Shared setup not found` | Ar setup yra bendrame sutartame kelyje? | Atsisiųsti setup arba perduoti absoliutų `SETUP_DIRECTORY`. |
| `Create ... env/local.env` | Ar yra privatus env / jo example? | `make init`, užpildyti sukurtą failą. Esant nestandartinei aplinkai sukurti jos privatų failą. |
| `Use one source layout` | Ar kartu turi `env/common.env` ir root `.env` / `compose.yaml`? | Peržiūrėti ir pasirinkti vieną šaltinių formatą; netaisyti sugeneruoto modelio. |
| `requires a different setup API` | `SETUP_REQUIRED_API` ir `make setup-info` | Naudoti suderinamą setup versiją; nekeisti API skaičiaus vien tam, kad apeitum klaidą. |
| Trūksta atvaizdo, `pull never` klaida | Ar pasikeitė versija/build argumentai/hash? | Vietiniam atvaizdui `make build`, išoriniam – `make pull`. |
| Portas užimtas | Kito projekto `env` ir veikiančių konteinerių portai | Parinkti kitą host portą; aplikacijos/SQL/smoke URL turi jį atitikti. |
| Domenas neveda į localhost | Host rezoliucija | `make bootstrap`; prireikus vykdyti jo pateiktą vienkartinį helper. |
| Trūksta TLS failų | `${PROJECT_DATA_DIRECTORY}/ssl/domain.crt`, `domain.key` | Local/test `make bootstrap`; prod pateikti savo tinkamus sertifikatus. |
| Nginx `host not found in upstream pma/mailpit` | Ar įjungti atitinkami servisai? | Minimalus Nginx command iš 2 skyriaus arba tinkamai įjungti abu virtualių hostų servisai. |
| `Missing shop configuration` kitai aplikacijai | `PROFILE` paveldėjimas | Aiškiai `PROFILE=` ar `akeneo`; PS projektui atkurti tikrą shop config. |
| PS 1.6 ieško modernių raktų | Ar tikras esamas formatas ir failai nepainioti? | Atkurti `config/settings.inc.php`; peržiūrėti 6 skyriaus formatų ribas. |
| `parameters.yml` nepalaikomas | Ankstyvas PS config formatas | Aplikacijos įrankiais konvertuoti arba sukurti savą projekto paruošimą. |
| DB `Access denied` po env pakeitimo | Ar esama DB dar turi seną vartotojo slaptažodį? | Suderinti env su realiu vartotoju arba sąmoningai pakeisti jį DB kliente. Netrinti DB katalogo. |
| Sveika DB, bet PS HTTP 500 | Ar įkeltas dump, išsaugoti shop raktai, teisingas config? | Peržiūrėti PHP/Apache logus, atlikti reikiamą atkūrimą. Healthcheck nepatvirtina lentelių turinio. |
| Smoke peradresuoja kitur | `SMOKE_URL`, PS DB domenas, HTTP/HTTPS/portas | Suderinti tikrą tos aplinkos URL. Testiniam Apache žr. 9 skyrių. |
| `.ini` pakeitimas nesimato | Ar perkrautas PHP? Ar žiūri CLI ar FPM? | Perkrauti servisą; HTTP keturis limitus keisti ir `PHP_*` env. |
| Nginx `directive is duplicate` / neteisingas kontekstas | Kur įtraukiamas tavo `.conf`? | Neįrašyti `location` tiesiai `http` lygyje ir nekartoti vienetinės direktyvos tame pačiame kontekste. |
| `Set SCHEMA_DIRECTORY` | Common env kelias | Forsenai `SCHEMA_DIRECTORY=app/database/schema`, ne vien `database/schema`. |
| Schema-check nesutampa po eksporto | Git indeksas | Peržiūrėti ir `git add -A database/schema`, tik tada kartoti check. |
| Hook neblokuoja aplikacijos commito | Kurios repo dalis yra schema? | Įdiegti patikrą reikiamoje aplikacijos repo, ne vien Docker repo. |
| Fixture rinkinys neegzistuoja | `FIXTURES_DIRECTORY`, `set`, katalogo vardas | Sukurti konkretų rinkinį arba pataisyti vardą; `fixtures=` importui pakeisti į `db-fixtures=`. |
| Pakartotinis fixture dubliuoja duomenis | SQL raktai ir insert logika | Naudoti stabilius raktus ir kartojamą SQL; rinkiniai savaime nevalo DB. |
| `An import ... already running` | Ar kitas importas dar veikia? | Sulaukti jo pabaigos. Lock failo buvimas savaime nereiškia aktyvaus užrakto; jo trinti nereikia. |
| Trūksta vietos `.sql.gz` importui | Laisva vieta ir nesuspausto dump dydis | Paruošti pakankamai vietos arba mažesnį testinį dump; backup taip pat užima vietą. |
| Testo writable mount atmetamas | Galutinis Compose kelias | Duomenis/rezultatus laikyti `.generated/test`, bendrą config/testus montuoti `:ro`. |
| Testas nemato naujo kodo | Ar atnaujinta kopija? | `make test-init refresh=1`, po to vėl paleisti testinę aplinką. |
| Redis neranda environment/redis.conf | Ar yra konkrečios `ENV` failas? | Sukurti `config/redis/test/redis.conf` ar atitinkamos aplinkos failą. |
| Playwright nepasiekia `DOMAIN:host-port` | Ar portas yra konteinerio ar kompiuterio? | Domeną nukreipti į host-gateway ir naudoti tikrą `SMOKE_URL`, kaip 8 skyriuje. |
| Baseline negali būti įrašytas | Ar `/tmp/phpstan/baselines` writable? | Generuoti local arba pasirinkti testinį rašomą rezultatą. |
| Doctrine `diff` nieko nežino apie modelį | Ar pateiktas ORM/schema provider? | Paruošti aplikacijos ORM integraciją arba rašyti migraciją; snapshot nėra ORM mapping. |
| Aplikacija po prod kelio pataisymo mato tuščią DB | Ar nepasikeitė tikras mount? | Grąžinti peržiūrėtą ankstesnį kelią, sustabdyti konkuruojančius procesus ir suplanuoti perkėlimą. |

Jei kyla neaiškumas, pradėk nuo `make setup-info`, `make check`, `make ps` ir konkretaus serviso logų. `make config` naudok lokaliai tikriems keliams patikrinti. Nepradėk nuo DB katalogų trynimo ar visų Docker atvaizdų valymo – daugumai konfigūracijos klaidų to nereikia.

## Papildomos kasdienės komandos

`make restart`, `make logs service=php-fpm follow=1 tail=100`, `make composer`, `make cache-clear`, `make runtime-info` ir `make db-backup-prune` su pilnais pavyzdžiais aprašyti [12 skyriuje](12-cache-ir-hibridines-aplinkos.md#kasdienės-komandos-ir-preflight).
