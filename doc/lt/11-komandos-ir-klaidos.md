# 11. Komandos ir klaidų sprendimai

[Turinys](README.md) · [Atgal](10-kasdienis-darbas.md) · [Toliau: cache ir resursai](12-cache-ir-hibridines-aplinkos.md)

Komandas vykdyk projekto kataloge (`~/Projects/demo/app`). Numatyta aplinka `local`, kitą pasirink `ENV=test|stage|prod` (ar [papildomą](09-aplinkos.md#papildomos-aplinkos-stage_environments), pvz. `ENV=mdv`). `make help` parodo trumpą sąrašą.

Setup repozitorijoje yra dvi komandos be `make`:

| Komanda | Ką daro |
| --- | --- |
| `~/Projects/setup/install-host [--check]` | Vieną kartą paruošia kompiuterį, žr. [pirmas paleidimas](02-pradzia.md#a-kompiuterio-paruošimas-vieną-kartą). |
| `~/Projects/setup/create-project [vardas] [parametrai]` | Sukuria naujo projekto failus ir deploy failą (`--ci bitbucket\|github`, numatyta `bitbucket`), žr. [naujas projektas](02-pradzia.md#c-naujas-projektas-su-create-project). |

## Paruošimas ir paleidimas

| Komanda | Ką daro |
| --- | --- |
| `make setup` | `init` → `bootstrap` → `build` → `app-prepare` → `up`. DB neliečia, pabaigoje praneša jos būseną. |
| `make init` | Sukuria privatų env iš pavyzdžio, sugeneruoja trūkstamus local/test slaptažodžius ir PS raktus, duomenų katalogus. |
| `make bootstrap` | Portai, vietinis DNS, TLS, kompiuterio Nginx maršrutas, PhpStorm. Tik local/test. |
| `make build` | Sukuria image'us (tik tuos, kurių šaltiniai pasikeitė). |
| `make pull` | Atsisiunčia išorinius image'us. |
| `make up` | Patikrina konfigūraciją, sukuria trūkstamus duomenų katalogus (pvz. naujo setup `data/metrics`) ir paleidžia servisus su esamais image'ais, laukia sveikatos patikrų. |
| `make down` | Pašalina konteinerius ir tinklą. Duomenys lieka. |
| `make restart [service=php-fpm]` | Perkuria servisą (be `service` – visus) su dabartinėmis env reikšmėmis. |
| `make ps` | Parodo konteinerius. |
| `make logs [service=database] [follow=1] [tail=100]` | Parodo log'us. |
| `make logs [service=php-fpm] day=2026-10-14\|today\|yesterday` | Tos dienos log'ų failai iš log'ų istorijos (`SERVICES=metrics`), ir jau perkurtų konteinerių. |
| `make shell` | Terminalas PHP konteineryje. |
| `make check` | Patikrina visą konfigūraciją nieko nepaleisdamas (įskaitant PS ir PHP suderinamumą). |
| `make config` | Įrašo galutinį Compose modelį į `.generated/compose.<ENV>.yaml`. |
| `make doctor` | Patikrina Docker, image'us, portus, prijungimus, TLS; veikiančiam PS – konfigūraciją ir DB ryšį. |
| `make setup-info` | Setup versija, commit'as ir necommit'inti pakeitimai. |
| `make runtime-info` | Išspręsti cache, pašto, resursų ir kiti nustatymai, be slaptažodžių. |
| `make image-prune [apply=1] [cache=1]` | Peržiūrėti / pašalinti nenaudojamus senus setup image'us (ir build cache). |

## Aplikacija

| Komanda | Ką daro |
| --- | --- |
| `make composer [dir=…] [cmd="install"]` | Composer PHP konteineryje. `dir` – kelias nuo `public/`. Be `cmd` parodo versiją. |
| `make npm [dir=themes/example/_dev] [cmd="run build"]` | npm PHP konteineryje. |
| `make cache-clear` | Išvalo aplikacijos cache ir perkrauna PHP (OPcache/APCu). Ne PS projektams reikia `CACHE_CLEAR_COMMAND`. |

## Duomenų bazė

| Komanda | Ką daro |
| --- | --- |
| `make db-prepare` | Paleidžia tik DB ir palaukia jos sveikatos. |
| `make db-import-plan file=…` | Parodo importo planą, SQL nevykdo. |
| `make db-import file=… [skip=1] [backup=1] [db-fixtures=RINKINYS]` | Užkelia visą dump'ą ir visą po-užkėlimo seką (`skip=1` – be registro `skip` lentelių duomenų). |
| `make db-load [set=dev] [replace=1] [backup=1]` | Užkelia schemą, bazinį rinkinį, fixtures ir po-užkėlimo seką. |
| `make db-dataset [set=dev]` | Sukuria anonimizuotą bazinį rinkinį iš vietinės LIVE kopijos (tik local). |
| `make migration-draft [name=trumpas-vardas]` | Eksportuoja schemą ir sugeneruoja migracijos juodraštį nuo paskutinio commit'o, be commit'o. |
| `make db-migrate [dry=1]` | Pritaiko laukiančias apsaugotas migracijas (`dry=1` – tik parodo SQL). |
| `make db-rollback [steps=N\|to=MIGRACIJA] [dry=1]` | Paleidžia paskutinių pritaikytų migracijų `-- Rollback:` dalį, nuo naujausios. Žr. [rollback](07-duomenu-baze.md#rollback). |
| `make schema-export` | Eksportuoja lentelių apibrėžtis į `database/schema/` (su papildomomis schemomis – kiekvieną schemą į savo katalogą). |
| `make schema-check` | Palygina DB su Git indeksu. |
| `make schema-hook-install` | Įdiegia commit hook'ą, kuris eksportuoja schemą ir stabdo commit'ą pasikeitus. |
| `make git-hooks` | Įrašo setup'o `pre-commit` (schemos patikra, tada `bin/pre-commit`) ir `pre-push` (`bin/pre-push`) hook'us šiame klone. Žr. [git hook'ai](08-kodo-kokybe.md#f-patikros-prieš-commitą-ir-pushą-git-hookai). |
| `make schema-resolve` | Klasifikuoja registre nežinomas lenteles (įtraukti / pašalinti). |
| `make db-backup [file=…] [full=1]` | Sukuria DB kopiją (`.generated/backups/`) pagal registrą; `full=1` – visų lentelių duomenys. |
| `make db-backup-prune [apply=1]` | Peržiūrėti / ištrinti senas kopijas. |
| `make db-pull [from=prod\|stage\|mdv…] [full=1] [fresh=1] [import=1] [backup=1] [configure=1]` | Parsisiunčia setup-server kopiją per SSH: naujausią pagal registrą, `full=1` – pilną, `fresh=1` – serveris padaro naują dabar; `import=1` – ir užkelia. Pirmą kartą paklausia serverio. Žr. [db-pull](07-duomenu-baze.md#serverio-kopija-db-pull). |
| `make ssh [from=prod\|stage\|mdv…] [root=1\|readonly=1] [cmd="…"]`, `host=1`, `docker="…"`, `access=1` | Terminalas ar komanda stage/prod serveryje per setup-server worker'į: php-fpm konteineryje, `host=1` – pats serveris, `docker=` – projekto konteineriai; lygį suteikia serveris. Žr. [prisijungimas](09-aplinkos.md#prisijungimas-prie-serverio-per-workerį). |
| `make db-fixtures-plan set=…` / `make db-fixtures-load set=…` | Peržiūrėti / įkelti fixtures rinkinį. |
| `make db-restrict-grants` | Vieną kartą susiaurina senos DB teises, root gauna atskirą slaptažodį. |
| `make db-monitor-user` | Sukuria arba atnaujina DB monitoriaus vartotoją. |
| `make doctrine-build`, `make doctrine cmd=…`, `make doctrine-diff [ref=HEAD]` | Neprivalomas Doctrine migracijų įrankis. |

## Testai ir kodo kokybė

| Komanda | Ką daro |
| --- | --- |
| `make test-prepare [set=dev]` | Atnaujina `ENV=test` kodo kopiją, užkelia rinkinį į testinę DB ir paleidžia `ENV=test`. |
| `make test-sync` | Atnaujina tik testinę kodo kopiją. |
| `make test-init [refresh=1]` | Paruošia (arba su `refresh=1` atnaujina) testinę kodo kopiją ir `env/test.env`. Paprastai užtenka `test-prepare`. |
| `make phpunit [module=VARDAS] [filter=ŠABLONAS] [sync=0]` | PHPUnit tik `ENV=test`. |
| `make e2e [cmd=…]` | Playwright tik `ENV=test`. |
| `make phpstan [cmd=…]` / `make phpstan-baseline` | PHPStan / baseline. |
| `make phpcs [cmd=…]` | PHP-CS-Fixer (numatyta – tik patikra). |
| `make ide-init` / `make ide-refresh` | PhpStorm nustatymai / tik privatus IDE Compose failas. |

## Release ir deploy

| Komanda | Ką daro |
| --- | --- |
| `make release ENV=stage\|prod [allow_dirty=1]` | Sukuria image'us iš commit'into setup, užrašo image lock failą. |
| `make deploy-images ENV=stage\|prod [transfer=0]` | Nusiunčia image'us į `DEPLOY_CONTEXT` ir paleidžia ten. |
| `make deploy ENV=stage\|prod ref=TAG [backup=1]` | Kodo release'as, DB kopija (tik su `backup=1`), rsync, projekto hook'as, PS modulių upgrade'ai, migracijos, cache. |
| `make deploy ENV=stage\|prod rollback=1` | Iš naujo išsiunčia ankstesnį sėkmingai įdiegtą release'ą. |
| `make cron-reload ENV=stage\|prod` | Veikiantis cron konteineris perskaito `config/cron/` be restart'o: crontab'as sugeneruojamas iš naujo, patikrinamas `supercronic -test`, supercronic gauna `SIGUSR2`. Vykdomos užduotys nenutraukiamos; netinkamas crontab'as nepritaikomas. |
| `make files-backup ENV=stage\|prod [file=…]` | `BACKUP_FILE_PATHS` keliai iš taikinio į `.generated/backups/files-<env>-…Z.tar.gz` ([failų kopijos](09-aplinkos.md#failų-kopijos-backup_file_paths)). |
| `make files-pull [from=prod\|stage] [fresh=1] [extract=1]` | Naujausia (`fresh=1` – nauja) serverio failų kopija į `.generated/backups`, su `extract=1` – ir į vietinį kodą ([failų kopijos](09-aplinkos.md#failų-kopijos-backup_file_paths)). |
| `make translations-migration name=VARDAS` | Vietiniai vertimai, pakeisti nuo atskaitos taško, į naują migraciją `translations/migrations/` ([vertimai](09-aplinkos.md#vertimai-translations)). |
| `make translations-migrate [ENV=stage\|prod] [dry=1]` | Pritaiko dar nepritaikytas vertimų migracijas (deploy'us tai daro pats); `dry=1` – tik parodo. |
| `make translations-base` | Iš naujo paima vietinių vertimų atskaitos tašką (`.generated/translations/base.json`). |
| `make module-pull ENV=stage\|prod name=MODULIS` | Nukopijuoja tik serveryje esantį modulį į vietinį parduotuvės kodą (tik `PROFILE=prestashop`). |
| `make pipeline-deploy [ENV=stage\|prod]` | Bitbucket arba GitHub Actions deploy žingsnis build root'e: patikros, checkout'as, `env/<env>.env`, `release`/`deploy-images`/`deploy`/`cron-reload` pagal `REF`, `IMAGES`, `CRON`, `ROLLBACK`, `BACKUP`. Žr. [Bitbucket Pipelines](09-aplinkos.md#bitbucket-pipelines), [GitHub Actions](09-aplinkos.md#github-actions). |
| `make deploy-variables [ENV=stage\|prod] [names=1] [from=FAILAS [replace=1]] [token=KELIAS]` | Palygina Bitbucket Deployment variables arba GitHub aplinkos secrets ir variables (`CI_PROVIDER`) su setup sąrašu, su `from` sukuria trūkstamus. Žr. [Deployment variables](09-aplinkos.md#deployment-variables), [GitHub'e](09-aplinkos.md#make-deploy-variables-githube). |

## Make argumentai

| Argumentas | Kur veikia | Pavyzdys |
| --- | --- | --- |
| `ENV` | Visos runner komandos | `make up ENV=test`. |
| `PROFILES` | Runner komandos | `make up PROFILES=` paleidžia tik pagrindą. |
| `SETUP_DIRECTORY` | Bendro setup vieta | `make SETUP_DIRECTORY=/opt/setup check`. |
| `service` | `restart`, `logs` | `service=php-fpm`. |
| `follow`, `tail` | `logs` | `follow=1 tail=200`. |
| `cmd`, `dir` | `composer`, `npm`, `phpstan`, `phpcs`, `e2e`, `doctrine` | `cmd="run build"`. Skaidoma į argumentus, ne shell: `&&` neveikia. |
| `file` | `db-import`, `db-import-plan`, `db-backup`, `files-backup` | `file=/kelias/dump.sql.gz` (`files-backup` – `.tar.gz`). |
| `skip`, `backup`, `db-fixtures` | `db-import` | `skip=1 backup=1`. |
| `full` | `db-backup` | `full=1`. |
| `set`, `replace`, `backup` | `db-load`, `db-dataset`, `test-prepare`, `db-fixtures-*` | `set=dev replace=1`. |
| `module`, `filter`, `sync` | `phpunit` | `module=prgenericsync`. |
| `ref`, `backup`, `rollback` | `deploy` (`ref` dar `doctrine-diff`) | `ref=v1.4.0`. |
| `allow_dirty` | `release` | `allow_dirty=1`. |
| `transfer` | `deploy-images` | `transfer=0`. |
| `name` | `module-pull` | `name=mymodule`. |
| `from`, `replace`, `token`, `names` | `deploy-variables` | `from=~/prod-secrets.env token=~/.config/bitbucket/demo-token` (GitHub'e – `token=~/.config/github/demo-token`). |
| `apply`, `cache` | `image-prune`, `db-backup-prune` | `apply=1`. |
| `refresh` | `test-init` | `refresh=1`. |
| `IDE_DOCKER_SERVER`, `IDE_CONFIG_DIRECTORY` | `ide-init` | `IDE_DOCKER_SERVER=Docker`. |

Senas importo argumentas `fixtures=` atmetamas, naudok `db-fixtures=`.

## Kai reikia Compose komandos, kurios make neturi

```bash
make config
docker compose -f .generated/compose.local.yaml exec php-fpm php --ini
docker compose -f .generated/compose.local.yaml exec webserver httpd -t
docker compose -f .generated/compose.local.yaml exec nginx-proxy nginx -t
```

Pirmiau visada sugeneruok šviežią modelį su `make config`. Testinei aplinkai: `make config ENV=test` ir `.generated/compose.test.yaml`. Tiesioginis Docker kvietimas runner'io apsaugų nevykdo, todėl `up` ir `down` daryk per `make`.

## Dažniausios klaidos

| Pranešimas arba požymis | Sprendimas |
| --- | --- |
| `Shared setup not found` | Setup nėra `~/Projects/setup`. Atsisiųsk arba perduok `SETUP_DIRECTORY=/absoliutus/kelias`. |
| `Root-level .env / compose.yaml sources are no longer supported` | Senas (v1) projektas. Perkelk pagal [RELEASES.md](../RELEASES.md). |
| `Create …/env/local.env from its example` | Nėra `env/local.env` ir jo pavyzdžio. Susikurk failą, žr. [pirmas paleidimas](02-pradzia.md#jei-make-setup-sustojo). |
| `Local DNS entry is missing. Run setup/install-host once…` | Paleisk `~/Projects/setup/install-host`. |
| `The host helpers changed with this setup version; rerun setup/install-host` | Paleisk `install-host` dar kartą. |
| `Set DATABASE_ROOT_PASSWORD in …` | Local/test: `make init`. Stage/prod: įrašyk į env arba Deployment variables. |
| `DATABASE_ROOT_PASSWORD must differ from DATABASE_PASSWORD` | Root slaptažodis turi būti kitoks. |
| `Unknown SERVICES entry: …` | Pataisyk vardą pagal [katalogą](05-servisai.md#servisų-katalogas-services). |
| `prod requires explicit container limits: …` | Įrašyk pasiūlytus `<SERVISAS>_MEMORY/_CPUS` į `env/prod.env` (ar `stage.env`). |
| `pma on prod requires PMA_ALLOWED_IPS or ALLOWED_IPS` | Įrašyk IP sąrašą arba išimk įrankį iš `SERVICES` toje aplinkoje. |
| `XDEBUG_MODE applies to local/test only` | Pašalink `XDEBUG_MODE` iš stage/prod env. |
| `PS_VERSION=… does not match the shop code` | Suderink `PS_VERSION` su kodu. |
| `Set PS_VERSION in env/common.env` | PS profiliui `PS_VERSION` privalomas. |
| `PHP … is not supported by PrestaShop …` | PHP versija netinka PS šakai, žr. [suderinamumas](06-aplikacija.md#prestashop-ir-php-suderinamumas). |
| `PS_VERSION … is not in the setup compatibility table` | Versijos nėra lentelėje. Patikrink `PS_VERSION` arba, fork'ui, `SETUP_COMPATIBILITY_CHECK=off`. |
| `No app/config/parameters.php yet and no keys to create it` | Local/test: `make init`. Stage/prod: pateik PS raktus env. |
| `… must extend the CORE image` | Projekto `Dockerfile` turi prasidėti `ARG SETUP_PHP_IMAGE` ir `FROM ${SETUP_PHP_IMAGE}`. |
| `Unknown template variable ${…}` konteinerio log'e | `.template` faile naudojamas kintamasis, kurio konteineris neturi. Perduok jį per Compose `environment` arba pataisyk vardą. |
| `MAIL_MODE=mailpit requires mailpit in SERVICES` | Įjunk `mailpit` arba pasirink kitą `MAIL_MODE`. |
| `INTEGRATIONS_ENABLED names unknown integrations` | Vardas turi sutapti su failu `database/after-import/integrations/<vardas>.sql`. |
| `… tables are not in the table registry` (commit metu) | `make schema-resolve`, tada `git add` registrą. |
| `The commit was stopped for review` | Peržiūrėk schemą ir migracijos juodraštį, `git add -A database`, commit'ink dar kartą. |
| `… still has a TODO; finish it before applying` | Parašyk `-- TODO:` bloką rankomis su patikra. |
| `Database … has N tables; replace=1 drops them first` | `make db-load set=dev replace=1` (ir `backup=1`, jei reikia kopijos). |
| `ENV=test is not prepared; run make test-prepare first` | Paleisk `make test-prepare`. |
| `Test service …: writable mount … must stay inside .generated/test` | Testinėje aplinkoje bendri failai prijungiami tik `:ro`, žr. [e2e](08-kodo-kokybe.md#c-e2e-testai-su-playwright). |
| `Setup has uncommitted changes (…); commit or tag it before a release` | Release'as daromas tik iš commit'into setup. |
| `ENV=prod runs on … from the release model; use make deploy-images` | Su `DEPLOY_CONTEXT` konteinerius paleidžia `make deploy-images`. |
| `Cannot pull … for the release` / `Release images are not on this machine` | Išorinio image'o (pvz. `getmeili/meilisearch`) nepavyko parsisiųsti arba image'as nesukurtas; release'as nerašo lock'o, kurio `docker save` neperduotų. |
| `config/deploy/after-rsync.sh failed (exit N); the deploy stopped` | Projekto hook'as nepavyko: naujas kodas taikinyje (istorijoje pažymėtas kaip nepavykęs), bet migracijos ir cache valymas nevykdyti. Pataisyk ir deploy'ink iš naujo arba `rollback=1`. |
| `DEPLOY_PROTECTED_PATHS entry is not a plain path inside the code tree` | Saugomas kelias turi būti santykinis kodo medžio kelias be `..`, pvz. `var/data/`. |
| `BACKUP_FILE_PATHS entry is not a plain path inside the code tree` | Tas pats `BACKUP_FILE_PATHS` keliui, pvz. `modules/*/translations/`. |
| `BACKUP_FILE_PATHS is empty: nothing to back up` | `make files-backup` reikia `BACKUP_FILE_PATHS` (`env/common.env`). |
| `files-backup copies a server environment's files` | `make files-backup` – tik `ENV=stage`, `prod` ar `STAGE_ENVIRONMENTS` aplinkai. |
| `Name the migration` | `make translations-migration` reikia `name=`. |
| `make translations-migration drafts from the local shop` | Juodraštis – tik vietinei aplinkai (be `ENV` arba `ENV=local`). |
| `… changed after it was applied here; it is not rerun` | Pritaikytos migracijos nekeisk: taisymui – nauja migracija. |
| `… here holds more than $_MODULE lines: fix it by hand` | Taikinio vertimų failas turi kitokio PHP kodo; sutvarkyk jį ir deploy'ink (migruok) iš naujo. |
| `PrestaShop 1.6 has no translation table` | PS 1.6 migracijoje – tik `legacy`. |
| `make files-pull downloads into a developer copy` | `make files-pull` – tik vietinei aplinkai (be `ENV` arba `ENV=local`). |
| `BACKUP_FILE_PATHS is empty in this checkout` | `make files-pull extract=1` rašo tik `BACKUP_FILE_PATHS` kelius: įrašyk jį `env/common.env`, kaip serveryje. |
| `The backup server has no files backups yet` | Serverio setup-server dar be failų kopijų: ten `bin/update`. |
| `The download is not a complete .tar.gz` | Atsisiuntimas nutrūko ar sugadintas; nieko neišsaugota – kartok. |
| `rsync from … failed (exit N); no backup written` | Taikinys nepasiektas arba nėra kodo katalogo; archyvas neparašytas, ankstesni lieka. |
| `This project needs setup … or newer (SETUP_MIN_VERSION in env/common.env)` | Projektui reikia naujesnio setup: `git -C ~/Projects/setup pull`, serveryje – naujesnis release tag'as. |
| `Run this from a Staging or Production deployment step` / `BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set` | `make pipeline-deploy` veikia tik Bitbucket žingsnyje su `deployment: Staging` arba `Production`. Kitaip pavadintai aplinkai pridėk `ENV=stage\|prod`. |
| `DEPLOY_ENVIRONMENT is not set` / `neither BITBUCKET_DEPLOYMENT_ENVIRONMENT nor GITHUB_ACTIONS is set` | GitHub job'as turi `environment: staging\|production` ir tą patį vardą perduoda `DEPLOY_ENVIRONMENT` (žr. `templates/pipelines/github-deploy.yml`). Be jokio CI komanda neveikia. |
| `Environment secrets or variables missing or empty (Settings -> Environments -> …): …` | Įrašyk vardus į tos GitHub aplinkos secrets (plain – variables): `make deploy-variables ENV=… from=…`. Jei pridėta `The job passes no DEPLOY_SECRETS_JSON`, workflow žingsniui trūksta `DEPLOY_SECRETS_JSON: ${{ toJSON(secrets) }}`. |
| `DEPLOY_SECRETS_JSON is not a JSON object` | Workflow turi perduoti `${{ toJSON(secrets) }}` (ir `${{ toJSON(vars) }}` – `DEPLOY_VARIABLES_JSON`), ne pavienį secret'ą. |
| `Warning: … is both a secret and a configuration variable; the secret is used.` | Tas pats vardas yra ir secret, ir variable: ištrink nereikalingą (`make deploy-variables` jį rodo kaip `both`). |
| `Deployment variables missing or empty (Repository settings -> Deployments -> …): …` | Įrašyk išvardytus vardus į tos aplinkos Deployment variables (`make deploy-variables ENV=… from=…`). Niekas nepakeista. Jei pranešimas baigiasi `Nothing was deployed`, vardą pridėjo deploy'inamas commit'as. |
| `Name the code tag or commit to deploy with REF (SHOP_REF)` | `app/public` – atskira kodo repozitorija: nurodyk jos tag'ą arba commit'ą `REF` kintamajame. |
| `Unknown ref … in the environment repository` / `in the code repository app/public` | Tokio tag'o ar commit'o nėra `origin` repozitorijoje (po `git fetch --tags`). |
| `No earlier completed deploy in … to roll back to` | Istorijoje nėra ankstesnio sėkmingo deploy'aus. Deploy'ink konkretų `REF`. |
| `REF, APP_REF name different refs: set only REF` | Nurodyk tik `REF`. |
| `… names … with an empty value, which hides the Deployment variable` | Ištrink tą eilutę iš `env/common.env` ar `env/<env>.env.example`: tuščia reikšmė uždengtų Deployment variable. |
| `The deploy failed after the containers were restarted on the images of …` | Automatiškai negrąžinta. Deploy'ink iš naujo su `IMAGES=yes` arba grąžink ankstesnį commit'ą su `REF=… IMAGES=yes`, kaip parašyta log'e. |
| `deploy-variables: Bitbucket answered 401` / `403` | Token'as neteisingas ar pasibaigęs / trūksta teisės: reikia **Repositories: Read**, **Pipelines: Read**, pildymui – **Pipelines: Edit variables**. |
| `deploy-variables: … is readable by others (mode …): chmod 600 …` | Token'o ar reikšmių failas turi būti tik tavo: `chmod 600 FAILAS`. |
| `… holds $ outside single quotes` | Reikšmę su `$` reikšmių faile rašyk viengubose kabutėse. |
| `Cannot tell the Bitbucket repository` | `origin` nėra `bitbucket.org`: nurodyk `DEPLOY_BITBUCKET_REPOSITORY=workspace/repo` (arba `CI_PROVIDER=github`, jei projektas GitHub'e). |
| `deploy-variables: GitHub answered 401` / `403` / `404` | Token'as neteisingas ar pasibaigęs / trūksta teisės: fine-grained token'ui reikia **Environments: Read-only**, pildymui – **Read and write** (classic – `repo`). 404 – nėra tokios repozitorijos ar aplinkos (`staging`, `production`) arba token'as jos nemato. |
| `writing environment secrets needs the GitHub CLI gh` | Secrets šifruoja `gh`: įdiek jį (`apt install gh`) arba įrašyk secrets ranka. Niekas neįrašyta. |
| `Cannot tell the GitHub repository` | `origin` nėra `github.com`: nurodyk `DEPLOY_GITHUB_REPOSITORY=savininkas/repo`. |
| `CI_PROVIDER must be bitbucket or github` | `env/common.env` – tik `CI_PROVIDER=bitbucket` arba `CI_PROVIDER=github`. |
| Trūksta image'o, `pull never` klaida | `make build` (vietiniai image'ai) arba `make pull` (išoriniai). |
| Portas užimtas | `LOCALHOST_PORT=0` ir `make bootstrap` parinks laisvą. |
| DB `Access denied` po env pakeitimo | Esamos DB slaptažodis env faile jos viduje nepasikeičia. Suderink env su tikru slaptažodžiu. DB katalogo netrink. |
| Sveika DB, bet svetainė rodo 500 | DB tuščia arba be lentelių: `make db-load set=dev` ar `make db-import`. Žiūrėk `make logs service=php-fpm`. |
| `.ini` pakeitimas nesimato | `make restart service=php-fpm`. HTTP ribas keisk `PHP_*` env raktais. |
| Nginx `directive is duplicate` | Ta pati direktyva tame pačiame kontekste. Naudok kitą lygį (`location.d`). |

Jei neaišku, pradėk nuo `make setup-info`, `make check`, `make ps` ir konkretaus serviso log'ų.
