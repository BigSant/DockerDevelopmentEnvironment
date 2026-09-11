# 10. Kasdienis darbas, PhpStorm, Git ir atnaujinimai

[Turinys](README.md) · [Atgal](09-aplinkos.md) · [Toliau: komandos ir klaidos](11-komandos-ir-klaidos.md)

## Įprastas rytas

```bash
cd ~/Projects/forsena/app
make up
make ps
```

Atidaryk projekto adresą. Dirbk su kodu `public/`. Jei pasikeitė tik PHP kodas, paprastai nereikia visko perstatyti. Darbo pabaigoje `make down` pašalina konteinerius ir tinklą, bet išsaugo bind mount duomenis.

Jei reikia tik sustabdyti procesus, išlaikant konteinerius, naudok tiesioginį Compose `stop`, aprašytą [komandų skyriuje](11-komandos-ir-klaidos.md). Neieškok `make stop` – tokio bendro target šioje versijoje nėra.

## PhpStorm pirmą kartą

```bash
make bootstrap
# Arba tik IDE daliai:
make ide-init
```

Atidaryk `~/Projects/forsena/app`, ne vien `public`, jei nori matyti ir bendras projekto paleidimo konfigūracijas. Patvirtink projekto pasitikėjimą. PhpStorm turi būti įjungtas Docker, PHP ir Shell Script palaikymas.

Generatorius paruošia projekto vardą, PHP kalbos lygį, Docker Compose interpretatorių, Git kelių atitikmenis ir Run veiksmus:

| PhpStorm veiksmas | Kas vykdoma |
| --- | --- |
| Setup: Prepare IDE | `make ENV=local ide-init` |
| Setup: Start | `make ENV=local up` |
| Setup: Stop | `make ENV=local down` |
| Setup: Check | `make ENV=local doctor` |
| Setup: PHP shell | `make ENV=local shell` |
| Setup: Export schema | `make ENV=local schema-export`, jei nurodytas schema kelias |
| Setup: Check schema | `make ENV=local schema-check`, jei nurodytas schema kelias |

Projekto atidarymo užduotis paruošia IDE, bet nepradeda Docker build ir neimportuoja DB. PHP interpretatorius vykdo PHP **jau veikiančiame** `php-fpm` konteineryje, todėl prieš jį naudodamas paleisk aplinką.

### IDE neranda Docker ryšio

Docker ryšio aprašas yra globalus PhpStorm nustatymas, kurio generatorius nekuria. IDE nustatymuose pridėk ryšį, pvz. vardu `Docker`, tada:

```bash
make ide-init IDE_DOCKER_SERVER=Docker
```

Nestandartiniam Linux IDE config katalogui:

```bash
make ide-init IDE_CONFIG_DIRECTORY=/tikras/kelias/PhpStorm/config
```

Numatytoji paieška naudoja `$XDG_CONFIG_HOME/JetBrains/PhpStorm*/options/remote-servers.xml`, paprastai `~/.config/JetBrains/...`. Savą Make parinktį, kurios reikia ir automatinio starto metu, išsaugok savo vietinėje Make konfigūracijoje; vienkartinis terminalo argumentas neperrašo visos komandos aplinkos visiems būsimiems startams.

### Kuriuos .idea failus dalintis

| Dalintis, jei komanda naudoja šį setup | Laikyti vietoje |
| --- | --- |
| `.name`, projekto `.iml`, `modules.xml`, `php.xml`, `vcs.xml`, `startup.xml`, valdomi `runConfigurations/setup_*.xml` | `workspace.xml`, shelf, asmeniniai UI nustatymai, DB prisijungimų paslaptys |

Forsenos `.gitignore` jau turi atrinktų IDE failų išimtis. Naujo projekto grouped `.gitignore` iš pradžių ignoruoja visą `.idea`; generatorius jo nepakeičia. Jei nori dalintis nustatymais, pridėk siaurą šių failų whitelist, o ne visą `.idea`.

Pavyzdinis `.gitignore` papildymas projektui `demo`:

```gitignore
!/.idea/
/.idea/*
!/.idea/.name
!/.idea/demo.iml
!/.idea/modules.xml
!/.idea/php.xml
!/.idea/vcs.xml
!/.idea/startup.xml
!/.idea/runConfigurations/
/.idea/runConfigurations/*
!/.idea/runConfigurations/setup_*.xml
```

`demo.iml` pakeisk savo projekto sugeneruotu vardu. Jei aplikacijos kodas yra atskira repozitorija, aplinkos `.gitignore` papildomai ignoruok `/public/`, `/stage/public/`, `/prod/public/`, kad neįtrauktum svetimo checkout turinio. Jei sąmoningai visą aplikaciją versijuoji vienoje bendroje repo, ši papildoma ignoravimo taisyklė netinka.

Privatus interpretatoriaus failas yra `.generated/phpstorm-compose.<ENV>.yaml`. Jame yra šio kompiuterio keliai ir išspręstos env reikšmės. Jo neversijuok. Sėkmingos runner komandos atnaujina jau paruoštą IDE kopiją; rankiniu būdu tai daro `make ide-refresh`.

Vienu metu IDE turi vieną parinktą numatytąją aplinką. `make ide-init ENV=test` gali ją pakeisti į testinę. `test-init` pats tavo vietinio interpretatoriaus į testinį nepersirenka.

## Dvi Git repozitorijos Forsenoje

```text
forsena/app/.git            # Aplinkos originalai ir nustatymai
forsena/app/public/.git     # Aplikacijos kodas
```

Tai reiškia, kad `git status` būdamas `app/` ir `git -C public status` rodo skirtingus darbus.

| Failai | Kurioje repozitorijoje commitinti |
| --- | --- |
| `compose`, viešas env, `config`, `qa`, `database/schema`, šis projekto README | Aplinkos `app` repozitorijoje, jei taip pasirinkta struktūra. |
| PHP moduliai, temos, aplikacijos pakeitimai | Aplikacijos `public` repozitorijoje. |
| `env/local.env`, `env/test.env`, `env/prod.env`, DB dump, `.generated`, `data` | Necommitinti. |
| Tikrų parduotuvės raktų turintis PS config | Pagal aplikacijos paslapčių atkūrimo tvarką; nedėti tikrų paslapčių į viešą pavyzdį. |

Bitbucket saugo šias Git repozitorijas taip pat kaip bet kurį kodą. Katalogas netampa submoduliu vien dėl to, kad jo viduje yra kitas `.git`. Šiame setup bendram `setup` naudojamas atskiras checkout, o ne kiekvieno projekto submodulis.

Įprastas peržiūrėto aplinkos pakeitimo pavyzdys:

```bash
git diff
git add compose/local.yaml config/php/local/20-runtime.ini
git commit -m "Padidinti vietinio importo PHP limitus"
git push origin master
```

Naudok savo tikrą šaką; `master` čia yra dabartinės Forsenos pavyzdys. Aplikacijos pakeitimus atskirai peržiūrėk ir commitink jos repozitorijoje.

## Bendro setup atnaujinimas

Situacija: komanda susitaria naudoti naują patikrintą setup tag. Visų projektų submodulių atnaujinti nereikia, bet vieno bendro checkout pakeitimą pajus visi projektai, kurie jį naudoja.

1. Patikrink, kad bendrame setup nėra necommitintų savo pakeitimų.
2. Perskaityk naujos versijos migravimo pastabas.
3. Užsirašyk dabartinę versiją ir svarbių veikiančių projektų atvaizdus/kelius.
4. Pereik į sutartą tag.
5. Kiekviename naudojamame projekte patikrink modelį ir, jei reikia, perstatyk atvaizdus.

Pavyzdys su šiame leidime esančia versija:

```bash
git -C ~/Projects/setup status --short
git -C ~/Projects/setup fetch origin --tags
git -C ~/Projects/setup switch --detach v1.0.0
cd ~/Projects/forsena/app
make setup-info
make check
make doctor
```

Jei `doctor` praneša, kad naujų atvaizdų nėra: `make build`, tada `make up`. Nekeisk versijos tikėdamasis, kad pats `up` atsisiųs ar pagamins trūkstamus atvaizdus – jis naudoja esamus.

Setup automatiškai prideda kontrolinę sumą prie vietinių atvaizdų vardų; projekto env jungiklio nereikia. Skaičiuojami bendri Docker šaltiniai/default, profilis ir išspręsti build argumentai. Keičiantis bendram receptui ar build parinkčiai senas tag neperrašomas tuo pačiu vardu. Vietinė ir testinė aplinka gali naudoti tą patį atvaizdą.

Tai nėra visų išorinių tag nekintamumo garantija. Aplikacijos priklausomybių lock failai, bazinių atvaizdų digest ir patikrintas release build lieka atskira atkuriamo diegimo dalis. Projekto savas Dockerfile yra to projekto atsakomybė; vien setup priesaga nėra viso projekto atvaizdo kilmės įrodymas.

### Jei skirtingiems projektams tuo metu reikia skirtingų setup versijų

Vienas katalogas negali tuo pačiu metu būti dviejose Git versijose. Pereinamuoju laikotarpiu sukurk atskirą checkout/worktree ir projektui perduok `SETUP_DIRECTORY`:

```bash
git -C ~/Projects/setup worktree add --detach ~/Projects/setup-v1 v1.0.0
make SETUP_DIRECTORY=~/Projects/setup-v1 check
```

Make paprastai patikimiau perduoti absoliutų kelią: `SETUP_DIRECTORY=/home/tomas/Projects/setup-v1`. Sutartą pasirinkimą dokumentuok projekte, kad terminalas, IDE ir hook nenaudotų skirtingų versijų.

### Grįžimas į ankstesnę versiją

Grąžink sutartą ankstesnį setup checkout/tag ir projekto Compose/env pakeitimus, tada `make check`, `make doctor`, `make up`. Senus atvaizdus išsaugok iki sėkmingo priėmimo. Jei keitei DB schemą ar variklį, vien Git rollback DB neatkuria – reikalingas atskirai patikrintas backup/atkūrimas.

Negalima vienu metu paleisti dviejų DB konteinerių virš to paties duomenų katalogo.

## Ką patvirtina setup CI

Patikrintame `v1.0.0` yra šaltinių/runner testai, PHP konfigūracijos ir hook testai su PHP 5.6/7.4/8.1/8.5 bei izoliuoti DB testai su MySQL 5.7/8.4 ir MariaDB 11.4. DB testai apima schemą, fixtures, kopijas, gzip importą ir eiliškumą.

Tai ne visų tų PHP ir DB kombinacijų pilnos PrestaShop parduotuvės suderinamumo matrica. Parduotuvės moduliai, tema, mokėjimai ir pilnas PS 1.6 atvaizdas reikalauja atskirų aplikacijos testų.

Bitbucket projekte norėdamas privalomų aplikacijos patikrų, paruošk atskirą `bitbucket-pipelines.yml`: įdiek priklausomybes, paleisk pasirinktą analizatorių/testus, DB testams įkelk mažą testinę DB. Šis setup automatiškai negeneruoja kiekvienam projektui tinkamo Bitbucket Pipeline, neatkuria jame privataus dump ir nenustato branch taisyklių.

## Seno projekto perkėlimas

Generatoriaus `--from-legacy` kopijuoja seno `app/docker` `.env` ir palaikomus nustatymus į naują vietą. Jis neperkelia veikiančių konteinerių, DB ar aplikacijos ir neinterpretuoja savavališko seno Dockerfile.

```bash
cd ~/Projects/setup
python3 prepare_project.py --from-legacy --check ../senas-projektas
```

Numatytai gaunama `<projektas>/docker` vieta. Jei nori `<projektas>/app`, pasirink `--layout app` ir peržiūrėk persidengiančius esamus failus. Nevykdyk migravimo komandos jau sutvarkytai Forsenai vien tam, kad „atnaujintum viską“.

Senas `new_host.sh`, `docker/Makefile`, `docker/Makefile.local` ir `db-init.sh` lieka istoriniam workflow. Jo `make up` ir DB inicializavimas turi kitą veikimą, gali gaminti atvaizdus bei interaktyviai siūlyti ištrinti DB. Dabartinio vadovo komandos skirtos projekto Makefile, kuris įtraukia `setup/docker/project.mk`.
