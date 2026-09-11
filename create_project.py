"""Prepare a new local project; start it only with an explicit --start."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace

SETUP = Path(__file__).resolve().parent
sys.path.insert(0, str(SETUP / 'docker'))

from prepare_project import planned_files
from project_bootstrap import available_ports, set_env_values
from project_ide import replace_file

MARKER = 'app/.generated/create-project.json'


def scaffold(name, parent):
    if not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', name) or len(name) > 32:
        raise ValueError('Pavadinimas turi prasidėti mažąja raide, būti iki 32 simbolių; naudok a-z, 0-9 ir brūkšnelius.')
    root = Path(parent).resolve() / name
    marker = root / MARKER
    if root.is_symlink():
        raise ValueError(f'Projekto katalogas negali būti nuoroda: {root}')
    if root.exists():
        if any(p.is_symlink() for p in (root / 'app', marker.parent, marker)):
            raise ValueError(f'Esama projekto paruošimo žyma negali būti nuoroda: {root}')
        if not marker.is_file() or json.loads(marker.read_text()) != {'creator': 'create-project', 'version': 1, 'name': name}:
            raise ValueError(f'Katalogas jau yra ir nepriklauso šiai komandai: {root}. Pasirink kitą pavadinimą; esami failai išsaugoti.')
        print(f'Projektas jau paruoštas; esami failai ir prisijungimai išsaugoti: {root}', flush=True)
        return root / 'app'

    app, files = planned_files(root, layout='app', sources='grouped')
    ports = available_ports(SimpleNamespace(root=root, name=name + '-local'))
    # Claim only a new project directory. Never merge a scaffold into someone else's files.
    root.mkdir()
    for path, contents in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(contents)
    public = app / 'public'
    public.mkdir()
    (public / 'index.php').write_text(
        "<?php\nheader('Content-Type: text/plain; charset=utf-8');\n"
        f"echo \"Projektas {name} veikia.\\n\";\n"
        "echo 'PHP: ' . PHP_VERSION . \"\\n\";\n"
        "echo 'Cache: ' . getenv('CACHE_MODE') . \"\\n\";\n")
    import secrets
    domain = name + '.local'
    set_env_values(app / 'env/local.env', {
        'DOMAIN': domain, 'LOCALHOST_PORT': ports[0], 'LOCALHOST_PORT_SSL': ports[1],
        'DATABASE_USER': name.replace('-', '_'), 'DATABASE_NAME': name.replace('-', '_'),
        'DATABASE_PASSWORD': secrets.token_hex(24), 'COMPOSE_PROFILES': '',
        'SMOKE_URL': f'http://{domain}/', 'SMOKE_EXPECT': name, 'HOST_PROXY': 'nginx',
    })
    # The marker is written last: retries may resume a complete scaffold, never guess
    # whether a pre-existing directory (or an interrupted file copy) belongs to us.
    replace_file(marker, json.dumps({'creator': 'create-project', 'version': 1, 'name': name}).encode(), private=True)
    print(f'Sukurti projekto failai: {app}', flush=True)
    return app


def prerequisites():
    missing = [name for name in ('make', 'docker', 'openssl', 'mkcert') if not shutil.which(name)]
    if missing:
        raise ValueError('Trūksta programų: ' + ', '.join(missing) + '. Įdiek jas ir pakartok paleidimą.')
    result = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, timeout=20)
    if result.returncode:
        raise ValueError('Docker neveikia. Paleisk Docker ir pakartok komandą.')
    result = subprocess.run(['docker', 'compose', 'version', '--short'], capture_output=True, text=True, timeout=20)
    version = re.search(r'(\d+)\.(\d+)\.(\d+)', result.stdout)
    if result.returncode or not version or tuple(map(int, version.groups())) < (2, 24, 4):
        raise ValueError('Reikia Docker Compose 2.24.4 arba naujesnio.')


def start(app):
    for action, description in (('bootstrap', 'Ruošiami portai, TLS ir PhpStorm'),
                                ('check', 'Tikrinama konfigūracija'),
                                ('build', 'Kuriami Docker atvaizdai'),
                                ('up', 'Paleidžiami servisai ir tikrinamas puslapis')):
        print(f'\n{description}…', flush=True)
        subprocess.run(['make', '--no-print-directory', '-C', str(app), f'SETUP_DIRECTORY={SETUP}', action], check=True)
    from project import Project
    project = Project(app)
    print(f'\nProjektas veikia: {project.settings["SMOKE_URL"]}\nPhpStorm atidaryk: {app}\nAplikacijos kodas: {app / "public"}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='create-project', description='Paruošia naują projektą šalia bendro setup. Aplinkos automatiškai nepaleidžia.')
    parser.add_argument('name', metavar='pavadinimas', help='Pvz. demo arba mano-projektas')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--start', action='store_true', help='Aiškiai paruošti host/TLS/IDE, sukurti atvaizdus ir paleisti aplinką')
    mode.add_argument('--no-start', action='store_true', help=argparse.SUPPRESS)  # Previous spelling remains harmless.
    args = parser.parse_args(argv)
    try:
        if args.start:
            prerequisites()
        app = scaffold(args.name, SETUP.parent)
        if args.start:
            start(app)
        else:
            print(f'Failai paruošti; aplinka nepaleista.\nPhpStorm atidaryk: {app}\n'
                  f'Paleidimui: {SETUP / "create-project"} {args.name} --start')
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Klaida: {error}\nPašalinęs priežastį pakartok tą pačią create-project komandą; paruošti failai išsaugomi.', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nNutraukta. Paruošti failai išsaugoti; gali pakartoti tą pačią komandą.', file=sys.stderr)
        return 130
    return 0


if __name__ == '__main__':
    sys.exit(main())
