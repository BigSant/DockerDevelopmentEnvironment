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

Nustatymai `env/common.env` (keliai nuo projekto šaknies):

```dotenv
SCHEMA_DIRECTORY=app/database/schema
FIXTURES_DIRECTORY=app/database/fixtures
```

Registras, migracijos ir rinkiniai ieškomi šalia schemos katalogo (`app/database/`). Be `SCHEMA_DIRECTORY` jie neveikia. After-import pagal nutylėjimą yra `app/database/after-import`.

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

1. `schema-export` įrašo kiekvienos lentelės `CREATE TABLE` į `database/schema/table-<lentelė>.sql` ir manifestą. Duomenys neeksportuojami, `AUTO_INCREMENT` skaitiklis pašalinamas.
2. `schema-resolve` paklausia apie kiekvieną registre nežinomą lentelę (žr. [C dalį](#c-lentelių-registras)).
3. `schema-hook-install` įdiegia `pre-commit` hook'ą aplinkos repozitorijoje. `make setup` tai padaro pats; jei repozitorija jau turi hook'ų tvarkyklę (`core.hooksPath`), tik praneša.

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

Hook'as veikia toje repozitorijoje, kuriai priklauso schemos katalogas (Forsenoje – aplinkos `app/`, ne parduotuvės `app/public`). Jei repozitorija naudoja `core.hooksPath` arba jau turi savo `pre-commit`, installeris jo neperrašo ir praneša, ką įrašyti rankomis.

**Commit'as `git worktree` kopijoje.** Hook'as bendras visoms tos repozitorijos darbo kopijoms (`git worktree add`). Kai commit'ini susietoje kopijoje, nustatymai ir DB lieka pagrindinės kopijos (ten yra `env/local.env` ir veikianti vietinė DB), bet schema eksportuojama į **commit'inamos kopijos** schemos katalogą (tas pats santykinis kelias) ir lyginama su **jos** indeksu; ten pat atsiranda migracijos juodraštis, o pranešimai rodo tos kopijos kelią. Pagrindinė kopija nekeičiama. Hook'as tai praneša eilute `Committing in the worktree …; DB and settings from …`. Jei kopijos šaka turi kitą (pvz. senesnį) snapshot'ą nei DB, commit'as sustabdomas kaip įprasta, o eksportas lieka toje kopijoje peržiūrai.

Rankinė patikra be commit'o:

```bash
make schema-check
```

Palygina DB su Git indeksu (tuo, kas pridėta `git add`). Paleista iš susietos `git worktree` kopijos (pvz. hook'ų tvarkyklės), lygina su tos kopijos indeksu, kaip ir hook'as. `make schema-export` ir DB užkrovimo ataskaita dirba su ta kopija, kurios nustatymus įkelia (`make` – aplinkos kopija su `env/local.env`); į susietą kopiją eksportą įrašo hook'as, sustabdęs commit'ą.

Snapshot'as apima tik lenteles. Views, triggers, procedūros ir events į jį nepatenka.

## C. Lentelių registras

Kiekviena DB lentelė privalo būti registre. Neklasifikuota lentelė sustabdo commit'ą ir bazinio rinkinio kūrimą. Taip LIVE šiukšlės (`*_bak_*`, `tmp_*`) nepasilieka nepastebėtos.

`database/table-registry.txt` – viena eilutė lentelei:

```text
# lentelė                     grupė      rinkinys  LIVE importas
{p}pr_supplier_feed           suppliers  full      import
{p}pr_supplier_queue          suppliers  empty     skip
{p}pr_api_log                 cache      empty     skip
```

| Laukas | Reikšmės |
| --- | --- |
| lentelė | Vardas. `{p}` – PrestaShop prefiksas (`PS_DATABASE_PREFIX`, numatyta `ps_`; generic projekte atmetamas), `*` – bet kokia seka. |
| grupė | Laisvas vardas, pvz. `config`, `catalog`, `customers`, `orders`, `cache`. Pagal jį grupuojami rinkinio failai. |
| rinkinys | `full` – visos eilutės; `slice` – tik prekių imties eilutės; `faker` – visos eilutės, asmens duomenys pakeisti; `empty` – be eilučių; `drop` – lentelė šalinama. |
| LIVE importas | `import` – importuoti duomenis; `skip` – importuoti tik struktūrą. |

Pirma skaitomas projekto registras, po jo – `prestashop` profilio numatytasis ([docker/database-profile/prestashop/table-registry.txt](../../docker/database-profile/prestashop/table-registry.txt)), kuris jau klasifikuoja standartines PS lenteles. Projekto eilutė laimi. Generic projektas profilio registro neturi: prieš `make db-dataset` jo lenteles klasifikuok `make schema-resolve` (kitaip komanda sustoja ir tai pasako).

```bash
make schema-resolve
```

Terminale paklausia apie kiekvieną nežinomą lentelę ir pasiūlo klasifikaciją: `[i]nclude` (įtraukti su pasiūlyta), `[e]dit` (įvesti savo `grupė rinkinys importas`), `[d]rop` (pažymėti apsaugotam `DROP`), `[s]kip` (palikti vėlesniam). Ne terminale (pvz. IDE) komanda tik parodo sąrašą su pasiūlymais ir baigiasi klaida. Pakeistą registrą peržiūrėk ir commit'ink kartu su schema.

## D. LIVE dump'o užkėlimas

```bash
make db-import file=/kelias/forsena.sql.gz
```

Tvarka:

```text
0. Patikra: kiekviena dump'o lentelė turi būti registre. Kitaip importas sustoja dar nieko nekeitęs.
1. Dump'as (.sql arba .sql.gz). Registre „skip“ pažymėtų lentelių duomenys praleidžiami.
2. Saugūs numatytieji (tik PrestaShop ir tik ne prod).
3. database/after-import/common/*.sql, tada database/after-import/<ENV>/*.sql
4. Integracijų išjungimas database/after-import/integrations/*.sql (tik ne prod).
5. Jei paprašyta: fixtures (db-fixtures=RINKINYS).
6. Laukiančios migracijos.
7. PS cache ir pašto politika, cache išvalymas, PHP perkrovimas (jei PHP veikia).
8. Schemos ataskaita: kuo DB skiriasi nuo šakos snapshot'o (tik įspėjimas).
```

**Praleidžiamos lentelės.** Cache, indeksų ir log'ų lentelių duomenys pagal registrą neimportuojami, todėl importas kelis kartus greitesnis. Struktūra sukuriama visada. Viską importuoti:

```bash
make db-import file=/kelias/forsena.sql.gz full=1
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

**After-import SQL.** Savo SQL dėk į `database/after-import/common/` arba `database/after-import/<ENV>/`, failus numeruok vienodo ilgio numeriais (`010-…`, `020-…`). Juose galima naudoti `${DOMAIN}`, o `PROFILE=prestashop` projekte – ir `${DB_PREFIX}` bei `${PS_SSL}` (generic projekte jie atmetami, nes PS prefikso ir SSL vėliavos ten nėra):

```sql
UPDATE `${DB_PREFIX}configuration` SET `value` = '0' WHERE `name` = 'MYMODULE_LIVE_MODE';
```

Kiti kintamieji šiuose failuose nepakeičiami.

**Kita:**

- `backup=1` prieš importą sukuria DB kopiją: `make db-import file=… backup=1`.
- Jei įdiegtas `pv`, terminale matai importo progresą.
- `.sql.gz` patikrinamas prieš rašant į DB. Klaida sustabdo tolesnius failus, bet jau įvykdyto SQL neatšaukia.
- Importas vykdomas kaip DB root, nes dump'uose būna `DEFINER` ar GTID eilučių.
- Vienoje aplinkoje vienu metu gali vykti tik vienas importas.
- **Nežinoma lentelė sustabdo importą.** Terminale `make db-import` paklausia apie kiekvieną tokią lentelę (kaip `schema-resolve`), įrašo sprendimą į `database/table-registry.txt` ir tęsia. Be terminalo (IDE, pipeline) ir `make db-import-plan` parodo lenteles su siūloma klasifikacija ir baigiasi klaida. Setup'o lentelė `setup_migrations` registro nereikalauja.

## E. Migracijos

Visi DB struktūros pakeitimai eina per migracijas `database/migrations/<YYYYMMDDHHMMSS>-<vardas>.sql`. Juodraštį sugeneruoja commit hook'as, tu jį peržiūri.

Migracijos yra **apsaugotos (idempotentiškos)**: antras paleidimas nieko nekeičia, o migracija nesusipjauna su modulio `install`/`upgrade` scriptais, kurie gal jau pakeitė tą pačią lentelę.

- nauja lentelė – `CREATE TABLE IF NOT EXISTS`;
- naujas stulpelis ar indeksas – tik jei `information_schema` sako, kad jo dar nėra;
- `DROP`, `RENAME`, pakeisti raktai – juodraštyje paliekami kaip `-- TODO:` blokas su siūlomu SQL. Juos parašyk rankomis su patikra. Kol liko `TODO`, commit'as stabdomas, o `make db-migrate` migracijos netaiko.

```bash
make db-migrate
```

Pritaiko laukiančias migracijas vieną kartą kiekvienai DB ir įrašo jas į lentelę `setup_migrations` (jos snapshot'e nėra). Nepavykusi migracija neįrašoma: pataisyk ir paleisk dar kartą. Jau pritaikyta, bet vėliau pakeista migracija nekartojama, tik įspėjama.

Migracijos automatiškai paleidžiamos po `db-import`, `db-load`, `test-prepare` ir `deploy`. Deploy'aus tvarka: kodas → modulių upgrade'ai → migracijos → schemos ataskaita.

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
```

Kopija rašoma į `.generated/backups/<projektas>-<ENV>-<laikas>.sql.gz` (teisės `0600`). Esamas failas neperrašomas, nepavykusi kopija galutiniu vardu neišsaugoma. Stage/prod kopija daroma pagal registrą: visų lentelių struktūra ir tik `import` lentelių duomenys, todėl ji maža ir naudinga.

```bash
make db-backup-prune
make db-backup-prune apply=1
```

Pirma komanda parodo, kurias automatiškai pavadintas šios aplinkos kopijas galima ištrinti, antra – ištrina. Paliekamos `BACKUP_KEEP_LAST` (numatyta 5) naujausios.

Atkūrimas yra tas pats `make db-import`, todėl po jo vėl vykdomi after-import ir saugūs numatytieji.

Jei projektas turi [papildomų schemų](#papildomos-schemos), kopija apima jas visas ir atkuria jas visas.

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
| Snapshot'as, registras, migracijos, bazinis rinkinys, `db-load` | Tik `DATABASE_NAME`. |

**Kopijos formatas.** Su papildomomis schemomis kopija daroma vienu `mysqldump --databases` paleidimu: visos schemos nuskaitomos vienoje transakcijoje (suderinta kopija), o kiekviena schema kopijoje turi `CREATE DATABASE IF NOT EXISTS` ir `USE`. Todėl kopija atkuria visas schemas tais pačiais vardais: `make db-import file=…` (arba `mysql < kopija.sql`). Stage/prod kopijoje registras taikomas tik pagrindinei DB, papildomos schemos kopijuojamos pilnai. Be papildomų schemų kopija tokia pati kaip anksčiau: be `USE`, todėl ją galima užkelti ir į kitą `DATABASE_NAME`.

**Importas.** Dump'as gali perjungti schemą (`USE`) tik į `DATABASE_NAME` arba išvardintas schemas. Kitaip importas sustoja dar nieko nekeitęs. Dažniausia priežastis – LIVE pagrindinės DB vardas kitoks: suvienodink `DATABASE_NAME` arba pataisyk `USE` dump'e. Registro patikra ir `skip` taisyklė taikomos tik pagrindinės DB lentelėms.

**Jau veikianti DB.** Schemos kuriamos tik pirmą kartą paleidžiant tuščią DB. Jei schemą įrašai į sąrašą vėliau, paleisk `make db-restrict-grants`: jis sukurs schemą ir suteiks teises. Iš sąrašo išbrauktos schemos teisės savaime neatimamos.

## Senos DB teisės

Prieš v2 sukurtoje DB aplikacijos vartotojas galėjo turėti teises į visą serverį. Vieną kartą susiaurink:

```bash
make db-restrict-grants
```

Root gauna atskirą slaptažodį `DATABASE_ROOT_PASSWORD`, aplikacijos vartotojas lieka tik su teisėmis į savo DB ir [papildomas schemas](#papildomos-schemos) (be `GRANT OPTION`). Naujos DB taip kuriamos iš karto. Teisėse `_` ekranuojamas (`shop\_main`): kitaip `_` reikštų bet kurį simbolį ir vartotojas galėtų susikurti panašaus vardo DB, pvz. `shopXmain`. Seną DB, kurios vardas turi `_`, sutvarko tas pats `make db-restrict-grants`.

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

`doctrine-build` sukuria įrankio image'ą (PHP `DOCTRINE_PHP_VERSION`, numatyta 8.3, nepriklausomai nuo aplikacijos PHP). `doctrine-diff` sugeneruoja Doctrine migraciją iš Git snapshot'o (`ref=HEAD` pagal nutylėjimą) ir vietinės DB skirtumo. PrestaShop projektams įprastas kelias yra apsaugotos SQL migracijos iš [E dalies](#e-migracijos).
