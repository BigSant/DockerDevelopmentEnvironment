# 9. Local, test, stage ir prod

[Turinys](README.md) · [Atgal](08-kodo-kokybe.md) · [Toliau: kasdienis darbas](10-kasdienis-darbas.md)

## Kiekviena aplinka turi savo vardą ir vietą

| Pasirinkimas | Privatus env | Numatytoji aplikacija | Duomenų bazinis katalogas |
| --- | --- | --- | --- |
| `ENV=local` arba nieko | `app/env/local.env` | `app/public` | `data` |
| `ENV=test` | `app/env/test.env` | `app/.generated/test/app` | `app/.generated/test/data` |
| `ENV=stage` | `app/env/stage.env` | `app/stage/public` | `data/stage` |
| `ENV=prod` | `app/env/prod.env` | `app/prod/public` | `data/prod` |

Keliai lentelėje yra nuo `<projektas>/`. Tai runner baziniai keliai. Galutinį konkretaus serviso mount dar gali pakeisti projekto Compose.

Konteinerių ir tinklo varduose yra aplinka: `demo-local-…`, `demo-test-…`. Testas naudoja vietines build stadijas ir atvaizdus, jei bendri build nustatymai sutampa. Skirtinga PHP ar kitų build argumentų reikšmė gali pareikalauti atskiro atvaizdo.

## A. Paruošti nepriklausomą testinę kopiją

Situacija: nori importuoti testinį dump nepakeisdamas savo vietinės parduotuvės.

```bash
make test-init
```

Komanda paruošia:

- `env/test.env` su atskirais prisijungimais, domeno/portų reikšmėmis;
- `.generated/test/app` kaip nepriklausomą esamo vietinio checkout kopiją;
- `.generated/test/data` DB, TLS ir rezultatams;
- žymą `.generated/test/initialized`, kad atskirtų savo valdytą kopiją nuo svetimo katalogo.

Nekopijuojami `.git`, `node_modules`, tiesioginiai aplikacijos `var/cache`, `var/logs`, `cache` katalogai; nesaugios išorinės symlink praleidžiamos. Kiti failai kopijuojami, todėl dideliam checkout reikia atitinkamos vietos. Testinė kopija nėra atskiras Git checkout su savo istorija.

Savavališki API prisijungimai iš `local.env` į `test.env` neperkeliami. Tačiau kopijuojami aplikacijos failai gali turėti senus integracijų nustatymus – pasirūpink testiniais adresais ir išjungtais išoriniais veiksmais aplikacijos konfigūracijoje. Ši funkcija izoliuoja Docker būseną, o ne automatiškai supranta visas verslo integracijas.

## B. Pirmą kartą užkrauti testinę PS parduotuvę

Prieš `up`, paruošk testui skirtus po-importo SQL ir trūkstamus pasirenkamų servisų config. Forsenoje `database/after-import/test/010-after-import.sql` jau yra: pakeičia domeną, URL ir išjungia laiškus.

```bash
make db-prepare ENV=test
make db-import-plan ENV=test file=/kelias/testams.sql.gz db-fixtures=test
make db-import ENV=test file=/kelias/testams.sql.gz db-fixtures=test
```

Testinę aplinką tikrink **po** jos HTTP konfigūracijos pritaikymo žemiau. Tuščia DB gali būti sveika kaip MySQL servisas, bet neturėti parduotuvės lentelių, todėl PS HTTP dar neveiks.

### Testinės aplinkos HTTP ir ankstesnių šablonų suderinamumas

`test-init` paruošia `<projektas>.test.local` domeną ir jo Nginx maršrutą be porto naršyklėje. Dabartinis setup su `ENV=test` parenka `HTTP_FORCE_HTTPS=off`, todėl papildomos Apache išimties nebereikia. Senesnėje v1.0.0 versijoje ne-local aplinkos būdavo automatiškai peradresuojamos į HTTPS.

Tik jei vis dar naudoji seną Apache šabloną, ankstesnis suderinamumo variantas `compose/test.yaml` yra:

```yaml
services:
  webserver:
    command:
      - /usr/local/bin/init.sh
      - ${DOMAIN}
      - ${DOCUMENT_ROOT}
      - local
```

Tai nekeičia runner `ENV=test`, jo DB, failų, tinklo ar portų. Pakeičiamas tik Apache elgesys, kad nebūtų automatinio prod tipo HTTP peradresavimo. PS DB domenas sutvarkomas testiniu after-import SQL.

Dabartinis Nginx inicializatorius optional virtualius hostus parenka pagal tikrai įjungtus servisus. Kai Mailpit/phpMyAdmin išjungti, papildomo minimalaus starto bloko nebereikia.

```bash
make check ENV=test
make up ENV=test
make doctor ENV=test
```

Patikra: naršyklėje atidaryk testinį domeną. Jei parduotuvė nukreipia į local/prod arba kitą portą, pirmiausia sutvarkyk jos DB/config.

Jei tikslas būtent HTTPS testai, vietoje HTTP pavyzdžio paruošk testinio domeno sertifikatą, tinkamą HTTPS portą, PS SSL ir URL nustatymus bei proxy antraštes. Po to naršyklėje arba Playwright teste tikrink tikslų testinį HTTPS adresą.

## C. Testo apsaugos ir pasirenkami QA įrankiai

Runner tikrina galutinį Compose modelį:

- writable bind mount turi likti `.generated/test` viduje;
- testiniai `container_name`, tinklai ir named volumes turi turėti testinio projekto prefiksą;
- external tinklai/volumes ir bendri konteinerių volumes neleidžiami;
- PS PHP turi jungtis į savo `database` servisą ir jo DB vardą;
- neleidžiami aplikacijos ar duomenų kelio override, rodantys už testinės vietos.

Pavyzdžiui, štai toks **klaidingas testinis prijungimas** bus atmestas:

```yaml
services:
  database:
    volumes:
      - ${PROJECT_DIRECTORY}/data/mysql:/var/lib/mysql
```

Jis vestų į vietinius duomenis. Teisingas testinis variantas remiasi `${PROJECT_DATA_DIRECTORY}/mysql`, nes runner jau parinko testinį bazinį kelią.

Bendrą QA config ar jau parašytas migracijas galima skaityti read-only. Jei šie servisai įtraukti projekte, `compose/test.yaml` papildymai:

```yaml
services:
  php-phpstan:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/qa/baselines:/tmp/phpstan/baselines:ro
  php-doctrine-migrations:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/database/doctrine:/tmp/doctrine-migrations/config:ro
```

Nedėk neegzistuojančio serviso bloko į minimalų projektą. Kiti QA prijungimai iš `compose/qa.yaml` jau nurodo read-only taisykles/testus, o cache/ataskaitos eina į `${PROJECT_DATA_DIRECTORY}`.

`make phpstan-baseline ENV=test` negalės rašyti bendro read-only baseline; generuok vietinėje aplinkoje arba sąmoningai pasirink testinį atskirą rezultatų failą. `make doctrine cmd=generate ENV=test` analogiškai reikalautų writable testinės migracijų kopijos.

## D. Atnaujinti testinį kodą

```bash
make test-init refresh=1
```

Komanda sustabdo testinį stack ir atnaujina jo kodo kopiją iš vietinio checkout. Testinė DB išsaugoma. Vietinės aplikacijos failai neištrinami.

Paprastas `make test-init` jau esamos kopijos neatnaujina. Jei taisai `public/`, testas pakeitimų nepamatys iki refresh. Refresh turi pakeisti kopiją, todėl pakeitimų tik `.generated/test/app` nelaikyk vienintele savo darbo vieta.

```bash
make up ENV=test
make e2e ENV=test
make down ENV=test
```

`down` pašalina konteinerius ir tinklą, bet ne bind mount duomenis. Čia nėra automatinės komandos, kuri išvalytų vietinę DB.

## E. Stage ir prod: ką būtina paruošti

Situacija: turi atskirą serverį arba atskirą aplikacijos kopiją ir nori naudoti tą patį failų organizavimą.

1. Paruošk `env/stage.env` ar `env/prod.env`. Generatorius turi prod pavyzdį; stage pavyzdį projektas turi pateikti pats arba aiškiai sukurti privatų failą.
2. Įdiek tinkamos versijos aplikaciją į `app/stage/public` arba `app/prod/public`, arba aiškiai nustatyk `APP_SOURCE_DIRECTORY`.
3. Paruošk atskirus DB duomenis, vartotojų teises, konfigūraciją ir sertifikatus. `make bootstrap ENV=prod` nepalaikomas.
4. Peržiūrėk pasirenkamus servisus ir `compose/prod.yaml`. Local patogumo įrankių į prod automatiškai neįjunk.
5. Įvertink bendrų dev atvaizdų nustatymus: DB privilegijos, crash-safety, bind-mounted kodas ir paslapčių perdavimas nėra automatiškai pakeičiami į gamybinę politiką.
6. Patikrink galutinį modelį, pagamink/deployink sutartus atvaizdus, paruošk DB, tada paleisk aplikaciją.

Komandų forma, kai minėti dalykai jau paruošti:

```bash
make check ENV=prod
make config ENV=prod
make build ENV=prod
make db-prepare ENV=prod
# Tik peržiūrėtas pirmos instaliacijos / migracijos veiksmas.
make up ENV=prod
make doctor ENV=prod
```

### V1.0.0 grouped šablono papildomas /prod

Runner nustato `${PROJECT_DATA_DIRECTORY}` į `<projektas>/data/prod`. Dabartinis `templates/grouped/compose/prod.yaml` to kelio nebedubliuoja. Senasis v1.0.0 šablonas pridėdavo dar `/prod`, todėl esamuose projektuose gali likti `data/prod/prod/mysql`.

Naujam projektui, kuriame pakanka runner atskyrimo, savo `compose/prod.yaml` gali pakeisti į:

```yaml
services: {}
```

Arba palik tik reikalingus prod nustatymus ir keliuose naudok `${PROJECT_DATA_DIRECTORY}/mysql`, `/ssl` ir pan., be papildomo `/prod`. Minimaliai Forsenai viso grouped prod failo kopijuoti nereikia: jame aprašyti ir jai neprijungti QA servisai.

**Esamai veikiančiai DB kelio netaisyk aklai.** Pirma patikrink tikrą mount ir duomenų vietą. Jei DB jau gyvena `data/prod/prod/mysql`, pakeitimas į kitą kelią atvers kitą arba tuščią DB. Reikia suplanuoto perkėlimo, sustabdžius vienintelį tuos failus naudojantį serverį.

### HTTPS peradresavimas, kai TLS užbaigia Nginx

Dabartinis bendras Apache tikrina `HTTP_FORCE_HTTPS` ir `X-Forwarded-Proto`, todėl Nginx jau priimto HTTPS iš naujo neperadresuoja. Senasis v1.0.0 šablonas galėjo sudaryti ciklą. Žemiau pateiktas atskiro projekto override vis dar tinka senam šablonui ar savarankiškai valdomam maršrutizavimui.

Situacija: vieši portai yra standartiniai 80/443, TLS užbaigia šio setup Nginx, norime išvengti ciklo. Paruošk savo `config/apache/sites.conf.template`:

```apache
<VirtualHost *:80>
    ServerName DOMAIN_VAR
    DocumentRoot /var/www/html

    RewriteEngine On
    RewriteCond %{HTTP:X-Forwarded-Proto} !^https$ [NC]
    RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=302,L]

    SetEnvIf X-Forwarded-Proto "https" HTTPS=on
    <Directory /var/www/html>
        DirectoryIndex index.php index.html
        AllowOverride All
        Options -Indexes
        Require all granted
        <FilesMatch \.php$>
            SetHandler "proxy:fcgi://php-fpm:9000/"
        </FilesMatch>
    </Directory>
</VirtualHost>
```

Failo galūnė `.template` parinkta tam, kad jo automatiškai neįtrauktų `config/apache/*.conf` paieška. `DOMAIN_VAR` ir document root pakeis esamas inicializatorius. Aplikacijos `.htaccess` ir kiti įjungti config vis tiek turi atitikti pasirinktą maršrutizavimą.

`compose/prod.yaml` papildymas:

```yaml
services:
  webserver:
    volumes:
      - ${PROJECT_DOCKER_DIRECTORY}/config/apache/sites.conf.template:/opt/setup-apache-sites.conf:ro
    command:
      - /bin/sh
      - -ec
      - 'cp /opt/setup-apache-sites.conf /usr/local/apache2/conf/sites.conf; exec /usr/local/bin/init.sh "$$@"'
      - project-apache
      - ${DOMAIN}
      - ${DOCUMENT_ROOT}
      - ${ENV}
```

Originalas prijungtas tik skaitymui, o inicializatorius keičia konteineryje padarytą jo kopiją. Patikrink `httpd -t`, HTTP → HTTPS peradresavimą ir galutinį HTTPS atsakymą. Kol tikrini, 302 neįrašo nuolatinio peradresavimo naršyklei; patikrinęs gali sąmoningai pasirinkti 301. Kitam TLS proxy ar nestandartiniams išoriniams portams reikia atitinkamai parinkti patikimas antraštes ir tikslinį HTTPS URL.

## F. Skirtingi aplikacijos ar duomenų keliai

Jei aplikacijos release jau laikomas `app/releases/current`, aplinkos env gali nurodyti:

```dotenv
APP_SOURCE_DIRECTORY=app/releases/current
DATA_DIRECTORY=data/prod
```

Kelias turi realiai likti projekto viduje; symlink, vedantis už projekto, netiks. Custom duomenų katalogus, kurių `make init` nepriskiria savo standartinėms vietoms, paruošk pats ir patikrink savininką/teises.

Tai kelio pasirinkimas, ne release įdiegimo mechanizmas. Rollback, migracijų tvarka ir srauto perjungimas priklauso tavo diegimo procedūrai.
