# 8. Testai ir kodo kokybė

[Turinys](README.md) · [Atgal](07-duomenu-baze.md) · [Toliau: aplinkos ir deploy](09-aplinkos.md)

## Testai tik izoliuotoje ENV=test aplinkoje

Testai **niekada nepasileidžia automatiškai** ir **niekada neliečia tavo darbinės DB**. PHPUnit ir e2e testai vykdomi tik `ENV=test` aplinkoje, kuri turi atskirą kodo kopiją, DB, konteinerius ir domeną. Priežastis: buvo atvejis, kai integracinis testas ant vietinės DB ištrynė tikrą tiekėją.

| | `ENV=local` | `ENV=test` |
| --- | --- | --- |
| Kodas | `app/public` | `app/.generated/test/app` (kopija) |
| Duomenys | `data/` | `app/.generated/test/data/` |
| DB | `DATABASE_NAME` | `<DATABASE_NAME>_test`, vartotojas `test` |
| Domenas | `demo.local` | `demo.test.local` |
| Env | `env/local.env` | `env/test.env` (sukuriamas automatiškai) |

## A. Paruošti testinę aplinką

```bash
make test-prepare
```

Komanda vykdoma iš įprastos (local) aplinkos ir:

1. Sukuria arba atnaujina kodo kopiją `.generated/test/app` (rsync). Nekopijuojami `.git`, `node_modules`, `var/cache`, `var/logs`, `cache` ir `TEST_COPY_EXCLUDE` keliai.
2. Pirmą kartą sukuria `env/test.env` su atskiru DB vardu, slaptažodžiais, portais ir domenu, paruošia domeną ir TLS.
3. Paleidžia testinę DB ir į ją užkelia [bazinį rinkinį](07-duomenu-baze.md#f-bazinis-rinkinys) (`set=dev` pagal nutylėjimą) su after-import ir migracijomis.
4. Paleidžia visą `ENV=test` ir parašo jos adresą.

Kitas rinkinys: `make test-prepare set=<rinkinys>` (katalogas `database/data/<rinkinys>/` turi egzistuoti). Testinė DB kiekvieną kartą perrašoma iš naujo, todėl testai visada pradeda nuo žinomos būsenos.

Vietoj rinkinio – dump'as: `make test-prepare dump=/kelias/backup.sql.gz` importuoja jį į testinę DB (kaip `make db-import ENV=test`: lentelės patikrinamos be klausimų, po to `common/` ir `test/` after-import SQL). Rinkinys tada nekeliamas. `set=` ir `dump=` kartu – klaida.

Serveryje (setup-server testų sesija) nėra mkcert CA ir sudo hosts įrašui ar host Nginx. Tam `env/test.env`:

```dotenv
TEST_TLS=self-signed      # savo pasirašytas sertifikatas (openssl), be root diegimo; Playwright ignoreHTTPSErrors jį priima
TEST_HOST_ROUTE=off       # be /etc/hosts įrašo ir host Nginx: domenas pasiekiamas tik projekto tinkle (kaip Playwright jį pasiekia)
```

Jei `env/test.env` jau yra (pvz. setup-server įrašė tik šiuos du raktus), `make test-prepare` papildo tik trūkstamas sugeneruotas reikšmes (domeną, portus, DB vardą ir slaptažodžius).

Dideli ne kodo katalogai (tiekėjų failų cache, media) kopijavimą sulėtina. Juos praleisk `env/common.env`:

```dotenv
TEST_COPY_EXCLUDE=/modules/genericsync/cache
```

## B. PHPUnit

```bash
make phpunit
make phpunit module=genericsync
make phpunit module=genericsync filter=ImportTest
```

`make phpunit` pats randa rinkinius: `phpunit.xml` arba `phpunit.xml.dist` kiekviename modulyje (`modules/<modulis>/` arba `modules/<modulis>/tests/`) ir parduotuvės šaknyje (vardas `shop`). Generic projekte (`PROFILE` ne `prestashop`) `modules/` – paprastas katalogas: rinkinys ieškomas tik aplikacijos šaknyje arba jos `tests/` (vardas `app`). `module=` paleidžia vieną rinkinį, `filter=` perduodamas PHPUnit `--filter`.

**Projekto rinkinys** laikomas aplinkos repozitorijoje, už document root ribų: `tests/phpunit.xml(.dist)` ir testai šalia (`tests/playwright/` lieka e2e). Testinėje kopijoje jis atsiduria `.setup-tests/project/`, todėl parduotuvės failus pasieki per `../../` (pvz. `bootstrap="../../config/config.inc.php"`). Paleidimas: `make phpunit module=project`.

Prieš paleidžiant kodo kopija atnaujinama iš tavo darbinio katalogo, todėl testai mato naujausius pakeitimus. `sync=0` šį žingsnį praleidžia.

PHPUnit versija imama iš rinkinio ar modulio `.tools/phpunit.phar`, modulio `vendor/bin/phpunit` arba parduotuvės `vendor/bin/phpunit`. Jei niekur nėra, setup atsisiunčia PHPUnit PHAR, tinkantį `PHP_VERSION` (pvz. PHP 8.1+ – 10.5).

Komanda atsisako veikti, jei `ENV=test` neparuošta (`run make test-prepare first`) arba jei jos DB vardas sutampa su vietine.

Atnaujinti tik kodo kopiją, be testų:

```bash
make test-sync
```

PhpStorm tai daro automatiškai prieš kiekvieną PHPUnit paleidimą, žr. [IDE](10-kasdienis-darbas.md#testai-iš-phpstorm).

## C. E2e testai su Playwright

E2e testai rašo užsakymus ir klientus, todėl jie irgi vykdomi tik `ENV=test`. `make e2e` pats nukreipia į testinę aplinką.

Paruošimas (vieną kartą):

1. `env/common.env`: `SERVICES` pridėk `playwright`.
2. Aplinkos repozitorijoje: konfigūracija `config/playwright/playwright.config.cjs`, testai `tests/playwright/`. Setup abu katalogus prijungia tik skaitymui (`/e2e/config`, `/e2e/tests`), papildomo Compose failo nereikia.
3. `env/common.env`:

```dotenv
PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs
```

`config/playwright/playwright.config.cjs` (testų katalogą, duomenų katalogą ir `BASE_URL` duoda setup):

```javascript
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const dataDirectory = process.env.PLAYWRIGHT_DATA_DIR;

module.exports = defineConfig({
  testDir: process.env.PLAYWRIGHT_TEST_DIR,
  outputDir: path.join(dataDirectory, 'test-results'),
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(dataDirectory, 'report'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL,
    ignoreHTTPSErrors: true,
    trace: 'retain-on-failure',
  },
});
```

`ignoreHTTPSErrors: true` reikalingas, nes testinė aplinka naudoja vietinį sertifikatą. Rezultatai visada rašomi į testinės aplinkos duomenų katalogą.

Testo pavyzdys `tests/playwright/homepage.spec.cjs`:

```javascript
const { test, expect } = require('@playwright/test');

test('pagrindinis puslapis pasiekiamas', async ({ page }) => {
  const response = await page.goto('/');
  expect(response.status()).toBe(200);
});
```

```bash
make build
make test-prepare
make e2e
```

### Specų priklausomybės (`make e2e-install`)

Jei `tests/playwright/` (gali būti git submodulis) turi `package.json` ir `package-lock.json` (pvz. TypeScript projektas su savo pagalbinėmis bibliotekomis), `make e2e-install` (iš `ENV=local`) nukopijuoja `package.json`, `package-lock.json` ir `.npmrc` (symlink'ai atmetami) į `.generated/e2e/` ir vienkartiniame Playwright konteineryje ten paleidžia `npm ci --ignore-scripts`. Kartojama tik pasikeitus kuriam nors iš tų trijų failų (ar `PLAYWRIGHT_VERSION`): žymė `.generated/e2e/.setup-e2e-install`. Kiekvienas paleidimas `.generated/e2e/node_modules` mato tik skaitymui kaip `/e2e/tests/node_modules` – tai, kas yra `tests/playwright/node_modules`, niekada neįkeliama. Projekto `@playwright/test`, `playwright` ir `playwright-core` po diegimo pašalinami – visada naudojamas image'o `@playwright/test` (jis atitinka image'o naršykles). Patys testų paleidimai specus ir konfigūraciją toliau mato tik skaitymui; Playwright konteineris veikia kaip paleidęs vartotojas, su `PLAYWRIGHT_MEMORY` (2g) ir `PLAYWRIGHT_CPUS` (2) ribomis, ne daugiau 1024 procesų, be capabilities ir su `no-new-privileges`.

### Atranka ir JSON ataskaita

```bash
make e2e grep='@cart' grep_invert='@slow' files='cart.spec.ts checkout/pay.spec.ts:12' json=1
```

`grep`/`grep_invert` – Playwright `--grep`/`--grep-invert` (reguliarioji išraiška pavadinimui su tagais), `files` – specų filtrai (`FAILAS[:EILUTĖ]`, be `..`). Jie pridedami prie `PLAYWRIGHT_COMMAND` (todėl komanda turi baigtis `playwright test …`; per `npm run` – su `--` gale). `json=1` reporterius pakeičia į `list,html,json`: `data/playwright/report.json` šalia HTML ataskaitos `data/playwright/report/`. `storage_states='admin=/kelias/state.json'` kiekvieną failą prijungia tik skaitymui kaip `/e2e/auth/<rolė>.json` (`E2E_STORAGE_STATE_ADMIN`, visi – JSON `E2E_STORAGE_STATES`). `container=VARDAS` – konteinerio vardas (kad kviečiantysis galėtų jį sustabdyti). Be šių parametrų `make e2e` veikia kaip anksčiau.

### Prieš tikrą adresą (STAGE / LIVE)

```bash
make e2e url=https://stage.example.com grep='@stage' json=1 data=/kelias/rezultatams
```

Tas pats Playwright image'as, specai ir konfigūracija, bet `BASE_URL` – nurodytas adresas, konteineris Docker numatytame `bridge` tinkle (jokio projekto tinklo), šios paskyros vardu, `E2E_EXTERNAL=1`. Paleidžiama iš `ENV=local` (ne `ENV=test`). `data=` – rezultatų katalogas (numatyta – aplinkos `data/playwright`). Kurie testai tinka STAGE ar LIVE, sprendžia kviečiantysis (setup-server tai daro tagais `@stage`, `@stage-write`, `@live`, `@deploy`).

Naršyklė kreipiasi į `https://<testinis domenas>` (`BASE_URL`) projekto tinkle. Ataskaita: `app/.generated/test/data/playwright/report/index.html`, nepavykusių testų medžiaga – `…/playwright/test-results`. Konkretus testas: `make e2e cmd='npx playwright test --config=/e2e/config/playwright.config.cjs --grep checkout'` (perduodama visa komanda, todėl nepamiršk `--config`). Konfigūracija: [Playwright](https://playwright.dev/docs/test-configuration).

## D. PHPStan

PHPStan analizuoja kodą ir nieko nekeičia, todėl jis vykdomas įprastoje aplinkoje.

1. `SERVICES` pridėk `php-phpstan`.
2. Sukurk aplinkos repozitorijoje katalogą `config/phpstan/` (konteineryje `/tmp/phpstan/config/`). Jei jame nėra `phpstan.neon`, pirmas `make phpstan` įrašo numatytąjį (visa aplikacija, `level: 6`), toliau failas tavo.
3. Baseline'ui `env/common.env`: `PHPSTAN_BASELINE_FILE=/tmp/phpstan/config/baseline.neon` (numatytoji reikšmė tuščia, be jos `make phpstan-baseline` sustoja). Failas atsiras `config/phpstan/baseline.neon`.

Prieš pirmą baseline sukurk tuščią `config/phpstan/baseline.neon`, nes `includes` reikalauja esamo failo:

```neon
parameters:
	ignoreErrors: []
```

`config/phpstan/phpstan.neon`, kai tikrini tik savo modulį:

```neon
includes:
    - baseline.neon

parameters:
    level: 6
    paths:
        - /var/www/html/modules/supplierimport
    tmpDir: /tmp/phpstan/cache
    excludePaths:
        analyse:
            - /var/www/html/modules/supplierimport/vendor
```

```bash
make build
make phpstan
make phpstan cmd='dir-analysis DIR=modules/supplierimport'
```

JSON ataskaita rašoma į `data/phpstan/report.json`. Kitos užduotys: `report-raw`, `report-html`, `git-analysis BASE_BRANCH=origin/master`.

**Baseline** – sutartas senų klaidų sąrašas, kad matytum tik naujas:

```bash
make phpstan-baseline
git diff -- config/phpstan/baseline.neon
git add config/phpstan/baseline.neon
```

Baseline yra ne pataisymas: peržiūrėk skirtumą prieš commit'indamas. Nustatymai: [PHPStan](https://phpstan.org/config-reference).

## E. PHP formatavimas (PHP-CS-Fixer)

1. `SERVICES` pridėk `php-cs`.
2. Sukurk aplinkos repozitorijoje katalogą `config/php-cs/` (konteineryje `/tmp/php-cs-fixer/config/`). Jei jame nėra `.php-cs-fixer.php`, pirmas `make phpcs` įrašo numatytąjį (visa aplikacija, `@PSR12` ir `@PHP80Migration`), toliau failas tavo.

`config/php-cs/.php-cs-fixer.php`, kai tvarkai tik savo modulį:

```php
<?php
$finder = PhpCsFixer\Finder::create()
    ->in('/var/www/html/modules/supplierimport')
    ->exclude('vendor');

return (new PhpCsFixer\Config())
    ->setRules(array('@PSR12' => true))
    ->setFinder($finder)
    ->setCacheFile('/tmp/php-cs-fixer/cache/.php-cs-fixer.cache');
```

```bash
make phpcs
make phpcs cmd='dir-check DIR=modules/supplierimport'
make phpcs cmd='dir-fix DIR=modules/supplierimport'
git -C public diff
```

Pirmos dvi komandos tik tikrina (`--dry-run --diff`). `dir-fix` ir `fix` keičia aplikacijos failus: peržiūrėk jų Git skirtumus. Senam PHP kodui nenaudok taisyklių, kurios generuoja naujesnio PHP sintaksę. Taisyklės: [PHP-CS-Fixer](https://cs.symfony.com/doc/config.html).

## F. Patikros prieš commit'ą ir push'ą (git hook'ai)

Klonas `.git/hooks` neatsineša, todėl hook'us įrašo setup: `make setup` (`ENV=local`, `bootstrap` žingsnyje) ir
`make git-hooks` (jau veikiančiame klone). Projekto patikros laikomos Git'e, aplinkos repozitorijos `bin/`
kataloge, o setup'o hook'as jas tik paleidžia:

| Hook'as | Ką paleidžia |
| --- | --- |
| `pre-commit` | PrestaShop parduotuvės vieno `.gitignore` patikrą (žr. žemiau), setup'o schemos patikrą ([DB kaip kodas](07-duomenu-baze.md#b-schema-git-ir-commit-hookas)), tada `bin/pre-commit`. Nepraėjus patikrai projekto scriptas nepaleidžiamas. |
| `pre-push` | `bin/pre-push`. Git jam perduoda remote'o vardą ir adresą argumentais, o push'inamas šakas – įvestimi (`<vietinė šaka> <sha> <remote šaka> <sha>`). |

- Scriptas turi būti vykdomas (`chmod +x`). Jo išėjimo kodas sustabdo commit'ą ar push'ą.
- Hook'as scriptą randa tik paleistas, todėl naują ar pakeistą `bin/` scriptą perinstaliuoti nereikia. Tik visai
  naujam hook'ui (pvz. projektas pirmą kartą pridėjo `bin/pre-push`) paleisk `make git-hooks`.
- Hook'as nerašomas, jei jam nėra ką paleisti (nei schemos patikros, nei projekto scripto).
- Kieno nors kito hook'o, symlink'o ar `core.hooksPath` tvarkyklės setup neliečia: praneša ir palieka. Setup'o
  hook'ą atpažįsta iš žymės `# Shared setup git hook v1` (ir senesnio schemos hook'o) ir perrašo.
- Kartą praleisti: `git commit --no-verify`, push'o atveju – ta pati `--no-verify` parinktis.

**Vienas parduotuvės `.gitignore` (PrestaShop).** Modulio autoriaus `.gitignore` tyliai paslepia failus, kuriuos
parduotuvės `.gitignore` nori sekti (modulio `vendor/`, testus, laiškus, paveikslėlius). Todėl visos taisyklės laikomos
parduotuvės šaknies `.gitignore`, keliais nuo šaknies, o commit'as, kuriame būtų dar vienas `.gitignore`, sustoja su
sąrašu ir nurodymu: taisykles apjungti į šaknies `.gitignore` (`data/*.xml` faile `modules/x/.gitignore` →
`/modules/x/data/*.xml`), sutvarkyti (išmesti tai, ką šaknies failas jau dengia, ir taisykles, slepiančias kodą), tada
failus pašalinti (`git rm`). Savo `.gitignore` palieka tik composer paketai `vendor/` kataloguose. Tikrinama tai, kas
būtų repozitorijoje: sekami ir nauji, neignoruojami `.gitignore`. Kai parduotuvė turi savo repozitoriją
(`app/public/.git`), setup jai įrašo atskirą `pre-commit` (tik ši patikra); kai ji yra aplinkos repozitorijos
katalogas, patikra eina aplinkos `pre-commit` su keliu (pvz. `public/`). Generic profilis netikrinamas.

Pavyzdys: projektas, kurio push'as į `master` deploy'ina, `bin/pre-push` faile paleidžia testus tik tokiam push'ui:

```sh
#!/bin/sh
while read -r local_ref local_sha remote_ref remote_sha; do
    [ "$remote_ref" = refs/heads/master ] && exec make phpstan cmd=bitbucket-analysis </dev/null
done
exit 0
```

## Kur kas laikoma

| Kas | Kur |
| --- | --- |
| Modulio PHPUnit rinkinys | Modulyje, keliauja kartu su moduliu (parduotuvės repozitorijoje). |
| E2e, PHPStan, PHP-CS konfigūracija | Aplinkos repozitorijoje: `config/playwright/`, `config/phpstan/`, `config/php-cs/`; e2e testai – `tests/playwright/`. |
| Rezultatai, cache, ataskaitos | `data/<įrankis>/` (testui – `.generated/test/data/`). Ne Git. |
