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

Patikra: `make doctor` su PS profiliu patikrina DB prisijungimą. Naršyklėje atidaryk `http://forsena.local/`.

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

Situacija: nori paruošti naują projektą `demo`, o paleisti jį vėliau.

```bash
cd ~/Projects/setup
./create-project demo
```

`demo` pakeisk savo pavadinimu. Galima rašyti didžiosiomis ir mažosiomis ASCII
raidėmis: sistema atskiria žodžius, išlaiko santrumpas viename žodyje ir techninius
vardus paverčia mažosiomis raidėmis.

| Įvedi | Katalogas, `PROJECT_NAME` | DB vardas ir vartotojas | PhpStorm pavadinimas | Adresas |
| --- | --- | --- | --- | --- |
| `Melga` | `melga` | `melga` | `Melga` | `http://melga.local` |
| `MelgaMCP` | `melga-mcp` | `melga_mcp` | `MelgaMCP` | `http://melga-mcp.local` |
| `GameroomAkeneo` | `gameroom-akeneo` | `gameroom_akeneo` | `GameroomAkeneo` | `http://gameroom-akeneo.local` |

```bash
./create-project MelgaMCP
# Sukuria ../melga-mcp/app; PhpStorm vardas – MelgaMCP.
```

Normalizuotas vardas turi prasidėti raide ir būti iki 32 simbolių. Leidžiami
skaičiai ir pavieniai `_` arba `-`; žodžiai katalogo, Docker ir domeno varduose
atskiriami brūkšneliais. DB vardui ir vartotojui naudojami pabraukimai, kad būtų
patogiau rašyti SQL. Esami projektų katalogai automatiškai nepervadinami. Jei toks
adresas jau priklauso kitam projektui, kūrimas sustoja prieš failų rašymą.

`env/common.env` saugomas tik `PROJECT_NAME=melga-mcp`. Originalus PhpStorm vardas
`MelgaMCP` įrašomas į `.idea/.name`; atskiro env lauko nereikia.
Paleidžiant ar kartojant kūrimą su techniniu vardu originali rašyba išlieka:

```bash
./create-project melga-mcp --start
```

Komanda tik sukuria failus:

1. Paruošia `~/Projects/demo/app` su `Makefile`, `.gitignore`, `compose/base.yaml` ir env failais. Naudojamas bendras setup Dockerfile.
2. Sukuria `public/index.php` su bandomuoju puslapiu.
3. Sugeneruoja privatų DB slaptažodį. Portai paskiriami vėliau per `make bootstrap`.
4. Įrašo `DOMAIN=demo.local` ir DB prisijungimus į `env/local.env`.

Naujo projekto šaltiniai:

```text
app/
├── Makefile
├── .gitignore
├── compose/
│   └── base.yaml
├── env/
│   ├── common.env
│   └── local.env
└── public/
    └── index.php
```

Dar sukuriamas `.idea/.name` su projekto pavadinimu, todėl atidarius `app/`
PhpStorm gali jį rodyti kaip `demo`, nelaukiant Docker paleidimo. Kitų IDE nustatymų
kūrimo metu nepridedama. Jei projektą jau buvai atidaręs be šio failo, pakartok
`./create-project demo`, uždaryk projektą ir atidaryk iš naujo. Jau esamas tavo
pasirinktas pavadinimas neperrašomas.

Taip pat sukuriama ignoruojama techninė žyma `.generated/create-project.json`, kad
pakartotinė komanda atpažintų savo projektą. `local.env.example` nekuriamas;
tikras `local.env` į Git nepatenka.

Po Git klonavimo susikurk `env/local.env`. Pavyzdžiui, projektui `demo`:

```dotenv
DOMAIN=demo.local
DATABASE_USER=demo
DATABASE_NAME=demo
DATABASE_PASSWORD=cia-irasyk-savo-lokalu-slaptazodi
```

Slaptažodį pakeisk savu, o failo teises apribok komanda `chmod 600 env/local.env`.
Jei naudoji jau sukurtą DB, įrašyk jos prisijungimus. Naujai DB šie prisijungimai
bus nustatyti pirmą kartą ją paleidžiant. Portus paruoš `make bootstrap`.

Redis, QA, Doctrine, fixtures, schemos ir prod pavyzdžių nei failai, nei katalogai
nekuriami. Projekto Dockerfile kopijos taip pat nėra. Paleidžiant paruošiami tik
bendrų servisų naudojami tušti `config/php/local`, `config/mysql/local`,
`config/apache/local`, `config/nginx-proxy/local` katalogai, vykdymo duomenys ir IDE
nustatymai. Konfigūracijos `.ini` ar `.conf` failus pridėsi tik tada, kai jų reikės.

**Konteineriai nepaleidžiami, atvaizdai nekuriami, sistemos Nginx, DNS ir TLS tuo
metu nekeičiami.** Failų sukūrimui pakanka Python 3.10+; jo komandos pačiam rašyti
nereikia. PhpStorm gali atidaryti sukurtą `app` katalogą iš karto.

### Kai nori paleisti

```bash
./create-project demo --start
```

Šis aiškus pasirinkimas paruošia DNS, TLS, kompiuterio Nginx maršrutą ir PhpStorm
Docker nustatymus, sukuria atvaizdus, paleidžia keturis pagrindinius servisus ir
sulaukia konteinerių sveikatos patikrų. Naršyklės adresas išlieka **http://demo.local/**.

Pavadinus projektą `melga`, adresas bus **http://melga.local/**. Kiekvienas
projektas gauna atskirus Docker portus, bet jų naršyklėje rašyti nereikia:
kompiuterio Nginx pagal domeną nukreipia į reikiamą projektą. Taip išsaugomas senas
adresų formatas ir keli projektai gali veikti kartu.

Kompiuteryje turi būti įdiegti Make, Docker/Compose, OpenSSL, mkcert ir Nginx su
`sites-available` / `sites-enabled`; Docker bei host Nginx turi veikti. Sistemos
failų paruošimui gali reikėti `sudo`, o pirmą kartą – `mkcert -install`.
Jei teisės nepakankamos, komanda parodo konkretų veiksmą; jį atlikęs kartok su
`--start`. Tai vienkartinis kompiuterio paruošimas, ne rankinis kiekvieno projekto
Compose ar env kūrimas.

Host maršrutą taip pat paruošia `make bootstrap`. Nginx konfigūracija patikrinama
su `nginx -t` prieš `reload`; svetimas esamas to domeno config neperrašomas.
Atskiro env jungiklio nereikia: domenas ir portai paruošiami kartu per `bootstrap`.

### Pakartojimas

```bash
./create-project demo
```

Esami failai, kodas ir prisijungimai išsaugomi. Pakartojimas irgi nieko nepaleidžia.
Ankstesnis `--no-start` vis dar priimamas, tačiau jo nebereikia. Esamas svetimas
katalogas neperimamas. Jei anksčiau sugeneruotas projektas turi `.localhost` adresą,
generatorius jo savavališkai neperrašo: pakeisk `DOMAIN` į `<vardas>.local`
ir atlik `make bootstrap`.

### Kur tęsti darbą

PhpStorm atidaryk `~/Projects/demo/app`, o savo aplikaciją laikyk `public/`.
Generatoriaus puslapį gali pakeisti savo kodu. Jei vietoje jo klonuosi atskirą
Git repozitoriją, pirmiau pašalink arba perkelk tik šį bandomąjį failą, kad klonavimo
katalogas būtų tuščias. Puslapį tikrink naršyklėje arba aplikacijos testais.

```bash
cd ~/Projects/demo/app
make up
make logs service=php-fpm
make down
```

`make down` sustabdo aplinką ir pašalina jos konteinerius, o DB failai lieka
`~/Projects/demo/data`. Kitų projektų aplinkos neliečiamos.

Pagal nutylėjimą tai bendras PHP projektas (be `PROFILE` įrašo) su Nginx, Apache, PHP ir
MySQL. Papildomi servisai net neįtraukti į Compose. Kai prireiks konkretaus
papildymo, tik jo failus nukopijuok iš bendro setup šablonų ir įtrauk į projektą
pagal [servisų vadovą](05-servisai.md). Vien `PROFILES=redis` naujame minimaliame
projekte Redis nepridės. Cache valdymas veikia ir be Redis; žr.
[hibridines aplinkas](12-cache-ir-hibridines-aplinkos.md).

**PrestaShop atvejis:** komanda neįdiegia parduotuvės ir negeneruoja jos tikrų
raktų. Kai į `public/` perkeliama jau įdiegta PS aplikacija, jos konfigūracija ir DB,
`env/common.env` nustatyk `PROFILE=prestashop`. Tada taikyk B dalies DB atkūrimo veiksmus
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
projektus. Paleidimui visada reikia aiškaus `--start` pasirinkimo.

## Kada ką kartoti

| Kas pasikeitė | Ką daryti |
| --- | --- |
| Aplikacijos PHP failas `public/` | Paprastai užtenka atnaujinti naršyklę. |
| Env arba Compose | `make check`, tada `make up`. |
| Tik prijungto `.ini`, `.cnf` ar `.conf` turinys | Perkrauti atitinkamą servisą; `make up` nebūtinai perkurs nepakeistą konteinerį. Žr. [komandų skyrių](11-komandos-ir-klaidos.md). |
| PHP versija, plėtinys ar Dockerfile | `make build`, tada `make up`. |
| Domenas | Atnaujinti env, `make bootstrap`; PS atveju dar pritaikyti DB domeną, žr. [DB skyrių](07-duomenu-baze.md). |
| Nori švarių testinių duomenų | Importuoti pasirinktą dump į `ENV=test`, po jo aiškiai įkelti fixtures. |
