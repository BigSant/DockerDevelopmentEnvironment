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

## C. Naujas projektas viena komanda

Situacija: nori naujo projekto `demo`, kad iš karto galėtum atidaryti veikiantį puslapį.

```bash
cd ~/Projects/setup
./create-project demo
```

Tai visa projekto sukūrimo ir pirmo paleidimo komanda. `demo` pakeisk savo pavadinimu,
pavyzdžiui `parduotuve-a`. Naudok mažąsias raides, skaičius ir pavienius brūkšnelius;
pavadinimas turi prasidėti raide ir būti iki 32 simbolių.

Komanda pati:

1. Sukuria `~/Projects/demo/app` su `Makefile`, projekto `Dockerfile`, `env/`, `compose/`, `config/`, `database/` ir `qa/`.
2. Sukuria `public/index.php` – bandomąjį puslapį su projekto vardu, PHP versija ir cache režimu.
3. Parenka laisvus vietinius HTTP/HTTPS portus, domeną `demo.localhost`, DB vardą ir atsitiktinį slaptažodį. Prisijungimai lieka privačiame `env/local.env` su `600` teisėmis.
4. Paruošia vietinį TLS ir PhpStorm projekto nustatymus.
5. Patikrina konfigūraciją, sukuria Docker atvaizdus ir paleidžia Nginx, Apache, PHP bei MySQL.
6. Palaukia servisų, patikrina bandomąjį puslapį ir išspausdina jo adresą.

Pabaigoje pamatysi, pavyzdžiui:

```text
Projektas veikia: http://demo.localhost:31820/
PhpStorm atidaryk: /home/tomas/Projects/demo/app
Aplikacijos kodas: /home/tomas/Projects/demo/app/public
```

Naudok komandos parodytą adresą: portą ji parenka automatiškai. Pirmas build gali
užtrukti, kol Docker atsisiunčia ir sukuria atvaizdus.

**Vienkartinis kompiuterio paruošimas.** Turi būti įdiegti šio skyriaus B dalyje
nurodyti Make, Python, Docker/Compose, OpenSSL ir mkcert. Docker turi veikti.
Jei trūksta pasitikėjimo vietiniu sertifikatu ar teisių domeno įrašui, komanda
parodys konkretų veiksmą. Jį atlikęs pakartok `./create-project demo` – jau paruošti
failai, aplikacija ir slaptažodis neperrašomi. Esamas svetimas katalogas neperimamas.

Nereikia pačiam paleisti `.py`, kopijuoti env ar kurti bandomojo PHP failo.
Bendras `setup` lieka vienas ir nepridedamas į projekto Git kaip submodulis.

### Jei nori tik failų

```bash
./create-project demo --no-start
```

Tai sukuria struktūrą, bandomąjį puslapį ir vietinius nustatymus, bet Docker,
TLS ir PhpStorm paruošimą atideda. Vėliau paleisk:

```bash
./create-project demo
```

### Kur tęsti darbą

PhpStorm atidaryk `~/Projects/demo/app`, o savo aplikaciją laikyk `public/`.
Generatoriaus puslapį gali pakeisti savo kodu. Jei vietoje jo klonuosi atskirą
Git repozitoriją, pirmiau pašalink arba perkelk tik šį bandomąjį failą, kad klonavimo
katalogas būtų tuščias. Atnaujink arba išvalyk `SMOKE_EXPECT` savo `env/local.env`,
kai naujas puslapis neberodys projekto vardo; `SMOKE_URL` turi tikrinti tavo aplikaciją.

```bash
cd ~/Projects/demo/app
make up
make logs service=php-fpm
make down
```

`make down` sustabdo aplinką ir pašalina jos konteinerius, o DB failai lieka
`~/Projects/demo/data`. Kitų projektų aplinkos neliečiamos.

Pagal nutylėjimą tai bendras PHP projektas (`PROFILE=`), o papildomi Redis,
Mailpit ir QA servisai išjungti. Jų konfigūracijos failų buvimas nereiškia, kad
servisai paleisti. Redis ar cache bandymams naudok
[hibridinės aplinkos nustatymus](12-cache-ir-hibridines-aplinkos.md).

**PrestaShop atvejis:** komanda neįdiegia parduotuvės ir negeneruoja jos tikrų
raktų. Kai į `public/` perkeliama jau įdiegta PS aplikacija, jos konfigūracija ir DB,
`env/common.env` nustatyk `PROFILE=ps`. Tada taikyk B dalies DB atkūrimo veiksmus
ir `make build`, `make up`. Tuščiam bandomajam puslapiui PS profilio nereikia.

## D. Paruošti daugiau projektų

Kiekvienas pavadinimas sukuria atskirą projektą šalia bendro setup:

```bash
cd ~/Projects/setup
./create-project parduotuve-a
./create-project parduotuve-b
```

Projektai gauna atskirus vardus, portus, prisijungimus, kodo ir duomenų katalogus.
Komandą galima iškviesti ir absoliučiu keliu iš bet kurio katalogo:

```bash
/home/tomas/Projects/setup/create-project demo
```

Projekto vietą lemia `setup` vieta, ne dabartinis terminalo katalogas.
Esamų repozitorijų paruošimui ir senos struktūros perkėlimui skirtas atskiras
[šaltinių generatorius](../PROJECT_TEMPLATES.md); `create-project` kuria tik naujus
projektus arba tęsia savo anksčiau sukurtų projektų paleidimą.

## Kada ką kartoti

| Kas pasikeitė | Ką daryti |
| --- | --- |
| Aplikacijos PHP failas `public/` | Paprastai užtenka atnaujinti naršyklę. |
| Env arba Compose | `make check`, tada `make up`. |
| Tik prijungto `.ini`, `.cnf` ar `.conf` turinys | Perkrauti atitinkamą servisą; `make up` nebūtinai perkurs nepakeistą konteinerį. Žr. [komandų skyrių](11-komandos-ir-klaidos.md). |
| PHP versija, plėtinys ar Dockerfile | `make build`, tada `make up`. |
| Domenas | Atnaujinti env, `make bootstrap`; PS atveju dar pritaikyti DB domeną, žr. [DB skyrių](07-duomenu-baze.md). |
| Nori švarių testinių duomenų | Importuoti pasirinktą dump į `ENV=test`, po jo aiškiai įkelti fixtures. |
