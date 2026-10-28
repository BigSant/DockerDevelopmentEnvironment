# 13. PHP ir JS/CSS įrankiai

[Turinys](README.md) · [Atgal](12-cache-ir-hibridines-aplinkos.md)

## Kur yra įrankiai

PHP 8.x konteineryje yra PHP, Composer, Node, npm, npx, Webpack ir PHPStan PHAR. Atskirų servisų jiems nereikia. Xdebug yra tik local/test image'uose, stage/prod jo neturi. Seni PHP 5.x/7.x image'ai neturi tokio paties Node įrankių rinkinio. Visi PHP image'ai turi sukompiliuotus, bet neįjungtus `redis` ir `memcached` plėtinius ([įjungimas](05-servisai.md#redis-ir-memcached)).

PHP konteineris komandas vykdo tavo vartotojo UID, todėl sukurti failai nepriklauso root.

## Versijos env failuose

Bendros versijos yra `setup/docker/.env`. Projekto `env/common.env` jas pakeičia visoms aplinkoms. Versijos paveldimos iš setup, todėl setup atnaujinimas pasiekia visus projektus, kurie jų neperrašė.

Pavyzdys PHP 8.4 projektui (Forsena):

```dotenv
PHP_VERSION=8.4
XDEBUG_VERSION=3.5.3
COMPOSER_VERSION=2.10.3
```

Visas versijų sąrašas: [nustatymų žinynas](03-nustatymai.md#programų-versijos).

- `PHP_VERSION` parenka Dockerfile katalogą, todėl rašyk šaką (`8.4`), ne `8.4.12`.
- Xdebug versija turi [derėti su PHP](https://xdebug.org/docs/compat).
- Keičiant NVM ar Supercronic versiją kartu keičiama ir kontrolinė suma (`NVM_INSTALL_SHA256`, `SUPERCRONIC_SHA1SUM`).
- PrestaShop projekte PHP versija turi būti [suderinama su PS šaka](06-aplikacija.md#prestashop-ir-php-suderinamumas), tai patikrina `make check`.

Pakeitus versiją:

```bash
make check
make build
make up
```

Pasikeitus PHP versijai perkuriami tik PHP image'ai, kiti lieka.

## Composer ir npm

`dir` yra kelias **nuo `public/`**:

```bash
make composer
make npm
make npm dir=themes/mytheme/_dev cmd=ci
make npm dir=themes/mytheme/_dev cmd='run build'
make npm dir=themes/mytheme/_dev cmd='run watch'
make composer dir=modules/mymodule cmd=install
```

Be `cmd` komandos parodo įrankio versiją. `ci` įdiegia priklausomybes pagal `package-lock.json`, `run build` surenka temos JS/CSS, `run watch` stebi pakeitimus (sustabdyk Ctrl+C). Nerašyk `dir=public/themes/...`: `public` konteineryje jau yra `/var/www/html`. PHP konteineris turi veikti.

npm podėlis yra `/tmp/setup-npm-cache`, Composer namų katalogas – `/tmp/setup-composer` (`NPM_CONFIG_CACHE`, `COMPOSER_HOME`). `/tmp` turinys dingsta perkūrus konteinerį.

Aplikacijos bibliotekų versijas lemia `composer.lock` ir `package-lock.json`, ne setup. `WEBPACK_VERSION` valdo tik globalų Webpack, o `npm run build` pirmiausia naudoja temos įdiegtą.

## Parduotuvės vendor ir composer

PrestaShop parduotuvės `vendor/` gali atsirasti dviem būdais (žr. [aplikacijos skyrių](06-aplikacija.md#oficialus-prestashop-release)):

- `PS_RELEASE=on` ir `PS_RELEASE_PATHS=vendor` – nukopijuojamas iš oficialaus release;
- projekto `config/composer/composer.json` ir `composer.lock` – `make setup` juos nukopijuoja į parduotuvę ir paleidžia `composer install`.

Deploy'aus metu build serveris vykdo `composer install --no-dev` release image'e, o asset'us (pvz. `npm ci && npm run build`) gali surinkti projekto `config/deploy/build.sh`. Jis veikia build kompiuterio naudotojo vardu (release'o kopija yra jo; failų savininką taikinyje lemia deploy'aus paskyra) be namų katalogo, todėl `HOME` ir Composer podėlis yra `/tmp`.

`build.sh` gauna ir katalogą, kuris išlieka tarp release'ų: `SETUP_BUILD_CACHE` (`/setup-build-cache`, build kompiuteryje `.generated/build-cache/` aplinkos repozitorijoje). Jame laikomas npm podėlis (`npm ci` atsisiunčia tik tai, ko dar neturi), o projektas gali ten laikyti savo build'ų rezultatus ir build'ą praleisti, kai šaltiniai nepasikeitė, pvz.:

```sh
key=$( (cd themes/mytheme/_dev && find . -path ./node_modules -prune -o -type f -print | LC_ALL=C sort \
        | xargs -d '\n' sha256sum) | sha256sum | cut -c1-16)
archive="$SETUP_BUILD_CACHE/builds/mytheme-$key.tar"
if [ -f "$archive" ]; then tar -xf "$archive"; else
    (cd themes/mytheme/_dev && npm ci && npm run build) && rm -rf themes/mytheme/_dev/node_modules
    mkdir -p "$SETUP_BUILD_CACHE/builds" && tar -cf "$archive" themes/mytheme/assets
fi
```

Release'o kopija į serverį nusiunčiama tokia, kokia yra, todėl `node_modules` po build'o ištrink.

Prieš keisdamas PHP versiją seno projekto `composer.lock` patikrink naujame PHP image'e:

```bash
make composer cmd='check-platform-reqs --lock --no-dev'
```

Sena biblioteka gali riboti PHP versiją, net jei PS šaka naują PHP palaiko. Atnaujink tik nesuderinamas bibliotekas ir commit'ink `composer.lock` kartu su `composer.json`.

## PHP image'o patikra

Surinktą vietinį PHP image'ą galima patikrinti be aplikacijos ir DB. `setup` kataloge:

```bash
python3 tests/build_php_image.py tests/fixtures/php-images/8.4.env
SETUP_PHP_TEST_IMAGE=setup-php-image-test python3 -m unittest discover -s tests -p test_php_image_integration.py -v
```

Tikrinami privalomi plėtiniai, GD su JPEG/WebP/FreeType, PHP/FPM nustatymai ir įrankių veikimas su UID 1000. Tai image'ų, o ne konkrečios PrestaShop versijos ar modulių suderinamumo testai. Paruoštos versijos: `tests/fixtures/php-images/8.4.env` ir `8.5.env`.
