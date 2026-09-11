# Docker aplinkos vadovas

Šis vadovas skirtas žmogui, kuris nori paleisti projektą ir suprasti, ką keičia. Išankstinių Docker žinių nereikia. Linux aplinkos pavyzdžiai remiasi bendru `setup` ir projekto struktūra, kuri naudojama Forsenoje.

**Pradėk nuo pirmų dviejų skyrių.** Kitus atsiversk pagal užduotį. Visuose pavyzdžiuose projekto vardas `demo` yra mokomasis: savo projekte jį pakeisk tikru vardu. Pavyzdiniai slaptažodžiai nėra Forsenos prisijungimai.

| Eilė | Skyrius | Ką išmoksi |
| --- | --- | --- |
| 1 | [Struktūra ir sąvokos](01-struktura.md) | Kur yra aplikacija, Docker receptai, nustatymai, DB ir testai; kurie katalogai veikia automatiškai. |
| 2 | [Pirmas paleidimas](02-pradzia.md) | Paruošti naują minimalų projektą arba paleisti esamą Forseną. |
| 3 | [Visų setup nustatymų žinynas](03-nustatymai.md) | Kiekvieno bendro kintamojo paskirtis, reikšmė, vieta ir pakeitimo poveikis. |
| 4 | [Compose ir Dockerfile](04-compose-ir-dockerfile.md) | Pridėti servisą, keisti jo konfigūraciją, plėsti PHP atvaizdą. |
| 5 | [Servisų konfigūracija](05-servisai.md) | PHP, Xdebug, MySQL, MariaDB, Apache, Nginx, Redis, Mailpit, phpMyAdmin ir cron. |
| 6 | [Aplikacijos konfigūracija](06-aplikacija.md) | PS 1.6, naujesnis PS, papildomi parametrai ir kitų aplikacijų paleidimo scriptai. |
| 7 | [Duomenų bazė](07-duomenu-baze.md) | Dump, atsarginės kopijos, SQL po importo, fixtures, schemos ir Doctrine migracijos. |
| 8 | [PHPStan, formatavimas ir Playwright](08-kodo-kokybe.md) | Įjungti reikalingą įrankį, saugoti baseline, parašyti ir paleisti testą. |
| 9 | [Local, test, stage ir prod](09-aplinkos.md) | Atskirti kodą, DB, portus ir nustatymus; išvengti neteisingų kelių. |
| 10 | [Kasdienis darbas, IDE ir atnaujinimai](10-kasdienis-darbas.md) | PhpStorm, Git/Bitbucket, bendro setup atnaujinimas ir grįžimas į ankstesnę versiją. |
| 11 | [Komandos ir klaidų sprendimai](11-komandos-ir-klaidos.md) | Visos runner komandos, jų argumentai ir dažniausių klaidų sprendimai. |

## Kaip skaityti pavyzdžius

- **Situacija** paaiškina, kada sprendimas reikalingas.
- **Failas** nurodo, kur rašyti pateiktą tekstą. Jei failas jau turi kitų nustatymų, pridėk arba pakeisk atitinkamą dalį; nekurk antro `services:` ar `environment:` bloko tame pačiame lygyje.
- **Komandos** vykdomos terminale. Jei nepasakyta kitaip, terminalo vieta yra `~/Projects/demo/app` arba `~/Projects/forsena/app`.
- **Patikra** paaiškina, ką tikėtis pamatyti.

Teksto blokas su `yaml`, `dotenv`, `ini`, `php` ar `sql` yra failo turinys, o ne terminalo komanda. Blokas su `bash` yra terminalo komandos. Eilutės su `#` komandinėje eilutėje yra komentarai.

## Kur ieškoti konkretaus sprendimo

| Noriu… | Atsiversk |
| --- | --- |
| PHP versiją nustatyti visoms aplinkoms, o DB slaptažodį kiekvienai atskirai | [Nustatymai](03-nustatymai.md) |
| Pridėti Redis tik vienam projektui | [Servisai](05-servisai.md) |
| Padidinti įkeliamo failo limitą | [PHP ir HTTP limitai](05-servisai.md) |
| Aplikacija nėra PrestaShop | [Aplikacijos konfigūracija](06-aplikacija.md) |
| Paleisti PS 1.6 | [PS formatų skirtumai](06-aplikacija.md) |
| Po dump importo pakeisti domeną ir išjungti laiškus | [Duomenų bazė](07-duomenu-baze.md) |
| Turėti skirtingus vietinius ir testinius duomenis | [Fixtures](07-duomenu-baze.md) ir [testinė aplinka](09-aplinkos.md) |
| Neleisti commitinti pasenusios DB schemos | [Schemos patikra](07-duomenu-baze.md) |
| Išbandyti kitą PHP plėtinį | [Dockerfile](04-compose-ir-dockerfile.md) |
| Paleisti testus nepakeičiant vietinės DB | [Testinė aplinka](09-aplinkos.md) |
| Suprasti, kodėl `.ini` pakeitimas neveikia | [Servisai](05-servisai.md) ir [klaidos](11-komandos-ir-klaidos.md) |

## Vadovo ribos ir tikslumas

Aprašomas `setup` API 1 ir `v1.0.0` veikimas. Nustatymai sutikrinti su [bendrais numatytaisiais nustatymais](../../docker/.env), [runner](../../docker/project.py), [Make komandomis](../../docker/project.mk), [servisų aprašais](../../docker/docker) ir [šablonais](../../templates/grouped). Tikrinta 2026-09-11.

Čia surašyti visi šių setup failų bendri konfigūracijos kintamieji ir prijungimo vietos. PHP, Nginx ar MySQL patys turi šimtus papildomų nustatymų, priklausančių nuo jų versijos. Atitinkamuose skyriuose pateikiami darbo pavyzdžiai ir nuorodos į pilnus oficialius jų žinynus. Savavališkai sukurtas env kintamasis pradeda veikti tik tada, kai jį perskaito Compose, konfigūracijos failas arba aplikacija.

Katalogų pavadinimų skaičius nėra ribotas: gali pridėti savo `config/rabbitmq`, `qa/psalm` ar `scripts`. Vadovas atskiria **jau prijungtus katalogus**, **pasirenkamus šablonų katalogus** ir **savus katalogus, kuriems reikia prijungimo**. Tai padeda nesitikėti automatikos ten, kur jos nėra.

Forsenos keturių servisų aplinka jau pritaikyta minimaliai konfigūracijai. Bendras generatorius sukuria platesnį šabloną. Vadove aiškiai nurodyta, kuriuo variantu remiasi pavyzdys. Kopijuodamas pasirenkamus įrankius, jų neprivalai įjungti visiems projektams.

Patikrintos vietinės nuorodos, shell/PHP/JavaScript/YAML/INI pavyzdžių sintaksė, Apache ir Nginx konfigūracijų sintaksė bei aprašytų minimalaus projekto, Redis, MariaDB, QA, testinės, prod ir papildomų UI servisų Compose variantų surinkimas. Aprašyti visi 69 bendrų env/interpoliavimo šaltinių raktai ir 30 runner veiksmų. Šios dokumentacijos patikros neįdiegė naujų aplikacijų, nevykdė verslo SQL tikrose DB ir neatstoja tavo aplikacijos priėmimo testų.
