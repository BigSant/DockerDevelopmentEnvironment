"""PrestaShop translations a project keeps in its environment repository, brought to the target by every deploy.

Since a deploy no longer writes module translations (they are the shop's content: the back office edits them), the
ones a developer makes travel apart from the code, in the environment repository:

  translations/legacy/<code tree path>        legacy module translations as PrestaShop writes them, e.g.
                                              translations/legacy/modules/mymodule/translations/lt.php
                                              ($_MODULE['<{mymodule}prestashop>file_md5'] = '…';), or a theme's
                                              themes/<theme>/modules/<module>/translations/<iso>.php
  translations/database/<locale>/<domain>.json  translations of the new system (the translation table):
                                              {"locale", "domain", "theme", "translations": {key: text}}

Every PrestaShop deploy (after the module upgrades) writes each translation the repository brings new or changed
since a deploy last wrote it, over what the target holds — a client's edit of that key too. One it brought before
and has not changed since is left alone, so whatever clients edit in the back office stays until the repository
changes that very translation. setup_translations (main database, setup's bookkeeping) keeps per entry the checksum
of the text last written. Nothing is deleted: a key gone from the repository stays on the target. A file is only
rewritten when every line of it is understood, and a module the target does not have gets no translation file.

  make translations-export module=a,b [domain=Modules.Name.*] [lang=lt]   (ENV=local) the local shop's ones into
                                              the repository: those modules' files, their (or the named) domains' rows
  make translations-apply ENV=prod [dry=1]    the deploy's step alone; dry=1 shows what would be written, and over
                                              which different target text, and changes nothing
"""

from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from database_client import identifier, literal

TABLE = 'setup_translations'
DIRECTORY = 'translations'
LEGACY_FILE = re.compile(r'(?:themes/[A-Za-z0-9_.-]+/)?modules/([A-Za-z0-9_-]+)/translations/([a-z]{2,5})\.php')
LOCALE = re.compile(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?')
DOMAIN = re.compile(r'[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*')
# One legacy entry, as PrestaShop writes it: $_MODULE['key'] = 'text'; (PHP single-quoted strings).
ENTRY = re.compile(r"\$_MODULE\[\s*'((?:[^'\\]|\\.)*)'\s*\]\s*=\s*'((?:[^'\\]|\\.)*)'\s*;", re.S)
HEADER = "<?php\n\nglobal $_MODULE;\n$_MODULE = array();\n"
SHOWN = 60


def php_unquote(text):
    """The value of a PHP single-quoted string's body: only \\\\ and \\' are escapes."""
    return re.sub(r"\\([\\'])", r'\1', text)


def php_quote(text):
    return "'" + text.replace('\\', '\\\\').replace("'", "\\'") + "'"


def parse_legacy(text):
    """{key: text} of a legacy translation file, or None when it holds anything else than its entries."""
    entries = {}
    for match in ENTRY.finditer(text):
        entries[php_unquote(match[1])] = php_unquote(match[2])
    rest = ENTRY.sub('', text)
    rest = re.sub(r'<\?php|\?>|global\s+\$_MODULE\s*;|\$_MODULE\s*=\s*array\(\s*\)\s*;|\$_MODULE\s*=\s*\[\s*\]\s*;', '', rest)
    rest = re.sub(r'//[^\n]*|#[^\n]*|/\*.*?\*/', '', rest, flags=re.S)
    return entries if not rest.strip() and text.count('$_MODULE[') == len(ENTRY.findall(text)) else None


def legacy_text(entries):
    return HEADER + ''.join(f'$_MODULE[{php_quote(key)}] = {php_quote(value)};\n' for key, value in entries.items())


def checksum(text):
    return hashlib.sha256(text.encode()).hexdigest()


def entry_id(*parts):
    return checksum('\0'.join(parts))


def shown(text):
    text = text.replace('\n', ' ')
    return repr(text if len(text) <= SHOWN else text[:SHOWN] + '…')


def prefix(project):
    return project.settings.get('PS_DATABASE_PREFIX') or 'ps_'


def repository_directory(project):
    return Path(project.directory) / DIRECTORY


# --- The repository's translations ----------------------------------------------------------------
def repository_translations(project):
    """({code tree path: {key: text}}, {(locale, domain, theme): {key: text}}) of the environment repository."""
    root = repository_directory(project)
    files, rows = {}, {}
    legacy = root / 'legacy'
    for path in sorted(legacy.rglob('*.php')) if legacy.is_dir() else []:
        relative = path.relative_to(legacy).as_posix()
        if not LEGACY_FILE.fullmatch(relative):
            raise ValueError(f'{path} is not a module translation file (modules/<module>/translations/<iso>.php)')
        entries = parse_legacy(path.read_text())
        if entries is None:
            raise ValueError(f'{path} holds more than $_MODULE[...] = ...; lines; export it again')
        files[relative] = entries
    database = root / 'database'
    for path in sorted(database.rglob('*.json')) if database.is_dir() else []:
        try:
            data = json.loads(path.read_text())
            locale, domain, theme = data['locale'], data['domain'], data.get('theme') or ''
            texts = data['translations']
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError(f'{path} is not a translations file ({error})') from None
        if not LOCALE.fullmatch(locale) or not DOMAIN.fullmatch(domain) or (theme and not DOMAIN.fullmatch(theme)) or \
                not isinstance(texts, dict) or not all(isinstance(key, str) and isinstance(text, str)
                                                       for key, text in texts.items()):
            raise ValueError(f'{path}: locale, domain, theme or translations are not as make translations-export writes them')
        rows.setdefault((locale, domain, theme), {}).update(texts)
    return files, rows


# --- The target's files ---------------------------------------------------------------------------
READ = 'd=$(dirname "$1"); [ -f "$1" ] && exec cat -- "$1"; [ -e "$1" ] && exit 2; [ -d "$(dirname "$d")" ] || exit 4; exit 3'


def read_target(target, path):
    """(state, text): ('file', text), ('missing', None) when the module is there, ('no-module', None) when it is not."""
    if target.local:
        file = Path(path)
        if file.is_file():
            return 'file', file.read_text(errors='surrogateescape')
        if file.exists():
            raise ValueError(f'{path} on the target is not a regular file')
        return ('missing', None) if file.parent.parent.is_dir() else ('no-module', None)
    result = target.run(target.ssh('sh', '-c', READ, 'sh', path), capture_output=True)
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


# --- The bookkeeping -------------------------------------------------------------------------------
def written_before(project, create):
    """{entry id: checksum of the text a deploy last wrote}; with create, the table is made when missing."""
    if create:
        project.database.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} (id char(64) NOT NULL PRIMARY KEY, '
                                 'checksum char(64) NOT NULL, applied_at datetime NOT NULL) ENGINE=InnoDB;\n')
    elif not project.database.query("SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
                                    f"AND TABLE_NAME = {literal(TABLE)};\n"):
        return {}
    return dict(project.database.query(f'SELECT id, checksum FROM {TABLE};\n'))


def record(project, written):
    rows = [f'({literal(identity)}, {literal(value)}, NOW())' for identity, value in written.items()]
    for start in range(0, len(rows), 500):
        project.database.execute(f'REPLACE INTO {TABLE} (id, checksum, applied_at) VALUES {", ".join(rows[start:start + 500])};\n')


# --- Applying them on the target -------------------------------------------------------------------
class Report:
    def __init__(self, dry):
        self.dry, self.written, self.over, self.equal, self.kept, self.skipped = dry, 0, [], 0, 0, []

    def overwrite(self, where, key, old, new):
        self.over.append(f'{where} {shown(key)}: {shown(old)} -> {shown(new)}')

    def lines(self):
        verb = 'would write' if self.dry else 'wrote'
        found = [f'==> translations: {verb} {self.written}, of them {len(self.over)} over a different text on the target '
                 f'(a client\'s edit, or a value of its own); {self.equal} already as in the repository, '
                 f'{self.kept} unchanged since a deploy wrote them (left as the target has them)']
        found += [f'    {"would overwrite" if self.dry else "overwrote"} {line}' for line in self.over[:200]]
        if len(self.over) > 200:
            found.append(f'    … and {len(self.over) - 200} more')
        found += [f'    skipped: {line}' for line in self.skipped]
        return found


def apply_legacy(project, target, code_root, files, before, report):
    written = {}
    for relative, entries in files.items():
        wanted = {key: text for key, text in entries.items()
                  if before.get(entry_id('file', relative, key)) != checksum(text)}
        report.kept += len(entries) - len(wanted)
        if not wanted:
            continue
        path = f'{code_root}/{relative}'
        state, text = read_target(target, path)
        if state == 'no-module':
            report.skipped.append(f'{relative}: the target has no module {LEGACY_FILE.fullmatch(relative)[1]}')
            continue
        current = parse_legacy(text) if text is not None else {}
        if current is None:
            report.skipped.append(f'{relative}: the target\'s file holds more than $_MODULE lines; not rewritten')
            continue
        merged, changed = dict(current), False
        for key, new in wanted.items():
            old = current.get(key)
            if old == new:
                report.equal += 1
            else:
                if old is not None:
                    report.overwrite(relative, key, old, new)
                merged[key] = new
                report.written += 1
                changed = True
            written[entry_id('file', relative, key)] = checksum(new)
        if changed and not report.dry:
            write_target(target, path, legacy_text(merged))
    return written


def apply_database(project, rows, before, report):
    if not rows:
        return {}
    if str(project.settings.get('PS_VERSION', '')).startswith('1.6'):
        raise ValueError('PrestaShop 1.6 has no translation table: keep its translations in translations/legacy/')
    table, lang = identifier(prefix(project) + 'translation'), identifier(prefix(project) + 'lang')
    languages = {locale.lower(): identity for identity, locale in project.database.query(f'SELECT id_lang, locale FROM {lang};\n')}
    wanted = {}
    for (locale, domain, theme), texts in rows.items():
        for key, text in texts.items():
            if before.get(entry_id('database', locale, domain, theme, key)) == checksum(text):
                report.kept += 1
            else:
                wanted[(locale, domain, theme, key)] = text
    if not wanted:
        return {}
    domains = sorted({domain for _, domain, _, _ in wanted})
    current = {}
    for identity, id_lang, domain, theme, key, text in project.database.query(
            f'SELECT id_translation, id_lang, domain, COALESCE(theme, \'\'), `key`, translation FROM {table} '
            f'WHERE domain IN ({", ".join(literal(domain) for domain in domains)});\n'):
        current.setdefault((id_lang, domain, theme, key), []).append((identity, text))
    statements, written, missing = [], {}, set()
    for (locale, domain, theme, key), new in wanted.items():
        id_lang = languages.get(locale.lower())
        if id_lang is None:
            missing.add(locale)
            continue
        found = [(identity, text) for identity, text in current.get((id_lang, domain, theme, key), []) if identity.isdigit()]
        if found and all(text == new for _, text in found):
            report.equal += 1
        else:
            for _, old in found[:1]:
                report.overwrite(f'{locale} {domain}{"@" + theme if theme else ""}', key, old, new)
            if found:
                statements.append(f'UPDATE {table} SET translation = {literal(new)} WHERE id_translation IN '
                                  f'({", ".join(identity for identity, _ in found)});')
            else:
                statements.append(f'INSERT INTO {table} (id_lang, `key`, translation, domain, theme) VALUES ({id_lang}, '
                                  f'{literal(key)}, {literal(new)}, {literal(domain)}, {literal(theme) if theme else "NULL"});')
            report.written += 1
        written[entry_id('database', locale, domain, theme, key)] = checksum(new)
    report.skipped += [f'{locale}: the target has no such language (the translation table rows wait for it)'
                       for locale in sorted(missing)]
    if statements and not report.dry:
        for start in range(0, len(statements), 500):
            project.database.execute('\n'.join(statements[start:start + 500]) + '\n')
    return written


def apply_translations(project, dry=False):
    """Write what the repository brings new or changed since the last deploy; a report of what happened."""
    from project_deploy import code_path
    from project_prestashop import is_prestashop, generic_refusal
    from project_release import release_settings
    from project_target import deploy_target
    if not is_prestashop(project):
        raise generic_refusal('translations-apply')
    files, rows = repository_translations(project)
    if not files and not rows:
        print(f'No translations in {repository_directory(project)} (make translations-export module=…).')
        return None
    _, deploy_path = release_settings(project)
    before = written_before(project, create=not dry)
    report = Report(dry)
    written = apply_legacy(project, deploy_target(project), code_path(project, deploy_path), files, before, report)
    written.update(apply_database(project, rows, before, report))
    if written and not dry:
        record(project, written)
    for line in report.lines():
        print(line)
    return report


# --- Exporting the local shop's ones ---------------------------------------------------------------
def module_domains(name):
    """The translation domains PrestaShop names after a module: Modules.<Name>.* (ps_ dropped, one capital)."""
    stem = name[3:] if name.startswith('ps_') else name
    return f'Modules.{stem.replace("_", "").capitalize()}.*'


def export_translations(project, modules=(), domains=(), languages=()):
    """Write the local shop's translations of these modules (files and domains) and domains into the repository."""
    from project_prestashop import is_prestashop, generic_refusal
    if not is_prestashop(project):
        raise generic_refusal('translations-export')
    if project.environment != 'local':
        raise ValueError('make translations-export reads the local shop: run it with ENV=local')
    if not modules and not domains:
        raise ValueError('Name what to export: make translations-export module=mymodule[,other] [domain=Modules.Name.*]')
    for name in modules:
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError(f'Not a module name: {name!r}')
    patterns = [*domains, *(module_domains(name) for name in modules)]
    for pattern in patterns:
        if not re.fullmatch(r'[A-Za-z0-9_.*]+', pattern):
            raise ValueError(f'Not a domain (or a pattern with *): {pattern!r}')
    root, code = repository_directory(project), Path(project.web_directory)
    # lang=lt matches lt.php and lt-LT; the language part decides.
    wanted = lambda code: not languages or code.lower().split('-')[0] in {lang.split('-')[0] for lang in languages}
    files = 0
    for name in modules:
        found = sorted(path for path in [*code.glob(f'modules/{name}/translations/*.php'),
                                         *code.glob(f'themes/*/modules/{name}/translations/*.php')]
                       if LEGACY_FILE.fullmatch(path.relative_to(code).as_posix()))
        for old in [*(root / 'legacy').glob(f'modules/{name}/translations/*.php'),
                    *(root / 'legacy').glob(f'themes/*/modules/{name}/translations/*.php')]:
            if wanted(old.stem):
                old.unlink()
        for path in found:
            relative = path.relative_to(code).as_posix()
            if not wanted(LEGACY_FILE.fullmatch(relative)[2]):
                continue
            entries = parse_legacy(path.read_text())
            if entries is None:
                raise ValueError(f'{path} holds more than $_MODULE[...] = ...; lines; not exported')
            if not entries:
                continue
            destination = root / 'legacy' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(legacy_text(dict(sorted(entries.items()))))
            files += 1
    rows = {}
    if patterns:
        table, lang = identifier(prefix(project) + 'translation'), identifier(prefix(project) + 'lang')
        like = ' OR '.join('t.domain LIKE ' + literal(pattern.replace('_', '\\_').replace('*', '%')) for pattern in patterns)
        for locale, domain, theme, key, text in project.database.query(
                f'SELECT l.locale, t.domain, COALESCE(t.theme, \'\'), t.`key`, t.translation FROM {table} t '
                f'JOIN {lang} l ON l.id_lang = t.id_lang WHERE {like};\n'):
            if wanted(locale) and any(fnmatchcase(domain, pattern) for pattern in patterns):
                rows.setdefault((locale, domain, theme), {})[key] = text
    for path in (root / 'database').rglob('*.json') if (root / 'database').is_dir() else []:
        data = json.loads(path.read_text())
        if wanted(data.get('locale', '')) and any(fnmatchcase(data.get('domain', ''), pattern) for pattern in patterns):
            path.unlink()
    for (locale, domain, theme), texts in sorted(rows.items()):
        destination = root / 'database' / locale / f'{domain}{"@" + theme if theme else ""}.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({'locale': locale, 'domain': domain, 'theme': theme or None,
                                           'translations': dict(sorted(texts.items()))}, ensure_ascii=False, indent=2) + '\n')
    print(f'Exported {files} legacy files and {sum(len(texts) for texts in rows.values())} translation table rows '
          f'({len(rows)} domains: {", ".join(sorted({domain for _, domain, _ in rows})) or "none"}) into {root}.')
    print('Review and commit them to the environment repository; the next deploy writes what is new or changed.')
    return files, rows
