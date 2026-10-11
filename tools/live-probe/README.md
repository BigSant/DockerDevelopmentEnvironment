# LIVE aplinkos zondas (setup-probe.php)

[Vadovas](../../doc/lt/README.md) · [Aplinkos ir deploy](../../doc/lt/09-aplinkos.md) · [Nustatymai](../../doc/lt/03-nustatymai.md)

`setup-probe.php` yra vienas PHP failas, kurį trumpam įkeli į LIVE serverį. Jis nustato, kokias reikšmes įrašyti į setup `env/common.env`, `env/prod.env`, `config/php`, `config/mysql` ir projekto `Dockerfile`, kad setup aplinka atitiktų LIVE. Ataskaitą galima atiduoti LLM: joje yra ir rizikos, ir instrukcija, kaip paruošti projekto aplinkos repozitoriją.

Veikia su PHP 5.6–8.5: PrestaShop 1.6–9 ir bendros PHP aplikacijos (Laravel, Symfony, composer, WordPress) dalijamame hostinge, DirectAdmin, cPanel ar VPS su Apache, LiteSpeed arba nginx + PHP-FPM.

## 0. Pašalinimas yra darbo dalis

Darbas nebaigtas, kol zondas neištrintas iš serverio ir nepatikrinta, kad jo adresas grąžina 404. Kiekvienas pamirštas diagnostikos failas (`phpinfo.php`, `i.php`…) kažkada buvo „laikinas“.

Zondą saugo trys nepriklausomi sluoksniai, bet nė vienas neatstoja pašalinimo:

1. **Token** – lyginamas `hash_equals`, numatytosios reikšmės nėra. Tuščias ar trumpesnis nei 24 simboliai token'as – zondas neveikia.
2. **IP sąrašas** – tik `REMOTE_ADDR`. `X-Forwarded-For` nenaudojamas: jį valdo klientas.
3. **Galiojimo pabaiga** – ne vėliau kaip po 7 parų. Pasibaigus net teisingas token'as duomenų negauna (tik `?action=delete`).

Kiekviena išvestis primena apie pašalinimą.

## 1. Paruošimas

Nusikopijuok abu failus į atskirą katalogą, pvz. `_setup-probe/`:

```text
_setup-probe/
├── setup-probe.php
└── .htaccess
```

`README.md` į serverį nekelk. Failo vardo nekeisk: `.htaccess` leidžia tik `setup-probe.php`.

`setup-probe.php` viršuje užpildyk tris konstantas. Kol bent viena tuščia, per HTTP zondas atsako `403 probe not configured`:

```php
define('PROBE_TOKEN', 'čia-sugeneruotas-token');
define('PROBE_ALLOWED_IPS', '203.0.113.10');
define('PROBE_EXPIRES', '2026-10-12 18:00');
```

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # token
curl -s https://ifconfig.me                                      # tavo išorinis IP
date -u -d '+1 day' '+%Y-%m-%d %H:%M'                            # galiojimas UTC
```

- `PROBE_ALLOWED_IPS` – keli adresai per kablelį. Tikrink savo IP kiekvieną kartą.
- `PROBE_EXPIRES` – UTC laikas `YYYY-MM-DD HH:MM`, ne daugiau kaip 7 paros į priekį. Pakanka kelių valandų.
- Token'ą laikyk atskirai (slaptažodžių tvarkyklėje), ne šalia failo ir ne Git.

Neprivalomos konstantos:

| Konstanta | Kada reikia |
| --- | --- |
| `PROBE_ROOT` | Aplikacija ne kataloge virš zondo. Numatyta: katalogas virš `_setup-probe/`. |
| `PROBE_APP_CONFIG` | Bendra aplikacija, kurios DB nustatymų zondas neranda pats (`.env`, `wp-config.php`, `config/db.php` su `define()`). Kelias nuo aplikacijos šaknies. |

PrestaShop DB nustatymai skaitomi iš `app/config/parameters.php` (1.7+) arba `config/settings.inc.php` (1.6), Laravel/Symfony – iš `.env` (`DB_*` arba `DATABASE_URL`).

## 2. Įkėlimas

Įkelk katalogą `_setup-probe/` į aplikacijos šaknį (PrestaShop – ten, kur `index.php` ir `config/`). Įkelk ir `.htaccess`: FTP klientai dažnai nerodo failų, prasidedančių tašku. Be jo Apache gali rodyti katalogo turinį.

nginx `.htaccess` neskaito: ten saugo tik token'as, IP ir galiojimas, todėl laikyk zondą kuo trumpiau.

## 3. Rezultatas

Užklausa turi eiti iš leisto IP. Token'ą geriau siųsti antrašte, kad jo nebūtų serverio log'uose:

```bash
TOKEN='čia-sugeneruotas-token'
URL='https://parduotuve.lt/_setup-probe/setup-probe.php'
curl -s -H "X-Probe-Token: $TOKEN" "$URL?format=md"   -o live-probe.md
curl -s -H "X-Probe-Token: $TOKEN" "$URL?format=env"  -o live-probe.env
curl -s -H "X-Probe-Token: $TOKEN" "$URL?format=json" -o live-probe.json
```

Naršyklėje veikia ir `?token=…` (numatytas formatas `md`). Kviesk tik per HTTPS: per HTTP token'as keliauja atviru tekstu (ataskaita tai pažymi kaip riziką).

| Formatas | Kas jame |
| --- | --- |
| `md` | Ataskaita žmogui ir LLM: santrauka, env blokas, drop-in failai, Dockerfile, after-import SQL juodraščiai, crontab, lentelių registro juodraštis, detalės, rizikos ir skyrius „Instructions for an LLM“. |
| `env` | Peržiūrai paruoštas blokas: `env/common.env`, `env/prod.env.example`, `env/local.env` raktų vardai, slaptų reikšmių sąrašas (tik vardai), `config/php/20-live.ini`, `config/mysql/20-live.cnf` (arba `config/mariadb/`), `app/Dockerfile`. Kiekviena eilutė su komentaru: LIVE reikšmė ir šaltinis, `GUESS` – įvertis, `HINT` – sprendimas tau. |
| `json` | Viskas mašinai: `_meta` (zondo versija, `collected_at`, `expires_at`, garantijos, pastabos), PHP, plėtiniai, web serveris, DB, PrestaShop, serveris, cron, `setup` (planas) ir `risks`. |

## 4. Per SSH (be token'o)

Jei turi SSH, zondo į web katalogą kelti nereikia. Iš komandinės eilutės token'as, IP ir galiojimas netikrinami:

```bash
php setup-probe.php --format=md --root=/home/vartotojas/domains/parduotuve.lt/public_html > live-probe.md
php setup-probe.php --format=env --root=… --config=.env
```

CLI rodo komandinės eilutės PHP: `memory_limit`, `max_execution_time`, web serveris ir HTTPS gali skirtis nuo svetainės. Zondas tai pažymi ir šias ribas ima iš šaknies `.htaccess` (`php_value`) bei `.user.ini`, jei jie yra. Tiksliausia – vieną kartą paleisti per HTTP.

## 5. Pašalinimas

```bash
curl -s -H "X-Probe-Token: $TOKEN" "$URL?action=delete"
curl -s -o /dev/null -w '%{http_code}\n' "$URL"   # turi būti 404
```

`?action=delete` veikia ir pasibaigus galiojimui (token'as ir IP vis tiek tikrinami). Jei kataloge yra tik zondo failai, ištrinamas visas katalogas; jei yra svetimų failų arba tai aplikacijos šaknis – tik `setup-probe.php`. Jei serveris neleidžia trinti, ištrink katalogą per FTP/SSH. Ištrink ir vietines kopijas su token'u.

## Ką zondas renka

- **PHP:** versija, SAPI, visos setup valdomos `ini` reikšmės (ribos, `max_input_vars`, OPcache, `realpath_cache_*`, sesijos, `serialize_precision`…), įkelti plėtiniai lyginant su setup CORE image'u tai PHP šakai, `disable_functions`, `open_basedir`.
- **Web serveris:** `SERVER_SOFTWARE`, PHP handler'is, Apache moduliai (kai `mod_php`), LiteSpeed, HTTPS, proxy/CDN užuominos, document root, `.htaccess` (dydis, `php_value`, direktyvos, kurioms setup Apache neturi modulio), `.user.ini`, hostingo panelė.
- **DB:** MySQL ar MariaDB ir tiksli versija, `sql_mode`, koduotės, laiko zona, `innodb_buffer_pool_size`, `max_allowed_packet`, `lower_case_table_names` ir kt., lentelių skaičius ir dydis, 20 didžiausių lentelių (eilutės apytiksliai iš `information_schema`), varikliai, prefiksas, view/trigger/routine/event `DEFINER`, kitos matomos schemos.
- **PrestaShop:** versija, multistore parduotuvės ir domenai, SSL, debug, cache, raktų **buvimas** (ne reikšmės), moduliai (versija, aktyvus), override failai, temos, paštas (be slaptažodžio), cron moduliai, CDN (media serveriai).
- **Serveris:** OS, laiko zona, lokalė, disko vieta, `img/`, `var/cache`, `upload/` ir kitų katalogų dydis (skenavimas ribotas laiku), RAM ir CPU iš `/proc`, Redis/Memcached plėtiniai ir ar atsako `127.0.0.1` (tik TCP prisijungimas), `crontab -l` (jei `exec` leidžiamas).
- **Bendros aplikacijos:** Laravel, Symfony, WordPress, Akeneo, `composer.json` (`php`, `ext-*`, `config.platform`), `.env` vardai (`APP_ENVIRONMENT` kandidatai).

## Ko zondas nedaro

- Nekelia PrestaShop ar kitos aplikacijos branduolio: konfigūraciją skaito pats, prie DB jungiasi per PDO arba mysqli.
- Nevykdo nė vienos rašančios užklausos: visos eina per vieną funkciją, kuri praleidžia tik vieną `SELECT` arba `SHOW`. `COUNT(*)` ant duomenų lentelių nenaudojamas.
- Neima asmens duomenų: jokių klientų, užsakymų, adresų eilučių, el. pašto adresų (crontab'e jie pakeičiami `[email]`).
- Nespausdina slaptų reikšmių: DB slaptažodžio ir vartotojo, PS raktų, SMTP slaptažodžio, token'ų. Paskutinis filtras pašalina jas iš bet kurios išvesties.

## Kaip panaudoti rezultatą

1. Perskaityk `live-probe.md` 4 skyrių (rizikos) ir `env` bloką.
2. Atiduok `live-probe.md` LLM kartu su projekto aplinkos repozitorija (`app/`). Skyrius „Instructions for an LLM“ aprašo setup taisykles ir komandas.
3. Slaptas reikšmes (PS raktus, DB slaptažodį) nusikopijuok pats iš LIVE konfigūracijos failo: lokaliai į `env/local.env`, serveriams – į Bitbucket Deployment variables ([deploy](../../doc/lt/09-aplinkos.md#deployment-variables)).
4. Patikrink: `make check`, `make setup`, `make db-import file=…` ([DB skyrius](../../doc/lt/07-duomenu-baze.md#d-live-dumpo-užkėlimas)).

`GUESS` eilutės (konteinerių limitai, `APP_UID`) yra įverčiai: jas parink pagal taikinio serverį. LIVE bendro hostingo `innodb_buffer_pool_size` aptarnauja visą serverį, todėl `DATABASE_BUFFER_POOL` siūlomas pagal DB dydį.

## Testai

```bash
python3 -m unittest discover -s tests -p test_live_probe.py
SETUP_PROBE_PHP56=1 python3 -m unittest discover -s tests -p test_live_probe.py   # dar ir php -l su php:5.6-cli
```

Testai paleidžia zondą su kompiuterio PHP ant PS 8, PS 1.6 ir Laravel fixture'ų (DB nepasiekiama), tikrina HTTP vartus per `php -S`, slaptų reikšmių nebuvimą ir zonde įrašytų setup lentelių atitiktį `compatibility.json`, `php.ini`, Dockerfile'ams ir PS lentelių registrui. Pakeitus tuos setup failus, atnaujink ir `setup-probe.php` funkciją `sp_setup_data()`.
