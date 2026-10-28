# 9. Aplinkos, release ir deploy

[Turinys](README.md) · [Atgal](08-kodo-kokybe.md) · [Toliau: kasdienis darbas](10-kasdienis-darbas.md)

## Keturios aplinkos

| `ENV` | Env failas | Kodas (nuo projekto šaknies) | Duomenys | Paskirtis |
| --- | --- | --- | --- | --- |
| `local` (numatyta) | `env/local.env` | `app/public` | `data/` | Kasdienis darbas. |
| `test` | `env/test.env` | `app/.generated/test/app` | `app/.generated/test/data/` | Testai, žr. [8 skyrių](08-kodo-kokybe.md). |
| `stage` | `env/stage.env` | `app/stage/public` | `data/stage/` | Tarpinis serveris. |
| `prod` | `env/prod.env` | `app/prod/public` | `data/prod/` | LIVE. |
| papildoma, pvz. `mdv` | `env/mdv.env` | `app/mdv/public` | `data/mdv/` | Kitas stage, pvz. laikinas didesniam darbui; `STAGE_ENVIRONMENTS`, žr. [žemiau](#papildomos-aplinkos-stage_environments). |

Konteinerių ir tinklo varduose yra aplinka (`demo-local-…`, `demo-test-…`), todėl kelios aplinkos gali veikti kartu. Aplinką pasirenka `ENV=` bet kurioje komandoje: `make ps ENV=test`.

## Papildomos aplinkos (`STAGE_ENVIRONMENTS`)

Kai vieno stage neužtenka (pvz. didesnis darbas, kurį reikia rodyti atskirai nuo įprasto stage), projektas gali turėti
daugiau serverio aplinkų. Kiekviena jų yra **dar vienas stage**: galioja visos stage taisyklės ir leidimai (limitai, IP
sąrašai, `APP_UID`, secret store, Mailpit, ne-prod after-import SQL, release'ai ir iš ne-tag setup). Prod tėra vienas.

Pridėti (pvz. `mdv`):

1. `env/common.env`: `STAGE_ENVIRONMENTS="mdv"` (kelios – per tarpą). Vardas: mažosios raidės ir skaitmenys, 2–16
   simbolių, pirmas – raidė; `local`, `test`, `stage`, `prod` negalimi. Neišvardytą `ENV=mdv` runner'is atmeta.
2. `env/mdv.env.example` – kaip `stage.env.example`, su savu `DOMAIN`, limitais ir IP sąrašais (be slaptažodžių).
3. Deploy aplinka **tuo pačiu vardu**: Bitbucket'e *Repository settings → Deployments → Staging → Add environment:
   `mdv`* (jos Deployment variables), GitHub'e – aplinka `mdv` (jos secrets). `staging`/`production` lieka stage ir prod.
4. Pipeline'as: Bitbucket'e custom pipeline'as `deploy-mdv` su `deployment: mdv` (pavyzdys
   `templates/pipelines/bitbucket-pipelines.yml` gale; setup-server sąsaja jį paleidžia mygtuku „Deploy į mdv“), GitHub'e –
   `mdv` workflow `environment` pasirinkimuose. `make pipeline-deploy` aplinką ima iš deploy aplinkos vardo ir dar kartą
   patikrina, ar deploy'inamas commit'as ją išvardija.
5. Jei taikinys kitas nei stage: setup-server'yje `sudo bin/add-project --target mdv=ssh://…` (be jo – bendras
   `target.env`).

Toliau viskas kaip stage: `make release ENV=mdv`, `make deploy ENV=mdv ref=…`, `make deploy-variables ENV=mdv`,
`make db-pull from=mdv`, `make ssh from=mdv`; kopijos `worker-mdv-…`, konteineriai `demo-mdv-…`.

Panaikinti: serveryje `make down ENV=mdv`, ištrink `data/mdv/` ir `app/mdv/` (jei kodas buvo ten), išbrauk vardą iš
`STAGE_ENVIRONMENTS`, ištrink `env/mdv.env.example`, pipeline'ą ir deploy aplinką. Kopijos `.generated/backups/*-mdv-*`
lieka, kol jų neištrini.

## Ką stage ir prod reikalauja

Tas pats CORE veikia visose aplinkose, bet stage/prod (ir [papildomos aplinkos](#papildomos-aplinkos-stage_environments)) turi griežtesnes taisykles. Komanda sustoja su klaida, jei:

- nenurodyti keturių pagrindinių servisų limitai `PHP_MEMORY`, `PHP_CPUS`, `DATABASE_MEMORY`, `DATABASE_CPUS`, `WEBSERVER_MEMORY`, `WEBSERVER_CPUS`, `PROXY_MEMORY`, `PROXY_CPUS`. Klaidos pranešimas pasiūlo reikšmes pagal Docker host'o dydį ([resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui));
- įjungtas `pma`, `mailpit`, `cron`, `dbmonitor` ar `metrics`, bet nėra jo IP sąrašo ([IP apribojimai](05-servisai.md#ip-apribojimai));
- įjungtas `metrics`, o nurodytas `METRICS_TOKEN` trumpesnis nei 24 simboliai (tuščias tinka: tada saugo tik IP sąrašas);
- nurodytas `XDEBUG_MODE` (stage/prod image'uose Xdebug nėra);
- release'ui nenurodyti `APP_UID`, `APP_GID`, `DEPLOY_PATH` (ir `DEPLOY_CONTEXT` siuntimui į serverį);
- nurodytas `DEPLOY_CONTEXT`, bet nėra `APP_UID`/`APP_GID` (bet kuri komanda).

Kiti skirtumai pagal nutylėjimą: `CACHE_MODE` įjungtas, `HTTP_FORCE_HTTPS=on`, prod `MAIL_MODE=preserve`, prod po DB užkėlimo saugūs numatytieji ir integracijų išjungimas **nevykdomi**. `make bootstrap` ir PS raktų generavimas veikia tik local/test: serverių reikšmės pateikiamos aiškiai.

Stage/prod konteineriai veikia kaip hosting'o (DirectAdmin) vartotojas `APP_UID`/`APP_GID`, todėl failų savininkas serveryje vienas.

## D modelis: kaip kodas pasiekia serverį

```text
build serveris (kol jo nėra – programuotojo kompiuteris)
  ├─ make release        → sukuria image'us iš commit'into setup, užrašo lock failą
  ├─ make deploy-images  → docker save | ssh | docker load (tik trūkstami), paleidžia konteinerius serveryje
  └─ make deploy ref=…   → paruošia kodo release'ą, DB kopija, rsync, hook'as, upgrade'ai, migracijos
                                │
LIVE serveris: tik Docker, kodas, duomenys ir DirectAdmin web serveris
```

- Image'ai kuriami build serveryje ir perduodami per SSH. LIVE neturi kūrimo įrankių ir setup kopijos: setup runtime scriptai ir CORE konfigūracija įkepti į image'us.
- Serveris valdomas per Docker context (`DEPLOY_CONTEXT`). Kai jis nurodytas, `make ps`, `make logs`, `make shell`, `make db-backup` ENV=stage/prod vykdomi serveryje, o `make up` / `make down` atmetami: konteinerius paleidžia `make deploy-images`.
- Serveriuose naudojamas fiksuotas setup tag'as: `make release ENV=prod` veikia tik kai setup yra tiksliai ant release tag'o (`vX.Y.Z`), stage tinka bet kuris commit'as. Release iš necommit'into setup atmetamas.
- **TLS baigiasi DirectAdmin'e** (jo Let's Encrypt). Konteinerio portas rišamas tik prie `127.0.0.1`, DirectAdmin web serveris perduoda užklausas į `http://127.0.0.1:<LOCALHOST_PORT>`. nginx-proxy priima tikrą kliento IP iš `X-Forwarded-For`, o `X-Forwarded-Proto: https` PHP pusėje tampa HTTPS žyma. Konkreti DirectAdmin proxy konfigūracija šiame vadove neaprašyta. Kai `DOMAIN` išvardija kelis hostname'us ([keli domenai](03-nustatymai.md#keli-domenai)), DirectAdmin'e kiekvienas jų turi savo sertifikatą ir tą patį proxy į projekto portą; nginx-proxy juos visus jau priima.
- **VPN serveris be DirectAdmin** (pvz. asana.local): `PUBLISH_ADDRESS` – serverio VPN sąsajos adresas (`env/prod.env`, pvz. `PUBLISH_ADDRESS=10.8.0.14`). Portai publikuojami tik toje sąsajoje, viešų portų nėra; VPN klientai jungiasi `http://<DOMAIN>:<LOCALHOST_PORT>` (vardas jų `/etc/hosts` rodo į VPN adresą). Klientų `X-Forwarded-*` antraštės ignoruojamos, IP sąrašuose – VPN klientų adresai ([Tikras kliento IP](05-servisai.md#ip-apribojimai)). Kliento IP išlieka tik su Docker numatytu iptables NAT; jei Docker'io iptables išjungti, visi klientai atrodo kaip gateway.
- **Sertifikatai serveryje nebūtini.** TLS baigiasi nginx-proxy, Apache dirba tik HTTP ir sertifikatų nenaudoja. Jei `data/<env>/ssl/` tuščias (DirectAdmin baigia TLS pats, VPN serveris dirba per HTTP), nginx-proxy paleidimo metu sugeneruoja savo pasirašytą porą `domain.crt`/`domain.key` konteinerio viduje (`/etc/nginx/ssl-generated`, savininkas `root`, raktas `0600`). Rankinio žingsnio nereikia, o `make doctor` tai tik pažymi. Jei kataloge yra tikra pora, ji naudojama ir niekada neperrašoma. Jei yra tik vienas iš dviejų failų, nginx-proxy nepasileidžia.

## LIVE parametrai iš esamo serverio

Perkeliant esamą projektą reikia žinoti, kuo veikia LIVE: PHP ir DB versijas, `php.ini` ribas, plėtinius, `sql_mode`, koduotes, cron. Juos surenka [LIVE zondas](../../tools/live-probe/README.md): vienas PHP 5.6–8.5 failas, kurį trumpam įkeli į serverį (arba paleidi per SSH). Jis grąžina `env/common.env` ir `env/prod.env` reikšmes, `config/php` ir `config/mysql` drop-in'us, projekto `Dockerfile` plėtinius ir rizikas, o Markdown ataskaitą galima atiduoti LLM. Zondas tik skaito, slaptų reikšmių nespausdina, o po darbo jį būtina ištrinti (`?action=delete`).

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

PMA_ALLOWED_IPS="203.0.113.10"
```

   **Kur deploy'inama, nusprendžia serveris, ne projektas.** Su setup-server taikinį (`DEPLOY_CONTEXT`, `DEPLOY_SSH`, `DEPLOY_PATH`, `APP_UID`, `APP_GID`) užregistruoja `bin/add-project` build kataloge (`target.env`; `target.<env>.env` – kai stage ir prod skirtinguose serveriuose), o `make pipeline-deploy` jį prideda prie `env/<env>.env`. Pavyzdys tų vardų rašyti negali: jei `target.env` yra, deploy'us tokį pavyzdį atmeta. Be setup-server (bandant iš savo kompiuterio) taikinį rašyk privačiame `env/prod.env`:

```dotenv
APP_UID=1001
APP_GID=1001
DEPLOY_CONTEXT=demo-prod
DEPLOY_SSH=VARTOTOJAS@SERVERIS
DEPLOY_PATH=/home/VARTOTOJAS/demo
```

4. **Slaptažodžiai** (`DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD`, PS raktai) į failus nerašomi. Jie ateina iš Bitbucket Deployment variables kaip proceso aplinka. Env faile jų nerašyk arba palik tuščius – tuščia reikšmė proceso aplinkos neuždengia. Bandant iš savo kompiuterio jie gali būti privačiame `env/prod.env`; tada laimi failas. Visų vardų sąrašą sudaro setup, žr. [Deployment variables](#deployment-variables).
   Aplikacijos slapti parametrai iš [`APP_ENVIRONMENT`](03-nustatymai.md#savi-aplikacijos-parametrai-app_environment) ateina taip pat, tik jų vardų serverio env faile nerašyk visai: čia ir tuščia reikšmė proceso aplinkos reikšmę uždengia.

## Image'ai serveryje

```bash
make release ENV=prod
```

Sukuria visus prod image'us iš commit'into setup ir užrašo `.generated/release/prod/images.lock.json`: setup versiją, image'ų ID ir bazinių image'ų digest'us (atkuriamiems build'ams). Jei įdiegtas [Trivy](https://trivy.dev), patikrina kritines spragas (tik įspėjimas). `allow_dirty=1` leidžia necommit'intą setup, bet tikram release'ui netinka.

**Išoriniai image'ai.** Projekto compose failo servisas be `build` (pvz. `image: getmeili/meilisearch:v1.10`) į release'ą patenka kaip ir sukurti image'ai: jei šiame kompiuteryje jo nėra, release'as jį parsisiunčia (`docker pull`), įrašo jo ID į `images` ir registro digest'ą į `external`, o `make deploy-images` jį perduoda kartu su kitais. Jau esantis image'as nepersiunčiamas iš naujo: naujesnę to paties tag'o versiją parsisiųsk rankomis (`docker pull`). Jei image'o gauti nepavyksta arba kuris nors image'as nesukurtas, release'as sustoja ir lock'o nerašo.

```bash
make deploy-images ENV=prod
```

Nusiunčia image'us į `DEPLOY_CONTEXT` (`docker save` → `docker load`) ir paleidžia konteinerius serveryje. Siunčiami tik tie image'ai, kurių serveris neturi arba turi kitą build'ą: lyginami sluoksniai ir paleidimo konfigūracija, todėl atnaujinus setup paprastai keliauja tik PHP image'as, o DB ir įrankių image'ai lieka. Jei serveris tą patį build'ą jau turi kitu vardu (kitas projektas su tokiu pat PHP image'u: ta pati setup versija, `PHP_VERSION`, profilis, PrestaShop versija ir projekto `Dockerfile`), image'as ten tik pažymimas nauju vardu (`docker tag`), nesiunčiamas. Jei build mašinos Docker naudoja klasikinį image store'ą (jo `docker save` sluoksnių nesuspaudžia), srautas suspaudžiamas `zstd` (jei įdiegtas); containerd store'as juos saugo jau suspaustus. Compose modelis su env reikšmėmis sugeneruojamas siuntimo metu ir perduodamas tiesiai, į diską nerašomas. Jame projekto keliai pakeisti į `DEPLOY_PATH`; prieš paleidžiant ten nukopijuojami prijungiami aplinkos failai (žr. „Aplinkos failai“ žemiau). `transfer=0` – image'ai jau serveryje, tik paleisti.

Prieš paleisdamas konteinerius `make deploy-images`:

- **patikrina, ar serverio konteineriai yra šio projekto** (dar prieš siųsdamas image'us). Konteinerių, tinklo ir compose projekto vardai susidaro iš `PROJECT_NAME` ir aplinkos, todėl kitas aplinkos repo su tuo pačiu `PROJECT_NAME` tame serveryje (pvz. nukopijuotas kaip naujo projekto pradžia) pakeistų jo konteinerius savais. Jei to vardo konteinerių prijungti katalogai yra ne po `DEPLOY_PATH`, komanda sustoja dar nieko nepakeitusi: kitam projektui duok savo `PROJECT_NAME` (`env/common.env`), o tyčia perkeltam projektui pirmiau sustabdyk senus konteinerius (`docker --context … compose -p <projektas>-<env> down`, komanda parašyta klaidoje). Tą patį tikrina `make deploy`: jo hook'ai, modulių upgrade'ai ir migracijos vyksta tų konteinerių viduje;
- **sukuria kodo ir duomenų katalogus** `DEPLOY_PATH` vietoje deploy'inančios paskyros vardu. Kitaip pirmą kartą juos sukurtų Docker kaip `root`, ir `APP_UID` konteineriai (php-fpm, MySQL) negalėtų į juos rašyti. Jei ankstesnis deploy'us katalogą paliko `root`, komanda sustoja ir parašo `chown` komandą;
- **patikrina publikuojamus portus** (savas Docker arba `DEPLOY_SSH`): jei portą laiko kitas procesas (kompiuterio web serveris, kitas stack'as), deploy'us sustoja dar nieko nepaleidęs. Taip pat paleistas, Docker konteinerio tinklų nesukonfigūruotų iki galo;
- **perkuria konteinerį, kuriam trūksta kurio nors modelio tinklo** (likutis po pusiau nepavykusio paleidimo, pvz. nginx-proxy tik metrics tinkle → `host not found in upstream "webserver"`). Compose tokio konteinerio pats nekeistų, nes jo konfigūracija nepasikeitusi.

Paleidęs konteinerius, `make deploy-images` **ištrina serveryje setup image'us** (label'is `setup.managed=1`), kurių nenaudoja nė vienas ten esantis konteineris (nei veikiantis, nei sustabdytas) ir kurie parą nebuvo nei atsiųsti, nei pažymėti. Taip dingsta po setup atnaujinimų likę senesni visų serverio projektų build'ai. Para saugo ką tik kito projekto deploy'aus atsiųstus, dar nepaleistus image'us; image'as ištrinamas pagal vardą ir be `-f`, todėl Docker nepaleidžia ištrinti image'o, kurį spėjo pasiimti naujas konteineris. Svetimi (ne setup) image'ai nesiliečiami. Projektas, kurio konteinerių serveryje nėra ilgiau nei parą, kitu deploy'umi gauna savo image'us iš naujo. Kai taikinys yra to paties kompiuterio Docker (`--target local`), nieko netrinama: ten image'us tvarko `make image-prune`. Su `transfer=0` tvarkymo nėra.

Pakeitus serverio env (limitus, IP sąrašus) pakartok `make deploy-images ENV=prod transfer=0`.

## Kodo deploy'us

```bash
make deploy ENV=prod ref=v1.4.0
```

`ref` – kodo (parduotuvės ar kitos aplikacijos) repozitorijos tag'as arba commit'as. Žingsniai, kurie skaito ar keičia aplikacijos specifinius dalykus, priklauso nuo `PROFILE`: PrestaShop dalykai vykdomi tik su `PROFILE=prestashop`, tuščias (generic) profilis jų neliečia. Eiga:

1. **Release'as:** `git archive` iš kodo repozitorijos, `composer install --no-dev` release PHP image'e, `PS_RELEASE_PATHS` (PrestaShop), projekto `config/deploy/build.sh` (pvz. frontend'o `npm ci && npm run build`), jei jis yra.
2. **DB kopija (tik su `backup=1`):** taikinio DB kopija į šį kompiuterį pagal registrą (`.generated/backups/`). Pagal nutylėjimą nedaroma; rinkis `backup=1`, kai deploy'us keičia duomenis (migracijos, modulių upgrade'ai).
3. **rsync** į `DEPLOY_SSH:DEPLOY_PATH/app/prod/public`:
   - saugomi keliai niekada neperrašomi ir netrinami. PrestaShop profilio: `var/`, `img/`, `upload/`, `download/`, `cache/`, `config/themes/`, konfigūracijos failas, `.htaccess`, `robots.txt`, `sitemap*.xml`, modulių ir temos modulių vertimai (`modules/*/translations/`, `themes/*/modules/*/translations/`), kalbų paketai (`translations/<locale>/`, pvz. `translations/lt-LT/`) ir vertimų eksportai (`translations/export/`) ir kt. Vertimus redaguoja BO serveryje, o kalbų paketus serveryje rašo paketų diegimas ar atnaujinimas BO ir vertimo moduliai (pvz. `ets_translate` papildo `*.xlf`), todėl jie yra turinys: parduotuvės repo jų nelaiko (ignoruoja), o rsync jų neliečia; programuotojo vertimus atveža 7 žingsnis ([Vertimai](#vertimai-translations)). Laiškai `mails/` yra kodas. Generic profilis jų nesaugo: `.htaccess` (maršrutai, Basic auth), `robots.txt` ir kiti sekami failai keliauja kaip kodas. Visiems profiliams papildomai – `DEPLOY_PROTECTED_PATHS` (pvz. duomenų ir log'ų katalogai kodo medyje) ir įrankių katalogai (`.git/`, `.idea/`, …);
   - release'o failai saugomuose keliuose atvežami atskirai: saugomi katalogai gauna tik tuos release failus, kurių taikinys neturi (`cache/.htaccess`, apsauginiai `index.php`), o `PS_RELEASE_PATHS` keliai saugomų katalogų viduje (pvz. `img/admin`) – visus release failus, be `--delete`;
   - failas taikinyje pašalinamas tik tada, kai jį atvežė ankstesnis release'as, o naujasis nebe. Pirmas deploy'us nieko netrina;
   - PrestaShop moduliai, kurie yra tik serveryje (pvz. įdiegti kliento), paliekami ir parodomi įspėjime.
   - **Aplinkos failai.** Konteineriai iš `DEPLOY_PATH` prijungia ir aplinkos repo dalis (`config/`, schemos katalogą ir pan.). Kai `DEPLOY_PATH` nėra pats šio checkout'o projekto šaknis (kitas katalogas ar serveris), jos kopijuojamos ten pagal deploy'inamą commit'ą (`rsync --delete`: iš repo pašalintas failas dingsta ir taikinyje). `.generated/` (kopijos, build būsena) niekada nekopijuojamas. Tą patį daro `make deploy-images` prieš paleisdamas konteinerius.
4. **Projekto hook'as** `config/deploy/after-rsync.sh`, jei jis yra: vykdomas taikinio `php-fpm` konteineryje (`sh -e`, kodo šaknyje), kai naujas kodas jau ten, pvz. `php bin/console doctrine:migrations:migrate --no-interaction`. Jam nepavykus deploy'us sustoja: migracijos ir cache valymas nevykdomi, o deploy'us įrašomas į istoriją kaip nepavykęs (`"failed": "after-rsync"`), nes naujas kodas jau taikinyje. Pataisęs deploy'ink iš naujo arba grįžk `rollback=1` į ankstesnį release'ą; kitas deploy'us failus šalina lyginant su tuo, kas iš tikrųjų taikinyje.
5. **Modulių upgrade'ai** pasikeitusiems moduliams – tik PrestaShop (PS 1.6 – per administravimo skydelį). Generic deploy'us `ps_module` lentelės neskaito.
6. **Setup SQL migracijos**, jei projektas jas turi (`database/migrations/`, be `DATABASE_AS_CODE=off`), ir schemos ataskaita.
7. **Vertimų migracijos** – tik PrestaShop, kai aplinkos repo turi `translations/migrations/`: kiekviena dar nepritaikyta,
   po vieną kartą ([Vertimai](#vertimai-translations)).
8. **Cache išvalymas:** PrestaShop – PS cache; kitiems profiliams – `CACHE_CLEAR_COMMAND` php-fpm konteineryje, o jei jis nenurodytas, cache nevalomas (parodomas pranešimas).
9. **php-fpm perkrovimas** (visiems profiliams): `USR2` php-fpm master procesui. Nauji worker'iai pradeda su tuščiu OPcache, todėl prod iškart vykdo naują kodą. Vykdomos užklausos gauna iki 30 s pabaigti (`process_control_timeout` CORE image'e). `DEPLOY_PHP_RELOAD=off` perkrovimą išjungia (tada OPcache gali rodyti seną kodą iki konteinerio restart'o).

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

### Failų kopijos (`BACKUP_FILE_PATHS`)

Kai kuriuos failus parduotuvė rašo pati taikinyje, o git juos ignoruoja (pvz. administravimo skydelyje redaguoti modulių
vertimai). Kodo repozitorijoje jų nėra, todėl juos saugo atskira kopija. PrestaShop profilis visada kopijuoja vertimus:
`modules/*/translations/`, `themes/*/modules/*/translations/` ir kalbų paketus `translations/*-*/`. Projekto keliai
išvardijami `env/common.env` (aplinkos `env/<env>.env` reikšmė laimi) ir pridedami prie profilio, ta pačia sintakse kaip
`DEPLOY_PROTECTED_PATHS`: nuo kodo šaknies, katalogas baigiasi `/`, `*` – bet koks vardas vienoje vietoje.

```dotenv
BACKUP_FILE_PATHS="modules/*/translations/ themes/*/lang/ config/local.php"
```

```bash
make files-backup ENV=prod [file=/kelias/files.tar.gz]
```

- rsync nukopijuoja tik šiuos kelius iš `DEPLOY_SSH:DEPLOY_PATH/app/<env>/public` (ar vietinio katalogo) į veidrodį
  `.generated/backups/.files-<env>/` (kitą kartą siunčiama tik tai, kas pasikeitė; ištrintas taikinyje failas dingsta ir
  veidrodyje), tada į `.generated/backups/files-<env>-<UTC laikas>Z.tar.gz` (0600; vardai nuo kodo šaknies, pvz.
  `modules/shop/translations/lt.php`). Archyvas rašomas į laikiną failą ir pervadinamas tik baigus.
- Taikinyje niekas nerašoma; slaptažodžių ir konteinerių nereikia. Kelias, kuriam taikinyje nieko neatitiko, – įspėjimas, ne klaida.
- **setup-server** worker'is daro tai kas naktį kiekvienai aplinkai, kurios `BACKUP_FILE_PATHS` netuščias, ir laiko
  7 dienų + 4 savaičių kopijas (`files-<env>-…Z.tar.gz`); jo valdymo sąsajos skiltyje „Failai“ archyvus galima peržiūrėti.

**Parsisiųsti į savo kompiuterį** – kaip [`make db-pull`](07-duomenu-baze.md#serverio-kopija-db-pull): tas pats serveris,
raktas ir leidimai (setup-server *Prieiga* → raktas → „Kopijos“: `db` – parsisiųsti, `fresh` – užsakyti naują).

```bash
make files-pull                    # naujausia prod failų kopija į .generated/backups (0600); kodas nekeičiamas
make files-pull extract=1          # + jos failai į vietinį kodą (tik BACKUP_FILE_PATHS keliai)
make files-pull fresh=1 extract=1  # serveris dabar nuskaito taikinį ir padaro naują kopiją
make files-pull from=stage         # stage (ar STAGE_ENVIRONMENTS aplinka)
```

- `extract=1` įrašo tik paprastus failus, kuriuos atitinka **šio** checkout'o `BACKUP_FILE_PATHS` (todėl jis turi būti
  `env/common.env`, kaip serveryje): jie pakeičia vietinius, kiti failai nekeičiami ir netrinami, nuorodos (symlink)
  praleidžiamos. Failų laikas – serverio.
- `fresh=1`: jei failų kopija jaunesnė nei 30 min., serveris atiduoda ją. Jau parsisiųsta kopija antrą kartą nesiunčiama;
  nutrūkęs ar ne iki galo perskaitomas archyvas neišsaugomas.
- „no files backups yet (update setup-server)“ – serverio setup-server dar be failų kopijų (`bin/update`); „no prod files
  backup of … yet“ – kopijos dar nebuvo (naktį ar `fresh=1`).

### Vertimai (`translations/`)

Modulių vertimus serveryje redaguoja klientas (BO), todėl parduotuvės repo jų nelaiko, o rsync jų neliečia. Programuotojo
vertimai keliauja kaip **migracijos** aplinkos repo `translations/migrations/` – kaip SQL migracijos: kiekviena pritaikoma
vieną kartą ir įrašo tik savo vertimus, todėl kliento pataisos neliečiamos ir į git nepatenka.

```bash
make db-pull import=1 && make files-pull extract=1   # serverio vertimai vietinėje aplinkoje = atskaitos taškas
# … verti vietiniame BO …
make translations-migration name=krepselio-tekstai   # translations/migrations/<laikas>-krepselio-tekstai.json: tik tai, ką pakeitei
make translations-migrate                            # kolegų migracijos vietinėje aplinkoje
make translations-migrate ENV=prod dry=1             # ką deploy'us įrašytų (ir ant kokio taikinio teksto); deploy'us tai daro pats
```

```json
{
  "legacy":   {"modules/mymodule/translations/lt.php": {"<{mymodule}prestashop>mymodule_5f3c…": "Pirkinių krepšelis"}},
  "database": {"lt-LT": {"Modules.Mymodule.Shop": {"Add to cart": "Į krepšelį"}, "Shop.Theme.Actions@classic": {"Buy": "Pirkti"}}}
}
```

- **Juodraštis.** `make translations-migration` palygina vietinius vertimus (`modules/*/translations/<iso>.php`,
  `themes/*/modules/*/translations/<iso>.php` ir `ps_translation` eilutes) su atskaitos tašku
  `.generated/translations/base.json` ir į naują failą surašo tik naujus ar pakeistus raktus; atskaitos taškas pasislenka.
  Jį iš naujo paima `db-pull import=1`, `db-import`, `db-load` (DB dalį) ir `files-pull extract=1` (failus), todėl serverio
  ir kliento tekstai yra jame, o ne juodraštyje. Pirmą kartą (be atskaitos taško) komanda jį tik paima: tada versk ir paleisk
  dar kartą. `make translations-base` – paimti iš naujo ranka (pvz. po importo ne per setup). Failą peržiūrėk ir commit'ink;
  ką ištrinsi, neįrašoma.
- **Pritaikymas.** Kiekvienas PrestaShop deploy'us po modulių upgrade'ų pritaiko dar nepritaikytas migracijas (vardo
  tvarka) ir įrašo jas į `setup_migrations` kaip `translations:<failas>` – atskiros lentelės nėra. Migracijos vertimai
  įrašomi ant to, kas taikinyje; kiti raktai ir failai neliečiami, niekas netrinama. Pritaikyta migracija daugiau
  nevykdoma, todėl ką klientas vėliau pataiso BO, lieka. Pakeista jau pritaikyta migracija nevykdoma iš naujo (pastaba):
  taisymui – nauja migracija. `make db-rollback` jų neatšaukia.
- **Ko taikinys neturi:** modulio – failas nekuriamas, kalbos – eilutė praleidžiama (abu parodomi, migracija įrašoma).
  Taikinio failas, kuriame yra daugiau nei `$_MODULE[...] = '...';` eilutės, neperrašomas: deploy'us sustoja, migracija
  neįrašoma, kol failas sutvarkomas.
- Vietinėje aplinkoje `make translations-migrate` pritaiko kolegų migracijas ir jų tekstus įrašo į atskaitos tašką (tavo
  kitas juodraštis jų nebeneš). Su `db-pull import=1` atkeliauja ir prod `setup_migrations`: pritaikomos tik tos, kurių
  prod dar neturi.
- PS 1.6 neturi `ps_translation`: tik `legacy`. Generic profilis vertimų migracijų neturi.

### Rollback

```bash
make deploy ENV=prod rollback=1
```

Iš naujo išsiunčia ankstesnį sėkmingai įdiegtą release'ą (build kompiuteris jų laiko kelis); release'ai, kurių hook'as nepavyko, ir dabartinio commit'o pakartotiniai deploy'ai praleidžiami. DB automatiškai negrąžinama. Jei atšauktas release'as pritaikė migracijas, deploy'us jas išvardija ir parašo komandą `make db-rollback ENV=prod steps=N` (pirma `dry=1`): ji paleidžia jų rollback dalis ([migracijos](07-duomenu-baze.md#rollback)). Duomenis grąžina tik kopija: `make db-import` iš prieš-deploy kopijos `.generated/backups/` (jei deploy'us buvo su `backup=1`) arba iš naujausios naktinės kopijos (setup-server).

## Bitbucket Pipelines

Deploy'ų iš Bitbucket'o atlieka setup'o komanda `make pipeline-deploy`. Projektas laiko tik `bitbucket-pipelines.yml` ir vardų sąrašus `env/common.env` faile. Savo `deploy/pipeline.sh` ar kintamųjų scripto nebereikia. Pavyzdys: [templates/pipelines/bitbucket-pipelines.yml](../../templates/pipelines/bitbucket-pipelines.yml) (nukopijuok į aplinkos repozitorijos šaknį; `./create-project` jį įrašo pats). GitHub projektas deploy'inamas ta pačia komanda iš GitHub Actions, žr. [GitHub Actions](#github-actions).

- Deploy'ų paleidžia žmogus: **Pipelines → Run pipeline → Custom: deploy-stage / deploy-production**. Kiekvienas paleidimas matomas **Deployments** istorijoje.
- Žingsnį vykdo self-hosted runner'is build serveryje (žymos `self.hosted`, `linux.shell`, `setup.build`). Jam reikia Docker teisių ir prieigos prie build root.
- **Build root** – projekto šaknis build serveryje: `app/` (aplinkos repozitorija) ir šalia bendras `setup`. Kelias – Repository variable `BUILD_ROOT`, o be jo – setup-server išdėstymas `/srv/setup-server/projects/<repo>`. Darbas vyksta build root'e. Runner'io klonas tik nurodo commit'ą.
- Žingsnio komanda: `make -C "$BUILD_ROOT/app" pipeline-deploy`. Aplinka imama iš Deployment aplinkos (`staging` → `ENV=stage`, `production` → `ENV=prod`). Kitaip pavadintai Bitbucket aplinkai pridėk `ENV=stage` arba `ENV=prod`. Už deploy žingsnio ribų (nėra `BITBUCKET_DEPLOYMENT_ENVIRONMENT`) komanda atsisako.
- Projektas gali reikalauti naujesnio setup: `SETUP_MIN_VERSION` ([nustatymai](03-nustatymai.md#tapatybė-profilis-ir-servisai)). Senesnis setup sustoja prieš bet kokį pakeitimą.

Paleidimo kintamieji (pipeline'o `variables`):

| Kintamasis | Numatyta | Ką daro |
| --- | --- | --- |
| `REF` | Tuščias | Ką deploy'inti: tag'as arba commit'as. Viena repozitorija: tuščias reiškia pipeline commit'ą. Atskira kodo repozitorija: kodo repo ref'as, privalomas (išskyrus `ROLLBACK=yes`). Seni vardai `APP_REF` ir `SHOP_REF` irgi veikia. Jei nurodyti keli skirtingi, komanda atsisako. |
| `IMAGES` | `no` | `yes`: prieš kodą `make release` ir `make deploy-images` (kai taikinys yra pats build serveris – `transfer=0`). Pirmam deploy'ui ir pakeitus serverio env būtina. `auto` (šablonų numatytasis): žingsnis sprendžia pats, žr. žemiau. |
| `CRON` | `auto` | Po deploy'aus `make cron-reload`: veikiantis cron konteineris perskaito `config/cron/`. `auto` – kai deploy'inami commit'ai keičia `config/cron/`; `yes` – visada; `no` – niekada. |
| `ROLLBACK` | `no` | `yes`: `make deploy rollback=1`. `REF` ignoruojamas. |
| `BACKUP` | `no` | `yes`: `make deploy backup=1`, prieš hook'us ir migracijas pasidaroma DB kopija. |

Du išdėstymai. Setup juos atskiria pagal tai, ar `app/public` turi savo `.git`:

- **Viena repozitorija** (pvz. asana.local: kodas yra aplinkos repozitorijos `public/` kataloge). Build root checkout'as visada yra deploy'inamas commit'as: `REF` arba pipeline commit'as. `ROLLBACK=yes` checkout'ina commit'ą, į kurį grąžina `make deploy rollback=1` (deploy istorija `.generated/release/<env>/deployments.json`). Po deploy'aus checkout'as seka tai, ką setup įrašė į istoriją. Tai svarbu, kai build root yra ir `DEPLOY_PATH`: veikiantys konteineriai naudoja jo `config/`.
- **Atskira kodo repozitorija** `app/public` (pvz. Forsena: parduotuvės repozitorija). Aplinkos checkout'as seka pipeline commit'ą, `REF` – kodo repozitorijos tag'as arba commit'as. Kodo repozitorijai daromas tik `git fetch --tags`, jos checkout'as nekeičiamas (`make deploy` kodą ima per `git archive`).

Eiga:

1. **Patikros prieš bet kokį pakeitimą:** Deployment aplinka, `REF`, `IMAGES`/`CRON`/`ROLLBACK`/`BACKUP` reikšmės, privalomi [Deployment variables](#deployment-variables). Jei kas nors negerai, komanda baigiasi kodu 64 ir pranešimu `Nothing was changed`.
2. `git fetch --tags` (abiem repozitorijoms) ir commit'o checkout'as build root'e.
3. `env/<env>.env` perrašomas iš to commit'o `env/<env>.env.example` ir build katalogo `target.env` (setup-server užregistruotas taikinys: `DEPLOY_CONTEXT`, `DEPLOY_SSH`, `DEPLOY_PATH`, `APP_UID`, `APP_GID`). Pavyzdys, kuris pats mini kurį nors iš jų, atmetamas; be `target.env` ir be `DEPLOY_PATH` pavyzdyje deploy'us nepradedamas (`No deploy target`). Be `target.env` lieka ankstesnė taisyklė: taikinys pavyzdyje, o kai `DEPLOY_CONTEXT=default` ir `DEPLOY_SSH` tuščias, pridedami runner'io vartotojo `APP_UID`/`APP_GID`.
4. Patikros dar kartą su deploy'inamo commit'o sąrašais ir `SETUP_MIN_VERSION`, kad naujas privalomas vardas būtų patikrintas jau pirmame deploy'uje. Tušti neprivalomi vardai išvardijami įspėjime (`Warning: empty Deployment variables`).
   **Per sena setup versija.** Jei deploy'inamas commit'as reikalauja naujesnio setup (`SETUP_MIN_VERSION`), o žingsnis vyksta setup-server worker'yje (runner'is perduoda `SETUP_SERVER_ROOT`, o setup checkout'as priklauso jo paskyrai), build root grąžinamas, setup checkout'as perjungiamas į naujausią to paties MAJOR release tag'ą (`vX.Y.Z`), kuris pakankamai naujas, ir žingsnis paleidžiamas iš naujo jau su juo (`==> setup 2.4.0 -> v2.7.0 …`). Naujas MAJOR, checkout'as su vietiniais pakeitimais ar tag'o nebuvimas – deploy'us sustoja ir paaiškina; programuotojo kompiuteryje setup savaime neatnaujinamas.
5. `IMAGES=yes`: `make release`, `make deploy-images`. Tada `make deploy ref=… backup=…` arba `make deploy rollback=1`.
6. `CRON=yes`: `make cron-reload`, kai kodas jau įdiegtas. Crontab'ą, kurio `supercronic -test` nepriima, cron nepritaiko: lieka ankstesnis grafikas, žingsnis baigiasi klaida, bet niekas negrąžinama.

**`auto` sprendimai** daromi prieš checkout'ą, lyginant build root'o veikiantį commit'ą su deploy'inamu, ir parašomi log'e su priežastimi (`==> IMAGES=auto: yes (changed since …: config/nginx/site.conf)`):

- `IMAGES=auto` – `yes`, kai dar nėra release image'ų (`.generated/release/<env>/images.lock.json`), kai setup pasikeitė nuo jų build'o (lock'o `setup` prieš `git describe` setup checkout'o, pvz. po `bin/update`), arba kai commit'ai keičia `Dockerfile`, `.dockerignore`, `docker/`, `compose/`, `config/` (be `config/cron/`), `env/common.env` ar `env/<env>.env.example`. `make deploy` kopijuoja tik kodą ir pasenusių image'ų nepastebi. `ROLLBACK=yes` su `auto` lieka be image'ų.
- `CRON=auto` – `yes`, kai commit'ai (ir `ROLLBACK=yes` atveju) keičia `config/cron/`. supercronic crontab'ą skaito tik startuodamas, o nei `make deploy`, nei `make deploy-images` cron konteinerio neperkuria, kai pasikeitė tik prijungtas failas (perbuildintas php-fpm image'as, kurį cron naudoja, dažniausiai išeina toks pat, todėl compose konteinerį palieka).

Kiekvienas `make` žingsnis vykdomas su tuo pačiu setup (`SETUP_DIRECTORY`) ir be Bitbucket'o `DOCKER_HOST`: jis rodo į Bitbucket'o debesies Docker servisą ir nustelbtų `DEPLOY_CONTEXT`.

**Nepavykus** build root checkout'as ir `env/<env>.env` grąžinami į anksčiau veikusį commit'ą. Jei nepavyko po rsync (setup istorijoje pažymėjo deploy'ų kaip nepavykusį), taikinyje jau naujas kodas: pataisyk ir deploy'ink iš naujo arba `ROLLBACK=yes`. Išimtis: jei šis paleidimas jau perkrovė konteinerius ant naujų image'ų (`IMAGES=yes` ar `auto` nusprendus `yes`), niekas negrąžinama, nes veikiantys konteineriai naudoja checkout'o konfigūraciją. Log'e parašyta, kaip grįžti: dar kartą su `IMAGES=yes`, arba ankstesnis commit'as su `REF=… IMAGES=yes` (atskiros kodo repozitorijos atveju – pipeline'as ant ankstesnio aplinkos commit'o).

### Deployment variables

Slaptažodžiai ir raktai – **Deployment variables (Secured)** Staging / Production aplinkose (**Repository settings → Deployments**). Jie gyvena tik žingsnio proceso aplinkoje ir į diską nerašomi. Vardų sąrašą sudaro setup, jo niekur kitur kartoti nereikia:

| Vardai | Kada | Privalomas |
| --- | --- | --- |
| `DATABASE_PASSWORD`, `DATABASE_ROOT_PASSWORD` | visada | taip |
| PS raktai pagal `PS_VERSION` (`PS_COOKIE_KEY`, `PS_COOKIE_IV`, `PS_NEW_COOKIE_KEY`, `PS_SECRET`, nuo PS 9 – `PS_API_PRIVATE_KEY`, `PS_API_PUBLIC_KEY`; PS 1.6 – `PS_RIJNDAEL_*`) | `PROFILE=prestashop` | taip |
| `DEPLOY_REQUIRED_VARIABLES` | projekto sąrašas | taip |
| `DATABASE_MONITOR_PASSWORD` | `SERVICES` turi `metrics` arba `dbmonitor` | ne |
| `METRICS_TOKEN` | `SERVICES` turi `metrics` | ne |
| `DBMONITOR_TOKEN` | `SERVICES` turi `dbmonitor` | ne |
| `PMA_HTPASSWD`, `MAILPIT_HTPASSWD`, `CRON_HTPASSWD`, `BACKUP_HTPASSWD`, `METRICS_HTPASSWD` | `SERVICES` turi tą įrankį ([slaptažodžiai įrankiams](05-servisai.md#slaptažodžiai-įrankiams-htpasswd)) | ne |
| `DEPLOY_OPTIONAL_VARIABLES` | projekto sąrašas | ne |
| `APP_ENVIRONMENT` vardai | kurių nemini nei `env/common.env`, nei `env/<env>.env.example` | ne |

- Vardas, kuriam env failas duoda reikšmę, nėra Deployment variable: laimi failas.
- Vardas, kurį env failas palieka tuščią, uždengia Deployment variable. Setup tokį vardą atmeta su klaida (išskyrus setup slaptažodžius, žr. [Kur rašyti reikšmę](03-nustatymai.md#kur-rašyti-reikšmę)). `APP_ENVIRONMENT` vardas su tuščia reikšme reiškia „išjungta“ ir į sąrašą nepatenka.
- `DEPLOY_PLAIN_VARIABLES` – vardai, saugomi ne Secured (pvz. URL ar ID, kuriuos naudinga matyti).

Be privalomo vardo deploy'us nepradedamas: pranešime išvardyti trūkstami vardai, ne reikšmės. Tušti neprivalomi vardai tik išvardijami įspėjime, nes jų integracijos lieka išjungtos.

Bitbucket'o pusę tikrina ir pildo `make deploy-variables`:

```bash
make deploy-variables ENV=prod names=1                             # sąrašas be tinklo
make deploy-variables ENV=prod token=~/.config/bitbucket/demo-token # ko trūksta (exit 1, jei trūksta)
make deploy-variables ENV=prod token=… from=~/prod-secrets.env      # sukuria trūkstamus iš failo
make deploy-variables ENV=prod token=… from=~/prod-secrets.env replace=1  # perrašo ir esamus
```

- Be `ENV` – `prod`. `ENV=stage` – Staging aplinka. Bitbucket aplinka randama pagal vardą arba tipą (`Staging`, `Production`).
- Repozitorija – `DEPLOY_BITBUCKET_REPOSITORY` (`workspace/repo`), o be jos – `origin` remote'as.
- Raktas – repozitorijos access token'as (**Repository settings → Security → Access tokens**) su teisėmis **Repositories: Read** ir **Pipelines: Read**, o kūrimui ir perrašymui dar **Pipelines: Edit variables** (`pipeline:variable`). Jį perduok failu `token=KELIAS` arba kintamuoju `BITBUCKET_ACCESS_TOKEN`.
- `from=FAILAS` – dotenv failas (`VARDAS=reikšmė`). Reikšmę su `$` rašyk viengubose kabutėse (pvz. htpasswd eilutes), kitaip komanda atsisako, nes Compose `$` išskleistų. Token'o ir reikšmių failai turi priklausyti tau ir turėti teises `0600`. Panaudojęs reikšmių failą ištrink.
- Lyginami tik vardai ir Secured žyma: Secured reikšmių Bitbucket neparodo. Reikšmės niekur nespausdinamos ir neperduodamos komandinėje eilutėje.
- Išėjimo kodai: 0 – visi vardai yra, 1 – kai kurių trūksta, 2 – Bitbucket klaida (atsakymas paaiškina, kurios teisės trūksta), 64 – argumentų, failų teisių ar sąrašo klaida.

### Komandos serveryje be pipeline'o (`secrets`)

Deployment variables egzistuoja tik pipeline žingsnyje. Serverio komandoms be pipeline'o (setup-server naktinė DB kopija, „backup now“, rankinis `make db-import ENV=prod`) reikia tų pačių reikšmių, bet jų kopija faile būtų antras šaltinis. Todėl stage/prod stack'as jas laiko pats:

- **Servisas `secrets`** (`<PROJECT_NAME>-<ENV>-secrets`) yra kiekviename stage/prod modelyje. Jo aplinkoje – visi šios aplinkos Deployment variables (tas pats sąrašas, kaip `make deploy-variables names=1`), kaip nuorodos `VARDAS: ${VARDAS-}`, todėl kiekvienas deploy'us įrašo pipeline'o reikšmes. Konteineris tik miega: be tinklo, be prijungimų, tik skaitymui, 16 MB.
- **Komanda be pipeline'o** (`CI` nėra `true`) vardą, kurio jos aplinkoje nėra, paima iš to konteinerio (`docker inspect`, su `DEPLOY_CONTEXT` – per jį). Proceso aplinkos reikšmė laimi.
- **Pipeline'e** (`CI=true`, taip nustato Bitbucket ir GitHub) store neskaitomas: Bitbucket'e ištrintas kintamasis negrįžta iš veikiančio stack'o.
- Reikšmės laikomos Docker konteinerio konfigūracijoje, kaip ir kitų konteinerių aplinka (DB slaptažodžiai DB konteineryje ten jau yra). Jas mato, kas turi Docker prieigą prie serverio.
- Kol stack'as dar nė karto nedeploy'intas, store'o nėra: pirmas deploy'us eina per pipeline'ą arba rankomis su reikšmėmis aplinkoje.

Deploy teisių ribojimas (tik tam tikri žmonės ar šakos konkrečiai aplinkai) Bitbucket'e yra Premium plano funkcija. Be jos produkcijos deploy'ų gali paleisti kiekvienas, turintis rašymo teisę į repozitoriją.

## GitHub Actions

GitHub projektas deploy'inamas taip pat kaip Bitbucket: ta pati komanda `make pipeline-deploy` build root'e, tie patys paleidimo kintamieji, patikros, abu išdėstymai, grąžinimas nepavykus ir deploy istorija (žr. [Bitbucket Pipelines](#bitbucket-pipelines)). Projektas laiko tik `.github/workflows/deploy.yml` ir tuos pačius vardų sąrašus `env/common.env`. Pavyzdys: [templates/pipelines/github-deploy.yml](../../templates/pipelines/github-deploy.yml); `./create-project --ci github` jį įrašo pats.

- Deploy'ų paleidžia žmogus: **Actions → Deploy → Run workflow**. Pasirenkama `environment` (`staging` arba `production`) ir `ref`, `images`, `rollback`, `backup` – tai Bitbucket'o `REF`, `IMAGES`, `ROLLBACK`, `BACKUP` su tomis pačiomis reikšmėmis. Mygtukas atsiranda, kai workflow failas yra numatytojoje šakoje. **Use workflow from** šaka ar tag'as nurodo pipeline commit'ą (`GITHUB_SHA`).
- Job'as turi `environment: staging` arba `production`. Todėl GitHub įrašo deploy'ų į **Deployments** istoriją ir atiduoda job'ui tos aplinkos secrets ir variables. Abi aplinkas sukurk **Settings → Environments**. Produkcijai verta įjungti **Required reviewers** ir **Deployment branches and tags**.
- Job'ą vykdo build serverio self-hosted runner'is su žyma `setup.build` (`runs-on: [self-hosted, linux, setup.build]`). `actions/checkout` nereikia: darbas vyksta build root'e.
- **Build root** – Repository variable `BUILD_ROOT` (**Settings → Secrets and variables → Actions → Variables**), o be jo – setup-server išdėstymas `$SETUP_SERVER_ROOT/projects/<repo>` (numatyta `/srv/setup-server`), kur `<repo>` yra repozitorijos vardas be savininko mažosiomis raidėmis (`GITHUB_REPOSITORY` antroji dalis, pvz. `BigSant/DemoShop` → `demoshop`). `SETUP_SERVER_ROOT` job'ui nustato runner'io servisas.
- `concurrency: deploy-<aplinka>`: į vieną aplinką vienu metu vyksta vienas deploy'us. Naujas laukia, o laukiantį pakeičia dar naujesnis paleidimas.
- GitHub neturi kintamojo su job'o aplinkos vardu, todėl workflow jį perduoda kintamuoju `DEPLOY_ENVIRONMENT`. Be jo komanda atsisako (`DEPLOY_ENVIRONMENT is not set`). Kitaip pavadintai aplinkai pridėk `ENV=stage` arba `ENV=prod`. GitHub aplinkos vardas lyginamas be didžiųjų raidžių skirtumo.
- Setup atpažįsta GitHub pagal `GITHUB_ACTIONS=true`, Bitbucket – pagal `BITBUCKET_*` kintamuosius.

Privačioje repozitorijoje environment secrets veikia tik su GitHub Pro, Team arba Enterprise planu. **Required reviewers** privačioje repozitorijoje yra tik Enterprise plane.

### Secrets ir variables

Bitbucket Deployment variables patenka į žingsnio aplinką patys. GitHub secrets – ne: kiekvieną reikia perduoti per `env:`, o sąrašas kiekviename projekte kitas. Todėl workflow žingsniui perduoda juos visus iš karto:

```yaml
env:
  DEPLOY_SECRETS_JSON: ${{ toJSON(secrets) }}
  DEPLOY_VARIABLES_JSON: ${{ toJSON(vars) }}
```

`make pipeline-deploy` iš šių JSON įrašo į savo proceso aplinką kiekvieną vardą, kurio job'o aplinkoje dar nėra:

- Job'o `env:` laimi, net tuščias. Taip secret'as negali pakeisti `REF`, `IMAGES` ar runner'io `PATH`. Vardą galima perduoti ir aiškiai (`DATABASE_PASSWORD: ${{ secrets.DATABASE_PASSWORD }}`).
- `GITHUB_*`, `RUNNER_*` ir `ACTIONS_*` vardai praleidžiami. Tarp jų – `GITHUB_TOKEN`, kuris visada yra `secrets` kontekste.
- Jei vardas yra ir secret, ir variable, naudojamas secret ir parašomas įspėjimas.
- Patys JSON į `make` žingsnius neperduodami. Reikšmės niekur nespausdinamos.

Vardų sąrašas tas pats kaip [Deployment variables](#deployment-variables). GitHub'e `DEPLOY_PLAIN_VARIABLES` vardai yra aplinkos **variables** (jų reikšmės matomos nustatymuose ir paleidimo log'e), visi kiti – aplinkos **secrets**. Pranešimas apie trūkstamus vardus: `Environment secrets or variables missing or empty (Settings -> Environments -> production): …`.

**Saugumo kaina.** `toJSON(secrets)` atiduoda žingsniui visus job'ui matomus secrets: aplinkos, repozitorijos ir organizacijos, ne tik deploy'ui reikalingus. Tiek pat Bitbucket'as atiduoda deploy žingsniui (visus Deployment ir Repository variables). Statiniai tikrintuvai (pvz. zizmor `overprovisioned-secrets`) tokį perdavimą žymi – čia tai sąmoningas pasirinkimas. Rizika ribojama taip:

- Workflow paleidžiamas tik rankomis (`workflow_dispatch`), ne iš pull request'ų, todėl svetimas kodas prie secrets ir runner'io neprieina. Self-hosted runner'į naudok tik privačiai repozitorijai.
- `permissions: contents: read`: workflow `GITHUB_TOKEN` gali tik skaityti. Build root kodą parsisiunčia su build serverio raktu.
- Runner'is log'e slepia secret reikšmes, taip pat jų JSON pavidalą (`\"`, `\n`). Variables neslepiami, todėl slaptų reikšmių į juos nedėk.

Jei projektui reikia griežčiau, JSON eilutes iš workflow išimk ir kiekvieną vardą perduok `env:` atskirai. Trūkstamą privalomą vardą `make pipeline-deploy` vis tiek praneš prieš bet kokį pakeitimą.

### make deploy-variables GitHub'e

Ta pati komanda tikrina ir pildo GitHub aplinkos secrets ir variables (komandos kaip [Deployment variables](#deployment-variables) skyriuje):

```bash
make deploy-variables ENV=prod names=1                                # sąrašas be tinklo
make deploy-variables ENV=prod token=~/.config/github/demo-token       # ko trūksta (exit 1, jei trūksta)
make deploy-variables ENV=prod token=… from=~/prod-secrets.env         # sukuria trūkstamus iš failo
```

- Tiekėją nurodo `CI_PROVIDER=github` (`env/common.env`, įrašo `create-project`). Be jo – vienintelis `DEPLOY_GITHUB_REPOSITORY` arba `DEPLOY_BITBUCKET_REPOSITORY`, o be jų – `origin` remote'as (`github.com` reiškia GitHub). Kitaip – Bitbucket.
- Repozitorija – `DEPLOY_GITHUB_REPOSITORY` (`savininkas/repo`), o be jos – `origin` remote'as. Aplinka – `staging` (`ENV=stage`) arba `production` (`ENV=prod`).
- Raktas – fine-grained personal access token'as tai repozitorijai su teise **Environments: Read-only** tikrinimui arba **Environments: Read and write** pildymui (**Metadata: Read-only** pridedama automatiškai). Tinka ir classic token'as su `repo`. Jį perduok failu `token=KELIAS` (`0600`) arba kintamuoju `GH_TOKEN` ar `GITHUB_TOKEN`.
- Variables setup rašo per GitHub API. Secret reikšmę reikia užšifruoti aplinkos viešuoju raktu (libsodium sealed box), todėl secrets rašo oficialus GitHub CLI: `gh secret set VARDAS --env production --repo savininkas/repo`, reikšmė perduodama per standartinį įvedimą, token'as – `GH_TOKEN` kintamuoju. Be `gh` (`apt install gh`, https://cli.github.com) nieko neįrašoma.
- `gh` nuskaito reikšmę be paskutinio eilutės lūžio. Secret'ą, kurio reikšmė baigiasi eilutės lūžiu, komanda praleidžia: įrašyk jį ranka.
- Secret reikšmių GitHub neparodo, todėl lyginami tik vardai ir rūšis (secret ar variable). Išėjimo kodai tokie patys: 0, 1, 2 (GitHub ar `gh` klaida), 64.

## Prisijungimas prie serverio per worker'į

Programuotojas prie stage/prod serverio tiesiogiai nesijungia: jo raktas yra tik setup-server worker'yje (tas pats, kaip
[`make db-pull`](07-duomenu-baze.md#serverio-kopija-db-pull)), o toliau worker'is jungiasi savo prieiga – ta pačia,
kuria deploy'ina (`DEPLOY_SSH`, Docker context'as). LIVE serveryje programuotojo raktas nereikalingas.

Ką raktas gali pasiekti, nurodo serverio administratorius setup-server pusėje, kiekvienai projekto aplinkai atskirai:
`sudo bin/add-backup-key …pub --name vardas --from <VPN tinklas> --grant PROJEKTAS:ENV=LYGIS` (`--grant` kartojamas).
Lygiai (kiekvienas apima ankstesnius):

| Lygis | Ką leidžia |
| --- | --- |
| `phpfpm-readonly` | Terminalas ar komanda php-fpm konteineryje kita paskyra, ne aplikacijos: failų keisti negali. DB slaptažodis konteinerio aplinkoje lieka, todėl duomenų bazės tai nesaugo. |
| `phpfpm` | Tas pats aplikacijos paskyra (`APP_UID`, failai lieka teisingo savininko), su `root=1` – root konteineryje. |
| `docker` | Projekto konteineriai: `ps`, `logs`, `exec`, `start`, `stop`, `restart`, `top`, `stats`, `inspect`. Serverio nustatymų ir kitų Docker veiksmų (`run`, volume'ai, image'ai) – ne; `exec` – ne į konteinerius su Docker socket'u (`metrics-docker`, `metrics-logs`). |
| `full` | Pats serveris deploy'aus paskyra, projekto kataloge (`DEPLOY_PATH`) – kaip prisijungus įprastai. Projektui, deploy'inamam į patį worker'į (`--target local`), tai paskyra `setup` worker'yje: visi jo projektai ir Docker. |

```bash
make ssh                                  # php-fpm terminalas prod aplinkoje
make ssh from=stage root=1                # stage, root konteineryje
make ssh readonly=1                       # tik skaityti (kita paskyra)
make ssh cmd="php bin/console cache:clear"    # viena komanda; išėjimo kodas – jos
make ssh cmd="sh -c 'tail -n 50 var/logs/*.log | grep -i error'"   # kanalai ir * – per sh -c
make ssh docker="ps -a"                   # projekto konteineriai
make ssh docker="logs --tail 200 -f nginx-proxy"
make ssh docker="restart php-fpm"
make ssh host=1                           # pats serveris (full)
make ssh host=1 cmd="df -h"
make ssh access=1                         # ką mano raktas gali pasiekti: PROJEKTAS ENV LYGIS
```

- Serveris ir raktas – `make db-pull` atsakymas (`BACKUP_SSH`, `BACKUP_SSH_KEY`, pirmą kartą paklausiama), projekto vardas
  serveryje – tas pats (`BACKUP_SERVER_PROJECT`, aplinkos repo vardas, `PROJECT_NAME`): iš jų imamas pirmas, kurį raktas
  gali pasiekti (`access`).
- `cmd=` ir `docker=` skaidomi į žodžius kaip shell'e, bet shell'as jų nevykdo: kanalams, `*` ir `&&` – `sh -c '…'`.
- Terminale ssh gauna TTY (`-t`: terminalas, klausimai, spalvos); su `|` ar `>` – be jo, todėl išvestį galima nukreipti:
  `make ssh cmd="php -i" > php-info.txt`.
- Kiekvienas prisijungimas ir komanda įrašomi worker'io žurnale su rakto vardu.
- „no access gate yet“ – serverio setup-server dar be šios funkcijos (`bin/update`) arba raktui nesuteiktas lygis;
  „may reach no project“ – raktas negauna šio projekto (`make ssh access=1` parodo, ką gauna) arba serveryje jis kitu
  vardu (`BACKUP_SERVER_PROJECT`).

## Bandymas iš savo kompiuterio

Kol build serverio nėra, jo vaidmenį atlieka programuotojo kompiuteris: tos pačios komandos su privačiu `env/prod.env`. Be `DEPLOY_CONTEXT` stage/prod konteineriai valdomi šio kompiuterio Docker'yje įprastomis komandomis (`make up ENV=prod`). `make deploy-images` ir `make deploy` reikalauja `DEPLOY_CONTEXT`: vietiniam bandymui tinka Docker context'as, rodantis į šį kompiuterį (standartinis `default`). Jei `DEPLOY_SSH` tuščias, `rsync` rašo į vietinį `DEPLOY_PATH`.
