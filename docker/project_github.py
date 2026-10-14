"""make deploy-variables for GitHub Actions: a repository's environment secrets and variables against setup's list.

  make deploy-variables [ENV=prod]               which listed names the environment lacks (exit 1)
  make deploy-variables ENV=… from=FILE           create the missing ones from a dotenv file
  make deploy-variables ENV=… from=FILE replace=1 also replace the ones that exist
  make deploy-variables ENV=… names=1             the list itself (no network)

The list is the one make pipeline-deploy checks (project_env_files.deployment_variables). On GitHub a
DEPLOY_PLAIN_VARIABLES name is an environment *variable* (readable in the settings and the log), every
other name an environment *secret*; the GitHub environment is staging (ENV=stage) or production
(ENV=prod). The repository is DEPLOY_GITHUB_REPOSITORY (owner/repository) or the origin remote. The
token comes from token=PATH (owned by you, 0600), GH_TOKEN or GITHUB_TOKEN.

Reading and variables use the REST API (urllib). A secret must be encrypted with the environment's
public key (a libsodium sealed box), which the standard library cannot do: the official GitHub CLI
does it, `gh secret set NAME --env ENV --repo OWNER/REPO` with the value on its standard input and the
token in GH_TOKEN. No value is ever printed and none is passed on a command line.
Exit status: 0 every listed variable is present, 1 some are missing, 2 GitHub or gh failed, 64 usage,
file permission or list error.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from project_bitbucket import Failure, NoRedirect, compare, load_list, print_names, private, summary
from project_env_files import DEPLOYMENT_NAMES
import project_remote

API = 'https://api.github.com'
KINDS = ('secret', 'variable')
PERMISSIONS = ('a fine-grained personal access token for the repository with the repository permission '
               'Environments: Read-only to check, Environments: Read and write to apply (Metadata: Read-only '
               'comes with it), or a classic token with the repo scope')
MAX_PAGES = 100
# The largest page each list allows.
PER_PAGE = {'secrets': 100, 'variables': 30, 'environments': 100}
# Tokens of the caller that gh would prefer to the one given here.
GH_TOKENS = ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN')


def repository(directory, values):
    """owner/repository: DEPLOY_GITHUB_REPOSITORY, else the origin remote of this checkout."""
    return project_remote.repository(directory, values, 'DEPLOY_GITHUB_REPOSITORY', 'github.com', 'GitHub', 'owner',
                                     Failure)


def error_message(data):
    return str(data.get('message') or '')[:300] if isinstance(data, dict) else ''


class GitHub:
    def __init__(self, api, token, repository, environment):
        self.api, self.token, self.repository, self.environment = api.rstrip('/'), token, repository, environment
        self.base = f'/repos/{repository}/environments/{urllib.parse.quote(environment, safe="")}'
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, body=None):
        """(status, decoded JSON or {}); the token goes in a header, a value only in the body."""
        url = self.api + path
        headers = {'Authorization': f'Bearer {self.token}', 'Accept': 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'setup-deploy-variables'}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        try:
            with self.opener.open(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=60) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, payload = error.code, error.read()
        except (urllib.error.URLError, OSError) as error:
            raise Failure(f'could not reach {url}: {getattr(error, "reason", error)}', 2) from None
        try:
            return status, json.loads(payload or b'{}')
        except ValueError:
            return status, {}

    def refused(self, status, what, data):
        """Stops on an answer every later request would get as well."""
        if status == 401:
            raise Failure(f'GitHub answered 401 to {what}: the token is wrong, expired or revoked. Use {PERMISSIONS}.', 2)
        if status == 403:
            message = error_message(data)
            raise Failure(f'GitHub answered 403 to {what}: the token lacks a permission. It needs {PERMISSIONS}.'
                          + (f'\nGitHub: {message}' if message else ''), 2)
        if status == 404:
            raise Failure(f'GitHub answered 404 to {what}: no repository {self.repository} or no environment '
                          f'{self.environment} for this token (GitHub answers 404 when a token may not see a private '
                          f'repository). {self.environments()}', 2)

    def environments(self):
        """The repository's environment names for the 404 message, when the token may list them."""
        status, data = self.request('GET', f'/repos/{self.repository}/environments?per_page={PER_PAGE["environments"]}')
        if status != 200 or not isinstance(data.get('environments'), list):
            return 'Create the environment in Settings -> Environments.'
        names = ', '.join(str(e.get('name', '')) for e in data['environments']) or 'none'
        return f'The repository has the environments: {names}.'

    def fetch(self, kind):
        """Every page of the environment's secrets or variables: {name: variable}."""
        found = {}
        for page in range(1, MAX_PAGES + 1):
            status, data = self.request('GET', f'{self.base}/{kind}?per_page={PER_PAGE[kind]}&page={page}')
            self.refused(status, f'GET {kind}', data)
            if not 200 <= status < 300:
                raise Failure(f'GitHub answered {status} to GET {kind}: {error_message(data)}', 2)
            items = data.get(kind) or []
            for item in items:
                found[item['name']] = item
            if not items or len(found) >= int(data.get('total_count') or 0):
                return found
        raise Failure(f'more than {MAX_PAGES} pages of {kind}', 2)

    def present(self):
        """{name: {'secured', 'both'}}: a secret is secured, a variable not; a name in both is used as the secret."""
        secrets, variables = self.fetch('secrets'), self.fetch('variables')
        present = {name: {'secured': False, 'both': False} for name in variables}
        present.update({name: {'secured': True, 'both': name in variables} for name in secrets})
        return present, set(secrets), set(variables)


def token_of(token_file, environ, slug):
    if token_file:
        private(token_file, 'Token file')
        token = ''.join(Path(token_file).read_text().split())
    else:
        token = environ.get('GH_TOKEN') or environ.get('GITHUB_TOKEN') or ''
    if not token:
        raise Failure(f'no token: set GH_TOKEN (or GITHUB_TOKEN) or pass token=PATH (a file only you can read) with '
                      f'{PERMISSIONS} ({slug})')
    return token


def set_secret(gh, client, name, value, environ):
    """gh secret set: gh encrypts the value with the environment's public key; it reads it from stdin."""
    child = {key: item for key, item in environ.items() if key not in GH_TOKENS}
    child.update({'GH_TOKEN': client.token, 'GH_PROMPT_DISABLED': '1', 'GH_NO_UPDATE_NOTIFIER': '1', 'NO_COLOR': '1'})
    try:
        result = subprocess.run([gh, 'secret', 'set', name, '--env', client.environment, '--repo', client.repository],
                                input=value, capture_output=True, text=True, env=child, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        return f'gh did not run: {error}'
    if result.returncode:
        lines = [line for line in (result.stderr or result.stdout or '').splitlines() if line.strip()]
        return f'gh exited {result.returncode}' + (f': {lines[-1].strip()[:300]}' if lines else '')
    return ''


def deploy_variables(directory, environment, source, replace, token_file, names_only, environ, out):
    environment, listed, values = load_list(directory, environment, source, replace)
    if names_only:
        print_names(listed, out, KINDS)
        return 0
    from project_env_files import file_settings
    settings, _ = file_settings(directory, environment)
    slug = repository(directory, settings)
    token = token_of(token_file, environ, slug)
    name = DEPLOYMENT_NAMES[environment]
    client = GitHub(environ.get('GITHUB_API_URL') or API, token, slug, name)
    label = f'GitHub {slug}, environment {name}'

    if values is None:
        present, _, _ = client.present()
        return 1 if compare(listed, present, label, out, 'GitHub', KINDS) else 0

    print(f'{label}: apply from {source}', file=out)
    _, secrets, variables = client.present()
    actions, kept = [], 0
    for key, variable in listed.items():
        exists = key in (secrets if variable['secured'] else variables)
        value = values.get(key, '')
        if exists and not replace:
            kept += 1
        elif value == '':
            why = f'not in {source}' if key not in values else f'empty in {source}'
            print(f"  {'MISSING' if not exists else 'kept':<9} {key} ({why})", file=out)
        elif variable['secured'] and value != value.rstrip('\r\n'):
            # gh drops a final line break of what it reads.
            print(f"  {'MISSING' if not exists else 'kept':<9} {key} (its value ends with a line break, which gh "
                  'would drop: set it in Settings -> Environments)', file=out)
        else:
            actions.append(('replace' if exists else 'create', key, variable['secured']))
    for key in values:
        if key not in listed:
            print(f'  skipped   {key} (in {source} but not in the list)', file=out)
    if kept:
        print(f'  {kept} existing variables kept (replace=1 overwrites them)', file=out)
    gh = shutil.which('gh', path=environ.get('PATH', os.defpath))
    if any(secured for _, _, secured in actions) and not gh:
        raise Failure(f'writing environment secrets needs the GitHub CLI gh (https://cli.github.com): it encrypts each '
                      'value with the environment\'s public key (libsodium sealed box). Install it, or set the secrets '
                      f'in Settings -> Environments -> {name}. Nothing was written.')
    failed = 0
    for action, key, secured in actions:
        if secured:
            error = set_secret(gh, client, key, values[key], environ)
        else:
            body = {'name': key, 'value': values[key]}
            if action == 'create':
                status, data = client.request('POST', f'{client.base}/variables', body)
            else:
                status, data = client.request('PATCH', f'{client.base}/variables/{urllib.parse.quote(key, safe="")}', body)
            client.refused(status, f'{action} {key}', data)
            error = '' if 200 <= status < 300 else f'GitHub answered {status} to {action}: {error_message(data)}'
        if error:
            failed += 1
            print(f'  {"FAILED":<9} {key}: {error}', file=out)
        else:
            print(f'  {action + "d":<9} {key} ({KINDS[0] if secured else KINDS[1]})', file=out)
    if failed:
        raise Failure(f'{failed} writes failed', 2)
    # The result as GitHub now has it.
    now, _, _ = client.present()
    missing = sum(1 for key in listed if key not in now)
    mismatched = sum(1 for key in listed if key in now and (now[key]['both'] or now[key]['secured'] != listed[key]['secured']))
    print(f'Now: {summary(len(listed) - missing, missing, mismatched, KINDS)}', file=out)
    return 1 if missing else 0
