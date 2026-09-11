# 2. Pirmas paleidimas

[Turinys](README.md) · [Atgal](01-struktura.md) · [Toliau: nustatymai](03-nustatymai.md)

## A. Jau turi Forseną šiame kompiuteryje

Situacija: kodas ir DB jau vietoje, nori pradėti darbą.

```bash
cd ~/Projects/forsena/app
make setup-info
make check
make up
make doctor
```

`check` tikrina Compose struktūrą. `up` paleidžia esamus atvaizdus, laukia servisų ir tikrina aplikaciją. `doctor` patikrina aplinkos paruošimą ir veikiančio PS DB/HTTP.

Patikra: turi matyti sėkmingą PrestaShop DB prisijungimą ir HTTP 200. Forsenos tikras vietinis patikros adresas nustatytas `SMOKE_URL=http://forsena.local/`.

Jei trūksta atvaizdų, prieš `up` vykdyk `make build`. Jei tai naujas kompiuteris, atlik kitą skyrių: seno kompiuterio privatūs env, DB ir sertifikatai automatiškai neatkeliauja su Git.

## B. Naujas kolegos kompiuteris, esamas projektas

1. Turėk Git, Make, Python 3.10+, Docker Engine ir Docker Compose 2.24.4+; Docker turi veikti tavo vartotojui. Domeno/TLS paruošimui reikia OpenSSL ir mkcert, testinei kodo kopijai – rsync.
2. Atsisiųsk bendrą setup į `~/Projects/setup` ir sutartą jo versiją. Projektui reikia suderinamo API; versijas tikrink su `make setup-info`.
3. Atsisiųsk aplinkos repozitoriją į `~/Projects/forsena/app`. Atskirą aplikacijos repozitoriją atsisiųsk į `~/Projects/forsena/app/public`.
4. Iš projekto pavyzdžio sukurk `env/local.env` ir įrašyk savo nustatymus. Tikri parduotuvės raktai turi atkeliauti su jos konfigūracijos atkūrimu, ne būti išgalvoti.
5. Atlik žemiau esančią komandų seką.

Jei programų dar nėra, sek savo Linux distribucijai skirtas instrukcijas: [Docker Engine Ubuntu sistemoje](https://docs.docker.com/engine/install/ubuntu/), [Compose plugin](https://docs.docker.com/compose/install/linux/), [mkcert diegimas ir vietinis pasitikėjimas](https://github.com/FiloSottile/mkcert). Docker instrukcijose pasirink savo OS variantą; Ubuntu komandų nekopijuok į kitą distribuciją. Git, Make, Python, OpenSSL ir rsync įdiek per jos paketų tvarkyklę. Terminale prieš tęsiant turi veikti `docker info`, `docker compose version`, `python3 --version` ir `make --version`.

Pirmas bendro setup atsisiuntimas, jei jo dar nėra:

```bash
mkdir -p ~/Projects
git clone git@github.com:BigSant/DockerDevelopmentEnvironment.git ~/Projects/setup
git -C ~/Projects/setup switch --detach v1.0.0
```

Aplikacijos Bitbucket adresą pateikia komanda. `git@bitbucket.org:KOMANDA/REPO.git` yra adreso forma, ne egzistuojanti mokomoji repozitorija. Neklonuok ant jau esančio checkout.

Iš projekto `app` katalogo:

```bash
make init
# Dabar redaktoriuje užpildyk env/local.env.
make bootstrap
make check
make build
make db-prepare
make db-import-plan file=/tikras/kelias/forsena.sql.gz
make db-import file=/tikras/kelias/forsena.sql.gz
make up
make doctor
```

Dump kelią pakeisk turimu failu. Importas nėra būtinas naujam paprastam PHP puslapiui, bet atkuriamai parduotuvei reikia jos DB.

`make bootstrap`:

- sukuria trūkstamą privatų env iš pavyzdžio;
- atpažįstamus pavyzdinius vardus/slaptažodį pakeičia vietiniais;
- parenka portus, jeigu jie tušti arba `0`;
- paruošia prijungiamus katalogus, tikrina domeną, sukuria ar atnaujina TLS;
- paruošia PhpStorm.

Esami tikri nustatymai išsaugomi. Ši komanda neatsisiunčia nežinomo aplikacijos kodo, neimportuoja DB ir nekuria atvaizdų. Jeigu reikia vienkartinio `mkcert -install` ar privilegijų domenui įrašyti, gausi konkrečią komandą. Ją įvykdęs pakartok `make bootstrap`.

## C. Naujas minimalus projektas nuo nulio

Situacija: kuri paprastą PHP projektą `demo`. Nereikia nei PS logikos, nei papildomų servisų.

### 1. Sukurk šabloną

```bash
cd ~/Projects/setup
python3 prepare_project.py --layout app --check ../demo
python3 prepare_project.py --layout app ../demo
cd ../demo/app
```

Pirmoji komanda tik parodo trūkstamų failų skaičių. Antroji juos sukuria. Generatorius pateikia pilną galimybių šabloną, todėl toliau aiškiai pasirenkame minimalų servisų sąrašą. Neįjungti `qa/`, `database/doctrine` ir Redis failai gali likti kaip pasirenkami šaltiniai; jų buvimas nepaleidžia servisų.

### 2. Įrašyk bendrus projekto nustatymus

Failas `env/common.env`:

```dotenv
PROJECT_NAME=demo
PROFILE=
PHP_VERSION=8.1
SETUP_REQUIRED_API=1
VERSIONED_IMAGES=1
COMPOSE_PROFILES=
PROJECT_COMPOSE_FILES=
POST_IMPORT_SQL_DIRECTORY=app/database/after-import
SCHEMA_DIRECTORY=app/database/schema
FIXTURES_DIRECTORY=app/database/fixtures
```

Tuščias `PROFILE` išjungia PS veiksmus. Tuščias `PROJECT_COMPOSE_FILES` šiuo atveju svarbus: minimalus pagrindas nebeturi QA servisų, kuriuos pilno šablono `qa.yaml` tik papildo.

### 3. Pasirink keturis servisus

Failas `compose/base.yaml`:

```yaml
name: ${PROJECT_NAME}-${ENV}
include:
  - ${ROOT_DIRECTORY}/docker/nginx-proxy/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/apache/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/mysql/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/php-fpm/docker-compose.yml
networks:
  network_app:
    driver: bridge
    name: ${PROJECT_NAME}-${ENV}-network
```

Failas `compose/common.yaml`:

```yaml
services:
  php-fpm:
    build:
      context: ${PROJECT_DOCKER_DIRECTORY}
      dockerfile: Dockerfile
      args:
        BASE_IMAGE: php-fpm-base-${IMAGE_ENV:-${ENV}}-${PHP_VERSION}-${XDEBUG_VERSION}-${COMPOSER_VERSION}${SETUP_IMAGE_SUFFIX:-}
      additional_contexts:
        profiles: ${ROOT_DIRECTORY}/profile
  nginx-proxy:
    command:
      - /bin/sh
      - -c
      - ': > /etc/nginx/conf.d/sites_env.conf; exec /usr/local/bin/init.sh "$$@"'
      - minimal-nginx
      - ${DOMAIN}
      - ${WHITELISTED_IP}
      - ${WEBSERVICE_TIMEOUT}
```

PHP dalis naudoja generatoriaus sukurtą projekto `Dockerfile`. Nginx dalis išjungia bendro vietinio atvaizdo phpMyAdmin ir Mailpit virtualius hostus, nes tų servisų čia nėra. Be šio pakeitimo Nginx gali ieškoti neegzistuojančio `pma` ar `mailpit`.

Pilno šablono `compose/local.yaml` turi Playwright papildymą. Minimaliame projekte pakeisk jo turinį į:

```yaml
services: {}
```

`prod.yaml` kol kas nenaudojamas. Prieš pirmą prod paleidimą jį sutvarkyk pagal [aplinkų skyrių](09-aplinkos.md): pilno šablono failas turi daugiau servisų ir papildomą `/prod` keliuose.

### 4. Pridėk aplikaciją

Sukurk katalogą `public` ir failą `public/index.php`:

```php
<?php
header('Content-Type: text/plain; charset=utf-8');
echo "Demo veikia\n";
```

Jei jau turi Git aplikaciją, šio bandomojo failo nereikia – į `public` įkelk visą jos checkout.

### 5. Paruošk privačius nustatymus ir paleisk

```bash
make bootstrap
```

Atidaryk `env/local.env`. Pamatysi `DOMAIN`, `LOCALHOST_PORT` ir `LOCALHOST_PORT_SSL`. Pavyzdžiui, jei bootstrap pasirinko `demo.localhost` ir HTTP portą `31820`, į tą patį privatų failą įrašyk:

```dotenv
SMOKE_URL=http://demo.localhost:31820/
SMOKE_EXPECT=Demo veikia
```

Naudok **savo sugeneruotą portą**, ne aklai `31820`.

```bash
make check
make build
make up
make doctor
```

Patikra: naršyklėje atidaręs tą patį adresą matai „Demo veikia“. `make up` patikrina HTTP ir laukiamą tekstą. PS prisijungimų failų šiame projekte niekas neieško.

## D. Paruošti daugiau projektų

```bash
cd ~/Projects/setup
python3 prepare_project.py --layout app --check ../parduotuve-a ../parduotuve-b
python3 prepare_project.py --layout app ../parduotuve-a ../parduotuve-b
```

Kiekvienas projektas turi savo `env/local.env`, vardą, portus ir DB duomenis. Generatorius patikrina visų projektų planą prieš pradėdamas rašyti, kuria tik trūkstamus failus ir neperrašo savininko esamų nustatymų. Jis nėra visų esamų failų atnaujinimo į naujausią šabloną komanda.

Jei bendras setup gyvena kitur, komandai nurodyk jo vietą:

```bash
make SETUP_DIRECTORY=/opt/komandos-setup check
```

## Kada ką kartoti

| Kas pasikeitė | Ką daryti |
| --- | --- |
| Aplikacijos PHP failas `public/` | Paprastai užtenka atnaujinti naršyklę. |
| Env arba Compose | `make check`, tada `make up`. |
| Tik prijungto `.ini`, `.cnf` ar `.conf` turinys | Perkrauti atitinkamą servisą; `make up` nebūtinai perkurs nepakeistą konteinerį. Žr. [komandų skyrių](11-komandos-ir-klaidos.md). |
| PHP versija, plėtinys ar Dockerfile | `make build`, tada `make up`. |
| Domenas | Atnaujinti env, `make bootstrap`; PS atveju dar pritaikyti DB domeną, žr. [DB skyrių](07-duomenu-baze.md). |
| Nori švarių testinių duomenų | Importuoti pasirinktą dump į `ENV=test`, po jo aiškiai įkelti fixtures. |
