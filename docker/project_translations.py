"""Translation migrations: the module translations a developer makes, brought to a PrestaShop target once each.

Module translations and mail subjects are the shop's content (the back office edits them; a deploy does not write
them). What a developer translates travels as migrations in the environment repository,
translations/migrations/<14 digits>-<name>.json:

  {"legacy":   {"modules/<module>/translations/<iso>.php": {"<{module}prestashop>file_md5": "text"},
                "mails/<iso>/lang.php": {"Subject": "text"}},
   "database": {"<locale>": {"<domain>[@<theme>]": {"key": "text"}}}}

legacy are the $_MODULE files (also themes/<theme>/modules/<module>/translations/<iso>.php) and the $_LANGMAIL mail
subject files (also themes/<theme>/mails/<iso>/lang.php); the mail templates beside them are code. database are the
translation table rows. Like setup's SQL migrations, each is applied once per database, in name order, and recorded in
setup_migrations as translations:<file> (no table of its own): every translation it holds is written over what the
target has; nothing else is touched, and an applied migration is never run again, so whatever clients edit in the back
office stays. A module or language the target lacks gets nothing (noted). Nothing is deleted.

  make translations-migration name=NAME      (ENV=local) a migration of what changed in the local shop since the base
  make translations-migrate [ENV=…] [dry=1]  the pending ones: locally (a teammate's), or on a target (every PrestaShop
                                             deploy runs it after the module upgrades)
  make translations-base                     take the base again

The base is .generated/translations/base.json: every local translation (files and table rows) as it was when the
local shop last took the server's (db-pull import=1, db-import, db-load, files-pull extract=1 retake their part) or
a migration was drafted. A draft holds what differs from it — the developer's own translations, not the client's.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile

from database_client import identifier, literal

MIGRATIONS = 'translations/migrations'
BASE = '.generated/translations/base.json'
RECORD = 'translations:'
NAME = re.compile(r'\d{14}-[a-z0-9-]+\.json')
LEGACY_FILE = re.compile(r'(?:themes/[A-Za-z0-9_.-]+/)?modules/([A-Za-z0-9_-]+)/translations/([a-z]{2,5})\.php')
MAIL_FILE = re.compile(r'(?:themes/[A-Za-z0-9_.-]+/)?mails/([a-z]{2,5})/lang\.php')
LOCALE = re.compile(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?')
DOMAIN = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*(?:@[A-Za-z0-9_.-]+)?')
MODULE, MAIL = '_MODULE', '_LANGMAIL'
LEGACY_GLOBS = ('modules/*/translations/*.php', 'themes/*/modules/*/translations/*.php', 'mails/*/lang.php',
                'themes/*/mails/*/lang.php')
SHOWN = 60


def php_unquote(text):
    """The value of a PHP single-quoted string's body: only \\\\ and \\' are escapes."""
    return re.sub(r"\\([\\'])", r'\1', text)


def php_quote(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


def variable(path):
    """The array a legacy file holds: $_MODULE (module translations), $_LANGMAIL (mail subjects); None: no such file."""
    return MODULE if LEGACY_FILE.fullmatch(path) else MAIL if MAIL_FILE.fullmatch(path) else None


def parse_legacy(text, name=MODULE):
    """{key: text} of a legacy translation file, or None when it holds anything else than its entries: one entry as
    PrestaShop writes it is $_MODULE['key'] = 'text'; (PHP single-quoted strings; $_LANGMAIL in a mail subject file)."""
    entry = re.compile(rf"\${name}\[\s*'((?:[^'\\]|\\.)*)'\s*\]\s*=\s*'((?:[^'\\]|\\.)*)'\s*;", re.S)
    entries = {}
    for match in entry.finditer(text):
        entries[php_unquote(match[1])] = php_unquote(match[2])
    rest = entry.sub('', text)
    rest = re.sub(rf'<\?php|\?>|global\s+\${name}\s*;|\${name}\s*=\s*array\(\s*\)\s*;|\${name}\s*=\s*\[\s*\]\s*;', '', rest)
    rest = re.sub(r'//[^\n]*|#[^\n]*|/\*.*?\*/', '', rest, flags=re.S)
    return entries if not rest.strip() and text.count(f'${name}[') == len(entry.findall(text)) else None


def legacy_text(entries, name=MODULE):
    return (f'<?php\n\nglobal ${name};\n${name} = array();\n' +
            ''.join(f'${name}[{php_quote(key)}] = {php_quote(value)};\n' for key, value in entries.items()))


def shown(text):
    text = text.replace('\n', ' ')
    return repr(text if len(text) <= SHOWN else text[:SHOWN] + '…')


def prefix(project):
    return project.settings.get('PS_DATABASE_PREFIX') or 'ps_'


def has_table(project):
    """PrestaShop 1.7+ keeps the new system's translations in a table; 1.6 has files only."""
    return not str(project.settings.get('PS_VERSION', '')).startswith('1.6')


def migrations_directory(project):
    return Path(project.directory) / MIGRATIONS


def empty():
    return {'legacy': {}, 'database': {}}


def checked(data, where):
    """A migration's (or the base's) content, refused unless it is as make translations-migration writes it."""
    if not isinstance(data, dict) or set(data) - {'legacy', 'database'}:
        raise ValueError(f'{where}: expected {{"legacy": …, "database": …}}')
    legacy, database = data.get('legacy') or {}, data.get('database') or {}

    def texts(value):
        return isinstance(value, dict) and all(isinstance(key, str) and isinstance(text, str) for key, text in value.items())
    if not isinstance(legacy, dict) or not all(variable(path) and texts(entries) for path, entries in legacy.items()):
        raise ValueError(f'{where}: "legacy" maps modules/<module>/translations/<iso>.php or mails/<iso>/lang.php '
                         'to {key: text}')
    if not isinstance(database, dict) or not all(
            LOCALE.fullmatch(locale) and isinstance(domains, dict) and
            all(DOMAIN.fullmatch(domain) and texts(entries) for domain, entries in domains.items())
            for locale, domains in database.items()):
        raise ValueError(f'{where}: "database" maps a locale to {{domain[@theme]: {{key: text}}}}')
    return {'legacy': legacy, 'database': database}


def count(data):
    return sum(len(entries) for entries in data['legacy'].values()) + \
        sum(len(entries) for domains in data['database'].values() for entries in domains.values())


# --- The local shop's translations, the base and drafts ---------------------------------------------
def local_translations(project, files=True, database=True):
    """Every translation of the local shop: its legacy files and translation table rows."""
    found = empty()
    code = Path(project.web_directory) if files else None
    for path in sorted(path for pattern in LEGACY_GLOBS for path in code.glob(pattern)) if files else []:
        relative = path.relative_to(code).as_posix()
        name = variable(relative)
        if not name:
            continue
        entries = parse_legacy(path.read_text(errors='surrogateescape'), name)
        if entries is None:
            print(f'Warning: {relative} holds more than ${name} lines; left out of translations')
        elif entries:
            found['legacy'][relative] = entries
    if database and has_table(project):
        table, lang = identifier(prefix(project) + 'translation'), identifier(prefix(project) + 'lang')
        for locale, domain, theme, key, text in project.database.query(
                f"SELECT l.locale, t.domain, COALESCE(t.theme, ''), t.`key`, t.translation FROM {table} t "
                f'JOIN {lang} l ON l.id_lang = t.id_lang;\n'):
            found['database'].setdefault(locale, {}).setdefault(domain + (f'@{theme}' if theme else ''), {})[key] = text
    return found


def base_path(project):
    return Path(project.directory) / BASE


def read_base(project):
    path = base_path(project)
    if not path.is_file():
        return None
    return checked(json.loads(path.read_text()), path)


def write_base(project, data):
    path = base_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True) + '\n')


def take_base(project, files=True, database=True):
    """Take the base again: both parts, or the one the local shop just took from the server."""
    from project_prestashop import is_prestashop
    if project.environment != 'local' or not is_prestashop(project):
        return None
    current = local_translations(project, files, database)
    data = read_base(project) or empty()
    if files:
        data['legacy'] = current['legacy']
    if database:
        data['database'] = current['database']
    write_base(project, data)
    part = 'files and table rows' if files and database else 'files' if files else 'table rows'
    print(f'Translations base taken ({part}): {count(data)} translations; '
          'make translations-migration name=… drafts what changes from here.')
    return data


def database_loaded(project):
    """After a load replaced the local database (import, data set): its translation rows are the new base."""
    from project_prestashop import is_prestashop
    if project.environment == 'local' and is_prestashop(project) and has_table(project):
        try:
            take_base(project, files=False)
        except ValueError as error:
            print(f'Warning: the translations base was not taken ({error}); run make translations-base')


def difference(current, base):
    """What current has new or different from base."""
    changed = empty()
    for path, entries in current['legacy'].items():
        before = base['legacy'].get(path, {})
        found = {key: text for key, text in entries.items() if before.get(key) != text}
        if found:
            changed['legacy'][path] = found
    for locale, domains in current['database'].items():
        for domain, entries in domains.items():
            before = base['database'].get(locale, {}).get(domain, {})
            found = {key: text for key, text in entries.items() if before.get(key) != text}
            if found:
                changed['database'].setdefault(locale, {})[domain] = found
    return changed


def draft_migration(project, name):
    """A migration of the local translations that changed since the base; the base moves on to them."""
    from project_prestashop import is_prestashop, generic_refusal
    if not is_prestashop(project):
        raise generic_refusal('translations-migration')
    if project.environment != 'local':
        raise ValueError('make translations-migration drafts from the local shop: run it with ENV=local')
    slug = re.sub(r'[^a-z0-9]+', '-', (name or '').lower()).strip('-')
    if not slug:
        raise ValueError('Name the migration: make translations-migration name=cart-texts')
    base = read_base(project)
    if base is None:
        take_base(project)
        print('There was no base yet, so nothing is drafted: translate in the back office now, then run this again.')
        return None
    current = local_translations(project)
    changed = difference(current, base)
    if not count(changed):
        print('No translation changed since the base; nothing drafted.')
        return None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    path = migrations_directory(project) / f'{stamp}-{slug}.json'
    if path.exists():
        raise ValueError(f'{path} exists already; wait a second and run it again')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(changed, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    write_base(project, current)
    print(f'Drafted {path.relative_to(project.directory)}: {count(changed)} translations '
          f'({len(changed["legacy"])} files, {sum(len(domains) for domains in changed["database"].values())} domains).')
    print('Review it (remove what is not yours) and commit it; every deploy writes it once.')
    return path


# --- Applying them --------------------------------------------------------------------------------
READ = '[ -f "$1" ] && exec cat -- "$1"; [ -e "$1" ] && exit 2; [ -d "$2" ] || exit 4; exit 3'


class LocalCode:
    """The local shop's code tree, for the migrations a developer takes from teammates."""
    local, label = True, ''

    def mkdir(self, paths):
        for path in paths:
            Path(path).mkdir(parents=True, exist_ok=True)

    def push(self, source, path, *options):
        Path(path).write_bytes(Path(source).read_bytes())


def owner(path):
    """The folder a legacy file belongs to, which the target must have for it to be written: the module's folder, or
    the language's mails/<iso>/ (its templates are there once the language is installed)."""
    return str(Path(path).parent if MAIL_FILE.search(path) else Path(path).parent.parent)


def read_target(target, path):
    """(state, text): ('file', text), ('missing', None) when its module (mails/<iso>/) is there, ('no-module', None)
    when it is not."""
    if target.local:
        file = Path(path)
        if file.is_file():
            return 'file', file.read_text(errors='surrogateescape')
        if file.exists():
            raise ValueError(f'{path} is not a regular file')
        return ('missing', None) if Path(owner(path)).is_dir() else ('no-module', None)
    result = target.run(target.ssh('sh', '-c', READ, 'sh', path, owner(path)), capture_output=True)
    if result.returncode == 0:
        return 'file', result.stdout.decode(errors='surrogateescape')
    if result.returncode in (3, 4):
        return ('missing' if result.returncode == 3 else 'no-module'), None
    raise ValueError(f'Cannot read {target.label}{path} (exit {result.returncode}): '
                     f'{result.stderr.decode(errors="replace").strip()[:200]}')


def write_target(target, path, text):
    with tempfile.TemporaryDirectory(prefix='setup-translations-') as directory:
        local = Path(directory) / Path(path).name
        local.write_text(text, errors='surrogateescape')
        target.mkdir([str(Path(path).parent)])
        target.push(local, path, '--chmod=F644')


def where(project):
    """(target, code root) the migrations write files to: the local code tree, or the Deploy target's."""
    if project.environment == 'local':
        return LocalCode(), str(project.web_directory)
    from project_deploy import code_path
    from project_release import release_settings
    from project_target import deploy_target
    _, deploy_path = release_settings(project)
    return deploy_target(project), code_path(project, deploy_path)


class Report:
    def __init__(self, dry):
        self.dry, self.written, self.equal, self.over, self.skipped = dry, 0, 0, [], []

    def lines(self):
        found = [f'    {"would write" if self.dry else "wrote"} {self.written}, {self.equal} already so'
                 + (f', {len(self.over)} over a different text:' if self.over else '')]
        found += [f'      {line}' for line in self.over[:100]]
        if len(self.over) > 100:
            found.append(f'      … and {len(self.over) - 100} more')
        return found + [f'    skipped: {line}' for line in self.skipped]


def apply_legacy(target, root, legacy, report):
    for relative, entries in legacy.items():
        path = f'{root}/{relative}'
        state, text = read_target(target, path)
        name = variable(relative)
        if state == 'no-module':
            module = LEGACY_FILE.fullmatch(relative)
            report.skipped.append(f'{relative}: ' + (f'no module {module[1]} here' if module else
                                                     f'no {owner(relative)}/ here (language not installed)'))
            continue
        current = parse_legacy(text, name) if text is not None else {}
        if current is None:
            raise ValueError(f'{relative} here holds more than ${name} lines: fix it by hand, then migrate again')
        merged = dict(current)
        for key, new in entries.items():
            old = current.get(key)
            if old == new:
                report.equal += 1
                continue
            if old is not None:
                report.over.append(f'{relative} {shown(key)}: {shown(old)} -> {shown(new)}')
            merged[key] = new
            report.written += 1
        if merged != current and not report.dry:
            write_target(target, path, legacy_text(merged, name))


def apply_database(project, database, report):
    """The statements that write a migration's translation table rows (UPDATE what is there, INSERT the rest)."""
    if not database:
        return []
    if not has_table(project):
        raise ValueError('PrestaShop 1.6 has no translation table: a migration for it holds "legacy" only')
    table, lang = identifier(prefix(project) + 'translation'), identifier(prefix(project) + 'lang')
    languages = {locale.lower(): identity for identity, locale in project.database.query(f'SELECT id_lang, locale FROM {lang};\n')}
    domains = sorted({domain.split('@')[0] for entries in database.values() for domain in entries})
    current = {}
    for identity, id_lang, domain, theme, key, text in project.database.query(
            f"SELECT id_translation, id_lang, domain, COALESCE(theme, ''), `key`, translation FROM {table} "
            f'WHERE domain IN ({", ".join(literal(domain) for domain in domains)});\n'):
        if identity.isdigit():
            current.setdefault((id_lang, domain, theme, key), []).append((identity, text))
    statements = []
    for locale, entries in database.items():
        id_lang = languages.get(locale.lower())
        if id_lang is None:
            report.skipped.append(f'{locale}: no such language here')
            continue
        for qualified, texts in entries.items():
            domain, _, theme = qualified.partition('@')
            for key, new in texts.items():
                found = current.get((id_lang, domain, theme, key), [])
                if found and all(text == new for _, text in found):
                    report.equal += 1
                    continue
                if found:
                    report.over.append(f'{locale} {qualified} {shown(key)}: {shown(found[0][1])} -> {shown(new)}')
                    statements.append(f'UPDATE {table} SET translation = {literal(new)} WHERE id_translation IN '
                                      f'({", ".join(identity for identity, _ in found)});')
                else:
                    statements.append(f'INSERT INTO {table} (id_lang, `key`, translation, domain, theme) VALUES ({id_lang}, '
                                      f'{literal(key)}, {literal(new)}, {literal(domain)}, {literal(theme) if theme else "NULL"});')
                report.written += 1
    return statements


def pending(project):
    """([(record name, path, checksum, content)] not applied yet, [names changed since they were applied])."""
    from database_migrations_guarded import applied_migrations
    directory = migrations_directory(project)
    if not directory.is_dir():
        return [], []
    strays = sorted(path.name for path in directory.iterdir() if path.is_file() and not NAME.fullmatch(path.name))
    if strays:
        raise ValueError(f'{directory}: {", ".join(strays)} is not named <14 digits>-<name>.json (make translations-migration)')
    applied = applied_migrations(project)
    found, changed = [], []
    for path in sorted(directory.glob('*.json')):
        text = path.read_text()
        name, checksum = RECORD + path.name, hashlib.sha256(text.encode()).hexdigest()
        if name not in applied:
            found.append((name, path, checksum, checked(json.loads(text), path)))
        elif applied[name] != checksum:
            changed.append(name)
    return found, changed


def migrate_translations(project, dry=False):
    """Apply the pending translation migrations in order; the applied names (none with dry)."""
    from project_prestashop import is_prestashop, generic_refusal
    if not is_prestashop(project):
        raise generic_refusal('translations-migrate')
    waiting, changed = pending(project)
    for name in changed:
        print(f'Warning: {name} changed after it was applied here; it is not rerun (write a new migration).')
    if not waiting:
        print('No pending translation migrations.')
        return []
    target, root = where(project)
    applied = []
    for name, path, checksum, data in waiting:
        report = Report(dry)
        print(f'{"Would apply" if dry else "Applying"} {name}', flush=True)
        apply_legacy(target, root, data['legacy'], report)
        statements = apply_database(project, data['database'], report)
        if not dry:
            for start in range(0, len(statements), 500):
                project.database.execute('\n'.join(statements[start:start + 500]) + '\n')
            project.database.execute(f'INSERT INTO setup_migrations (name, checksum, applied_at) '
                                     f'VALUES ({literal(name)}, {literal(checksum)}, NOW());\n')
            applied.append(name)
            if project.environment == 'local':
                remember(project, data)
        for line in report.lines():
            print(line)
    print(f'{"Would apply" if dry else "Applied"} {len(waiting)} translation migrations.')
    return applied


def remember(project, data):
    """A teammate's migration applied locally is no change of this developer's: the base takes its texts."""
    base = read_base(project)
    if base is None:
        return
    for path, entries in data['legacy'].items():
        base['legacy'].setdefault(path, {}).update(entries)
    for locale, domains in data['database'].items():
        for domain, entries in domains.items():
            base['database'].setdefault(locale, {}).setdefault(domain, {}).update(entries)
    write_base(project, base)
