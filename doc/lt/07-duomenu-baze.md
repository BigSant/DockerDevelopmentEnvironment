# 7. DB: nuo dump iki schemos ir testinių duomenų

[Turinys](README.md) · [Atgal](06-aplikacija.md) · [Toliau: kodo kokybė](08-kodo-kokybe.md)

## Pirmiausia atskirk keturis dalykus

| Dalykas | Paprastai | Kur laikomas |
| --- | --- | --- |
| Dump / backup | DB būsenos kopija: struktūra ir duomenys, priklausomai nuo dump | Privatus `.sql` / `.sql.gz`; setup backup – `.generated/backups`. |
| Schema | „Kokie stalčiai turi būti“: lentelės, stulpeliai, indeksai | `database/schema`. |
| Migracija | „Kaip pakeisti stalčius“: konkretus DB pakeitimas | `database/doctrine/versions`. |
| Fixture | „Ką įdėti į stalčius“: sutarti bandomieji įrašai | `database/fixtures/<rinkinys>`. |

`database/after-import` yra dar viena paskirtis: pritaikyti ką tik atkurtą DB pasirinktos aplinkos domenui, paštui ir pan. Serverio `.cnf` lieka `config/mysql` arba `config/mariadb`.

Visoms šiame skyriuje naudojamoms komandoms sukonfigūruok `env/common.env`:

```dotenv
POST_IMPORT_SQL_DIRECTORY=app/database/after-import
SCHEMA_DIRECTORY=app/database/schema
FIXTURES_DIRECTORY=app/database/fixtures
```

Keliai skaičiuojami nuo `<projektas>/`. Standartinis DB serviso vardas turi būti `database`. Komandos naudoja veikiančio konteinerio prisijungimus ir tikrina, ar jo DB vardas sutampa su pasirinktu projektu.

## A. Importuoti gautą parduotuvės dump

```bash
make db-prepare
make db-import-plan file=/kelias/parduotuve.sql.gz
make db-import file=/kelias/parduotuve.sql.gz
make up
```

`db-prepare` paleidžia tik DB. Planas parodo failų eiliškumą, bet SQL nevykdo. Dump turi būti netuščias `.sql` arba `.sql.gz`; zip, tar ir MySQL duomenų katalogas šiai komandai netinka.

`db-import` tvarka:

```text
1. Dump
2. database/after-import/common/*.sql
3. database/after-import/<ENV>/*.sql
4. Tik jeigu paprašyta: fixtures/common/*.sql
5. Tik jeigu paprašyta: fixtures/<pasirinktas-rinkinys>/*.sql
```

Kiekvienoje grupėje failai vykdomi pagal vardą, pvz. `010-domain.sql`, `020-mail.sql`. `2-mail.sql` ir `10-domain.sql` nėra patikima numeravimo schema – naudok vienodo ilgio numerius.

Dump importas nėra automatinis visos DB išvalymas: kas bus ištrinta ar pakeista, lemia dump SQL. Kitam serveriui skirtas `USE kita_db` ar `CREATE DATABASE` faile taip pat yra SQL, todėl importuok tik suprantamą savo pasirinktai aplinkai skirtą dump.

Prieš vykdymą tikrinami įvesties failai ir gzip vientisumas. `.sql.gz` prieš pirmą DB rašymą dar išskleidžiamas į privatų laikiną failą `.generated` viduje; reikia vietos ir **nesuspaustam** SQL. Klaida sustabdo kitus failus, tačiau jau įvykdyti SQL pakeitimai automatiškai neatšaukiami.

## B. Prieš importą turėti grįžimo kopiją

```bash
make db-backup
```

Rezultatas – naujas privatus `.generated/backups/<projektas>-<ENV>-<laikas>.sql.gz` failas. Norėdamas pasirinkti vardą:

```bash
make db-backup file=/privatus/kelias/pries-modulio-atnaujinima.sql.gz
```

Esamas failas neperrašomas. Nepavykusi arba tuščia kopija nepublikuojama galutiniu vardu. Failo teisės `0600`. Kopija apima lenteles ir duomenis, taip pat dump kliento palaikomus routines/events/triggers; vartotojui reikia tam teisių.

Sujungtas veiksmas:

```bash
make db-import file=/kelias/naujas-dump.sql.gz backup=1
```

Kopija sukuriama prieš pirmą importo SQL. Jai nepavykus, importas nepradedamas. Importai ir fixtures toje pačioje projekto aplinkoje naudoja bendrą užraktą, todėl jų vienu metu nepaleisi. Atskirai paleistas `db-backup` nėra tas importo užraktas: kopijos metu neplanuok schemos keitimo.

`--single-transaction` padeda kopijuoti transakcines lenteles, bet tai nėra visų įmanomų DB variklių ir kartu vykdomų DDL nuoseklumo garantija. Svarbiam atnaujinimui pristabdyk rašymą ir patikrink atkūrimą atskiroje DB.

Atkūrimas naudoja tą patį importą, taigi **po atkūrimo vėl vykdomi after-import SQL**. Jei nori tiksliai atkurti kopiją be pritaikymų, suplanuok atskirą projekto importo aplinką su tuščiu after-import katalogu; nelaikyk įprasto importo „baitas į baitą“ grąžinimu.

## C. Po importo pakeisti PS domeną ir išjungti laiškus

Situacija: dump gautas iš tikros parduotuvės, vietoje domenas `demo.localhost:31820`.

`env/local.env`:

```dotenv
DOMAIN=demo.localhost
SQL_DOMAIN=demo.localhost:31820
```

`database/after-import/local/010-local-shop.sql`:

```sql
UPDATE ps_configuration
SET value = '${DOMAIN}'
WHERE name IN ('PS_SHOP_DOMAIN', 'PS_SHOP_DOMAIN_SSL');

UPDATE ps_shop_url
SET domain = '${DOMAIN}', domain_ssl = '${DOMAIN}',
    physical_uri = '/', virtual_uri = '';

UPDATE ps_configuration
SET value = '0'
WHERE name IN ('PS_SSL_ENABLED', 'PS_SSL_ENABLED_EVERYWHERE');

UPDATE ps_configuration
SET value = '3'
WHERE name = 'PS_MAIL_METHOD';
```

Čia `${DOMAIN}` pakeičiamas `SQL_DOMAIN`, o jei jo nėra – `DOMAIN`. Tai ribotas hostname/porto pakeitimas, **ne universalus visų env kintamųjų SQL šablonų variklis**. Rašyk `${DOMAIN}` tik ten, kur reikia domeno teksto.

Pavyzdys skirtas vienos parduotuvės DB su `ps_` prefiksu. Jei prefiksas kitoks, pakeisk lentelių vardus. Multistore atveju `ps_shop_url` UPDATE apribok konkrečiu `id_shop`; neperrašyk visų parduotuvių adresų vienu pavyzdžiu.

Failas nevykdomas nuo `make up` ar MySQL konteinerio starto. Jis vykdomas **po `make db-import`**. Pakeisti jį ir perkrauti konteinerį nepakanka jau importuotai DB.

Jei dump iš naujo importuoti nereikia, gali aiškiai vykdyti peržiūrėtą vienkartinį SQL DB klientu. Jei veiksmą nori kartoti per Make, sukurk atskirą fixture rinkinį, pvz. `database/fixtures/domain-fix`, į jį įdėk tik norimą SQL, ir pirmiausia patikrink `make db-fixtures-plan set=domain-fix`. Atmink: kartu bus vykdomi ir `fixtures/common` failai.

## D. Skirtingi vietiniai ir testiniai fixture duomenys

Situacija: savo bandomoje aplikacijoje nori matyti tris kontaktus vietoje ir vieną tiksliai žinomą kontaktą testuose. Pavyzdys naudoja atskirą mokomąją lentelę, o ne nepilnai sukurtus PS klientus.

Lentelę pirma turi sukurti aplikacijos migracija arba tavo bandomos DB SQL:

```sql
CREATE TABLE demo_contact (
    code varchar(32) NOT NULL PRIMARY KEY,
    email varchar(190) NOT NULL,
    label varchar(100) NOT NULL
);
```

`database/fixtures/common/010-support.sql`:

```sql
INSERT INTO demo_contact (code, email, label)
VALUES ('support', 'support@example.invalid', 'Pagalba')
ON DUPLICATE KEY UPDATE email = 'support@example.invalid', label = 'Pagalba';
```

`database/fixtures/local/010-local-users.sql`:

```sql
INSERT INTO demo_contact (code, email, label)
VALUES ('local-a', 'a@example.invalid', 'Vietinis A'),
       ('local-b', 'b@example.invalid', 'Vietinis B')
ON DUPLICATE KEY UPDATE label = VALUES(label);
```

`database/fixtures/test/010-checkout-contact.sql`:

```sql
INSERT INTO demo_contact (code, email, label)
VALUES ('checkout', 'checkout@example.invalid', 'Testinis pirkėjas')
ON DUPLICATE KEY UPDATE email = 'checkout@example.invalid', label = 'Testinis pirkėjas';
```

Vietoje:

```bash
make db-fixtures-plan set=local
make db-fixtures-load set=local
```

Testinėje DB:

```bash
make db-fixtures-plan ENV=test set=test
make db-fixtures-load ENV=test set=test
```

Patikra: DB klientu atlik `SELECT code, email FROM demo_contact ORDER BY code;`. Vietiniame variante tikėkis `support`, `local-a`, `local-b`; testiniame – `support`, `checkout`, jei pradėjai nuo tuščios lentelės.

`ENV` pasirenka DB, o `set` pasirenka duomenų rinkinį. `make db-fixtures-load ENV=local set=test` tyčia įkelia testinį rinkinį **į vietinę DB**. Šios dvi sąvokos nesujungtos automatiškai.

Savi rinkiniai, pvz. `pristatymas`, leidžiami: mažosios raidės, skaitmenys, `_`, `-`, pirmas simbolis raidė/skaitmuo. Rinkinio katalogas turi egzistuoti. Skaitomi tik tiesioginiai `*.sql` failai, ne gilesni poaplankiai; symlink rinkinių ir fixture failų nenaudok.

Rinkinio pakeitimas neištrina ankstesnių įrašų. Naudok kartojamą SQL su stabiliais raktais arba prieš scenarijų atkurk žinomą dump. Fixtures turi laikyti paruoštus duomenis; `CREATE TABLE` kasdieniuose rinkiniuose nemaišyk su migracijomis.

Po dump automatiškai pridėti pasirinktą rinkinį gali taip:

```bash
make db-import-plan ENV=test file=/kelias/testams.sql.gz db-fixtures=test
make db-import ENV=test file=/kelias/testams.sql.gz db-fixtures=test
```

Senas argumentas `fixtures=test` atmetamas. Teisingas importo argumentas yra `db-fixtures=test`; atskirai įkeliant – `set=test`.

PS klientams, produktams ir užsakymams reikia kelių susijusių lentelių bei versijai tinkamų laukų. Tokius fixtures kurk su aplikacijos įrankiais arba patikrintais jos versijai skirtais SQL. Aukščiau esanti kontaktų lentelė nėra PS kliento kūrimo receptas.

## E. Išsaugoti DB schemą Git

Situacija: modulis pridėjo stulpelį, komanda turi matyti struktūros pakeitimą.

```bash
make schema-export
git diff -- database/schema
git add -A database/schema
make schema-check
```

`database/schema` atsiras `schema.manifest.json` ir po `table-<lentelė>.sql` kiekvienai lentelei. Eksportuojamas `SHOW CREATE TABLE`, ne eilutės. Kintantis lentelės `AUTO_INCREMENT=N` skaitiklis pašalinamas, bet pats auto-increment stulpelio požymis išsaugomas.

`schema-check` lygina veikiančią DB su **Git indeksu** – tuo, ką jau pasirinkai commitui su `git add`. Todėl `schema-export` be `git add` vis dar gali duoti klaidą. Rankomis rašytas README kataloge išsaugomas, o pasenusios anksčiau valdomos lentelių kopijos pašalinamos per eksportą.

Šis mechanizmas apima bazines lenteles. Views, triggers, procedures, functions ir events į schemos snapshot neįtraukiami. Tai skiriasi nuo backup komandos, kuri gali kopijuoti ir dalį šių objektų.

## F. Neleisti commitinti pasenusios schemos

Po sėkmingo eksporto, `git add` ir patikros:

```bash
make schema-hook-install
```

Nuo tada įprastas commitas toje repozitorijoje kvies schemos patikrą. Jei DB pasikeitė, bet neatnaujintas Git indeksas, commitas sustos. Sprendimas: eksportuoti, peržiūrėti, `git add`, patikrinti, tada commitinti.

Forsenoje aplinkos repozitorija yra `app/`, o aplikacijos repozitorija – `app/public/`. Hook prie `app/database/schema` **neblokuoja `app/public` commitų**. Jei reikia tikrinti būtent aplikacijos commitus, rinkis schemos katalogą jos repozitorijoje, pvz. `SCHEMA_DIRECTORY=app/public/database/schema`, ir ten versijuok schemą, arba integruok patikros komandą į aplikacijos esamą hook sistemą.

Installeris nekeičia globalaus Git config, neperrašo svetimo hook ir atsisako `core.hooksPath` valdomos instaliacijos. Į esamą hook galima įrašyti komandą su konkrečiais keliais:

```sh
make -C /home/tomas/Projects/forsena/app ENV=local schema-check
```

Parinktoji DB turi veikti net commito, kuris keičia tik README, metu. Hook yra vietinis ir apeinamas; Bitbucket serveris automatiškai jo nevykdo. Komandos susitarimui reikia atskiros CI patikros. Norėdamas hook pašalinti, pašalink tik šio setup sugeneruotą hook po jo peržiūros; netrink viso `.git/hooks` katalogo.

## G. Doctrine migracijos

Situacija: naujoje aplikacijoje nori aprašyti DB pakeitimus kaip peržiūrimus PHP failus.

1. Prijunk `compose/doctrine.yaml` iš grouped šablono per `PROJECT_COMPOSE_FILES`.
2. Turėk `database/doctrine/connection.php`, `migrations.php` ir `versions/`.
3. Pasirink įrankiui suderinamas PHP/Composer/Doctrine versijas.

`connection.php` pagrindiniai laukai: `driver=pdo_mysql`, `host`, `user`, `password`, `dbname` iš runtime env, `charset=utf8mb4`. PHP `getenv` leidžia nelaikyti slaptažodžio Git faile.

`migrations.php`:

```php
<?php
return array(
    'table_storage' => array('table_name' => 'doctrine_migration_versions'),
    'migrations_paths' => array('DoctrineMigrations' => __DIR__ . '/versions'),
    'all_or_nothing' => false,
    'transactional' => false,
    'check_database_platform' => true,
);
```

`table_storage` pasako, kur registruoti jau įvykdytas migracijas. `migrations_paths` susieja PHP namespace su katalogu. MySQL DDL gali atlikti implicit commit, todėl šablone pasirinktas netransakcinis schemos pakeitimų vykdymas. Pilnas parametrų žinynas: [Doctrine Migrations konfigūracija](https://www.doctrine-project.org/projects/doctrine-migrations/en/3.9/reference/configuration.html).

```bash
make init
make doctrine-build
make db-prepare
make doctrine cmd=status
make doctrine cmd=generate
```

Sugeneruotos klasės `up()` metode, pavyzdžiui, pridėk:

```php
$this->addSql('ALTER TABLE demo_contact ADD phone VARCHAR(32) DEFAULT NULL');
```

`down()` turi aprašyti apgalvotą atšaukimą. Stulpelio ištrynimas sunaikintų jo duomenis, todėl atšaukimo nevertink kaip nemokamo undo.

```bash
make doctrine cmd='migrate --dry-run'
make db-backup
make doctrine cmd='migrate --no-interaction'
make schema-export
git add -A database/schema database/doctrine
make schema-check
```

`generate` sukuria karkasą, o ne perskaito PS ir išgalvoja visas migracijas. `diff` reikia tavo aplikacijos ORM mapping ir schema provider. Įprastas PS, kurio moduliai patys vykdo SQL, to automatiškai neturi. Schema-export leidžia užfiksuoti esamą rezultatą net be Doctrine. Automatinį skirtumo surinkimą be ORM suteikia žemiau aprašyta `make doctrine-diff` komanda.

Doctrine įrankis gali sukurti root savininko failus, priklausomai nuo jo vartotojo. Sugeneruotų failų savininką patikrink prieš commitą. Testinėje aplinkoje writable migracijų katalogą reikia laikyti jos atskiroje vietoje; shared `database/doctrine` mount turi būti read-only, jei tik vykdai jau parašytas migracijas.

## H. Automatiškai surinkti DB pakeitimus be ORM

Situacija: vietoje modulis pridėjo lauką arba pats pakeitei lentelę. Nori gauti
Doctrine migraciją, kuri tą patį pakeitimą vėliau pritaikys STAGE ir LIVE.
`make doctrine-diff` palygina **Git commitą** su **dabartine vietine DB**. Atskirų
STAGE ir LIVE schemos medžių nereikia: schema ir migracijos keliauja su kodo versija.

Įrankis pasirenkamas vienam projektui. Naujo projekto generatorius jo neprideda.
Iš projekto `app` katalogo pasiruošk:

```bash
mkdir -p compose database/doctrine/versions
cp ../../setup/templates/grouped/compose/doctrine.yaml compose/doctrine.yaml
cp ../../setup/templates/grouped/database/doctrine/connection.php database/doctrine/
cp ../../setup/templates/grouped/database/doctrine/migrations.php database/doctrine/
```

Į `env/common.env` įrašyk šias reikšmes. Jei papildomų YAML jau yra, papildyk esamą
sąrašą, jo neperrašyk:

```dotenv
SCHEMA_DIRECTORY=app/database/schema
PROJECT_COMPOSE_FILES=compose/doctrine.yaml
```

Schema ir Doctrine failai turi priklausyti tai pačiai Git repozitorijai. Jei kodas
yra atskiroje `app/public` repozitorijoje, perkelk abu katalogus į ją ir atitinkamai
pakeisk `SCHEMA_DIRECTORY` bei Doctrine YAML konfigūracijos prijungimo šaltinį.
Doctrine katalogas aptinkamas pagal serviso mount, todėl naujo env kelio nereikia.
`migrations.php` turi nurodyti vieną namespace ir migracijų katalogą savo viduje.
Šis failas turi veikti savarankiškai: palyginimo konteineriui perduodamas tik jis,
be `connection.php` ar kitų projekto failų.

**Pirmą kartą, dar prieš DB pakeitimus:**

```bash
make doctrine-build
make doctrine cmd=sync-metadata-storage
make schema-export
git add database/schema database/doctrine compose/doctrine.yaml env/common.env
git commit -m "Record initial schema and Doctrine configuration"
```

`sync-metadata-storage` paruošia Doctrine įvykdytų migracijų lentelę vietinėje DB.
Įrankis naudoja atskirą PHP 8.3, todėl aplikacija gali toliau naudoti savo PHP versiją,
įskaitant seną PrestaShop. Neįrašius pradinės schemos į Git, ankstesnės būsenos
įrankis negali atspėti ir migracijos negeneruos.

**Kai vietinė DB jau pakeista:**

```bash
make doctrine-diff
```

Numatyta pradinė schema imama iš `HEAD`, net jei schema jau eksportuota į darbo
katalogą ar pridėta per `git add`. Jei sąmoningai reikia kito Git taško:

```bash
make doctrine-diff ref=v1.2.0
```

Rezultatas – `database/doctrine/versions/Version<laikas>.php`. Pavyzdžiui, pridėjus
`supplier_code`, jo `up()` turės atitinkamą `ALTER TABLE ... ADD ...`.
Komanda projekto DB nekeičia, schemos neeksportuoja ir failų į Git neprideda.
Pakartojus su tais pačiais duomenimis grąžinamas jau sukurtas failas. Jei yra kita
necommitinta migracija, naujas persidengiantis pakeitimas negeneruojamas: pirmą
migraciją commitink su jos schema arba sąmoningai pašalink keičiamą juodraštį ir
sugeneruok bendrą pakeitimą iš naujo. Tas pats galioja pasirinkus senesnį Git tašką,
nuo kurio jau pridėta kitų migracijų.

**Peržiūra ir commitas:**

Vietinė DB jau turi tavo rankomis ar modulio atliktą pakeitimą. Todėl tos pačios
migracijos ten dar kartą nevykdyk. Peržiūrėjęs SQL, pažymėk tik šią migraciją kaip
įvykdytą lokaliai. Pavyzdyje klasę pakeisk į tikrą sugeneruoto failo klasę:

```bash
make doctrine cmd='version "DoctrineMigrations\Version20260911203000000000" --add --no-interaction'
make schema-export
git add database/schema database/doctrine/versions
# Toje pačioje repozitorijoje pridėk ir susijusius aplikacijos kodo failus.
make schema-check
git commit -m "Add supplier code and its migration"
```

**STAGE ir LIVE:** įdiek tą patį patikrintą kodą, schemą ir migraciją. Šiose DB
migracijos nežymėk kaip jau įvykdytos – ją reikia realiai įvykdyti:

```bash
make doctrine ENV=stage cmd='migrate --dry-run'
make doctrine ENV=stage cmd='migrate --no-interaction'
# Po testavimo ir įprastos LIVE atsarginės kopijos bei diegimo procedūros:
make doctrine ENV=prod cmd='migrate --dry-run'
make doctrine ENV=prod cmd='migrate --no-interaction'
```

`ENV=prod` parenka šio Docker konteksto Compose aplinką; tai savaime neprisijungia
prie nuotolinio LIVE serverio. Komandas vykdyk teisingame diegimo kontekste.
Doctrine kiekvienoje DB seka įvykdytas migracijas. Jei lentelę jau keičia PrestaShop
modulio upgrade skriptas, pasirink vieną pakeitimo vykdytoją – to paties ALTER
nedubliuok ir modulyje, ir Doctrine migracijoje.

Generavimas patikrinamas laikinoje DB: į ją atkuriamos abi schemos, Doctrine DBAL
sugeneruoja SQL, SQL įvykdomas senos schemos kopijoje ir rezultatas palyginamas su
norima struktūra. Nepalaikomi ar nepilnai atkurti skirtumai baigiasi klaida, o ne
nepilna migracija. Laikini konteineriai pašalinami; jie nenaudoja projekto duomenų,
prisijungimų, tinklo ar init skriptų. Kiekvienam skiriama iki 512 MiB atminties.

Ši patikra naudoja tuščias lenteles. Ji nepatvirtina, kad LIVE duomenys atitiks naują
UNIQUE, NOT NULL ar siauresnį tipą. Migraciją išbandyk su senos DB kopija ir duomenimis.
Stulpelių pervadinimus, trynimus bei duomenų perkėlimą peržiūrėk pats. `down()`
automatiškai nenaikina duomenų – atšaukimą reikia parašyti ir išbandyti atskirai.

Apimamos bazinės lentelės, kaip ir `schema-export`. Views, triggers, procedures,
functions, events ir duomenų eilutės į šį palyginimą nepatenka. Doctrine migracijų
istorijos lentelė ignoruojama. [Techninė eiga ir apribojimai](../DATABASE_MIGRATIONS.md).
