"""make deploy-variables: a repository's Bitbucket Deployment variables against setup's list (names only).

  make deploy-variables [ENV=prod]               which listed names the environment lacks (exit 1)
  make deploy-variables ENV=… from=FILE           create the missing ones from a dotenv file
  make deploy-variables ENV=… from=FILE replace=1 also replace the ones that exist
  make deploy-variables ENV=… names=1             the list itself (no network)

The list is the one make pipeline-deploy checks (project_env_files.deployment_variables): setup's own
secrets for the environment, DEPLOY_REQUIRED_VARIABLES, DEPLOY_OPTIONAL_VARIABLES and the
APP_ENVIRONMENT names the env files leave to the process; DEPLOY_PLAIN_VARIABLES are stored not
Secured. The repository is DEPLOY_BITBUCKET_REPOSITORY (workspace/repository) or the origin remote.
The token comes from token=PATH (owned by you, 0600) or BITBUCKET_ACCESS_TOKEN. No value is ever
printed and none is passed on a command line: values travel only in request bodies.
Exit status: 0 every listed variable is present, 1 some are missing, 2 Bitbucket failed, 64 usage,
file permission or list error.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from project_ci import Failure, NoRedirect, compare, load_list, print_names, read_token, summary
from project_env_files import file_settings
from project_environments import deployment_name
import project_remote

API = 'https://api.bitbucket.org/2.0'
SCOPES = ('Repositories: Read and Pipelines: Read (scope pipeline) to check; apply also needs '
          'Pipelines: Edit variables (scope pipeline:variable)')
MAX_PAGES = 100
KINDS = ('secured', 'plain')


def repository(directory, values):
    """workspace/repository: DEPLOY_BITBUCKET_REPOSITORY, else the origin remote of this checkout."""
    return project_remote.repository(directory, values, 'DEPLOY_BITBUCKET_REPOSITORY', 'bitbucket.org', 'Bitbucket',
                                     'workspace', Failure)


class Bitbucket:
    def __init__(self, api, token, repository):
        self.api, self.token, self.repository = api.rstrip('/'), token, repository
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, url, body=None):
        """(status, decoded JSON or {}); the token goes in a header, a value only in the body."""
        headers = {'Authorization': f'Bearer {self.token}', 'Accept': 'application/json'}
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
            raise Failure(f'Bitbucket answered 401 to {what}: the token is wrong, expired or revoked. Use a repository '
                          f'access token of {self.repository} with {SCOPES}.', 2)
        if status == 403:
            message = error_message(data)
            raise Failure(f'Bitbucket answered 403 to {what}: the token lacks a scope. It needs {SCOPES}.'
                          + (f'\nBitbucket: {message}' if message else ''), 2)
        if status == 404:
            raise Failure(f'Bitbucket answered 404 to {what}: no repository {self.repository} (or no such environment) '
                          'for this token.', 2)

    def fetch(self, url, kind):
        """Every page of a Bitbucket list."""
        values = []
        for _ in range(MAX_PAGES):
            status, data = self.request('GET', url)
            self.refused(status, f'GET {kind}', data)
            if not 200 <= status < 300:
                raise Failure(f'Bitbucket answered {status} to GET {kind}: {error_message(data)}', 2)
            values += data.get('values', [])
            url = data.get('next') or ''
            if not url:
                return values
            # The next link would carry the token along: follow it only to the same API.
            if not url.startswith(self.api + '/'):
                raise Failure(f'Bitbucket returned a next page outside {self.api}: {url}', 2)
        raise Failure(f'more than {MAX_PAGES} pages of {kind}', 2)


def error_message(data):
    error = data.get('error') if isinstance(data, dict) else None
    return str((error or {}).get('message') or '')[:300] if isinstance(error, dict) else ''


def environment_url(client, base, wanted):
    environments = client.fetch(f'{base}/environments', 'environments')
    for field in (lambda e: e.get('name', ''), lambda e: (e.get('environment_type') or {}).get('name', '')):
        matches = [e['uuid'] for e in environments if field(e).casefold() == wanted.casefold()]
        if len(matches) == 1:
            return f'{base}/deployments_config/environments/{urllib.parse.quote(matches[0], safe="")}/variables'
    names = ', '.join(e.get('name', '') for e in environments) or 'none'
    raise Failure(f'no Bitbucket deployment environment named {wanted} (the repository has: {names})', 2)


def run(directory, environment, source, replace, token_file, names_only, environ, out):
    environment, listed, values = load_list(directory, environment, source, replace)
    if names_only:
        print_names(listed, out, KINDS)
        return 0
    settings, _ = file_settings(directory, environment)
    slug = repository(directory, settings)
    token = read_token(token_file, environ, ('BITBUCKET_ACCESS_TOKEN',))
    if not token:
        raise Failure('no token: set BITBUCKET_ACCESS_TOKEN or pass token=PATH (a file only you can read) with a '
                      f'repository access token of {slug}: {SCOPES}')
    client = Bitbucket(environ.get('BITBUCKET_API_URL') or API, token, slug)
    url = environment_url(client, f'{client.api}/repositories/{slug}', deployment_name(environment))
    label = f'Bitbucket {slug}, environment {deployment_name(environment)}'

    def present():
        return {v['key']: {'secured': bool(v.get('secured')), 'uuid': v.get('uuid', '')}
                for v in client.fetch(url, 'variables')}

    if values is None:
        return 1 if compare(listed, present(), label, out, 'Bitbucket', KINDS) else 0

    print(f'{label}: apply from {source}', file=out)
    existing = present()
    actions, kept = [], 0
    for name, variable in listed.items():
        if name in existing and not replace:
            kept += 1
        elif name not in values or values[name] == '':
            why = f'not in {source}' if name not in values else f'empty in {source}'
            print(f"  {'MISSING' if name not in existing else 'kept':<9} {name} ({why})", file=out)
        else:
            actions.append(('replace' if name in existing else 'create', name, variable['secured']))
    for name in values:
        if name not in listed:
            print(f'  skipped   {name} (in {source} but not in the list)', file=out)
    if kept:
        print(f'  {kept} existing variables kept (replace=1 overwrites them)', file=out)
    failed = 0
    for action, name, secured in actions:
        body = {'key': name, 'value': values[name], 'secured': secured}
        if action == 'create':
            status, data = client.request('POST', url, body)
        else:
            status, data = client.request('PUT', f"{url}/{urllib.parse.quote(existing[name]['uuid'], safe='')}", body)
        client.refused(status, f'{action} {name}', data)
        kind = 'secured' if secured else 'plain'
        if 200 <= status < 300:
            print(f'  {action + "d":<9} {name} ({kind})', file=out)
        else:
            failed += 1
            print(f'  {"FAILED":<9} {name}: Bitbucket answered {status} to {action}: {error_message(data)}', file=out)
    if failed:
        raise Failure(f'{failed} requests failed', 2)
    # The result as Bitbucket now has it.
    now = present()
    missing = sum(1 for name in listed if name not in now)
    mismatched = sum(1 for name in listed if name in now and now[name]['secured'] != listed[name]['secured'])
    print(f'Now: {summary(len(listed) - missing, missing, mismatched, KINDS)}', file=out)
    return 1 if missing else 0
