# 2. Pirmas paleidimas

[Turinys](README.md) · [Atgal](01-struktura.md) · [Toliau: nustatymai](03-nustatymai.md)

Darbas susideda iš dviejų žingsnių:

1. **Kompiuteris** paruošiamas vieną kartą: `setup/install-host` (sudo slaptažodis įvedamas vieną kartą).
2. **Projektas** paruošiamas viena komanda: `make setup` (be sudo, DB neliečia).

## A. Kompiuterio paruošimas vieną kartą

Reikia Linux (Debian/Ubuntu tipo, nes trūkstami paketai diegiami per `apt-get`), Python 3.10+ ir Docker Engine su Compose 2.24.4+. Docker turi veikti tavo vartotojui: terminale turi pavykti `docker info`. Docker diegimas: [Docker Engine](https://docs.docker.com/engine/install/).

Atsisiųsk bendrą setup, jei jo dar nėra:

```bash
mkdir -p ~/Projects
git clone git@github.com:BigSant/DockerDevelopmentEnvironment.git ~/Projects/setup
```

Paleisk kompiuterio paruošimą kaip įprastas vartotojas:

```bash
~/Projects/setup/install-host
```

Komanda vieną kartą paprašo sudo slaptažodžio ir:

- patikrina Docker, Compose ir Python versijas;
- įdiegia trūkstamus įrankius: `make`, `openssl`, `mkcert`, `nginx`, `rsync`, `git`, `curl`;
- įdiegia root priklausančias DNS ir Nginx pagalbinių scriptų kopijas į `/usr/local/lib/setup`;
- įrašo siaurą sudo taisyklę `/etc/sudoers.d/shared-setup`, kuri leidžia be slaptažodžio vykdyti **tik** tas dvi kopijas;
- paleidžia `mkcert -install`, kad naršyklė pasitikėtų vietiniais sertifikatais;
- įjungia kompiuterio Nginx, kuris pagal domeną nukreipia į reikiamą projektą.

Jei nori tik pažiūrėti, ko trūksta, nieko nekeisdamas:

```bash
~/Projects/setup/install-host --check
```

Po setup atnaujinimo `make setup` ar `make bootstrap` gali parašyti, kad pagalbiniai scriptai pasikeitė. Tada dar kartą paleisk `install-host`.

## B. Esamas projektas iš švaraus clone'o

Situacija: kolega jau turi projektą, tu nori jį paleisti savo kompiuteryje. Pavyzdys su Forsena, kur aplinka ir parduotuvė yra atskiros repozitorijos:

```bash
git clone git@bitbucket.org:KOMANDA/forsena_docker.git ~/Projects/forsena/app
git clone git@bitbucket.org:KOMANDA/forsena.git ~/Projects/forsena/app/public
cd ~/Projects/forsena/app
make setup
```

`KOMANDA` pakeisk tikru Bitbucket workspace vardu. Bendras setup turi būti `~/Projects/setup` (šalia projektų katalogo).

`make setup` rodo, kuris žingsnis vykdomas, ir sustoja ties pirma klaida:

```text
==> [1/5] init
==> [2/5] bootstrap
==> [3/5] build
==> [4/5] app-prepare
==> [5/5] up
```

| Žingsnis | Kas vyksta |
| --- | --- |
| `init` | Sukuria privatų `env/local.env` iš `env/local.env.example`, jei jo dar nėra. Sugeneruoja trūkstamą `DATABASE_ROOT_PASSWORD` ir (PrestaShop) trūkstamus PS raktus. Sukuria duomenų katalogus. |
| `bootstrap` | Parenka laisvus portus, sukuria vietinį DNS įrašą, mkcert sertifikatą, kompiuterio Nginx maršrutą (`demo.local`, taip pat `pma.`, `mailpit.`, `cron.`, `dbmonitor.` poddomenius) ir PhpStorm nustatymus. Jei esamam maršrutui trūksta naujesnių poddomenių, jį atnaujina (be `install-host` tik įspėja). Kai projektas turi `SCHEMA_DIRECTORY`, įdiegia schemos commit hook'ą. |
| `build` | Sukuria image'us. Perkuriami tik tie, kurių šaltiniai pasikeitė. |
| `app-prepare` | Paruošia aplikaciją pagal `PROFILE`. PrestaShop: nukopijuoja `PS_RELEASE_PATHS` iš oficialaus release ir įdiegia `config/composer` paketus. Kitiems profiliams nieko nedaro. `make app-prepare` pakartoja tik šį žingsnį (pvz. pakeitus `PS_RELEASE_PATHS`); jis eina po `build`, nes failus ima iš PHP image'o. |
| `up` | Patikrina konfigūraciją ir paleidžia servisus, laukia jų sveikatos patikrų. |

Pabaigoje `make setup` tik praneša DB būseną. Jei DB tuščia, pasiūlo komandas:

```text
Setup complete. Database forsena is empty — load data deliberately:
  make db-load set=dev          # versioned dev data set
  make db-import file=dump.sql  # a LIVE dump
```

DB užkeli sąmoningai, žr. [duomenų bazės skyrių](07-duomenu-baze.md). Po to atidaryk `https://forsena.local/` (projektui `demo` – `https://demo.local/`).

### Pakartotinis make setup

Komandą saugu paleisti bet kada, kai „kažkas neveikia“. Esamos env reikšmės, slaptažodžiai, sertifikatai ir DB išsaugomi. Image'ai perkuriami tik pasikeitus jų šaltiniams. `PS_RELEASE_PATHS` katalogai vėl sinchronizuojami su release.

Atskirus žingsnius gali kartoti ir pavieniui:

```bash
make init
make bootstrap
make build
make up
```

`app-prepare` atskiros `make` komandos neturi: jį pakartoja `make setup`.

### Jei make setup sustojo

| Pranešimas | Ką daryti |
| --- | --- |
| `Local DNS entry is missing. Run setup/install-host once…` | Kompiuteris neparuoštas: paleisk `~/Projects/setup/install-host`, tada vėl `make setup`. |
| `The host helpers changed with this setup version; rerun setup/install-host` | Setup atnaujintas: dar kartą `install-host`. |
| `Create …/env/local.env from its example` | Projekte nėra `env/local.env.example`. Susikurk `env/local.env` pats (žr. žemiau). |
| `Application checkout is missing` | Nėra `app/public`. Klonuok aplikacijos repozitoriją. |
| `PS_VERSION=… does not match the shop code` | Env versija nesutampa su kodu. Pataisyk `PS_VERSION` arba atnaujink kodą. |

Kitos klaidos: [komandų ir klaidų skyrius](11-komandos-ir-klaidos.md#dažniausios-klaidos).

Jei projekte nėra pavyzdinio env failo, `env/local.env` gali susikurti pats:

```dotenv
DOMAIN=demo.local
DATABASE_USER=demo
DATABASE_NAME=demo
DATABASE_PASSWORD=cia-irasyk-savo-lokalu-slaptazodi
```

Teises apribok `chmod 600 env/local.env`. Portus, `DATABASE_ROOT_PASSWORD` ir PS raktus įrašys `make setup`.

## C. Naujas projektas su ./create-project

`create-project` tik sukuria naujo projekto failus. Jis nediegia parduotuvės, neatsisiunčia kodo ir nieko nepaleidžia.

### Klausimai terminale

```bash
cd ~/Projects/setup
./create-project
```

Komanda paklausia, o kiekvienas klausimas turi numatytąjį atsakymą (Enter):

| Klausimas | Numatyta | Pastaba |
| --- | --- | --- |
| Projekto vardas | – | Pvz. `demo`, `Melga`, `GameroomAkeneo`. |
| Profilis | `prestashop` | `prestashop` arba `generic` (bendras PHP). |
| PrestaShop versija | `9.0.2` | Klausiama tik `prestashop` profiliui. |
| PHP versija | naujausia tinkama | Siūlomos tik tos versijos, kurias palaiko PS šaka ir kurioms setup turi image'ą. |
| DB variklis | `mysql` | `mysql` arba `mariadb`. |
| DB versija | naujausia tinkama | Iš suderinamumo lentelės. |
| Servisai | `pma mailpit` | Sąrašas per tarpus, `-` – jokių. |

Prieš rašant failus parodoma santrauka (vardas, domenas, profilis, versijos, servisai, laiko zona `Europe/Vilnius`) ir klausimas `Create these files? [Y/n]`. Atšaukus niekas nesukuriama. Domenas išvedamas iš vardo (`demo.local`), slaptažodžiai sugeneruojami, portus vėliau parenka `make setup`.

`./create-project demo` terminale paklaus tik likusių klausimų.

### Tie patys atsakymai parametrais

```bash
./create-project demo --profile prestashop --ps-version 9.0.2 --php-version 8.4 \
  --database mysql --database-version 8.4 --services "pma mailpit"
```

Kai pateikti visi parametrai, komanda nieko neklausia. `--yes` praleidžia patvirtinimo klausimą. `--services ""` reiškia „jokių papildomų servisų“. Nesuderinama PHP ir PS pora atmetama prieš rašant failus.

Ne terminale (pvz. scripte) vardas privalomas. Su vien vardu ir be kitų parametrų sukuriamas minimalus bendro PHP projektas.

### Kas sukuriama

```text
~/Projects/demo/app/
├── Makefile
├── .gitignore
├── env/common.env            # PROJECT_NAME, PROFILE, PS_VERSION, PHP_VERSION, DB variklis ir versija, SERVICES
├── env/local.env             # DOMAIN, DB vardas, vartotojas, slaptažodis, DATABASE_ROOT_PASSWORD
├── .idea/.name               # PhpStorm projekto pavadinimas
└── public/index.php          # Tik generic profiliui: bandomasis puslapis
```

Dar sukuriama ignoruojama žyma `.generated/create-project.json`, kad pakartota komanda atpažintų savo projektą. Pakartojus `./create-project demo` esami failai ir slaptažodžiai išsaugomi. Svetimas esamas katalogas neperimamas.

**PrestaShop projektui `public/` nekuriamas:** ten klonuok parduotuvės repozitoriją ir paleisk `make setup`:

```bash
git clone git@bitbucket.org:KOMANDA/demo-shop.git ~/Projects/demo/app/public
make -C ~/Projects/demo/app setup
```

### Vardų taisyklės

| Įvedi | Katalogas, `PROJECT_NAME` | DB vardas ir vartotojas | PhpStorm pavadinimas | Adresas |
| --- | --- | --- | --- | --- |
| `Melga` | `melga` | `melga` | `Melga` | `melga.local` |
| `MelgaMCP` | `melga-mcp` | `melga_mcp` | `MelgaMCP` | `melga-mcp.local` |
| `GameroomAkeneo` | `gameroom-akeneo` | `gameroom_akeneo` | `GameroomAkeneo` | `gameroom-akeneo.local` |

Normalizuotas vardas prasideda raide ir turi iki 32 simbolių. Leidžiami skaičiai ir pavieniai `_` ar `-`. Jei toks domenas jau priklauso kitam projektui, kūrimas sustoja prieš rašant failus. Originali rašyba saugoma `.idea/.name`.

### Generic projektą iš karto paleisti

```bash
./create-project demo --profile generic --services "" --start
```

`--start` dar patikrina, ar įdiegti `make`, `docker`, `openssl`, `mkcert` ir ar veikia Docker, tada paleidžia `make bootstrap`, `make check`, `make build` ir `make up`. Adresas: `http://demo.local/`. PrestaShop projektui `--start` netinka, nes dar nėra parduotuvės kodo: klonuok jį ir naudok `make setup`.

## D. Kada ką kartoti

| Kas pasikeitė | Ką daryti |
| --- | --- |
| Aplikacijos PHP failas `public/` | Paprastai užtenka atnaujinti naršyklę. |
| Env reikšmė (limitai, `XDEBUG_MODE`, `TZ`, IP sąrašai) | `make restart` arba `make up`. Image'o perkurti nereikia. |
| Drop-in failas `config/<servisas>/` | `make restart service=<servisas>`. |
| `SERVICES` sąrašas | `make up` (naujiems servisams pirmą kartą gali reikėti `make build`). |
| PHP versija, `PS_RELEASE`, projekto `Dockerfile` | `make build`, tada `make up`. |
| Domenas | Pakeisk `DOMAIN` ir paleisk `make bootstrap`. PS DB domeną pakeičia [after-import](07-duomenu-baze.md). |
| Setup atnaujintas | `make setup`. |
