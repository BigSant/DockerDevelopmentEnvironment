# 9. Aplinkos, release ir deploy

[Turinys](README.md) · [Atgal](08-kodo-kokybe.md) · [Toliau: kasdienis darbas](10-kasdienis-darbas.md)

## Keturios aplinkos

| `ENV` | Env failas | Kodas (nuo projekto šaknies) | Duomenys | Paskirtis |
| --- | --- | --- | --- | --- |
| `local` (numatyta) | `env/local.env` | `app/public` | `data/` | Kasdienis darbas. |
| `test` | `env/test.env` | `app/.generated/test/app` | `app/.generated/test/data/` | Testai, žr. [8 skyrių](08-kodo-kokybe.md). |
| `stage` | `env/stage.env` | `app/stage/public` | `data/stage/` | Tarpinis serveris. |
| `prod` | `env/prod.env` | `app/prod/public` | `data/prod/` | LIVE. |

Konteinerių ir tinklo varduose yra aplinka (`demo-local-…`, `demo-test-…`), todėl kelios aplinkos gali veikti kartu. Aplinką pasirenka `ENV=` bet kurioje komandoje: `make ps ENV=test`.

## Ką stage ir prod reikalauja

Tas pats CORE veikia visose aplinkose, bet stage/prod turi griežtesnes taisykles. Komanda sustoja su klaida, jei:

- nenurodyti keturių pagrindinių servisų limitai `PHP_MEMORY`, `PHP_CPUS`, `DATABASE_MEMORY`, `DATABASE_CPUS`, `WEBSERVER_MEMORY`, `WEBSERVER_CPUS`, `PROXY_MEMORY`, `PROXY_CPUS`. Klaidos pranešimas pasiūlo reikšmes pagal Docker host'o dydį ([resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui));
- įjungtas `pma`, `mailpit`, `cron`, `dbmonitor` ar `metrics`, bet nėra jo IP sąrašo ([IP apribojimai](05-servisai.md#ip-apribojimai));
- įjungtas `metrics`, bet `METRICS_TOKEN` trumpesnis nei 24 simboliai;
- nurodytas `XDEBUG_MODE` (stage/prod image'uose Xdebug nėra);
- release'ui nenurodyti `APP_UID`, `APP_GID`, `DEPLOY_PATH` (ir `DEPLOY_CONTEXT` siuntimui į serverį);
- nurodytas `DEPLOY_CONTEXT`, bet nėra `APP_UID`/`APP_GID` (bet kuri komanda).

Kiti skirtumai pagal nutylėjimą: `CACHE_MODE` įjungtas, `HTTP_FORCE_HTTPS=on`, prod `MAIL_MODE=preserve`, prod po DB užkėlimo saugūs numatytieji ir integracijų išjungimas **nevykdomi**. `make bootstrap` ir PS raktų generavimas veikia tik local/test: serverių reikšmės pateikiamos aiškiai.

Stage/prod konteineriai veikia kaip hosting'o (DirectAdmin) vartotojas `APP_UID`/`APP_GID`, todėl failų savininkas serveryje vienas.

## D modelis: kaip kodas pasiekia serverį

```text
build serveris (kol jo nėra – programuotojo kompiuteris)
  ├─ make release        → sukuria image'us iš commit'into setup, užrašo lock failą
  ├─ make deploy-images  → docker save | ssh | docker load, paleidžia konteinerius serveryje
  └─ make deploy ref=…   → paruošia kodo release'ą, DB kopija, rsync, hook'as, upgrade'ai, migracijos
                                │
LIVE serveris: tik Docker, kodas, duomenys ir DirectAdmin web serveris
```

- Image'ai kuriami build serveryje ir perduodami per SSH. LIVE neturi kūrimo įrankių ir setup kopijos: setup runtime scriptai ir CORE konfigūracija įkepti į image'us.
- Serveris valdomas per Docker context (`DEPLOY_CONTEXT`). Kai jis nurodytas, `make ps`, `make logs`, `make shell`, `make db-backup` ENV=stage/prod vykdomi serveryje, o `make up` / `make down` atmetami: konteinerius paleidžia `make deploy-images`.
- Serveriuose naudojamas fiksuotas setup tag'as: `make release ENV=prod` veikia tik kai setup yra tiksliai ant release tag'o (`vX.Y.Z`), stage tinka bet kuris commit'as. Release iš necommit'into setup atmetamas.
- **TLS baigiasi DirectAdmin'e** (jo Let's Encrypt). Konteinerio portas rišamas tik prie `127.0.0.1`, DirectAdmin web serveris perduoda užklausas į `http://127.0.0.1:<LOCALHOST_PORT>`. nginx-proxy priima tikrą kliento IP iš `X-Forwarded-For`, o `X-Forwarded-Proto: https` PHP pusėje tampa HTTPS žyma. Konkreti DirectAdmin proxy konfigūracija šiame vadove neaprašyta.
- **VPN serveris be DirectAdmin** (pvz. asana.local): `PUBLISH_ADDRESS` – serverio VPN sąsajos adresas (`env/prod.env`, pvz. `PUBLISH_ADDRESS=10.8.0.14`). Portai publikuojami tik toje sąsajoje, viešų portų nėra; VPN klientai jungiasi `http://<DOMAIN>:<LOCALHOST_PORT>` (vardas jų `/etc/hosts` rodo į VPN adresą). Klientų `X-Forwarded-*` antraštės ignoruojamos, IP sąrašuose – VPN klientų adresai ([Tikras kliento IP](05-servisai.md#ip-apribojimai)). Kliento IP išlieka tik su Docker numatytu iptables NAT; jei Docker'io iptables išjungti, visi klientai atrodo kaip gateway.

## Paruošimas vieną kartą

1. **Serveryje:** Docker, DirectAdmin vartotojas ir projekto katalogas (`DEPLOY_PATH`), kuriame bus `app/` ir `data/`.
2. **Build kompiuteryje:** `setup/install-host`, Docker ir SSH raktas į serverį. Docker context'as:

```bash
docker context create demo-prod --docker host=ssh://VARTOTOJAS@SERVERIS
```

3. **Aplinkos failas be slaptažodžių** `env/prod.env.example` (commit'inamas):

```dotenv
DOMAIN=demo.example.com
DATABASE_USER=demo_live
DATABASE_NAME=demo_live

PHP_MEMORY=2g
PHP_CPUS=2
DATABASE_MEMORY=4g
DATABASE_CPUS=2
WEBSERVER_MEMORY=256m
WEBSERVER_CPUS=0.5
PROXY_MEMORY=128m
PROXY_CPUS=0.5

APP_UID=1001
APP_GID=1001
DEPLOY_CONTEXT=demo-prod
DEPLOY_SSH=VARTOTOJAS@SERVERIS
DEPLOY_PATH=/home/VARTOTOJAS/demo

PMA_ALLOWED_IPS="203.0.113.10"
```

4. **Slaptažodžiai** (`DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD`, PS raktai) į failus nerašomi. Jie ateina iš Bitbucket Deployment variables kaip proceso aplinka. Bandant iš savo kompiuterio jie gali būti privačiame `env/prod.env`.

## Image'ai serveryje

```bash
make release ENV=prod
```

Sukuria visus prod image'us iš commit'into setup ir užrašo `.generated/release/prod/images.lock.json`: setup versiją, image'ų ID ir bazinių image'ų digest'us (atkuriamiems build'ams). Jei įdiegtas [Trivy](https://trivy.dev), patikrina kritines spragas (tik įspėjimas). `allow_dirty=1` leidžia necommit'intą setup, bet tikram release'ui netinka.

**Išoriniai image'ai.** Projekto compose failo servisas be `build` (pvz. `image: getmeili/meilisearch:v1.10`) į release'ą patenka kaip ir sukurti image'ai: jei šiame kompiuteryje jo nėra, release'as jį parsisiunčia (`docker pull`), įrašo jo ID į `images` ir registro digest'ą į `external`, o `make deploy-images` jį perduoda kartu su kitais. Jau esantis image'as nepersiunčiamas iš naujo: naujesnę to paties tag'o versiją parsisiųsk rankomis (`docker pull`). Jei image'o gauti nepavyksta arba kuris nors image'as nesukurtas, release'as sustoja ir lock'o nerašo.

```bash
make deploy-images ENV=prod
```

Nusiunčia image'us į `DEPLOY_CONTEXT` (`docker save` → `docker load`) ir paleidžia konteinerius serveryje. Compose modelis su env reikšmėmis sugeneruojamas siuntimo metu ir perduodamas tiesiai, į diską nerašomas. Jame projekto keliai pakeisti į `DEPLOY_PATH`. `transfer=0` – image'ai jau serveryje, tik paleisti.

Pakeitus serverio env (limitus, IP sąrašus) pakartok `make deploy-images ENV=prod transfer=0`.

## Kodo deploy'us

```bash
make deploy ENV=prod ref=v1.4.0
```

`ref` – kodo (parduotuvės ar kitos aplikacijos) repozitorijos tag'as arba commit'as. Žingsniai, kurie skaito ar keičia aplikacijos specifinius dalykus, priklauso nuo `PROFILE`: PrestaShop dalykai vykdomi tik su `PROFILE=prestashop`, tuščias (generic) profilis jų neliečia. Eiga:

1. **Release'as:** `git archive` iš kodo repozitorijos, `composer install --no-dev` release PHP image'e, `PS_RELEASE_PATHS` (PrestaShop), projekto `config/deploy/build.sh` (pvz. frontend'o `npm ci && npm run build`), jei jis yra.
2. **DB kopija:** taikinio DB kopija į šį kompiuterį pagal registrą (`.generated/backups/`). `backup=0` ją praleidžia.
3. **rsync** į `DEPLOY_SSH:DEPLOY_PATH/app/prod/public`:
   - saugomi keliai niekada neperrašomi ir netrinami. PrestaShop profilio: `var/`, `img/`, `upload/`, `download/`, `cache/`, `config/themes/`, konfigūracijos failas, `.htaccess`, `robots.txt`, `sitemap*.xml` ir kt. Generic profilis jų nesaugo: `.htaccess` (maršrutai, Basic auth), `robots.txt` ir kiti sekami failai keliauja kaip kodas. Visiems profiliams papildomai – `DEPLOY_PROTECTED_PATHS` (pvz. duomenų ir log'ų katalogai kodo medyje) ir įrankių katalogai (`.git/`, `.idea/`, …);
   - failas taikinyje pašalinamas tik tada, kai jį atvežė ankstesnis release'as, o naujasis nebe. Pirmas deploy'us nieko netrina;
   - PrestaShop moduliai, kurie yra tik serveryje (pvz. įdiegti kliento), paliekami ir parodomi įspėjime.
4. **Projekto hook'as** `config/deploy/after-rsync.sh`, jei jis yra: vykdomas taikinio `php-fpm` konteineryje (`sh -e`, kodo šaknyje), kai naujas kodas jau ten, pvz. `php bin/console doctrine:migrations:migrate --no-interaction`. Jam nepavykus deploy'us sustoja: migracijos ir cache valymas nevykdomi, o deploy'us įrašomas į istoriją kaip nepavykęs (`"failed": "after-rsync"`), nes naujas kodas jau taikinyje. Pataisęs deploy'ink iš naujo arba grįžk `rollback=1` į ankstesnį release'ą; kitas deploy'us failus šalina lyginant su tuo, kas iš tikrųjų taikinyje.
5. **Modulių upgrade'ai** pasikeitusiems moduliams – tik PrestaShop (PS 1.6 – per administravimo skydelį). Generic deploy'us `ps_module` lentelės neskaito.
6. **Setup SQL migracijos**, jei projektas jas turi (`SCHEMA_DIRECTORY` su `migrations/`), ir schemos ataskaita.
7. **Cache išvalymas:** PrestaShop – PS cache; kitiems profiliams – `CACHE_CLEAR_COMMAND` php-fpm konteineryje, o jei jis nenurodytas, cache nevalomas (parodomas pranešimas).

PS priežiūros režimas deploy'aus metu neįjungiamas.

Generic projekto pavyzdys (`env/prod.env.example` ir aplinkos repo):

```dotenv
PROFILE=
DEPLOY_PROTECTED_PATHS="var/data/ var/log/"
CACHE_CLEAR_COMMAND="php bin/console cache:clear"
```

```bash
# config/deploy/after-rsync.sh
php bin/console doctrine:migrations:migrate --no-interaction --allow-no-migration
```

Modulį, kuris yra tik serveryje, perkelk į kodą (tik `PROFILE=prestashop`):

```bash
make module-pull ENV=prod name=mymodule
git -C public status
```

Modulis nukopijuojamas į `app/public/modules/mymodule`. Peržiūrėk ir commit'ink jį į parduotuvės repozitoriją. Jei toks katalogas lokaliai jau yra, komanda atsisako (kodo kopija laimi).

### Rollback

```bash
make deploy ENV=prod rollback=1
```

Iš naujo išsiunčia ankstesnį sėkmingai įdiegtą release'ą (build kompiuteris jų laiko kelis); release'ai, kurių hook'as nepavyko, ir dabartinio commit'o pakartotiniai deploy'ai praleidžiami. DB automatiškai negrąžinama: jei reikia, atkurk ją iš prieš-deploy kopijos `.generated/backups/` su `make db-import`.

## Bitbucket Pipelines

Realus pavyzdys – Forsenos aplinkos repozitorijos `deploy/PIPELINES.md`, `deploy/pipeline.sh` ir `bitbucket-pipelines.yml`. Principai:

- Deploy'ų paleidžia žmogus: **Pipelines → Run pipeline → Custom: deploy-stage / deploy-production**.
- Žingsnį vykdo self-hosted runner'is build serveryje (žymos `self.hosted`, `linux.shell`, `setup.build`). Jam reikia Docker teisių ir prieigos prie projekto katalogo.
- Repository variable `BUILD_ROOT` – projekto šaknis build serveryje. Ten `app/` (aplinkos repo), `app/public/` (parduotuvės repo) ir šalia – bendras `setup`.
- Slaptažodžiai ir PS raktai – **Deployment variables (Secured)** Staging / Production aplinkoms. Jie gyvena tik proceso aplinkoje ir į diską nerašomi.
- `pipeline.sh` nukopijuoja `env/<aplinka>.env.example` į `env/<aplinka>.env` ir paleidžia `make release` + `make deploy-images` (kai `IMAGES=yes`), tada `make deploy ref=$SHOP_REF` arba `make deploy rollback=1` (kai `ROLLBACK=yes`).
- Kiekvienas paleidimas matomas **Deployments** istorijoje.

Deploy teisių ribojimas (tik tam tikri žmonės ar šakos konkrečiai aplinkai) Bitbucket'e yra Premium plano funkcija. Be jos produkcijos deploy'ų gali paleisti kiekvienas, turintis rašymo teisę į repozitoriją.

## Bandymas iš savo kompiuterio

Kol build serverio nėra, jo vaidmenį atlieka programuotojo kompiuteris: tos pačios komandos su privačiu `env/prod.env`. Be `DEPLOY_CONTEXT` stage/prod konteineriai valdomi šio kompiuterio Docker'yje įprastomis komandomis (`make up ENV=prod`). `make deploy-images` ir `make deploy` reikalauja `DEPLOY_CONTEXT`: vietiniam bandymui tinka Docker context'as, rodantis į šį kompiuterį (standartinis `default`). Jei `DEPLOY_SSH` tuščias, `rsync` rašo į vietinį `DEPLOY_PATH`.
