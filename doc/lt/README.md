# Docker aplinkos vadovas (setup v2)

Šis vadovas skirtas žmogui, kuris nori paleisti projektą ir suprasti, ką keičia. Daug Docker žinių nereikia. Pavyzdžiai skirti Linux ir remiasi bendru `setup` bei projekto struktūra `env/` + `compose/`.

**Pradėk nuo pirmų dviejų skyrių.** Kitus atsiversk pagal užduotį. Projekto vardas `demo` yra mokomasis, savo projekte jį pakeisk tikru vardu. Forsena minima tik kaip realus pavyzdys. Pavyzdiniai slaptažodžiai ir IP adresai nėra tikri.

| Eilė | Skyrius | Ką išmoksi |
| --- | --- | --- |
| 1 | [Struktūra ir sąvokos](01-struktura.md) | Kas kur gyvena, kurie katalogai skaitomi automatiškai, kas eina į Git. |
| 2 | [Pirmas paleidimas](02-pradzia.md) | `setup/install-host`, `make setup`, naujas projektas su `./create-project`. |
| 3 | [Nustatymų žinynas](03-nustatymai.md) | Visi env raktai: kur rašyti, ką daro, kokia numatytoji reikšmė. |
| 4 | [Compose, Dockerfile ir image'ai](04-compose-ir-dockerfile.md) | Compose skirtumų failai, projekto `Dockerfile`, fingerprint'ai, `make image-prune`. |
| 5 | [Servisai ir drop-in failai](05-servisai.md) | `SERVICES=` katalogas, drop-in failai kiekvienam konteineriui, `.template`, IP sąrašai, cron, DB monitorius. |
| 6 | [Aplikacija: PrestaShop ir kiti](06-aplikacija.md) | PS konfigūracijos failas iš env, `PS_VERSION`, `PS_RELEASE`, debug, kitų aplikacijų startup scriptai. |
| 7 | [Duomenų bazė kaip kodas](07-duomenu-baze.md) | Schema ir commit hook'as, lentelių registras, migracijos, `db-import`, bazinis rinkinys, kopijos. |
| 8 | [Testai ir kodo kokybė](08-kodo-kokybe.md) | `make test-prepare`, `make phpunit`, e2e, PHPStan ir PHP-CS-Fixer. |
| 9 | [Aplinkos, release ir deploy](09-aplinkos.md) | local, test, stage, prod; `make release`, `make deploy`, Bitbucket Pipelines. |
| 10 | [Kasdienis darbas, IDE ir atnaujinimai](10-kasdienis-darbas.md) | PhpStorm, dvi Git repozitorijos, setup atnaujinimas, vietos atlaisvinimas. |
| 11 | [Komandos ir klaidos](11-komandos-ir-klaidos.md) | Visos `make` komandos, jų argumentai ir dažniausių klaidų sprendimai. |
| 12 | [Cache, paštas ir resursai](12-cache-ir-hibridines-aplinkos.md) | `CACHE_MODE`, `MAIL_MODE`, `<SERVISAS>_MEMORY/_CPUS`, log'ai ir diskas. |
| 13 | [PHP ir JS/CSS įrankiai](13-php-ir-build-irankiai.md) | Versijos, Composer, npm ir PrestaShop/PHP suderinamumas. |

Trumpas angliškas architektūros aprašas agentams ir prižiūrėtojams: [ARCHITECTURE.md](../ARCHITECTURE.md). Kas pasikeitė kiekvienoje setup versijoje: [RELEASES.md](../RELEASES.md).

## Kaip skaityti pavyzdžius

- **Situacija** paaiškina, kada sprendimas reikalingas.
- **Failas** nurodo, kur rašyti tekstą. Jei faile jau yra nustatymų, pridėk arba pakeisk tik reikiamą dalį.
- **Komandos** vykdomos terminale projekto kataloge, pvz. `~/Projects/demo/app`, jei nepasakyta kitaip.
- **Patikra** paaiškina, ką turi pamatyti.

Blokas su `dotenv`, `yaml`, `ini`, `php` ar `sql` yra failo turinys. Blokas su `bash` yra terminalo komandos. Eilutė, prasidedanti `#`, komandų bloke yra komentaras.

## Kur ieškoti konkretaus sprendimo

| Noriu… | Atsiversk |
| --- | --- |
| Paruošti naują kompiuterį | [Pirmas paleidimas](02-pradzia.md#a-kompiuterio-paruošimas-vieną-kartą) |
| Paleisti projektą iš švaraus clone'o | [Pirmas paleidimas](02-pradzia.md#b-esamas-projektas-iš-švaraus-cloneo) |
| Įjungti phpMyAdmin, Mailpit, cron ar Redis | [Servisai](05-servisai.md#servisų-katalogas-services) |
| Pakeisti PHP, MySQL, Apache ar Nginx nustatymą | [Drop-in failai](05-servisai.md#drop-in-failai-papildyti-core-konfigūraciją) |
| Apriboti įrankių prieigą pagal IP | [IP sąrašai](05-servisai.md#ip-apribojimai) |
| Užkelti dev duomenis arba LIVE dump'ą | [Duomenų bazė](07-duomenu-baze.md) |
| Pakeisti DB struktūrą ir commit'inti | [Schema ir migracijos](07-duomenu-baze.md#b-schema-git-ir-commit-hookas) |
| Paleisti PHPUnit nepaliečiant savo DB | [Testai](08-kodo-kokybe.md) |
| Padidinti konteinerio atmintį | [Resursai](12-cache-ir-hibridines-aplinkos.md#resursai-kiekvienam-servisui) |
| Įjungti Xdebug profiliavimą | [Xdebug](05-servisai.md#php-ir-xdebug) |
| Išleisti kodą į STAGE ar LIVE | [Release ir deploy](09-aplinkos.md) |
| Sužinoti, kokias env reikšmes turi LIVE serveris | [LIVE zondas](../../tools/live-probe/README.md) |
| Atlaisvinti vietos nuo senų image'ų | [make image-prune](04-compose-ir-dockerfile.md#image-fingerprintai-ir-make-image-prune) |

## Vadovo ribos

Aprašoma setup 2.0.0 (failas `VERSION`, versijos įrašas [RELEASES.md](../RELEASES.md)). Nustatymai sutikrinti su [bendrais numatytaisiais nustatymais](../../docker/.env), [nustatymų sąrašu](../../docker/project-settings.yaml), [runner'iu](../../docker/project.py), [Make komandomis](../../docker/project.mk) ir [servisų aprašais](../../docker/docker).

PHP, Nginx, Apache ar MySQL patys turi šimtus nustatymų. Vadove aprašyta, **kur** juos dėti, o pilni sąrašai yra oficialiuose tų programų žinynuose. Savavališkai sukurtas env kintamasis pradeda veikti tik tada, kai jį perskaito runner'is, Compose, `.template` failas arba aplikacija.

**v1 projektai be pakeitimų neveikia.** Jei projektas dar naudoja senąjį kelią, jį reikia perkelti pagal [RELEASES.md](../RELEASES.md) (skyrius „Migrating a v1 project“).
