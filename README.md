# setup – bendra Docker aplinka PHP projektams

Vienas bendras checkout'as (`~/Projects/setup`) paruošia ir paleidžia atskirą Docker aplinką kiekvienam PHP projektui (PrestaShop 1.6–9, Akeneo ar bendras PHP): nginx-proxy, Apache, PHP-FPM ir MySQL/MariaDB, o pagal poreikį phpMyAdmin, Mailpit, cron, Redis, Memcached ir kitus servisus. Tas pats CORE veikia lokaliai, testinėje, STAGE ir LIVE aplinkose, skiriasi tik env reikšmės.

## Greita pradžia

Kompiuteris paruošiamas vieną kartą (sudo slaptažodis įvedamas vieną kartą):

```bash
git clone git@github.com:BigSant/DockerDevelopmentEnvironment.git ~/Projects/setup
~/Projects/setup/install-host
```

Esamas projektas paleidžiamas iš švaraus clone'o viena komanda, be sudo:

```bash
cd ~/Projects/demo/app
make setup
make db-load set=dev
```

`make setup` paruošia env, vietinį domeną, TLS, image'us ir paleidžia servisus. DB jis neliečia: duomenis užkeli sąmoningai (`make db-load` arba `make db-import file=…`).

Naujo projekto failai sukuriami su klausimais arba parametrais:

```bash
cd ~/Projects/setup
./create-project
./create-project demo --profile prestashop --ps-version 9.0.2 --php-version 8.4 \
  --database mysql --database-version 8.4 --services "pma mailpit" --ci bitbucket
```

## Dokumentacija

- **[Vadovas lietuviškai](doc/lt/README.md)** – nuo pirmo paleidimo iki servisų, DB kaip kodo, testų, release ir deploy'aus.
- **[ARCHITECTURE.md](doc/ARCHITECTURE.md)** – trumpas angliškas architektūros aprašas agentams ir prižiūrėtojams.
- **[RELEASES.md](doc/RELEASES.md)** – kas pasikeitė kiekvienoje versijoje ir kaip perkelti v1 projektą.
- **[LIVE zondas](tools/live-probe/README.md)** – vienas PHP failas, kuris LIVE serveryje nustato setup env reikšmes ir paruošia ataskaitą LLM.

Projekte `make help` parodo visas komandas.

## Priežiūros taisyklė

Keičiant Docker logiką (Dockerfile, Compose, runner'į, profilius, drop-in taškus) tame pačiame pakeitime atnaujink `doc/lt/` ir `doc/ARCHITECTURE.md`. Naujas nustatymas įrašomas į `docker/project-settings.yaml`.

Testai: `python3 -m unittest discover -s tests`.
