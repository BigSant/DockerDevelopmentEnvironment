"""PrestaShop shop keys: which env keys a version needs, and local generation for bootstrap."""
import base64
import hashlib
import os
from pathlib import Path
import re
import secrets
import string
import subprocess
import tempfile

KEY_NAMES = ('PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_RIJNDAEL_KEY', 'PS_RIJNDAEL_IV',
             'PS_NEW_COOKIE_KEY', 'PS_SECRET', 'PS_API_PRIVATE_KEY', 'PS_API_PUBLIC_KEY')
# env key -> (parameters.php key, settings.inc.php constant); mirrors runtime/prestashop-keys.php
FILE_NAMES = {'PS_COOKIE_KEY': ('cookie_key', '_COOKIE_KEY_'), 'PS_COOKIE_IV': ('cookie_iv', '_COOKIE_IV_'),
              'PS_RIJNDAEL_KEY': (None, '_RIJNDAEL_KEY_'), 'PS_RIJNDAEL_IV': (None, '_RIJNDAEL_IV_'),
              'PS_NEW_COOKIE_KEY': ('new_cookie_key', None), 'PS_SECRET': ('secret', None),
              'PS_API_PRIVATE_KEY': ('api_private_key', None), 'PS_API_PUBLIC_KEY': ('api_public_key', None)}


def is_prestashop(project):
    """The profile rule: only PROFILE=prestashop (or ps) reads or changes PrestaShop things; anything else is generic."""
    settings = getattr(project, 'settings', project)
    return settings.get('PROFILE') in ('ps', 'prestashop')


def generic_refusal(what):
    return ValueError(f'{what} belongs to PrestaShop; set PROFILE=prestashop, or remove it from this generic project')


def legacy(ps_version):
    return ps_version.startswith('1.6')


def required_keys(ps_version):
    if legacy(ps_version):
        return ['PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_RIJNDAEL_KEY', 'PS_RIJNDAEL_IV']
    keys = ['PS_COOKIE_KEY', 'PS_COOKIE_IV', 'PS_NEW_COOKIE_KEY', 'PS_SECRET']
    if int(ps_version.split('.')[0]) >= 9:
        keys += ['PS_API_PRIVATE_KEY', 'PS_API_PUBLIC_KEY']
    return keys


def shop_code_missing(web_directory):
    """No checkout yet (missing or empty web directory): PrestaShop steps wait for the clone instead of failing."""
    path = Path(web_directory)
    return not path.is_dir() or next(path.iterdir(), None) is None


SHOP_CODE_MISSING = 'No shop code in {} yet: PrestaShop preparation skipped; clone the shop repository there and run make up again.'


def config_file(web_directory, ps_version):
    return Path(web_directory) / ('config/settings.inc.php' if legacy(ps_version) else 'app/config/parameters.php')


def _random(length):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _defuse_key():
    """A Defuse\\Crypto\\Key in its ASCII-safe form, as PrestaShop's installer stores it."""
    header, key = b'\xde\xf0\x00\x00', os.urandom(32)
    return (header + key + hashlib.sha256(header + key).digest()).hex()


def _rsa_pair():
    with tempfile.TemporaryDirectory() as directory:
        private = Path(directory) / 'private.pem'
        subprocess.run(['openssl', 'genrsa', '-out', str(private), '2048'], check=True, capture_output=True)
        public = subprocess.run(['openssl', 'rsa', '-in', str(private), '-pubout'], check=True,
                                capture_output=True).stdout
        return (base64.b64encode(private.read_bytes()).decode(), base64.b64encode(public).decode())


def existing_keys(web_directory, ps_version):
    """Keys already in the shop configuration file, as env values (PEM keys base64 encoded)."""
    path = config_file(web_directory, ps_version)
    if not path.is_file():
        return {}
    text = path.read_text(errors='replace')
    found = {}
    for env, (parameter, constant) in FILE_NAMES.items():
        if legacy(ps_version) and constant:
            match = re.search(r"define\(\s*['\"]" + constant + r"['\"]\s*,\s*'((?:[^'\\]|\\.)*)'", text)
        elif not legacy(ps_version) and parameter:
            match = re.search(r"'" + parameter + r"'\s*=>\s*'((?:[^'\\]|\\.)*)'", text)
        else:
            continue
        if match:
            value = match[1].replace("\\'", "'").replace('\\\\', '\\')
            if env.startswith('PS_API_'):
                value = base64.b64encode(value.replace('%%', '%').encode()).decode()
            elif not legacy(ps_version):
                value = value.replace('%%', '%')
            found[env] = value
    return found


def generated_keys(ps_version):
    """Fresh local keys in the formats the PrestaShop installer uses."""
    if legacy(ps_version):
        return {'PS_COOKIE_KEY': _random(56), 'PS_COOKIE_IV': _random(8), 'PS_RIJNDAEL_KEY': _random(32),
                'PS_RIJNDAEL_IV': base64.b64encode(os.urandom(16)).decode()}
    keys = {'PS_COOKIE_KEY': _random(64), 'PS_COOKIE_IV': _random(32), 'PS_NEW_COOKIE_KEY': _defuse_key(),
            'PS_SECRET': _random(64)}
    if int(ps_version.split('.')[0]) >= 9:
        keys['PS_API_PRIVATE_KEY'], keys['PS_API_PUBLIC_KEY'] = _rsa_pair()
    return keys


def local_key_values(web_directory, ps_version, present):
    """Missing keys for a private local/test env: from the shop file when it has them, else new."""
    wanted = [key for key in required_keys(ps_version) if key not in present]
    if not wanted:
        return {}
    source = existing_keys(web_directory, ps_version)
    fresh = None
    values = {}
    for key in wanted:
        if key in source:
            values[key] = source[key]
        else:
            fresh = fresh or generated_keys(ps_version)
            values[key] = fresh[key]
    return values
