# 10. Kasdienis darbas, PhpStorm, Git ir atnaujinimai

[Turinys](README.md) · [Atgal](09-aplinkos.md) · [Toliau: komandos ir klaidos](11-komandos-ir-klaidos.md)

## Įprasta diena

```bash
cd ~/Projects/demo/app
make up
make ps
```

`make up` paleidžia servisus su jau sukurtais image'ais ir laukia jų sveikatos patikrų. `make ps` parodo, kas veikia. Dirbk su kodu `public/`: pakeitus PHP failą užtenka atnaujinti naršyklę.

```bash
make logs service=php-fpm follow=1
make restart service=php-fpm
make down
```

`logs` rodo paskutines 100 eilučių (`tail=`), `follow=1` seka naujas. `restart` perkuria servisą su dabartinėmis env reikšmėmis be image'o perkūrimo. `down` pašalina konteinerius ir tinklą, o DB ir kiti duomenys lieka `data/`.

## PhpStorm

```bash
make ide-init
```

`make bootstrap` (taigi ir `make setup`) tai padaro automatiškai. Atidaryk `~/Projects/demo/app`, ne vien `public`. PhpStorm turi būti įjungti Docker, PHP ir Shell Script įskiepiai.

Kas paruošiama:

- projekto pavadinimas, PHP kalbos lygis, Git kelių atitikmenys;
- PHP interpretatorius `demo-local PHP`, kuris vykdo PHP **jau veikiančiame** `php-fpm` konteineryje (prieš naudojant paleisk `make up`);
- **atskiras `demo-test PHP` interpretatorius** testams: jis pasiekia tik testinę DB;
- Run konfigūracijos:

| PhpStorm veiksmas | Kas vykdoma |
| --- | --- |
| Setup: Prepare IDE | `make ide-init` |
| Setup: Start / Stop | `make up` / `make down` |
| Setup: Check | `make doctor` |
| Setup: PHP shell | `make shell` |
| Setup: Export schema / Check schema | `make schema-export` / `make schema-check` (kai nurodytas `SCHEMA_DIRECTORY`) |
| Setup: Prepare tests | `make test-prepare` |
| Setup: Sync test copy | `make test-sync` |
| Setup: PHPUnit (all suites) | `make phpunit` |
| Setup: PHPStan | `make phpstan` |
| Setup: E2E tests | `make e2e` |

Projekto atidarymas paleidžia tik „Setup: Prepare IDE“: Docker build ar DB importas automatiškai nevyksta.

### Testai iš PhpStorm

Kiekvienam rastam PHPUnit rinkiniui sukuriama konfigūracija `PHPUnit: <rinkinys>` su `demo-test PHP` interpretatoriumi. Prieš paleidimą ji automatiškai paleidžia „Setup: Sync test copy“, todėl testai mato naujausią kodą, bet jungiasi tik prie testinės DB. Prieš pirmą kartą paleisk „Setup: Prepare tests“. Naujas rinkinys atsiranda po `make ide-init`.

### IDE neranda Docker ryšio

Docker ryšys yra globalus PhpStorm nustatymas. IDE nustatymuose pridėk ryšį, pvz. vardu `Docker`, tada:

```bash
make ide-init IDE_DOCKER_SERVER=Docker
```

Nestandartiniam IDE konfigūracijos katalogui: `make ide-init IDE_CONFIG_DIRECTORY=/kelias/PhpStorm/config`. Numatyta ieškoma `~/.config/JetBrains/PhpStorm*/options/remote-servers.xml`.

### Kuriuos .idea failus dalintis

| Dalintis (Git) | Laikyti vietoje |
| --- | --- |
| `.name`, projekto `.iml`, `modules.xml`, `php.xml`, `vcs.xml`, `startup.xml`, `runConfigurations/setup_*.xml` | `workspace.xml`, asmeniniai UI nustatymai, DB prisijungimų slaptažodžiai |

Naujo projekto `.gitignore` ignoruoja visą `.idea`. Jei nori dalintis, pridėk siaurą išimčių sąrašą:

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

Privatus interpretatoriaus Compose failas `.generated/phpstorm-compose.<ENV>.yaml` turi šio kompiuterio kelius ir slaptažodžius, jo neversijuok. Jį atnaujina sėkmingos runner komandos arba `make ide-refresh`.

## Dvi Git repozitorijos

```text
forsena/app/.git            # Aplinkos repozitorija: env, compose, config, database, qa
forsena/app/public/.git     # Parduotuvės kodas
```

| Failai | Kur commit'inti |
| --- | --- |
| `compose/`, `env/common.env`, `env/*.env.example`, `config/`, `database/`, `qa/` | Aplinkos repozitorijoje (`app/`). |
| Moduliai, temos, parduotuvės kodas | Parduotuvės repozitorijoje (`app/public/`). |
| `env/local.env`, `env/test.env`, `env/stage.env`, `env/prod.env`, dump'ai, `.generated/`, `data/` | Niekur. |

Aplinkos `.gitignore` turi ignoruoti `/public/`, kad parduotuvės checkout'as nepatektų į aplinkos repozitoriją. DB schemos commit hook'as veikia tik aplinkos repozitorijoje.

## Bendro setup atnaujinimas

Lokaliai naudojamas naujausias setup `master`, serveriuose – fiksuotas tag'as (žr. [deploy](09-aplinkos.md)).

```bash
git -C ~/Projects/setup status --short
git -C ~/Projects/setup pull
cd ~/Projects/demo/app
make setup-info
make setup
```

`setup-info` parodo setup versiją, commit'ą ir ar yra necommit'intų pakeitimų. `make setup` perkuria tik tuos image'us, kurių šaltiniai pasikeitė. Jei pranešama, kad pasikeitė pagalbiniai scriptai, paleisk `~/Projects/setup/install-host`.

Vienas setup checkout'as galioja visiems projektams. Prieš atnaujindamas perskaityk [RELEASES.md](../RELEASES.md).

### Kai projektui reikia kitos setup versijos

Vienas katalogas negali būti dviejose versijose. Sukurk atskirą worktree ir projektui nurodyk `SETUP_DIRECTORY`:

```bash
git -C ~/Projects/setup worktree add --detach ~/Projects/setup-kita v2.0.0
make SETUP_DIRECTORY=/home/tomas/Projects/setup-kita check
```

Naudok absoliutų kelią ir susitarimą užrašyk projekte, kad terminalas, IDE ir hook'ai naudotų tą pačią versiją.

## Vietos atlaisvinimas

```bash
make image-prune
make image-prune apply=1
make db-backup-prune
make db-backup-prune apply=1
```

Be `apply=1` komandos tik parodo, ką pašalintų. Plačiau: [image'ai](04-compose-ir-dockerfile.md#image-fingerprintai-ir-make-image-prune), [kopijos](07-duomenu-baze.md#g-atsarginės-kopijos). Netrink DB katalogų ir visų Docker image'ų „dėl viso pikto“: daugumai klaidų to nereikia.
