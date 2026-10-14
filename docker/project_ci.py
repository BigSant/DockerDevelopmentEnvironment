"""The project's CI provider: Bitbucket Pipelines or GitHub Actions.

CI_PROVIDER=bitbucket|github in env/common.env (create-project writes it) decides; without it a single
DEPLOY_GITHUB_REPOSITORY or DEPLOY_BITBUCKET_REPOSITORY does, then the origin remote (github.com means
GitHub), else Bitbucket. make pipeline-deploy needs none of this: it reads the provider from the CI
job's own environment. make deploy-variables goes to the provider's module; what both providers share (the
list and values-file checks, the token file, the check report) is here.
"""

import os
from pathlib import Path
import stat
import sys
import urllib.request

from project_env_files import DEPLOYMENT_NAMES, deployment_variables, read_dotenv
from project_remote import origin_url

PROVIDERS = ('bitbucket', 'github')
DEFAULT = 'bitbucket'
TEMPLATES = Path(__file__).resolve().parent.parent / 'templates/pipelines'
# The example in templates/pipelines and where it goes in the environment repository.
WORKFLOWS = {'bitbucket': ('bitbucket-pipelines.yml', 'bitbucket-pipelines.yml'),
             'github': ('github-deploy.yml', '.github/workflows/deploy.yml')}


# make deploy-variables: what both providers share. Each provider module keeps its API and its writes.
class Failure(Exception):
    def __init__(self, message, status=64):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would carry the token to another address: report it instead."""

    def redirect_request(self, *args, **kwargs):
        return None


def private(path, label):
    """A file only its owner can read: the token and the values file."""
    try:
        info = os.stat(path)
    except OSError as error:
        raise Failure(f'{label} {path}: {error.strerror}') from None
    if not stat.S_ISREG(info.st_mode):
        raise Failure(f'{label} {path} is not a regular file')
    if info.st_uid != os.geteuid():
        raise Failure(f'{label} {path} must be owned by you (uid {os.geteuid()}), not uid {info.st_uid}')
    if info.st_mode & 0o077:
        raise Failure(f'{label} {path} is readable by others (mode {stat.S_IMODE(info.st_mode):04o}): chmod 600 {path}')


def read_token(token_file, environ, names):
    """The API token: from token_file (a file only you can read; whitespace dropped), else the first of names set in
    environ; '' when there is none (the provider says which token it needs)."""
    if token_file:
        private(token_file, 'Token file')
        return ''.join(Path(token_file).read_text().split())
    return next((environ[name] for name in names if environ.get(name)), '')


def compare(listed, present, label, out, provider, kinds):
    """The check report; returns the number of missing names.

    present: {name: {'secured': bool, 'both': bool}}; kinds names a secured and a plain one.
    """
    missing = mismatched = 0
    print(f'{label}: {len(listed)} listed variables', file=out)
    for name, variable in listed.items():
        kind = kinds[0] if variable['secured'] else kinds[1]
        if name not in present:
            missing += 1
            print(f"  MISSING   {name} ({kind}{', required' if variable['required'] else ''})", file=out)
        elif present[name].get('both'):
            mismatched += 1
            print(f'  present   {name}: both a {kinds[0]} and a {kinds[1]} in {provider} (the {kinds[0]} is used), '
                  f'the list says {kind}', file=out)
        elif present[name]['secured'] != variable['secured']:
            mismatched += 1
            have = kinds[0] if present[name]['secured'] else kinds[1]
            print(f'  present   {name}: {have} in {provider}, the list says {kind}', file=out)
        else:
            print(f'  present   {name}', file=out)
    for name in present:
        if name not in listed:
            print(f'  extra     {name} (not in the list)', file=out)
    print(summary(len(listed) - missing, missing, mismatched, kinds), file=out)
    return missing


def summary(present, missing, mismatched, kinds):
    return (f'{present} present, {missing} missing, {mismatched} {kinds[0]}/{kinds[1]} mismatch'
            + ('es' if mismatched != 1 else ''))


def load_list(directory, environment, source, replace):
    """(ENV, the listed variables, the values of from=FILE or None): the checks every provider shares."""
    # The default is production, as in the deploy step that runs it most.
    environment = 'prod' if environment == 'local' else environment
    if environment not in DEPLOYMENT_NAMES:
        raise Failure('Deployment variables belong to ENV=stage or ENV=prod')
    if replace and not source:
        raise Failure('replace=1 belongs to from=FILE (apply)')
    try:
        listed = deployment_variables(directory, environment)
        values = None
        if source:
            private(source, 'Values file')
            values = read_dotenv(source, strict=True)
    except (ValueError, OSError) as error:
        raise Failure(str(error)) from None
    return environment, listed, values


def print_names(listed, out, kinds):
    for name, variable in listed.items():
        print(f"{name} {kinds[0] if variable['secured'] else kinds[1]} "
              f"{'required' if variable['required'] else 'optional'} ({variable['source']})", file=out)


def ci_provider(directory, values):
    """bitbucket or github for the project in directory, from its settings or its origin remote."""
    configured = str(values.get('CI_PROVIDER') or '').strip()
    if configured:
        if configured not in PROVIDERS:
            raise ValueError(f"CI_PROVIDER must be {' or '.join(PROVIDERS)}, not {configured!r}")
        return configured
    github = str(values.get('DEPLOY_GITHUB_REPOSITORY') or '').strip()
    bitbucket = str(values.get('DEPLOY_BITBUCKET_REPOSITORY') or '').strip()
    if bool(github) != bool(bitbucket):
        return 'github' if github else 'bitbucket'
    if 'github.com' in origin_url(directory):
        return 'github'
    return DEFAULT


def deploy_variables(directory, environment='local', source=None, replace=False, token_file=None, names_only=False,
                     environ=None, out=None, err=None):
    """make deploy-variables for the project's CI provider; returns the exit status."""
    from project_bitbucket import run
    from project_env_files import file_settings
    import project_github
    environ = os.environ if environ is None else environ
    out, err = out or sys.stdout, err or sys.stderr
    directory = Path(directory).resolve()
    try:
        try:
            values, _ = file_settings(directory, 'prod' if environment == 'local' else environment)
            provider = ci_provider(directory, values)
        except (ValueError, OSError) as error:
            raise Failure(str(error)) from None
        command = project_github.deploy_variables if provider == 'github' else run
        return command(directory, environment, source, replace, token_file, names_only, environ, out)
    except Failure as failure:
        print(f'deploy-variables: {failure}', file=err)
        return failure.status
