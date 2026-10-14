"""PrestaShop release line -> supported PHP and database versions (compatibility.json)."""
import json
from pathlib import Path

DATA = json.loads((Path(__file__).with_name('compatibility.json')).read_text())['prestashop']


def release_line(ps_version):
    """The table key for a full version: 1.6.1.24 -> 1.6, 1.7.8.11 -> 1.7.8, 9.0.2 -> 9.0."""
    parts = ps_version.split('.')
    line = '.'.join(parts[:2]) if parts[0] == '1' and parts[1] == '6' else \
        '.'.join(parts[:3]) if parts[0] == '1' else '.'.join(parts[:2])
    if line not in DATA:
        raise ValueError(f'PS_VERSION {ps_version} is not in the setup compatibility table')
    return line


def check(ps_version, php_version, engine, database_version):
    """(errors, warnings) for one project's selected versions."""
    errors, warnings = [], []
    line = release_line(ps_version)
    php = '.'.join(str(php_version).split('.')[:2])
    if php not in DATA[line]['php']:
        errors.append(f"PHP {php} is not supported by PrestaShop {line} (supported: {', '.join(DATA[line]['php'])})")
    db = '.'.join(str(database_version).split('.')[:2])
    if database_version and db not in DATA[line].get(engine, []):
        warnings.append(f"{engine} {db} is outside the versions listed for PrestaShop {line}: {', '.join(DATA[line].get(engine, []))}")
    return errors, warnings
