# 7. Duomenų bazė kaip kodas

[Turinys](README.md) · [Atgal](06-aplikacija.md) · [Toliau: testai](08-kodo-kokybe.md)

DB struktūra, migracijos, lentelių registras ir anonimizuotas bazinis rinkinys laikomi aplinkos repozitorijoje (`app/database/`). Taip LIVE ir kodas galiausiai sutampa, o naujas programuotojas gauna veikiančią DB viena komanda.

## Sąvokos

| Dalykas | Paprastai | Kur laikomas |
| --- | --- | --- |
| Schema (snapshot'as) | „Kokie stalčiai turi būti“: lentelės, stulpeliai, indeksai | `database/schema/` |
| Lentelių registras | Kiekvienos lentelės grupė ir taisyklės | `database/table-registry.txt` |
| Migracija | „Kaip pakeisti stalčius“: apsaugotas SQL | `database/migrations/` |
| Bazinis rinkinys | Anonimizuoti duomenys iš LIVE, dev ir testams | `database/data/<rinkinys>/` |
| After-import | SQL, kuris po užkėlimo pritaiko DB aplinkai | `database/after-import/` |
| Fixtures | Papildomi SQL rinkiniai | `database/fixtures/<rinkinys>/` |
| Dump / kopija | Visos DB kopija | Privatus `.sql` / `.sql.gz`; kopijos `.generated/backups/` |

DB kaip kodas įjungtas pagal nutylėjimą: schema laikoma `app/database/schema`, o registras, migracijos ir rinkiniai ieškomi šalia jos (`app/database/`). After-import pagal nutylėjimą yra `app/database/after-import`. Nieko rašyti nereikia; `env/common.env` keičia tik nestandartinius kelius (nuo projekto šaknies):

```dotenv
SCHEMA_DIRECTORY=app/database/schema
FIXTURES_DIRECTORY=app/database/fixtures
```

Įjungtas režimas reiškia: `make setup` įdiegia schemos commit hook'ą, `db-import` sustoja ties neklasifikuota lentele, po užkėlimo taikomos migracijos. Kol schema neeksportuota ir lentelės neklasifikuotos (žr. B ir C dalis), hook'as stabdo commit'us. Projektas, kuris DB kaip kodo nenaudoja (pvz. be DB arba su savo migracijų įrankiu), jį išjungia:

```dotenv
DATABASE_AS_CODE=off
```

Tada schemos komandos (`schema-export`, `schema-check`, `db-dataset`…) sustoja su klaida, o `make setup` hook'o nediegia. Anksčiau įdiegtas hook'as commit'ų nestabdo: tik praneša, kad režimas išjungtas.

## A. Pirmas DB užkėlimas

`make setup` DB neliečia. Duomenis užkeli sąmoningai, vienu iš dviejų būdų.

**Dev rinkinys** (greitas, be asmens duomenų):

```bash
make db-load set=dev
```

Įkeliama schema iš snapshot'o, rinkinys `database/data/dev/`, after-import SQL, fixtures, laukiančios migracijos. Prisijungimas prie BO: `employee<id>@example.test` / `dev12345` (klientai – `customer<id>@example.test`). Forsenoje tai konfigūracija ir apie 2000 prekių.

**LIVE dump'as** (realūs duomenys, rankinis failas):

```bash
make db-import-plan file=/kelias/forsena.sql.gz
make db-import file=/kelias/forsena.sql.gz
```

Pirma komanda parodo, kokie failai bus vykdomi, SQL nevykdo. Antra užkelia dump'ą ir viską, kas aprašyta [D dalyje](#d-live-dumpo-užkėlimas). Jei DB konteineris dar nepaleistas, `make db-prepare` paleidžia tik jį.

## B. Schema Git ir commit hook'as

Pirmą kartą projekte:

```bash
make schema-export
make schema-resolve
git add -A database/schema database/table-registry.txt
git commit -m "Record the database schema"
make schema-hook-install
```

1. `schema-export` įrašo kiekvienos lentelės `CREATE TABLE` į `database/schema/table-<lentelė>.sql` ir manifestą. Duomenys neeksportuojami, `AUTO_INCREMENT` skaitiklis pašalinamas. Projekte su [papildomomis schemomis](#papildomos-schemos) kiekviena schema turi savo katalogą: `database/schema/<DATABASE_NAME>/` ir `database/schema/<schema>/`. Išdėstymą parenka tik eksportas: įjungus pirmą papildomą schemą (ar išjungus paskutinę) `schema-export` perkelia failus, o hook'as tai rodo kaip `moved:` – migracijos nereikia, tik `git add -A`. Visi kiti žingsniai (hook'as, migracijos, `db-load`, ataskaita po importo ar deploy'aus) failus randa per manifestą, todėl serverio `DATABASE_NAME` gali skirtis nuo vietinio.
2. `schema-resolve` paklausia apie kiekvieną registre nežinomą lentelę (žr. [C dalį](#c-lentelių-registras)).
3. `schema-hook-install` įdiegia `pre-commit` hook'ą aplinkos repozitorijoje. `make setup` ir `make git-hooks` tai padaro patys; jei repozitorija jau turi hook'ų tvarkyklę (`core.hooksPath`), tik praneša. Po schemos patikros tas pats hook'as paleidžia projekto `bin/pre-commit`, jei jis yra ([git hook'ai](08-kodo-kokybe.md#f-patikros-prieš-commitą-ir-pushą-git-hookai)).

**Kaip veikia hook'as.** Kiekvieno commit'o metu jis:

- eksportuoja schemą iš tavo vietinės DB ir patikrina, ar visos lentelės yra registre;
- jei schema pasikeitė, sugeneruoja apsaugotos migracijos juodraštį `database/migrations/<laikas>-schema.sql` ir **sustabdo commit'ą** peržiūrai;
- antras bandymas praeina tik tada, kai snapshot'as, migracija (be `-- TODO:`) ir registras sutampa ir yra pridėti su `git add`;
- jei DB nepasiekiama (pvz. Docker išjungtas), tik įspėja ir commit'ą praleidžia.

Įprasta eiga, kai modulis pridėjo stulpelį:

```bash
git commit -m "Add supplier code"
# Hook'as sustabdo: schema pasikeitė, migracijos juodraštis database/migrations/20261009120000-schema.sql
# Peržiūrėk juodraštį ir pakeitimus
git add -A database
git commit -m "Add supplier code"
```

Hook'as veikia toje repozitorijoje, kuriai priklauso schemos katalogas (Forsenoje – aplinkos `app/`, ne parduotuvės `app/public`). Jei repozitorija naudoja `core.hooksPath` arba jau turi kieno nors kito `pre-commit` (ne setup'o), installeris jo neperrašo ir praneša. Savo patikras projektas deda į `bin/pre-commit`, ne į hook'ą.

**Commit'as `git worktree` kopijoje.** Hook'as bendras visoms tos repozitorijos darbo kopijoms (`git worktree add`). Kai commit'ini susietoje kopijoje, nustatymai ir DB lieka pagrindinės kopijos (ten yra `env/local.env` ir veikianti vietinė DB), bet schema eksportuojama į **commit'inamos kopijos** schemos katalogą (tas pats santykinis kelias) ir lyginama su **jos** indeksu; ten pat atsiranda migracijos juodraštis, o pranešimai rodo tos kopijos kelią. Pagrindinė kopija nekeičiama. Hook'as tai praneša eilute `Committing in the worktree …; DB and settings from …`. Jei kopijos šaka turi kitą (pvz. senesnį) snapshot'ą nei DB, commit'as sustabdomas kaip įprasta, o eksportas lieka toje kopijoje peržiūrai.

Rankinė patikra be commit'o:

```bash
make schema-check
```

Palygina DB su Git indeksu (tuo, kas pridėta `git add`). Paleista iš susietos `git worktree` kopijos (pvz. hook'ų tvarkyklės), lygina su tos kopijos indeksu, kaip ir hook'as. `make schema-export` ir DB užkrovimo ataskaita dirba su ta kopija, kurios nustatymus įkelia (`make` – aplinkos kopija su `env/local.env`); į susietą kopiją eksportą įrašo hook'as, sustabdęs commit'ą.

Snapshot'as apima tik lenteles. Views, triggers, procedūros ir events į jį nepatenka.

## C. Lentelių registras

Kiekviena DB lentelė privalo būti registre. Neklasifikuota lentelė sustabdo commit'ą ir bazinio rinkinio kūrimą. Taip LIVE šiukšlės (`*_bak_*`, `tmp_*`) nepasilieka nepastebėtos. Registrą patogiausia tvarkyti naršyklėje: [lentelių registro sąsaja](05-servisai.md#lentelių-registras-backup) (`SERVICES` su `backup`).

`database/table-registry.txt` – viena eilutė lentelei:

```text
# lentelė                     grupė      rinkinys  LIVE importas
{p}pr_supplier_feed           suppliers  full      import
{p}pr_supplier_queue          suppliers  empty     skip
{p}pr_api_log                 cache      empty     skip
```

| Laukas | Reikšmės |
| --- | --- |
| lentelė | Vardas. `{p}` – PrestaShop prefiksas (`PS_DATABASE_PREFIX`, numatyta `ps_`; generic projekte atmetamas), `*` – bet kokia seka. [Papildomos schemos](#papildomos-schemos) lentelė – `schema.lentelė` (pvz. `asana_git.*`): eilutė su tašku galioja tik papildomoms schemoms, be taško – tik pagrindinei DB. |
| grupė | Laisvas vardas, pvz. `config`, `catalog`, `customers`, `orders`, `cache`. Pagal jį grupuojami rinkinio failai. |
| rinkinys | `full` – visos eilutės; `slice` – tik prekių imties eilutės; `faker` – visos eilutės, asmens duomenys pakeisti; `empty` – be eilučių; `drop` – lentelė šalinama. |
| LIVE importas | Kas patenka į eksportą (`make db-backup`, serverio kopijos): `import` – struktūra ir duomenys; `skip` – tik struktūra. Užkeliant dump'ą (`make db-import`) įkeliama viskas, kas faile. |

Lentelę pašalinus iš DB, `make schema-export`, `make migration-draft` ir commit hook'as kartu pašalina ir jos projekto eilutę (tik tą, kuri lentelę vadina tiksliai; `*` šablonai ir profilio taisyklės lieka). Kol LIVE dar turi tą lentelę (migracija dar nesudeploy'inta), senesnio LIVE dump'o `make db-import` paklaus, kaip ją klasifikuoti.

Pirma skaitomas projekto registras, po jo – `prestashop` profilio numatytasis ([docker/database-profile/prestashop/table-registry.txt](../../docker/database-profile/prestashop/table-registry.txt)), kuris jau klasifikuoja standartines PS lenteles. Paieškos ir kainų filtro indeksai (`{p}search_index`, `{p}search_word`, `{p}layered_price_index`) ten pažymėti `skip`: įkėlus tokią kopiją, paieškos indeksą perstatyk BO (Parduotuvės nustatymai → Paieška), o kainų indeksą – ps_facetedsearch modulyje. Projekto eilutė laimi. Generic projektas profilio registro neturi: prieš `make db-dataset` jo lenteles klasifikuok `make schema-resolve` (kitaip komanda sustoja ir tai pasako).

```bash
make schema-resolve
```

Terminale paklausia apie kiekvieną nežinomą lentelę ir pasiūlo klasifikaciją: `[i]nclude` (įtraukti su pasiūlyta), `[e]dit` (įvesti savo `grupė rinkinys importas`), `[d]rop` (pažymėti apsaugotam `DROP`), `[s]kip` (palikti vėlesniam). Ne terminale (pvz. IDE) komanda tik parodo sąrašą su pasiūlymais ir baigiasi klaida. Pakeistą registrą peržiūrėk ir commit'ink kartu su schema.

## D. LIVE dump'o užkėlimas

```bash
make db-import file=/kelias/forsena.sql.gz
```

Prieš pirmą importą verta žinoti LIVE DB variklį, versiją, `sql_mode`, koduotes, didžiausias lenteles ir `DEFINER` objektus. Juos parodo [LIVE zondas](../../tools/live-probe/README.md), kartu su `config/mysql` drop-in'u ir lentelių registro juodraščiu.

Tvarka:

```text
0. Patikra: kiekviena dump'o lentelė turi būti registre. Kitaip importas sustoja dar nieko nekeitęs.
1. Dump'as (.sql arba .sql.gz): viskas, kas faile (su skip=1 – be registre „skip“ pažymėtų lentelių duomenų).
2. Saugūs numatytieji (tik PrestaShop ir tik ne prod).
3. database/after-import/common/*.sql, tada database/after-import/<ENV>/*.sql (kiekvienoje – pirma pagrindinės DB, tada papildomų schemų <schema>/*.sql)
4. Integracijų išjungimas database/after-import/integrations/*.sql (tik ne prod).
5. Jei paprašyta: fixtures (db-fixtures=RINKINYS).
6. Laukiančios migracijos.
7. PS cache ir pašto politika, cache išvalymas, PHP perkrovimas (jei PHP veikia).
8. Schemos ataskaita: kuo DB skiriasi nuo šakos snapshot'o (tik įspėjimas).
```

**Importuojama viskas, kas faile.** Registro `skip` taikomas eksportuojant ([atsarginės kopijos](#g-atsarginės-kopijos)), todėl serverio kopijoje cache, log'ų ir eilių lentelių duomenų jau nėra. Didelį pilną dump'ą (pvz. tiesiai iš LIVE) galima užkelti be `skip` lentelių duomenų, struktūra sukuriama visada:

```bash
make db-import file=/kelias/forsena.sql.gz skip=1
```

**Saugūs numatytieji** ne prod aplinkoje ([šaltiniai](../../docker/database-profile/prestashop/after-import)):

- parduotuvės domenas `PS_SHOP_DOMAIN(_SSL)` ir pagrindinės parduotuvės URL = `DOMAIN` (arba `SQL_DOMAIN`);
- `PS_SSL_ENABLED` pagal `PS_SSL` (numatyta `on`);
- žinomi mokėjimų moduliai perjungiami į sandbox: PayPal, Paysera, PrestaShop Checkout, Adyen. Kitus mokėjimo modulius sutvarko projekto after-import.

**Paštas.** Laiškų nustatymą tvarko ne SQL, o `MAIL_MODE`: lokaliai su Mailpit laiškai eina į Mailpit, be jo – išjungiami ([paštas](12-cache-ir-hibridines-aplinkos.md#paštas-mail_mode)).

**Integracijos.** Kiekvienas failas `database/after-import/integrations/<vardas>.sql` išjungia vieną išorinę integraciją (tiekėjų sinchronizaciją, kurjerius, rinkodaros įrankius). Po kiekvieno užkėlimo ne prod aplinkoje visos jos išjungiamos. Kad kuri nors liktų įjungta:

```dotenv
# env/local.env
INTEGRATIONS_ENABLED="tecdoc"
```

Tada užkelk DB iš naujo. Nežinomas vardas yra klaida. Forsenoje integracijos yra `couriers`, `gtm`, `omnisend`, `suppliers`, `tecdoc`.

**After-import SQL.** Savo SQL dėk į `database/after-import/common/` arba `database/after-import/<ENV>/`, failus numeruok vienodo ilgio numeriais (`010-…`, `020-…`). Juose galima naudoti `${DOMAIN}` (pagrindinį hostname) ir `DOMAIN` išvardytus `${DOMAIN_<VARDAS>}` ([keli domenai](03-nustatymai.md#keli-domenai)), o `PROFILE=prestashop` projekte – ir `${DB_PREFIX}` bei `${PS_SSL}` (generic projekte jie atmetami, nes PS prefikso ir SSL vėliavos ten nėra):

```sql
UPDATE `${DB_PREFIX}configuration` SET `value` = '0' WHERE `name` = 'MYMODULE_LIVE_MODE';
UPDATE `${DB_PREFIX}shop_url` SET `domain` = '${DOMAIN_LV}', `domain_ssl` = '${DOMAIN_LV}' WHERE `id_shop` = 2;
```

Kiti kintamieji šiuose failuose nepakeičiami.

Papildomos schemos ([papildomos schemos](#papildomos-schemos)) SQL dėk į tos schemos pakatalogį, pvz. `database/after-import/local/asana_memory/010-reset.sql`: setup jį vykdo toje schemoje (prideda `USE`), po tos grupės pagrindinės DB failų. Pakatalogis su `.sql` failais, kurio vardo nėra `DATABASE_EXTRA_SCHEMAS`, yra klaida.

**Kita:**

- `backup=1` prieš importą sukuria DB kopiją: `make db-import file=… backup=1`.
- Jei įdiegtas `pv`, terminale matai importo progresą.
- `.sql.gz` patikrinamas prieš rašant į DB. Klaida sustabdo tolesnius failus, bet jau įvykdyto SQL neatšaukia.
- Importas vykdomas kaip DB root, nes dump'uose būna `DEFINER` ar GTID eilučių.
- Vienoje aplinkoje vienu metu gali vykti tik vienas importas.
- **Nežinoma lentelė sustabdo importą.** Terminale `make db-import` paklausia apie kiekvieną tokią lentelę (kaip `schema-resolve`), įrašo sprendimą į `database/table-registry.txt` ir tęsia. Be terminalo (IDE, pipeline) ir `make db-import-plan` parodo lenteles su siūloma klasifikacija ir baigiasi klaida. Setup'o lentelė `setup_migrations` registro nereikalauja.

## E. Migracijos

Visi DB struktūros pakeitimai eina per migracijas `database/migrations/<YYYYMMDDHHMMSS>-<vardas>.sql`. Juodraštį sugeneruoja commit hook'as arba komanda, tu jį peržiūri:

```bash
make migration-draft name=drop-oneoff-tables
```

Eksportuoja schemą ir sugeneruoja juodraštį (po vieną kiekvienai pasikeitusiai DB) nuo paskutinio commit'o iki tavo DB – be commit'o ir nepriklausomai nuo to, kas jau stage'inta (net jei `make schema-export` jau padarytas). Neredaguotas juodraštis perrašomas tame pačiame faile. `make schema-export` vienas migracijos nesukuria: LIVE pakeitimą gauna tik per migraciją, todėl snapshot'o pakeitimą commit'ink kartu su ja (ne `--no-verify`).

Migracijos yra **apsaugotos (idempotentiškos)**: antras paleidimas nieko nekeičia, o migracija nesusipjauna su modulio `install`/`upgrade` scriptais, kurie gal jau pakeitė tą pačią lentelę.

- nauja lentelė – `CREATE TABLE IF NOT EXISTS`;
- naujas stulpelis ar indeksas – tik jei `information_schema` sako, kad jo dar nėra;
- pašalinta lentelė, stulpelis ar indeksas – `DROP` taip pat apsaugotas ir įrašomas kaip bet kuris kitas pakeitimas (deploy'us jį įvykdys ir LIVE – duomenys dings, todėl prieš deploy'ų pasidaryk kopiją). Pervadinimas yra šalinimas ir nauja lentelė ar stulpelis; jei duomenis reikia perkelti, tai daro tolesnė migracija;
- pakeisti raktai (gali atmesti esamas eilutes) ir lentelės parametrai – juodraštyje paliekami kaip `-- TODO:` blokas su siūlomu SQL. Kol liko `TODO`, commit'as stabdomas, o `make db-migrate` migracijos netaiko.

```bash
make db-migrate          # pritaiko laukiančias migracijas
make db-migrate dry=1    # tik parodo, kurios ir kokį SQL vykdytų
```

Pritaiko laukiančias migracijas vieną kartą kiekvienai DB ir įrašo jas į lentelę `setup_migrations` pagrindinėje DB (jos snapshot'e nėra): vardą, kontrolinę sumą ir laiką. Nepavykusi migracija neįrašoma: pataisyk ir paleisk dar kartą (jau įvykdyti apsaugoti sakiniai praleidžiami). Jau pritaikyta, bet vėliau pakeista migracija nekartojama, tik įspėjama; rollback dalies pakeitimas po pritaikymo įspėjimo nesukelia.

Migracijos automatiškai paleidžiamos po `db-import`, `db-load`, `test-prepare` ir `deploy`. Deploy'aus tvarka: kodas → modulių upgrade'ai → migracijos → schemos ataskaita.

### Po `git pull`

Kolegos migracijos ateina su kodu, bet tavo DB savaime nesikeičia: paleisk `make db-migrate` (pirma gali `dry=1`). Kol commit'intų migracijų nepritaikei, commit hook'as sustoja dar prieš eksportą („N committed migrations are not applied to this database yet … run make db-migrate first“), kad senesnė tavo DB neperrašytų naujesnio snapshot'o ir nesugeneruotų atgalinio juodraščio. Tavo paties migracijos (tos, kurias hook'as sugeneravo iš tavo DB) commit'o metu įrašomos kaip pritaikytos, nes tavo DB tą pakeitimą jau turi.

### Rollback

Juodraštis turi dvi dalis: viršuje – kas vykdoma pirmyn, po eilute `-- Rollback:` – kelias atgal. Hook'as rollback dalį sugeneruoja pats: pridėtą lentelę ar stulpelį pašalina, pakeistą stulpelį grąžina į ankstesnį apibrėžimą, pašalintą lentelę ar stulpelį sukuria iš naujo (tik struktūrą – duomenys negrįžta). Pakeisti raktai ir lentelės parametrai lieka `-- TODO:` abiejose dalyse.

```bash
make db-rollback                  # atšaukia paskutinę pritaikytą migraciją
make db-rollback steps=3          # paskutines tris, nuo naujausios
make db-rollback to=20261013120000-schema.sql   # viską, kas pritaikyta po jos (ji lieka)
make db-rollback dry=1            # tik parodo SQL
```

Vykdoma rollback dalis, o migracija išbraukiama iš `setup_migrations`, todėl kitas `make db-migrate` ją pritaikys vėl. Prieš keičiant ką nors patikrinamos visos atšaukiamos migracijos: jei kuri neturi `-- Rollback:` dalies, joje liko `TODO` arba jos failo nebėra, niekas nevykdoma. Senos migracijos be rollback dalies atšaukiamos tik ją papildžius (tai pritaikytos migracijos nekeičia) arba iš kopijos. Duomenis grąžina tik kopija: `make db-backup` prieš rizikingą pakeitimą.

### Kelios DB

Kaip ir snapshot'as: su viena DB migracijos guli `database/migrations/`, su [papildomomis schemomis](#papildomos-schemos) – kiekvienos DB savo kataloge: `database/migrations/<DATABASE_NAME>/` ir `database/migrations/<schema>/`. Hook'as sugeneruoja po juodraštį kiekvienai pasikeitusiai DB, ir kiekvienai pasikeitusiai DB commit'e reikia naujos migracijos jos kataloge. Papildomos schemos migracija vykdoma toje schemoje (setup prideda `USE`), todėl SQL rašomas be schemos vardo; `setup_migrations` ją įrašo kaip `<schema>/<failas>`. Pagrindinės DB migracija įrašoma vien failo vardu, todėl senos plokščios migracijos lieka galioti ir perkėlus jas į `<DATABASE_NAME>/`. Vykdymo tvarka – pagal laiką faile, tą pačią sekundę pirma pagrindinė DB, tada schemos `DATABASE_EXTRA_SCHEMAS` tvarka.

### Kai projektas migruoja pats (Doctrine)

```dotenv
# env/common.env
DATABASE_MIGRATIONS=off
```

Setup migracijų negeneruoja, nereikalauja ir nevykdo (`db-migrate`, `db-rollback` atsisako; po importo ir deploy'aus jų nėra). Snapshot'as, registras ir commit hook'as lieka: hook'as vis tiek sustabdo commit'ą, jei DB struktūra skiriasi nuo stage'into snapshot'o. Projekto migracijas paleisk pats, pvz. deploy'aus hook'e `config/deploy/after-rsync.sh` (`php bin/console migrations:migrate --no-interaction`).

## F. Bazinis rinkinys

Bazinis rinkinys išvedamas iš LIVE kopijos vietinėje DB. Jis anonimizuotas ir sumažintas, todėl laikomas Git. Dev ir mock (testų) duomenys yra tas pats rinkinys.

```bash
make db-import file=/kelias/live.sql.gz
make db-dataset set=dev
git add database/data/dev
```

`make db-dataset` veikia tik `ENV=local` ir reikalauja pilno registro. Kas daroma:

- kiekviena lentelė apdorojama pagal registro rinkinio taisyklę (`full`, `slice`, `faker`, `empty`, `drop`);
- `slice` lentelėms imama prekių imtis pagal receptą. Numatytasis receptas paima 2000 aktyvių prekių. Projekto receptas `database/data/recipe.txt` laimi prieš [profilio](../../docker/database-profile/prestashop/data-recipe.txt): `@products SELECT …` eilutė parenka imtį (reikia tik kai yra `slice` lentelių arba sąlygų su `{products}`, todėl generic projektas gali be jos), kitos eilutės `lentelė sąlyga` – sąlygas lentelėms be `id_product` stulpelio;
- asmens duomenų stulpeliai gauna pastovias dev reikšmes, kontaktai pakeičiami visur, konfigūracijos slaptažodžiai ir raktai išvalomi (jungikliai lieka);
- darbuotojų ir klientų slaptažodis – `dev12345` (bcrypt);
- failai grupuojami pagal registro grupę (`config.sql`, `catalog.sql`, `customers.sql`…), eilutės rūšiuojamos pagal pirminį raktą po vieną eilutėje. Pakartotinis kūrimas iš tos pačios DB duoda tuos pačius baitus, todėl diff'ai maži.

Užkelti rinkinį:

```bash
make db-load set=dev
make db-load set=dev replace=1
make db-load set=dev replace=1 backup=1
```

Netuščiai DB reikia `replace=1` – visos lentelės pirma ištrinamos. `backup=1` prieš tai pasidaro kopiją. Po užkėlimo vykdoma ta pati seka kaip po importo (after-import, fixtures, migracijos, politika, ataskaita). Jei nurodytas `FIXTURES_DIRECTORY`, kartu įkeliami `database/fixtures/common/` ir `database/fixtures/<rinkinys>/` (jei toks katalogas yra) failai.

**PS 1.6:** slaptažodžiai priklauso nuo `PS_COOKIE_KEY`, todėl rinkinys turi savo dev raktą (ne LIVE raktą), commit'inamą `database/data/<rinkinys>/dataset.env`. Dev prisijungimams įrašyk jį kaip `PS_COOKIE_KEY` į `env/local.env`; kitaip `db-load` įspėja. LIVE dump'ui reikia LIVE rakto, todėl perjungiant tarp jų keičiasi ir `PS_COOKIE_KEY`.

## G. Atsarginės kopijos

```bash
make db-backup
make db-backup file=/privatus/kelias/pries-atnaujinima.sql.gz
make db-backup full=1        # visų lentelių duomenys, registras netaikomas
```

Kopija rašoma į `.generated/backups/<projektas>-<ENV>-<laikas>.sql.gz` (teisės `0600`). Esamas failas neperrašomas, nepavykusi kopija galutiniu vardu neišsaugoma. Kopija (eksportas) visose aplinkose daroma pagal registrą: visų lentelių struktūra ir tik `import` lentelių duomenys, todėl ji maža ir naudinga; `full=1` įrašo viską. Kopija, kurią `db-import backup=1` ar `db-load backup=1` pasidaro prieš keisdami DB, visada pilna – ji skirta atkūrimui.

Po kiekvienos kopijos (ir prieš deploy'ų) setup patikrina, ar visos DB lentelės yra registre. Neregistruotos lentelės išvardijamos įspėjime (jų duomenys kopijoje yra) ir įrašomos į `data/<ENV>/table-registry/status.json`, iš kurio jas rodo [metrikos](05-servisai.md#lentelių-registras). Nepavykusi patikra kopijos nesustabdo.

```bash
make db-backup-prune
make db-backup-prune apply=1
```

Pirma komanda parodo, kurias automatiškai pavadintas šios aplinkos kopijas galima ištrinti, antra – ištrina. Paliekamos `BACKUP_KEEP_LAST` (numatyta 5) naujausios.

Atkūrimas yra tas pats `make db-import`, todėl po jo vėl vykdomi after-import ir saugūs numatytieji.

Jei projektas turi [papildomų schemų](#papildomos-schemos), kopija apima jas visas ir atkuria jas visas.

### Serverio kopija (db-pull)

Kai reikia veikiančios aplinkos su tikrais duomenimis, nebūtina prašyti dump'o: setup-server kas naktį (ir prieš kiekvieną deploy'ų) daro kopijas pagal registrą, kartą per savaitę – pilną, o programuotojas parsisiunčia ją per SSH. LIVE serveris neliečiamas.

Vieną kartą:

1. Serverio administratorius įrašo tavo **viešą** raktą (`~/.ssh/id_ed25519.pub`) – setup-server `sudo bin/add-backup-key …pub --name vardas --from <VPN tinklas>` (su `--allow-fresh` gali prašyti ir šviežios kopijos). Raktas veiks tik iš VPN ir tik kopijoms (be shell'o). Jei serveris atsako `Connection refused`, jame nėra sshd: administratorius pirmiausia paleidžia `sudo bin/install-ssh --from <VPN tinklas>`.
2. Pirmas `make db-pull` paklausia serverio (`setup@10.8.0.14`) ir SSH rakto (tuščia – numatytasis) ir išsaugo atsakymą setup'o `docker/.env.backup` (0600, ne Git). Juo naudojasi visi šio kompiuterio projektai; projekto `BACKUP_SSH` / `BACKUP_SSH_KEY`, jei nurodyti, laimi. `make db-pull configure=1` paklausia iš naujo.
3. Projekto vardas serveryje randamas pats: aplinkos repo vardas (`origin`, mažosiomis raidėmis – taip serveris vadina build katalogą), tada `PROJECT_NAME`. Jei serveris nežino nė vieno, komanda paklausia ir įrašo `BACKUP_SERVER_PROJECT` į `env/local.env`.

Kaskart (prisijungus prie VPN):

```bash
make db-pull                       # naujausia kopija pagal registrą (naktinė, „fresh“ ar prieš deploy'ų)
make db-pull full=1                # naujausia pilna (savaitinė): visų lentelių duomenys
make db-pull fresh=1               # serveris padaro naują kopiją dabar ir ji parsiunčiama
make db-pull fresh=1 full=1        # nauja pilna kopija dabar
make db-pull import=1              # + užkelia (kaip make db-import: after-import, migracijos)
make db-pull fresh=1 import=1 backup=1   # prieš užkeliant – vietinės DB kopija
make db-pull from=stage            # stage
```

Kiekvienas žingsnis rodomas atskirai, terminale – su juosta (dalis, kiekis, likęs laikas):

```text
==> [1/3] A new prod backup of asana_tracker on the server
Backup on the server     [##############..............]  51%  1.12 GB / 2.20 GB  3:10 left
==> [2/3] Download fresh-prod-20261013T150000Z.sql.gz (2.20 GB)
Download                 [############################] 100%  2.20 GB / 2.20 GB  1:42
==> [3/3] Import into the local database (asana): dump, after-import, migrations
Unpack dump              [############################] 100%  2.20 GB / 2.20 GB  0:58
Import                   [##########..................]  37%  4.10 GB / 11.08 GB  6:20 left
```

- **Kopija serveryje** (`fresh=1`): procentai – apytiksliai, lyginant su ankstesnės tokio pat turinio kopijos dydžiu (pirmoji – tik kiekis ir laikas). Jei tokio pat turinio kopija jaunesnė nei 30 min. arba tuo metu daroma, serveris atiduoda ją, užuot daręs antrą.
- **Atsisiuntimas**: dydį serveris pasako iš anksto. Siunčiamas failas tuo pačiu patikrinamas (gzip iki galo) ir perskaitomas (dydis, schemos, lentelės), todėl iškart po jo užkeliant didelis failas nebeskaitomas iš naujo. Nutrūkęs ar per trumpas atsisiuntimas neišsaugomas.
- **Užkėlimas** (`import=1`): tas pats kaip `make db-import` – išskleidimas, dump'as, `database/after-import` scriptai, migracijos, schemos ataskaita. Be terminalo (CI, log'as) – po vieną eilutę kiekvieno žingsnio pabaigoje.
- Jau parsisiųsta kopija antrą kartą nesiunčiama.
- **Kopija pagal registrą** (be `full`): `skip` lentelės – tik struktūra, be `DROP`, todėl užkėlus jų vietiniai duomenys lieka. **Pilna** (`full=1`): visų lentelių duomenys, todėl užkėlus pakeičiamos ir sinchronizuojamos lentelės.
- **Kopijoje yra tikri klientų duomenys.** Failas privatus (`0600`); ištrink, kai nebereikia. Kasdieniam darbui be asmens duomenų – [dev rinkinys](#f-bazinis-rinkinys).
- Kopija su [papildomomis schemomis](#papildomos-schemos) turi serverio schemų vardus (`USE`): vietinis `DATABASE_NAME` ir schemos turi sutapti su serverio.
- Klaida „Cannot reach the backup server“ – patikrink VPN, serverio adresą (`make db-pull configure=1`) ir ar raktas įrašytas serveryje. „may not ask for a fresh backup“ – raktas įrašytas be `--allow-fresh`. „too old“ – serverio setup-server dar be savaitinių ir šviežių kopijų (`bin/update`).

## H. Fixtures

Fixtures – papildomi SQL rinkiniai, pvz. testams reikalingi įrašai.

```text
database/fixtures/
├── common/010-support.sql     # Kartu su kiekvienu rinkiniu
├── local/010-local-users.sql
└── test/010-checkout.sql
```

```bash
make db-fixtures-plan set=local
make db-fixtures-load set=local
make db-import file=/kelias/dump.sql.gz db-fixtures=test
```

`ENV` pasirenka DB, `set` – rinkinį: `make db-fixtures-load ENV=local set=test` tyčia įkelia testinį rinkinį į vietinę DB. Skaitomi tik tiesioginiai `*.sql` failai pagal vardą. Rinkinys senų įrašų nevalo, todėl rašyk kartojamą SQL (`INSERT … ON DUPLICATE KEY UPDATE`).

## Papildomos schemos

Kai kurios sistemos (pvz. asana.local) naudoja kelias DB schemas. Jas išvardink `env/common.env`:

```dotenv
DATABASE_EXTRA_SCHEMAS="asana_frontapp asana_memory asana_graph asana_bitbucket asana_git"
```

Vardai – raidės, skaitmenys ir `_` (iki 64 simbolių), be pasikartojimų, ne `DATABASE_NAME` ir ne sistemos schemos (`mysql`, `sys`…). Kitaip runner'is sustoja.

| Kas | Su papildomomis schemomis |
| --- | --- |
| Pirmas tuščios DB paleidimas | Sukuriamos. Aplikacijos vartotojas gauna `ALL PRIVILEGES` tik į pagrindinę ir išvardintas schemas: be globalių teisių, be `GRANT OPTION`, kitų DB kurti negali. |
| `make db-restrict-grants` | Susiaurina teises iki pagrindinės ir išvardintų schemų, trūkstamas sukuria. Kartoti saugu. |
| `make db-backup`, kopija prieš deploy'ų | Apima visas schemas (formatas žemiau). |
| `make db-import` | Užkelia ir kelių schemų dump'ą. |
| Lentelių registras | Visos schemos: papildomos schemos lentelė rašoma `schema.lentelė` (žr. [C dalį](#c-lentelių-registras)). Neklasifikuota bet kurios schemos lentelė stabdo commit'ą ir `db-import`; backup'o patikra ir metrikos apima visas schemas. |
| Snapshot'as | Visos schemos, kiekviena savo kataloge: `database/schema/<DATABASE_NAME>/table-*.sql` ir `database/schema/<schema>/table-*.sql`; manifeste papildomos schemos lentelė – `schema.lentelė`. Commit hook'as lygina ir jas. Schemos pirmas snapshot'as yra jos bazė: tam commit'ui migracijos nereikia, vėlesniems pakeitimams – reikia. |
| Apsaugotos migracijos | Visos schemos: juodraštis papildomos schemos lentelę vadina `` `schema`.`lentelė` ``, patikros – `TABLE_SCHEMA = 'schema'`. |
| After-import SQL | Pagrindinė DB – `database/after-import/<grupė>/*.sql`, papildoma schema – `database/after-import/<grupė>/<schema>/*.sql` (setup prideda `USE`). |
| Bazinis rinkinys, `db-load`, Doctrine diff | Tik `DATABASE_NAME`. |

**Kopijos formatas.** Su papildomomis schemomis kopija daroma vienu `mysqldump --databases` paleidimu: visos schemos nuskaitomos vienoje transakcijoje (suderinta kopija), o kiekviena schema kopijoje turi `CREATE DATABASE IF NOT EXISTS` ir `USE`. Todėl kopija atkuria visas schemas tais pačiais vardais: `make db-import file=…` (arba `mysql < kopija.sql`). Stage/prod kopijoje registras taikomas visoms schemoms: `skip` lentelių duomenys nekopijuojami, o papildomos schemos `skip` lentelių struktūra rašoma po savo `USE`. Be papildomų schemų kopija tokia pati kaip anksčiau: be `USE`, todėl ją galima užkelti ir į kitą `DATABASE_NAME`.

**Importas.** Dump'as gali perjungti schemą (`USE`) tik į `DATABASE_NAME` arba išvardintas schemas. Kitaip importas sustoja dar nieko nekeitęs. Dažniausia priežastis – LIVE pagrindinės DB vardas kitoks: suvienodink `DATABASE_NAME` arba pataisyk `USE` dump'e. Registro patikra ir `skip` taisyklė taikomos visoms schemoms; papildomos schemos lentelė registre yra `schema.lentelė`.

**Jau veikianti DB.** Schemos kuriamos tik pirmą kartą paleidžiant tuščią DB. Jei schemą įrašai į sąrašą vėliau, paleisk `make db-restrict-grants`: jis sukurs schemą ir suteiks teises. Iš sąrašo išbrauktos schemos teisės savaime neatimamos.

### Testų schemos

Jei aplikacijos testai patys kuria ir trina savo DB (pvz. asana.local testai – `<schema>_test` kiekvienai schemai), išvardink jas `env/local.env`, ne `DATABASE_EXTRA_SCHEMAS`:

```dotenv
DATABASE_TEST_SCHEMAS="asana*_test"
```

Vardas turi baigtis `_test`, `*` – bet kokie simboliai, todėl šablonas negali pasiekti tikros schemos. Aplikacijos vartotojas gauna teises į atitinkančias schemas, bet setup jų **nekuria** ir niekur kitur neliečia: jų nėra snapshot'e, kopijoje, importe, registre ir migracijose. Teisės suteikiamos pirmą kartą paleidžiant tuščią DB ir per `make db-restrict-grants`. Stage/prod runner'is šio nustatymo nepriima – testai serverio DB nevyksta.

## Senos DB teisės

Prieš v2 sukurtoje DB aplikacijos vartotojas galėjo turėti teises į visą serverį. Vieną kartą susiaurink:

```bash
make db-restrict-grants
```

Root gauna atskirą slaptažodį `DATABASE_ROOT_PASSWORD`, aplikacijos vartotojas lieka tik su teisėmis į savo DB ir [papildomas schemas](#papildomos-schemos) (be `GRANT OPTION`). Naujos DB taip kuriamos iš karto. Teisėse `_` ekranuojamas (`shop\_main`): kitaip `_` reikštų bet kurį simbolį ir vartotojas galėtų susikurti panašaus vardo DB, pvz. `shopXmain`. Seną DB, kurios vardas turi `_`, sutvarko tas pats `make db-restrict-grants`.

Komanda prisijungia nauju root slaptažodžiu, root su aplikacijos slaptažodžiu arba aplikacijos vartotoju (senos DB root dažnai turėjo aplikacijos slaptažodį). Jei **abu** slaptažodžiai nauji (pvz. DB failai perkelti iš seno stack'o, o Deployment variables nustatyti iš naujo), prisijungti ji negali (`Cannot connect as root or as a privileged application user`). Tada pirma senu root slaptažodžiu suteik aplikacijos vartotojui naują slaptažodį (jį konteineris jau turi `MYSQL_PASSWORD`), paskui paleisk `make db-restrict-grants`:

```bash
docker exec -it <projektas>-<env>-mysql-<versija> bash -c 'mysql -uroot -p -e "ALTER USER <vartotojas>@\"%\" IDENTIFIED BY \"$MYSQL_PASSWORD\""'
```

## Stebėjimo paskyra

DB statistiką (Prometheus [DB metrikos](05-servisai.md#db-metrikos), [DB monitorius](05-servisai.md#db-monitorius)) skaito atskira paskyra `DATABASE_MONITOR_USER` (numatyta `<DATABASE_NAME>_monitor`), ne aplikacijos vartotojas ir ne root:

```bash
make db-monitor-user
```

Ji turi tik `PROCESS` ir skaitymą `performance_schema` bei `sys`: mato skaitiklius, buffer pool ir užklausų šablonus (reikšmės pakeistos `?`), bet ne projekto schemų eilutes. Užklausų ir lentelių statistikai reikia `DATABASE_PERFORMANCE_SCHEMA=on` (kelis šimtus MB DB atminties); išjungus ji rodoma kaip būsena `database_performance_schema 0`. Statistika kaupiama nuo DB paleidimo; monitorius jos nevalo.

## Doctrine migracijos (neprivaloma)

Jei aplikacija naudoja Doctrine migracijas, nusikopijuok šabloną iš setup:

```bash
mkdir -p compose database/doctrine/versions
cp ../../setup/templates/grouped/compose/doctrine.yaml compose/
cp ../../setup/templates/grouped/database/doctrine/connection.php database/doctrine/
cp ../../setup/templates/grouped/database/doctrine/migrations.php database/doctrine/
```

`env/common.env` papildyk `PROJECT_COMPOSE_FILES=compose/doctrine.yaml` (jei sąrašas jau yra, pridėk prie jo). Tada:

```bash
make doctrine-build
make doctrine cmd=status
make doctrine cmd='migrate --dry-run'
make doctrine-diff
```

Jei DB struktūrą keičia tik Doctrine migracijos, `env/common.env` įrašyk ir `DATABASE_MIGRATIONS=off` ([kai projektas migruoja pats](#kai-projektas-migruoja-pats-doctrine)), kad setup nereikalautų savo migracijų.

`doctrine-build` sukuria įrankio image'ą (PHP `DOCTRINE_PHP_VERSION`, numatyta 8.3, nepriklausomai nuo aplikacijos PHP). `doctrine-diff` sugeneruoja Doctrine migraciją iš Git snapshot'o (`ref=HEAD` pagal nutylėjimą) ir vietinės DB skirtumo. PrestaShop projektams įprastas kelias yra apsaugotos SQL migracijos iš [E dalies](#e-migracijos).
