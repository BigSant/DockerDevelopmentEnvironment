# 8. PHPStan, formatavimas ir Playwright

[Turinys](README.md) · [Atgal](07-duomenu-baze.md) · [Toliau: aplinkos](09-aplinkos.md)

## Kuriam darbui kuris įrankis

| Užduotis | Įrankis | Kur taisyklės | Kur rezultatai |
| --- | --- | --- | --- |
| Rasti klaidingus tipus, neegzistuojančius metodus | PHPStan | `qa/phpstan/phpstan.neon` | `${PROJECT_DATA_DIRECTORY}/phpstan` |
| Patikrinti arba sutvarkyti PHP formatavimą | PHP-CS-Fixer | `qa/php-cs/.php-cs-fixer.php` | `${PROJECT_DATA_DIRECTORY}/php-cs` cache; taisomas aplikacijos kodas |
| Sutarti, kurių senų klaidų kol kas netaisyti | Įrankio baseline | `qa/baselines` | Git versijuojamas failas |
| Patikrinti veiksmą tikroje naršyklėje | Playwright | `qa/playwright/playwright.config.cjs` ir `tests/` | `${PROJECT_DATA_DIRECTORY}/playwright` |

QA įrankiai nėra privalomi kiekvienam projektui. Pats jų katalogas įrankio neįdiegia ir jo nesukonfigūruoja.

## A. Prijungti QA prie minimalios Forsenos tipo struktūros

Grouped generatoriaus pilname variante šie servisai ir failai jau aprašyti. Minimalioje Forsenoje jų nėra.

1. Nusikopijuok reikalingus `qa/` failus ir `compose/qa.yaml` iš [grouped šablono](../../templates/grouped).
2. Pridėk `compose/qa.yaml` į `PROJECT_COMPOSE_FILES`.
3. Į minimalų `compose/base.yaml` include sąrašą pridėk tik naudojamus servisus. Jei palieki visą šablono `qa.yaml`, reikia visų trijų aprašų:

```yaml
  - ${ROOT_DIRECTORY}/docker/php-phpstan/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/php-cs/docker-compose.yml
  - ${ROOT_DIRECTORY}/docker/playwright/docker-compose.yml
```

4. Į `env/common.env` įrašyk:

```dotenv
PHPSTAN_BASELINE_FILE=/tmp/phpstan/baselines/phpstan.neon
PLAYWRIGHT_COMMAND=npx playwright test --config=/e2e/config/playwright.config.cjs
```

5. Patikrink ir pagamink pasirinktus įrankius:

```bash
make init
make check
make build PROFILES=phpstan,phpcs,playwright
```

QA profilių nereikia nuolat dėti į `COMPOSE_PROFILES`. Komandos `make phpstan`, `make phpcs`, `make e2e` pačios pasirenka savo įrankio profilį. `make build PROFILES=phpstan` taip pat gamina pagrindinius servisus – tai nėra komanda „statyti tik vieną konteinerį“.

Jei reikia vien PHPStan, gali palikti tik jo include ir tik jo bloką projekto `qa.yaml`. Likę šablono papildymai neturi virsti servisais be `image` ar `build`.

## B. PHPStan tik savo moduliui

Situacija: senoje parduotuvėje nenori iš karto analizuoti viso framework, nori prižiūrėti tik savo modulį `supplierimport`.

`qa/phpstan/phpstan.neon`:

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

`qa/baselines/phpstan.neon`, kol išimčių nėra:

```neon
parameters:
    ignoreErrors: []
```

```bash
make phpstan
```

Patikra: komanda turi analizuoti pasirinktą modulį. Trūkstami framework simboliai gali reikšti, kad reikia projekto `bootstrapFiles`, autoload arba `stubFiles`, o ne baseline kiekvienai nesukonfigūruoto analizatoriaus klaidai.

| Nustatymas | Ką daro |
| --- | --- |
| `level` | Taisyklių griežtumas; pasirink pagal įrankio versiją ir projekto būklę. |
| `paths` | Tikrinami katalogai konteinerio viduje. |
| `tmpDir` | Rašomas cache, atskirtas nuo Git taisyklių. |
| `includes` | Papildomi NEON failai, pvz. baseline. |
| `excludePaths.analyse` | Kurių failų neanalizuoti. |
| `bootstrapFiles` | PHP paruošimo failai, kuriuos įrankis turi įkelti. |
| `stubFiles` | Aprašai simboliams, kurių įrankis kitaip nežino. |
| `phpVersion` | Analizuojamo kodo tikslinė PHP versija, neprivalanti sutapti su analizatoriaus PHP. |

Pilnas atitinkamos PHPStan versijos žinynas: [PHPStan konfigūracija](https://phpstan.org/config-reference).

Numatytoji `make phpstan` viduje kviečia `make report`. JSON ataskaita rašoma į `data/phpstan/report.json`. Jei analizė grąžina klaidos kodą, HTML žingsnis gali nebūti įvykdytas. Tada, kai JSON jau yra, atskirai vykdyk `make phpstan cmd=report-html` ir tikrink sugeneruotą ataskaitą cache kataloge.

Kiti scenarijai:

```bash
make phpstan cmd='dir-analysis DIR=modules/supplierimport'
make phpstan cmd=report-raw
make phpstan cmd=report-html
```

## C. Baseline senoms klaidoms

Situacija: nustatymai jau teisingi, bet modulyje yra daug žinomų senų klaidų. Norime matyti naujas, kol senas taisome palaipsniui.

```bash
make phpstan-baseline
git diff -- qa/baselines/phpstan.neon
git add qa/baselines/phpstan.neon
make phpstan
```

Baseline yra sutartas klaidų sąrašas, ne jų pataisymas. Į jį įtraukiami įrankio sugeneruoti konkretūs pranešimai ir vietos. Nepersigeneruok jo aklai kiekvieną kartą, kai atsiranda nauja klaida – peržiūrėk skirtumą.

Generavimui `qa/baselines` mount turi būti writable. Testinėje aplinkoje paprastai jį prijunk read-only ir naują baseline generuok vietoje. Kitų įrankių failai gali būti `qa/baselines/psalm.xml`, `deptrac.yaml` ir pan., tačiau jų skaitymą bei generavimo komandą turi sukonfigūruoti tų įrankių aprašuose. Setup neturi universalaus `make baseline` visiems įrankiams.

## D. PHP formatavimo tikrinimas ir taisymas

Situacija: komanda susitarė dėl PSR-12, bet nori liesti tik savo modulį.

`qa/php-cs/.php-cs-fixer.php`:

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
```

Tai patikros: naudojamas `--dry-run --diff`. Kai nori keisti failus:

```bash
make phpcs cmd='dir-fix DIR=modules/supplierimport'
git -C public diff
```

`make phpcs cmd=fix` gali sutvarkyti visus pagal config atrinktus failus. Jis realiai rašo į aplikaciją; peržiūrėk jos Git skirtumus.

Grouped šablone papildomai įjungta `@PHP80Migration`. Senam PS 1.6 moduliui to aklai nenaudok: taisyklės gali generuoti kodą, netinkamą senai PHP versijai. Pritaikyk taisykles tiksliniam projektui. Konfigūracijos API: [PHP-CS-Fixer](https://cs.symfony.com/doc/config.html).

## E. Pirmas Playwright testas

Situacija: nori patikrinti, kad aplikacijos pagrindinis puslapis pasiekiamas. Pradžiai naudok ne-PS `demo` pavyzdį, kuris nekeičia domeno ir porto per peradresavimus.

`qa/playwright/playwright.config.cjs`:

```javascript
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const data = process.env.PLAYWRIGHT_DATA_DIR || '/e2e/data';
module.exports = defineConfig({
  testDir: process.env.PLAYWRIGHT_TEST_DIR || './tests',
  outputDir: path.join(data, 'test-results'),
  timeout: 30000,
  retries: 0,
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(data, 'report'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.BASE_URL,
    ignoreHTTPSErrors: process.env.PLAYWRIGHT_IGNORE_HTTPS_ERRORS === 'true',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
```

`qa/playwright/tests/homepage.spec.cjs`:

```javascript
const { test, expect } = require('@playwright/test');

test('pagrindinis puslapis pasiekiamas', async ({ page }) => {
  const response = await page.goto('/');
  expect(response.status()).toBe(200);
  await expect(page.locator('body')).toContainText('Demo veikia');
});
```

`compose/local.yaml` papildymas:

```yaml
services:
  playwright:
    environment:
      BASE_URL: http://${DOMAIN}
```

Šiuo variantu domeno tinklo alias veda tiesiai į `nginx-proxy` konteinerio 80 portą, todėl kompiuterio HTTP porto nereikia. Pirmiausia paleisk ir patikrink aplikaciją:

```bash
make up
make e2e
```

Patikra: testas praeina, HTML ataskaita yra `data/playwright/report/index.html`, nesėkmingo testo medžiaga – `data/playwright/test-results`.

Tavo parduotuvėje vietoje „Demo veikia“ gali būti matomas paieškos laukas, prekių kategorijos pavadinimas ar mygtukas. Pirkimo testui prireiks žinomų fixture duomenų: prekės, atsargų, kliento ir mokėjimo būdo. Vien homepage testas pirkimo nepatvirtina.

| Playwright laukelis / env | Paskirtis |
| --- | --- |
| `testDir` / `PLAYWRIGHT_TEST_DIR` | Kur ieškoti testų. QA prijungimas nustato `/e2e/tests`. |
| `outputDir` / `PLAYWRIGHT_DATA_DIR` | Kur rašyti rezultatus; QA naudoja `/e2e/data`. |
| `use.baseURL` / `BASE_URL` | Pradinis naršyklės adresas. |
| `use.ignoreHTTPSErrors` / `PLAYWRIGHT_IGNORE_HTTPS_ERRORS` | Vietinių nepatikimų TLS klaidų ignoravimas tik sąmoningai pasirinktam bandymui. |
| `timeout` | Vieno testo laiko limitas milisekundėmis. |
| `retries` | Kiek kartų kartoti nepraėjusį testą; pradžioje geriau matyti klaidą iš karto. |
| `reporter` | Terminalo ir failinių ataskaitų pasirinkimas. |
| `trace`, `screenshot` | Kokius diagnostikos failus saugoti. |
| `projects` | Keli naršyklių arba testų konfigūracijos variantai. |

Pilnas konfigūracijos aprašas: [Playwright](https://playwright.dev/docs/test-configuration).

## F. Parduotuvė peradresuoja į adresą su kompiuterio portu

Situacija: PS DB saugo `demo.localhost:31820`. Konteinerio DNS alias veda į proxy, bet proxy **viduje** neklauso 31820 porto.

Tokiam projektui naudok tikrą išorinį adresą ir priskirk domeną Docker hostui:

```yaml
services:
  playwright:
    extra_hosts:
      - "${DOMAIN}:host-gateway"
    environment:
      BASE_URL: http://${DOMAIN}:${LOCALHOST_PORT}
```

Šiame variante naršyklė kreipiasi į kompiuterio paskelbtą portą. Testui tą papildymą dėk į `compose/test.yaml`, naudok būtent testinį URL ir [sutvarkyk testinės HTTP aplinkos paruošimą](09-aplinkos.md).

## G. Kitas analizatorius arba daugiau testų katalogų

Gali sukurti `qa/psalm`, `qa/deptrac`, `qa/phpunit`, `qa/baselines/psalm.xml`, `public/tests/Unit` ar `public/tests/Integration`. Pasirinkimas priklauso nuo to, ar testai versijuojami su aplikacija, ar su aplinkos įrankiais.

Kad tai veiktų, reikia įrankio atvaizdo arba aplikacijos priklausomybės, jo config, prijungimų ir paleidimo komandos. Bendras runner turi konkrečias `phpstan`, `phpcs`, `e2e`, `doctrine` komandas; savą `psalm` servisą kviesk per tiesioginę Compose komandą arba pridėk projektinį Make target po bendro include. [Compose plėtimo skyrius](04-compose-ir-dockerfile.md) parodo visus reikalingus prijungimo laukus.

Komandų vidiniai `git-analysis`, `git-check` ir panašūs variantai remiasi aplikacijos Git būsena bei bazinės šakos vardu. Jei tavo šaka yra `master`, o įrankis numato `origin/main`, perduok `BASE_BRANCH=origin/master` vidinei komandai. Patikra tik pagal pakeistus failus nepakeičia viso projekto analizės, nes vieno failo klaida gali priklausyti nuo kito failo pakeitimo.
