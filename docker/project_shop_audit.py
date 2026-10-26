"""make shop-audit [live=DIR]: a preliminary, read-only check that a PrestaShop shop repository holds its code.

Every file of the shop belongs to one place: the official release (PS_RELEASE_PATHS), something setup or the shop
writes at run time (generated), server content the deploy never touches (the profile's protected paths and
DEPLOY_PROTECTED_PATHS), tool state, or code, which the shop repository must track. index.php and .htaccess guards
are code wherever they are (except the generated root .htaccess). The audit reads the checkout and git only: no
container, no network, nothing written.

Errors: code that .gitignore hides; tracked files in release paths (make setup refuses to copy over them); a shop
without config/defines.inc.php or vendor/; with live=DIR (a copy of the server's code tree, e.g. from FTP) code that
exists only there. Warnings: tracked files in protected paths (a deploy never updates them on the server), broad
.gitignore rules (by extension or folder name anywhere) and tracked files that look like dumps or exports.
"""

from collections import Counter, defaultdict
import os
from pathlib import Path
import re
import subprocess

from project_prestashop import is_prestashop

GENERATED = ('/.htaccess', '/robots.txt', '/*sitemap*.xml', '/app/config/parameters.php', '/app/config/parameters.yml',
             '/app/config/parameters.old.yml', '/config/settings.inc.php', '/var/', '/cache/', '/config/themes/',
             '/config/xml/*.xml', '/config/xml/themes/*.xml', '/translations/sf-*.zip', '/modules/*/config*.xml',
             '/modules/*/logs/', '/modules/*/uploads/', '/modules/*/php_errors.log', '/webservice/php_errors.log',
             '/modules/*/vendor/mpdf/mpdf/tmp/', '/composer.json', '/composer.lock', '/install/',
             '/themes/classic/', '/themes/hummingbird/')
# Tool state and the folder of the old Bitbucket webhook deploy that PrestaRock shops kept in the code tree.
TOOLS = ('/.git/', '/.idea/', '/.claude/', '/.setup-tools/', '/**/node_modules/', '/**/.asana-memory-session',
         '/**/.phpunit.result.cache', '/**/.DS_Store', '/**/tests/.tools/', '/deploy/')
# Guards (index.php, .htaccess) are code in these content folders and one level below them (img/c/index.php).
GUARD_ROOTS = ('/img/', '/upload/', '/download/', '/cache/')
GUARDS = ('index.php', '.htaccess')
# Dumps and keys outside modules/ (their install SQL is code) and vendor/, and data exports anywhere.
SENSITIVE = re.compile(r'^(?!/modules/)(?!.*/vendor/).*\.(?:sql(?:\.gz)?|dump|pem|key)$'
                       r'|/id_rsa[^/]*$|/exports?/[^/]+\.(?:csv|xlsx?)$', re.I)
BROAD_NAMES = re.compile(r'^!?(\*\.[A-Za-z0-9]+|\.htaccess|composer\.lock|[A-Za-z0-9_.-]+/?)$')
EXAMPLES = 3


def pattern(entry):
    """A setup path pattern (/a/, /a/*.xml, /**/x) as a regex on a shop-relative path."""
    anchored = entry.strip('/')
    if entry.endswith('/'):
        anchored += '/**'
    out = ''
    for part in re.split(r'(\*\*/|\*\*|\*)', anchored):
        out += {'**/': '(?:.*/)?', '**': '.*', '*': '[^/]*'}.get(part, re.escape(part))
    return re.compile(out + '$')


def rules(project):
    from project_deploy import protected_paths
    admin = project.settings.get('PS_ADMIN_DIR') or 'admin'
    release = ['/' + (admin if path == 'admin' else path).strip('/') + ('' if '.' in path.split('/')[-1] else '/')
               for path in project.settings.get('PS_RELEASE_PATHS', '').split()]
    content = [p for p in protected_paths(project) if p not in GENERATED and not p.startswith(('/.git', '/.idea', '/.claude', '/.setup'))]
    # A guard is code in the root of a generated or content folder and one level below it (img/c/index.php).
    project_paths = ['/' + p.strip('/') + '/' for p in (project.settings.get('DEPLOY_PROTECTED_PATHS') or '').split() if p.endswith('/')]
    roots = [p.strip('/') for p in (*GUARD_ROOTS, *project_paths)]
    guards = [re.compile(pattern('/' + root).pattern[:-1] + '(?:/[^/]+)?$') for root in roots]
    compiled = [(name, [pattern(p) for p in patterns]) for name, patterns in
                (('release', release), ('tools', TOOLS), ('generated', GENERATED), ('content', content))]
    return {'categories': compiled, 'guards': guards}


def category(path, compiled):
    """release, tools, generated, content or code for a shop-relative file path."""
    for name, patterns in compiled['categories'][:2]:
        if any(p.match(path) for p in patterns):
            return name
    parent, _, name = path.rpartition('/')
    if name in GUARDS and path != '.htaccess' and (not parent or any(g.match(parent) for g in compiled['guards'])):
        return 'code'
    for name, patterns in compiled['categories'][2:]:
        if any(p.match(path) for p in patterns):
            return name
    return 'code'


def guard_files(shop, folder, compiled):
    """index.php / .htaccess guards in an ignored folder and its direct subfolders that are code."""
    root = Path(shop) / folder
    candidates = [root / name for name in GUARDS]
    try:
        candidates += [child / name for child in root.iterdir() if child.is_dir() and not child.is_symlink() for name in GUARDS]
    except OSError:
        return []
    found = [os.path.relpath(c, shop) for c in candidates if c.is_file()]
    return [p for p in found if category(p, compiled) == 'code']


def git(shop, *args):
    result = subprocess.run(['git', '-C', str(shop), *args], capture_output=True)
    if result.returncode:
        raise ValueError(f'git {" ".join(args)} failed in {shop}: {result.stderr.decode(errors="replace").strip()}')
    return [os.fsdecode(name) for name in result.stdout.split(b'\0') if name]


def groups(paths, depth=2):
    counter = Counter('/'.join(p.split('/')[:depth]) for p in paths)
    examples = defaultdict(list)
    for p in paths:
        key = '/'.join(p.split('/')[:depth])
        if len(examples[key]) < EXAMPLES:
            examples[key].append(p)
    return [(key, count, examples[key]) for key, count in counter.most_common()]


def hiding_paths(shop, paths):
    """{path: 'source:line: rule'} — the ignore rule that hides each path (source relative to the shop)."""
    if not paths:
        return {}
    result = subprocess.run(['git', '-C', str(shop), 'check-ignore', '--no-index', '-v', '-z', '--stdin'],
                            input=b'\0'.join(os.fsencode(p) for p in paths), capture_output=True)
    fields = [os.fsdecode(f) for f in result.stdout.split(b'\0')]
    return {fields[i + 3]: f'{fields[i]}:{fields[i + 1]}: {fields[i + 2]}' for i in range(0, len(fields) - 3, 4) if fields[i]}


def broad_rules(shop):
    found = []
    path = Path(shop) / '.gitignore'
    if not path.is_file():
        return found
    for number, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
        rule = line.strip()
        if not rule or rule.startswith('#') or rule.lstrip('!').startswith('/'):
            continue
        if BROAD_NAMES.match(rule):
            found.append(f'.gitignore:{number}: {rule}')
    return found


def live_files(live):
    out = []
    for directory, dirs, names in os.walk(live):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules')]
        for name in names:
            out.append(os.path.relpath(os.path.join(directory, name), live))
    return out


def live_admin(live, local_admin):
    """The server's admin folder: a top-level folder with the back office entry files and its themes."""
    for entry in sorted(Path(live).iterdir()):
        if entry.is_dir() and (entry / 'init.php').is_file() and (entry / 'themes').is_dir() and (entry / 'filemanager').is_dir():
            return entry.name
    return local_admin


def audit(project, live=None):
    """{'errors': [(title, paths)], 'warnings': [(title, paths)], 'notes': [...]} for the shop checkout."""
    if not is_prestashop(project):
        raise ValueError('shop-audit checks a PrestaShop shop; set PROFILE=prestashop')
    shop = Path(project.web_directory)
    if not (shop / '.git').exists():
        raise ValueError(f'{shop} is not a git checkout of the shop')
    compiled = rules(project)
    tracked = set(git(shop, 'ls-files', '-z'))
    ignored = git(shop, 'ls-files', '-z', '--others', '--ignored', '--exclude-standard', '--directory', '--no-empty-directory')
    # --directory folds whole ignored folders; open the ones that are not plain release/content/tool folders.
    expanded = []
    for name in ignored:
        if not name.endswith('/'):
            expanded.append(name)
        elif category(name.rstrip('/') + '/x.php', compiled) == 'code':
            for directory, dirs, files in os.walk(shop / name):
                dirs[:] = [d for d in dirs if d != 'node_modules']
                expanded += [os.path.relpath(os.path.join(directory, f), shop) for f in files]
        elif category(name.rstrip('/') + '/x.php', compiled) != 'release':
            expanded += guard_files(shop, name, compiled)
    errors, warnings, notes = [], [], []
    hidden = sorted(p for p in expanded if category(p, compiled) == 'code')
    by_rule = hiding_paths(shop, hidden)
    shop_rule = sorted(p for p, source in by_rule.items() if source.split(':')[0] in ('.gitignore', '.git/info/exclude'))
    module_rule = sorted(p for p, source in by_rule.items() if p not in set(shop_rule))
    if shop_rule:
        errors.append(('Code hidden by the shop .gitignore (a clean clone and every release lack it; track it, or list '
                       'server content in DEPLOY_PROTECTED_PATHS)', shop_rule))
    if module_rule:
        warnings.append(("Files a module's own .gitignore hides (often dev tools; check the ones the shop needs)", module_rule))
    if hidden:
        notes.append(('Rules that hide that code', [f'{count:6}  {rule}' for rule, count in
                                                    Counter(by_rule[p] for p in hidden if p in by_rule).most_common(15)]))
    in_release = sorted(p for p in tracked if category(p, compiled) == 'release')
    if in_release:
        errors.append(('Tracked files in PS_RELEASE_PATHS (make setup refuses to copy the release over them)', in_release))
    for required, why in (('config/defines.inc.php', 'PHP does not start without it'), ('vendor', 'PrestaShop needs its libraries')):
        provided = (any(p == required or p.startswith(required + '/') for p in tracked)
                    or category(required + ('/x.php' if required == 'vendor' else ''), compiled) == 'release'
                    or (required == 'vendor' and (Path(project.directory) / 'config/composer/composer.json').is_file()))
        if not provided:
            errors.append((f'{required}: neither tracked nor in PS_RELEASE_PATHS ({why})', [required]))
    in_content = sorted(p for p in tracked if category(p, compiled) == 'content')
    if in_content:
        warnings.append(('Tracked files in protected paths (a deploy never updates them on the server; ignore them)', in_content))
    broad = broad_rules(shop)
    if broad:
        warnings.append(('.gitignore rules by file type or folder name anywhere (they hide code in modules)', broad))
    sensitive = sorted(p for p in tracked if SENSITIVE.search('/' + p))
    if sensitive:
        warnings.append(('Tracked files that look like dumps, keys or exports', sensitive))
    if live:
        live = Path(live)
        if not live.is_dir():
            raise ValueError(f'live={live} is not a directory')
        admin = project.settings.get('PS_ADMIN_DIR') or 'admin'
        server_admin = live_admin(live, admin)
        visible = set(git(shop, 'ls-files', '-z', '--others', '--exclude-standard'))
        not_committed, missing = [], []
        for path in live_files(live):
            mapped = admin + path[len(server_admin):] if path.startswith(server_admin + '/') else path
            if mapped in tracked or category(mapped, compiled) != 'code':
                continue
            (not_committed if mapped in visible else missing).append(mapped)
        if missing:
            errors.append((f'Code on the server copy that the shop checkout lacks or ignores (admin {server_admin} '
                           f'read as {admin})', sorted(missing)))
        if not_committed:
            errors.append(('Code on the server copy that is in the checkout but not committed', sorted(not_committed)))
    return {'errors': errors, 'warnings': warnings, 'notes': notes}


def print_audit(project, live=None):
    result = audit(project, live)
    shop = project.web_directory
    print(f'Shop audit of {shop} (rules: PS_RELEASE_PATHS, protected paths, DEPLOY_PROTECTED_PATHS)')
    for kind in ('errors', 'warnings'):
        for title, paths in result[kind]:
            print(f'\n{"ERROR" if kind == "errors" else "Warning"}: {title}: {len(paths)}')
            if paths and ':' in paths[0] and paths[0].startswith('.gitignore'):
                for line in paths:
                    print(f'  {line}')
                continue
            for key, count, examples in groups(paths)[:20]:
                print(f'  {count:6}  {key}  (e.g. {", ".join(examples)})')
    for title, lines in result['notes']:
        print(f'\n{title}:')
        for line in lines:
            print(f'  {line}')
    errors = len(result['errors'])
    print(f'\n{errors} errors, {len(result["warnings"])} warnings.')
    if errors:
        raise ValueError('shop-audit found errors (see above); a clean clone would not run the shop as the server does')
