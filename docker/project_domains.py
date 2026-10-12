"""DOMAIN as one hostname or a list: hostnames and DOMAIN_<NAME> variables, separated by spaces.

    DOMAIN_LT=demo.lt
    DOMAIN_LV=demo.lv
    DOMAIN="DOMAIN_LT DOMAIN_LV demo.ee"

The first hostname is the primary one: every existing use of DOMAIN (service host names such as
pma.<DOMAIN>, Apache, templates, after-import SQL, the default shop's safe default) keeps one hostname.
All hostnames reach the places that route a request: nginx-proxy's server_name and network aliases,
the local /etc/hosts entries, the host Nginx route and the local certificate. Setup never writes them
into the database; after-import SQL may use ${DOMAIN_<NAME>} for the variables DOMAIN names.

Setup's env parser does not interpolate while Compose does, so DOMAIN names variables instead of
using ${...}: both read the same line the same way. Names are upper case and hostnames lower case.
"""

import hashlib
import json
import re

from project_env_files import read_dotenv

HOSTNAME = re.compile(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*')
REFERENCE = re.compile(r'DOMAIN_[A-Z0-9_]+')
# Setup passes every hostname to the containers under this name (SETUP_ names are setup's own).
HOSTS_VARIABLE = 'SETUP_DOMAIN_HOSTS'


def resolve_domains(raw, values):
    """(hosts, references): DOMAIN's hostnames in order without repeats, and each DOMAIN_<NAME> it uses.

    values: the env files' entries. A single word that is not a variable name stays exactly as before.
    """
    words = str(raw or '').split()
    if len(words) == 1 and not REFERENCE.fullmatch(words[0]):
        return words, {}
    hosts, references = [], {}
    for word in words:
        if REFERENCE.fullmatch(word):
            host = str(values.get(word) or '').strip().lower()
            if not host:
                raise ValueError(f'DOMAIN names {word}, which the env files leave unset or empty')
            if not HOSTNAME.fullmatch(host):
                raise ValueError(f'{word} must be one hostname without scheme, port or path')
            references[word] = host
        elif HOSTNAME.fullmatch(word):
            host = word
        else:
            raise ValueError(f'DOMAIN lists lower-case hostnames and DOMAIN_<NAME> variables separated by '
                             f'spaces; {word!r} is neither')
        if host not in hosts:
            hosts.append(host)
    return hosts, references


def apply_domains(project, settings):
    """Resolve DOMAIN for the runner and Compose: DOMAIN becomes the primary hostname everywhere."""
    raw = str(settings.get('DOMAIN') or '')
    values = {}
    for env_file in project.env_files:
        values.update(read_dotenv(env_file))
    hosts, references = resolve_domains(raw, values)
    primary = hosts[0] if hosts else ''
    if settings.get('SQL_DOMAIN') == raw:
        settings['SQL_DOMAIN'] = primary  # its default is DOMAIN itself (project-settings.yaml)
    settings['DOMAIN'] = primary
    settings[HOSTS_VARIABLE] = ' '.join(hosts)
    settings['DOMAIN_REFERENCES'] = references
    # The process environment wins over --env-file, so Compose interpolates the resolved values too.
    project.process_env['DOMAIN'] = primary
    project.process_env[HOSTS_VARIABLE] = settings[HOSTS_VARIABLE]


def extra_hosts(settings):
    """The hostnames after the primary one."""
    primary = settings.get('DOMAIN', '')
    return [host for host in str(settings.get(HOSTS_VARIABLE) or '').split() if host != primary]


def attach_domain_aliases(project):
    """Every hostname resolves to nginx-proxy inside the project network, like DOMAIN itself."""
    hosts = str(project.settings.get(HOSTS_VARIABLE) or '').split()
    if len(hosts) < 2:
        return
    from project_ide import checked_path, replace_file
    contents = ('services:\n  "nginx-proxy":\n    networks:\n      network_app:\n        aliases: '
                + json.dumps(hosts) + '\n').encode()
    digest = hashlib.sha256(contents).hexdigest()[:20]
    path = checked_path(project, f'.generated/domain-aliases-{digest}.yaml')
    replace_file(path, contents, private=True)
    project.command += ['-f', str(path)]
