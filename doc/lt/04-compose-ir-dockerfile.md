# 4. Compose ir Dockerfile: kaip projektą išplėsti

[Turinys](README.md) · [Atgal](03-nustatymai.md) · [Toliau: servisai](05-servisai.md)

## Trys skirtingi pakeitimai

| Situacija | Keičiamas failas | Kodėl |
| --- | --- | --- |
| PHP reikia daugiau atminties | Env ir, prireikus, `config/php/*.ini` | Tai jau įdiegtos programos nustatymas. |
| PHP trūksta sistemos programos ar plėtinio | Projekto `Dockerfile` | Reikia pagaminti kitokį atvaizdą. |
| Reikia Redis konteinerio arba kito prijungiamo katalogo | Projekto `compose/*.yaml` | Keičiasi paleidžiamos aplinkos dalys. |

## Kokia tvarka sujungiami YAML failai

```text
compose/base.yaml
    ↓
compose/common.yaml, jei yra
    ↓
PROJECT_COMPOSE_FILES failai nurodyta tvarka
    ↓
compose/<ENV>.yaml, jei yra
```

Pavyzdžiui, `PROJECT_COMPOSE_FILES=compose/redis.yaml compose/qa.yaml` reiškia, kad Redis ir QA failai skaitomi prieš `local.yaml`. `compose/eksperimentas.yaml` vien dėl savo buvimo kataloge neperskaitomas.

Pagrindinio failo `include` importuoja bendrus servisų aprašus. Vieną servisą įtrauk vieną kartą; vėlesnius jo pakeitimus rašyk override failuose. Nereikia kopijuoti viso bendro PHP aprašo vien tam, kad pridėtum env reikšmę.

## Svarbiausi Compose laukeliai

| Laukas | Ką reiškia šiame projekte | Pavyzdys |
| --- | --- | --- |
| `services` | Servisų žemėlapis | Po juo rašai `php-fpm`, `database`, `redis`. |
| `image` | Paleidžiamo atvaizdo vardas/tag | `redis:7.4-alpine`. Tai ne katalogas diske. |
| `build.context` | Failai, kuriuos gali matyti Dockerfile | `${PROJECT_DOCKER_DIRECTORY}`. |
| `build.dockerfile` | Recepto kelias build kontekste arba absoliutus kelias | `Dockerfile`, `dockerfiles/worker.Dockerfile`. |
| `build.target` | Kuri Dockerfile stadija bus rezultatas | `env-local`, `env-prod`. |
| `build.args` | Reikšmės tik gaminimui | `BASE_IMAGE`, paketo versija. Tikrų slaptažodžių ten nedėk. |
| `build.additional_contexts` | Papildomi vardiniai failų šaltiniai | `profiles: ${ROOT_DIRECTORY}/profile`. |
| `environment` | Konteineryje prieinami env kintamieji | `REDIS_HOST: redis`. |
| `env_file` | Papildomas env failas konteineriui | Tai atskiras mechanizmas nuo runner skaitomų `env/*.env`. Neperduok visų slaptažodžių visiems servisams. |
| `volumes` | Katalogų/failų prijungimai | `hosto_kelias:konteinerio_kelias:ro`. |
| `ports` | Kompiuterio portas → konteinerio portas | `127.0.0.1:3309:3306`. Tarp konteinerių jo nereikia. |
| `profiles` | Kada pasirenkamas papildomas servisas | `[redis]`. Be profilio servisas laikomas pagrindiniu. |
| `command` | Ką vykdyti paleidžiant | `[redis-server, /kelias/redis.conf]`. |
| `entrypoint` | Programa, kuri gauna `command` | Keitimas gali apeiti bendrą PS/startup paruošimą. |
| `depends_on` | Nuo ko priklauso startas | PHP sveikata prieš Apache; `service_healthy` laukia healthcheck. |
| `healthcheck` | Patikra konteineryje | Testo komanda, intervalas, timeout, retries, start_period. |
| `networks` | Tinklai, kuriuose servisas pasiekiamas | `[network_app]`; alias – papildomas DNS vardas tame tinkle. |
| `user` | Proceso UID:GID | `${HOST_UID}:${HOST_GID}`; nulemia rašomų failų savininką. |
| `working_dir` | Komandos darbinis katalogas | `/var/www/html`. |
| `restart` | Automatinio pakartotinio starto politika | `unless-stopped` servisui; `"no"` vienkartiniam įrankiui. |
| `mem_limit`, `cpus` | Konteinerio resursų ribos | `mem_limit: 2g`, `cpus: 2`. Tai ne PHP vienos užklausos limitas. |
| `container_name` | Fiksuotas konteinerio vardas | Jei nustatai testui, privalomas testinio projekto prefiksas. Dažnai pakanka Compose sugeneruoto. |
| `logging` | Docker logų saugojimo nustatymai | `max-size` ir `max-file` gali riboti sukauptų logų dydį. |
| `secrets`, `configs` | Aiškiai servisui prijungiami failai | Aplikacija vis tiek turi mokėti juos skaityti; dabartinis PS updater laukia env prisijungimų. |

Tai dažniausiai reikalingi plėtimo laukeliai. Pilnas, pagal Docker versiją kintantis laukų sąrašas yra [oficialiame Compose services žinyne](https://docs.docker.com/reference/compose-file/services/). Šio setup runner papildomai riboja testinės aplinkos prijungimus.

## Pakeisti vieną esamo serviso reikšmę

Situacija: tik vietoje reikia savos integracijos adreso.

`compose/local.yaml`:

```yaml
services:
  php-fpm:
    environment:
      INTEGRATION_URL: ${INTEGRATION_URL:?Užpildyk vietinį env}
```

Likusi PHP konfigūracija paveldima. Patikrink su `make check`, pritaikyk su `make up`.

## Prijungti papildomą projekto katalogą

Situacija: PHP turi tik skaityti `tiekejai` failus.

`compose/common.yaml` papildymas:

```yaml
services:
  php-fpm:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/tiekejai:/opt/tiekejai:ro
```

Kairėje yra kompiuterio kelias, dešinėje – PHP matomas kelias. `ro` reiškia „tik skaityti“. Prieš paleisdamas sukurk realų katalogą. `make init` nekuria visų tavo sugalvotų kelių.

Jei PHP turi ten rašyti, naudok atskirą `${PROJECT_DATA_DIRECTORY}/tiekejai` katalogą ir nepridėk `:ro`. Testinėje aplinkoje rašymo vieta turi likti `.generated/test` viduje. Vien tai, kad Forsenoje yra `tiekejai`, nereiškia, kad jis jau prijungtas.

## Pakeisti sąrašą, o ne pridėti dar vieną elementą

Situacija: DB portą nori atverti tik savo kompiuteriui.

`compose/local.yaml`:

```yaml
services:
  database:
    ports: !override
      - "127.0.0.1:3309:3306"
```

Tada DB klientas kompiuteryje jungiasi prie `127.0.0.1:3309`, o PHP vis tiek naudoja `database:3306`.

`environment` reikšmės su tuo pačiu raktu pakeičiamos. `volumes` atpažįstami pagal konteinerio paskirties kelią. `ports` sąrašas gali būti papildytas, todėl pilnam pakeitimui naudok `!override`. `command`, `entrypoint` ir healthcheck komanda pakeičiamos, o ne sujungiamos. `!reset []` išvalo sąrašą. Šios taisyklės ir `!override` reikalavimas Compose 2.24.4+ aprašyti [Docker merge dokumentacijoje](https://docs.docker.com/reference/compose-file/merge/).

## Projekto Dockerfile: kai reikia papildomos programos

Situacija: aplikacija naudoja `envsubst`, todėl reikia `gettext-base`. Bendro setup nekeiti.

Jei projekto `Dockerfile` nėra, paimk [grouped Dockerfile](../../templates/grouped/Dockerfile) ir jo `php-fpm.build` prijungimą iš [pirmo paleidimo skyriaus](02-pradzia.md). Išsaugok `profiles` vardinį kontekstą – iš jo imami bendri aplikacijos profilio failai.

Projekto `Dockerfile` pabaigos pavyzdys, po bendros `base` stadijos ir profilio pritaikymo:

```dockerfile
FROM base AS project-common
RUN apt-get update \
    && apt-get install -y --no-install-recommends gettext-base \
    && rm -rf /var/lib/apt/lists/*

FROM project-common AS env-local
FROM project-common AS env-prod
FROM project-common AS env-stage
```

Esamas tris `FROM base AS env-*` eilutes pakeisk šiomis, nepalik dviejų stadijų tuo pačiu vardu. `ENV=test` naudoja `env-local`, todėl atskiros `env-test` nereikia.

```bash
make build
make up
make shell
# Dabar jau konteineryje:
command -v envsubst
exit
```

Tai Debian pagrindu veikiančio PHP atvaizdo pavyzdys. Alpine atvaizdui reikėtų `apk`, o ne `apt-get`.

### Pridėti PHP plėtinį

Pavyzdžiui, jei aplikacija reikalauja `pcntl` ir pasirinktas oficialaus PHP pagrindo receptas jį palaiko, į bendrą projekto stadiją pridėk:

```dockerfile
RUN docker-php-ext-install pcntl
```

Po build patikrink `php --ri pcntl` per `make shell`. Redis konteinerio pridėjimas savaime neįdiegia PHP Redis plėtinio; aplikacijai atskirai reikia jos naudojamo kliento bibliotekos arba plėtinio.

### Atskirti vietinį ir prod receptą

Tą patį `project-common` galima išplėsti tik vietinei aplinkai:

```dockerfile
FROM project-common AS env-local
RUN apt-get update \
    && apt-get install -y --no-install-recommends less \
    && rm -rf /var/lib/apt/lists/*

FROM project-common AS env-prod
FROM project-common AS env-stage
```

Jei labiau tinka atskiri failai, laikyk juos `dockerfiles/local.Dockerfile` ir `dockerfiles/prod.Dockerfile`, o atitinkamame Compose faile pakeisk `build.dockerfile`. Juose turi egzistuoti paveldėtas `env-local` / `env-prod` target arba aiškiai pakeisk ir `build.target`.

## .dockerignore: ką leidžiama įdėti į atvaizdą

Grouped šablonas leidžia build kontekstui tik `Dockerfile`. Todėl `COPY dockerfiles/...` neveiks, kol neleisi to kelio.

Pavyzdys, kai reikalingas `dockerfiles/`:

```dockerignore
**
!Dockerfile
!dockerfiles/
!dockerfiles/**
```

Pridėk tik reikalingus failus. `env/`, `.generated/`, dump ir `public/` nereikia siųsti į kontekstą vien dėl vieno papildomo sistemos paketo.

Atvaizdai saugomi Docker saugykloje, ne projekto `images/` kataloge. Projekte laikomi jų **receptai ir į juos kopijuojami failai**. `images/` su paveikslėliais priklauso aplikacijai, jei ji tokį katalogą naudoja.

## Pamatyti tikrą galutinį rezultatą

```bash
make check
make config
```

Antroji komanda išspausdina sugeneruoto `.generated/compose.local.yaml` kelią. Atidaryk jį lokaliai: ten matysi galutinius `image`, `volumes`, `ports`, `environment`. Jame gali būti slaptažodžių, todėl jo necommitink ir nenaudok kaip nuolat taisomo originalo.

Pasirenkami profiliai tame faile įtraukti tam, kad galėtum juos apžiūrėti. Tai nereiškia, kad visi jie jau paleisti. Kas veikia, parodo `make ps`.
