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

Dideli ne kodo katalogai (tiekėjų failų cache, media) kopijavimą sulėtina. Juos praleisk `env/common.env`:

```dotenv
TEST_COPY_EXCLUDE=/modules/prgenericsync/cache
```

## B. PHPUnit

```bash
make phpunit
make phpunit module=prgenericsync
make phpunit module=prgenericsync filter=ImportTest
```

`make phpunit` pats randa rinkinius: `phpunit.xml` arba `phpunit.xml.dist` kiekviename modulyje (`modules/<modulis>/` arba `modules/<modulis>/tests/`) ir parduotuvės šaknyje (vardas `shop`). `module=` paleidžia vieną rinkinį, `filter=` perduodamas PHPUnit `--filter`.

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
2. Nusikopijuok setup šablono failus: `templates/grouped/compose/qa.yaml` → `compose/qa.yaml`, `templates/grouped/qa/playwright/` → `qa/playwright/`.
3. `env/common.env`:

```dotenv
PROJECT_COMPOSE_FILES=compose/qa.yaml
PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs
```

`compose/qa.yaml` prijungia `qa/playwright` konfigūraciją ir testus tik skaitymui. Be jo setup tik skaitymui prijungia `config/playwright/` (konfigūraciją) ir `tests/playwright/` (testus). Rezultatai visada rašomi į testinės aplinkos duomenų katalogą.

Testo pavyzdys `qa/playwright/tests/homepage.spec.cjs`:

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

Naršyklė kreipiasi į `https://<testinis domenas>` (`BASE_URL`) projekto tinkle. Ataskaita: `app/.generated/test/data/playwright/report/index.html`, nepavykusių testų medžiaga – `…/playwright/test-results`. Konkretus testas: `make e2e cmd='npx playwright test --config=/e2e/config/playwright.config.cjs --grep checkout'` (perduodama visa komanda, todėl nepamiršk `--config`). Konfigūracija: [Playwright](https://playwright.dev/docs/test-configuration).

## D. PHPStan

PHPStan analizuoja kodą ir nieko nekeičia, todėl jis vykdomas įprastoje aplinkoje.

1. `SERVICES` pridėk `php-phpstan`.
2. Nusikopijuok `templates/grouped/compose/qa.yaml` (jei dar neturi) ir `templates/grouped/qa/phpstan/`, `templates/grouped/qa/baselines/` į projektą, `PROJECT_COMPOSE_FILES=compose/qa.yaml`.
3. `env/common.env`: `PHPSTAN_BASELINE_FILE=/tmp/phpstan/baselines/phpstan.neon`.

`qa/phpstan/phpstan.neon`, kai tikrini tik savo modulį:

```neon
includes:
    - ../baselines/phpstan.neon

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
git diff -- qa/baselines/phpstan.neon
git add qa/baselines/phpstan.neon
```

Baseline yra ne pataisymas: peržiūrėk skirtumą prieš commit'indamas. Nustatymai: [PHPStan](https://phpstan.org/config-reference).

## E. PHP formatavimas (PHP-CS-Fixer)

1. `SERVICES` pridėk `php-cs`.
2. `templates/grouped/qa/php-cs/.php-cs-fixer.php` → `qa/php-cs/.php-cs-fixer.php`, `PROJECT_COMPOSE_FILES=compose/qa.yaml`.

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

## Kur kas laikoma

| Kas | Kur |
| --- | --- |
| Modulio PHPUnit rinkinys | Modulyje, keliauja kartu su moduliu (parduotuvės repozitorijoje). |
| E2e, PHPStan, PHP-CS konfigūracija | Aplinkos repozitorijoje `qa/`. |
| Rezultatai, cache, ataskaitos | `data/<įrankis>/` (testui – `.generated/test/data/`). Ne Git. |
