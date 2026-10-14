"""The project's CI provider: Bitbucket Pipelines or GitHub Actions.

CI_PROVIDER=bitbucket|github in env/common.env (create-project writes it) decides; without it a single
DEPLOY_GITHUB_REPOSITORY or DEPLOY_BITBUCKET_REPOSITORY does, then the origin remote (github.com means
GitHub), else Bitbucket. make pipeline-deploy needs none of this: it reads the provider from the CI
job's own environment. make deploy-variables goes to the provider's module.
"""

import os
from pathlib import Path
import sys

from project_remote import origin_url

PROVIDERS = ('bitbucket', 'github')
DEFAULT = 'bitbucket'
TEMPLATES = Path(__file__).resolve().parent.parent / 'templates/pipelines'
# The example in templates/pipelines and where it goes in the environment repository.
WORKFLOWS = {'bitbucket': ('bitbucket-pipelines.yml', 'bitbucket-pipelines.yml'),
             'github': ('github-deploy.yml', '.github/workflows/deploy.yml')}


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
    from project_bitbucket import Failure, run
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
