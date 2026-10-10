# 4. Compose, Dockerfile ir image'ai

[Turinys](README.md) · [Atgal](03-nustatymai.md) · [Toliau: servisai](05-servisai.md)

## Kurį failą keisti

| Situacija | Ką keisti | Kodėl |
| --- | --- | --- |
| Reikia kito PHP, MySQL, Apache ar Nginx nustatymo | Env raktas arba drop-in failas `config/<servisas>/` | Programa jau įdiegta, keičiasi tik jos nustatymas. Žr. [servisai](05-servisai.md). |
| Reikia papildomo serviso iš katalogo (Redis, cron, PMA…) | `SERVICES=` env faile | Viena eilutė, be YAML. |
| Aplikacijai (`php-fpm`, `cron`) reikia savo env kintamojo | `APP_ENVIRONMENT=` env faile | Tik vardas, be YAML. Žr. [savi aplikacijos parametrai](03-nustatymai.md#savi-aplikacijos-parametrai-app_environment). |
| Reikia kitaip prijungti katalogą, pakeisti portą ar pridėti savo servisą | `compose/common.yaml` arba `compose/<ENV>.yaml` | Keičiasi paleidžiamos aplinkos dalys. |
| PHP trūksta sistemos programos ar plėtinio | Projekto `Dockerfile` | Reikia kitokio image'o. |

## Kokia tvarka sujungiami Compose failai

```text
keturi pagrindiniai servisai iš setup  # nginx-proxy, Apache, MySQL/MariaDB, PHP-FPM ir tinklas
    ↓
APP_ENVIRONMENT vardai, jei nurodyti   # sugeneruotas .generated/app-environment-*.yaml (php-fpm, cron)
    ↓
compose/base.yaml, jei yra            # projekto papildomi servisai ar skirtumai
    ↓
SERVICES= servisų aprašai iš setup    # pvz. docker/pma/docker-compose.yml
    ↓
compose/common.yaml, jei yra
    ↓
PROJECT_COMPOSE_FILES failai nurodyta tvarka
    ↓
compose/<ENV>.yaml, jei yra
```

Vėlesnis failas papildo arba pakeičia ankstesnį. Failas `compose/eksperimentas.yaml` vien dėl buvimo kataloge neskaitomas: jį reikia įrašyti į `PROJECT_COMPOSE_FILES`.

Projekto Compose failuose rašyk **tik skirtumus**. Nekopijuok viso CORE serviso aprašo vien tam, kad pridėtum vieną reikšmę.

## Pakeisti vieną esamo serviso reikšmę

Situacija: tik lokaliai PHP reikia integracijos adreso.

`compose/local.yaml`:

```yaml
services:
  php-fpm:
    environment:
      INTEGRATION_URL: ${INTEGRATION_URL:?Užpildyk vietinį env}
```

Likusi konfigūracija paveldima. Patikrink su `make check`, pritaikyk su `make up`.

## Prijungti papildomą katalogą

Situacija: PHP turi skaityti projekto katalogą `tiekejai`.

`compose/common.yaml`:

```yaml
services:
  php-fpm:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/tiekejai:/opt/tiekejai:ro
```

Kairėje kompiuterio kelias, dešinėje kelias konteineryje, `ro` – tik skaityti. Katalogą sukurk pats prieš paleisdamas. Jei PHP ten turi rašyti, naudok `${PROJECT_DATA_DIRECTORY}/tiekejai` ir nerašyk `:ro`. `ENV=test` rašoma vieta turi likti `.generated/test` viduje.

## Pakeisti sąrašą, o ne pridėti elementą

`environment` reikšmės su tuo pačiu raktu pakeičiamos, `volumes` atpažįstami pagal konteinerio kelią, o `ports` sąrašas papildomas. Kad pakeistum visą sąrašą, naudok `!override`:

```yaml
services:
  database:
    ports: !override
      - "127.0.0.1:3309:3306"
```

`!reset []` išvalo sąrašą. Taisyklės: [Docker merge dokumentacija](https://docs.docker.com/reference/compose-file/merge/). Reikia Compose 2.24.4 ar naujesnio.

DB portas pagal nutylėjimą nepublikuojamas. Jei jį atveri, rišk tik prie `127.0.0.1`, kaip pavyzdyje.

## Savas servisas

Savas servisas, kurio nėra kataloge, aprašomas projekto Compose faile. Pavyzdys `compose/common.yaml`:

```yaml
services:
  worker:
    image: ${PROJECT_NAME}-php-fpm-${IMAGE_ENV}${SETUP_IMAGE_SUFFIX_PHP_FPM:-}
    user: "${HOST_UID}:${HOST_GID}"
    command: ["php", "/var/www/html/bin/worker.php"]
    volumes:
      - ${PROJECT_WEB_DIRECTORY}:/var/www/html
    networks: [network_app]
    depends_on:
      database:
        condition: service_healthy
    restart: unless-stopped
    environment:
      SUPPLIER_TOKEN: ${SUPPLIER_TOKEN-}
    logging:
      driver: json-file
      options:
        max-size: ${CONTAINER_LOG_MAX_SIZE:-10m}
        max-file: "${CONTAINER_LOG_MAX_FILES:-3}"
```

Šis servisas naudoja tą patį projekto PHP image'ą. Patikrink `make check`, paleisk `make up`. Runner'io DB komandos ir PHP preflight'ai savam servisui netaikomi.

Savas servisas iš setup gauna tik tai, kas parašyta jo apraše: `APP_ENVIRONMENT` ir DB prisijungimas jam neperduodami, todėl reikalingus kintamuosius išvardyk `environment`. `logging` blokas taiko tą pačią log'ų rotaciją kaip setup servisai (`CONTAINER_LOG_MAX_SIZE`, `CONTAINER_LOG_MAX_FILES`). Keliems savo servisams jį gali aprašyti vieną kartą YAML ankeriu (`x-logging: &logging` ir `logging: *logging`).

## MySQL ar MariaDB

Pagrindinius servisus prijungia pats runner'is, projektas setup kelių nežino. DB variklį renkasi `env/common.env`:

```dotenv
DATABASE_ENGINE=mariadb   # numatyta mysql
MARIADB_VERSION=11.4
```

`create-project` šią eilutę įrašo pagal atsakymą. Projekto `compose/base.yaml`, kuris dar `include`'ina setup'o pagrindinius servisus (v1 būdas), yra klaida su paaiškinimu: include ir tinklą reikia ištrinti, palikti tik tikrus skirtumus. MariaDB failai rašomi į `data/mariadb`. Esamą DB perkelk per dump ir importą į naują tuščią katalogą, ne prijungdamas senus MySQL failus.

## Projekto Dockerfile

Projektas savo `Dockerfile` gali tik **papildyti** CORE PHP image'ą. Runner'is tai patikrina: pirmas `FROM` turi būti `${SETUP_PHP_IMAGE}`.

Situacija: aplikacijai reikia `envsubst`, todėl reikia paketo `gettext-base`.

`app/Dockerfile`:

```dockerfile
ARG SETUP_PHP_IMAGE
FROM ${SETUP_PHP_IMAGE}
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext-base \
    && rm -rf /var/lib/apt/lists/*
```

Build kontekstas yra visas `app/` katalogas, kartu su `public/`. Kad Docker nesiųstų jo viso, šalia sukurk `app/.dockerignore`:

```dockerignore
**
!Dockerfile
```

Jei `Dockerfile` kopijuoja failus (`COPY`), juos atskirai leisk `.dockerignore` faile.

```bash
make build
make up
make shell
# Dabar konteineryje:
command -v envsubst
exit
```

Kiti pavyzdžiai:

- PHP plėtinys, kurio CORE neturi: `RUN docker-php-ext-install pcntl`. Patikra: `make shell`, tada `php --ri pcntl`.
- `memcached` ir `redis` plėtiniai jau sukompiliuoti CORE image'uose, bet neįjungti. Jiems `Dockerfile` nereikia, užtenka ini drop-in failo, žr. [cache servisai](05-servisai.md#redis-ir-memcached).

Projekto `Dockerfile` vienodas visoms aplinkoms. Jis įeina į projekto PHP image'o fingerprint'ą, todėl jį pakeitus perkuriamas tik tas image'as.

## Pamatyti galutinį rezultatą

```bash
make check
make config
```

`make check` patikrina visą modelį nieko nepaleisdamas. `make config` įrašo galutinį modelį į `.generated/compose.<ENV>.yaml` ir išspausdina kelią. Ten matysi galutinius `image`, `volumes`, `ports`, `environment`. Faile gali būti slaptažodžių: jo necommit'ink ir netaisyk. Kas tikrai veikia, parodo `make ps`.

## Image fingerprint'ai ir make image-prune

Kiekvieno setup kuriamo image'o vardas baigiasi jo **paties** įvesčių santrauka (fingerprint'u): Dockerfile, į jį kopijuojami failai, build argumentai, profilis ir tėvinio image'o fingerprint'as. Todėl pakeitus, pvz., Apache Dockerfile perkuriamas tik Apache image'as, o ne visi. `make up` naudoja tik jau sukurtus image'us. Jei image'o trūksta, paleisk `make build`.

Laikui bėgant kaupiasi seni image'ai. Pažiūrėk, ką galima pašalinti:

```bash
make image-prune
```

Komanda parodo setup image'us (pažymėtus `setup.*` label'iais arba atpažįstamus pagal setup vardų schemą), kurių nenaudoja joks konteineris, nesitikisi nė vienas šalia esantis v2 projektas ir nėra stage/prod release lock faile (jų reikia deploy'ui ir rollback'ui). Niekas netrinama.

```bash
make image-prune apply=1
```

Pašalina parodytus image'us. Svetimi (ne setup) image'ai niekada neliečiami.

```bash
make image-prune cache=1
make image-prune cache=1 apply=1
```

Pirma komanda parodo Docker build cache užimamą vietą, antra išvalo **visą** Docker build cache, ir kitų projektų.

**Atsargiai su v1 projektais.** Senuoju keliu veikiančių projektų runner'is nebeperskaito, todėl jų image'ai be veikiančio konteinerio atrodo nenaudojami. Prieš `apply=1` peržiūrėk sąrašą.
