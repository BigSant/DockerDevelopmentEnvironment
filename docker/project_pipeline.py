"""The CI deploy step: make pipeline-deploy [ENV=stage|prod] in the build root.

Two CI providers run it the same way. Bitbucket Pipelines: a deployment step (deployment: Staging or
Production) whose Deployment variables are the step's process environment. GitHub Actions: a job with
environment: staging or production that passes the environment's name in DEPLOY_ENVIRONMENT (GitHub
has no built-in variable for it) and its secrets and configuration variables as JSON in
DEPLOY_SECRETS_JSON (toJSON(secrets)) and DEPLOY_VARIABLES_JSON (toJSON(vars)); the step expands
each name the job's own environment lacks into its process environment and never prints a value.

The step runs on a self-hosted shell runner of the build server; the work happens in the persistent
build root (its environment checkout, the directory this runs in), not in the runner's own clone,
which only identifies the commit (BITBUCKET_COMMIT, GITHUB_SHA). Two layouts:

  one repository (asana.local): the code is part of the environment repository (app/public has no
      .git of its own). The checkout is the commit being deployed: REF (APP_REF), else the pipeline
      commit; ROLLBACK=yes checks out the commit setup's rollback returns to. When the build root is
      also DEPLOY_PATH the running containers mount its config, so it must always match the code.
  separate code repository (Forsena): app/public is the shop's own repository. The environment
      checkout follows the pipeline commit; REF (SHOP_REF) names the code commit to deploy and is
      required unless ROLLBACK=yes.

Before anything changes: the required Deployment variables (setup's and DEPLOY_REQUIRED_VARIABLES)
must be set. Then env/<env>.env is written from the deployed commit's env/<env>.env.example plus the
deploy target setup-server registered in the build root (target.env: DEPLOY_CONTEXT, DEPLOY_SSH,
DEPLOY_PATH, APP_UID, APP_GID; the example must not name them). Without target.env the example names
the target itself (plus the runner account's APP_UID/APP_GID when DEPLOY_CONTEXT=default deploys to
this same host).
IMAGES=yes runs make release + make deploy-images, then make deploy (BACKUP=yes: backup=1) or
make deploy rollback=1. A failure returns the checkout and env file to the previously live commit,
unless this run already restarted the containers on the new images: then nothing is undone and the
step prints the way back.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from project_env_files import (TARGET_KEYS, file_settings, names_setting, needed_setup_version, read_dotenv,
                               target_file, target_settings)

SETUP_ROOT = Path(__file__).resolve().parent.parent
# The deployment environment names of both providers: staging -> ENV=stage, production -> ENV=prod.
DEPLOYMENT_ENVIRONMENTS = {'staging': 'stage', 'production': 'prod'}
BITBUCKET_ENVIRONMENTS = DEPLOYMENT_ENVIRONMENTS
DEPLOYMENT_NAMES = {setup: name for name, setup in DEPLOYMENT_ENVIRONMENTS.items()}
BITBUCKET_NAMES = DEPLOYMENT_NAMES
# GitHub Actions: the job's secrets and configuration variables, as toJSON(secrets) / toJSON(vars).
SECRETS_JSON, VARIABLES_JSON = 'DEPLOY_SECRETS_JSON', 'DEPLOY_VARIABLES_JSON'
# Names the expansion never sets: GitHub's own (GITHUB_TOKEN is in the secrets context) and the runner's.
RESERVED_PREFIXES = ('GITHUB_', 'RUNNER_', 'ACTIONS_')
ENVIRONMENT_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# One ref variable; the per-layout names of the former project scripts keep working.
REF_VARIABLES = ('REF', 'APP_REF', 'SHOP_REF')
SWITCHES = (('IMAGES', 'no'), ('ROLLBACK', 'no'), ('BACKUP', 'no'))
USAGE = 64
# The step's make variables would leak into the make steps it starts; Bitbucket's DOCKER_HOST points
# at its cloud Docker service and would override DEPLOY_CONTEXT (Docker reads it first). The JSON of
# every GitHub secret stays in this process: the steps get the expanded names only.
DROPPED_VARIABLES = ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES', 'DOCKER_HOST', SECRETS_JSON, VARIABLES_JSON)


class Refusal(Exception):
    """A check failed; status 64 (usage) unless a step's own status is passed on."""

    def __init__(self, message, status=USAGE):
        super().__init__(message)
        self.status = status


class SetupTooOld(Refusal):
    """The deployed commit needs a newer setup (SETUP_MIN_VERSION)."""

    def __init__(self, wanted, current):
        super().__init__(f'This project needs setup {wanted} or newer (SETUP_MIN_VERSION in env/common.env); '
                         f'the setup at {SETUP_ROOT} is {current}')
        self.wanted = wanted


class MissingVariables(Refusal):
    """Required Deployment variables are empty."""


class StepFailed(Refusal):
    """A make step failed; it has printed its own error."""


class Provider:
    """What differs between the CI providers: where the environment name, the commit and the secrets come from."""

    def __init__(self, label, environment, commit, settings, outside, rerun):
        self.label, self.environment, self.commit = label, environment, commit
        self.settings, self.outside, self.rerun = settings, outside, rerun


BITBUCKET = Provider(
    'Bitbucket', 'BITBUCKET_DEPLOYMENT_ENVIRONMENT', 'BITBUCKET_COMMIT',
    'Deployment variables missing or empty (Repository settings -> Deployments -> {name})',
    'Run make pipeline-deploy from a Bitbucket deployment step (deployment: Staging or Production); '
    'BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set',
    'run the pipeline on commit {commit}')
GITHUB = Provider(
    'GitHub', 'DEPLOY_ENVIRONMENT', 'GITHUB_SHA',
    'Environment secrets or variables missing or empty (Settings -> Environments -> {name})',
    'Run make pipeline-deploy from a GitHub Actions job with environment: staging or production that passes '
    'the same name in DEPLOY_ENVIRONMENT (templates/pipelines/github-deploy.yml); DEPLOY_ENVIRONMENT is not set',
    'run the workflow from a branch or tag at commit {commit}')
NO_PROVIDER = Provider(
    'CI', '', '', '',
    'Run make pipeline-deploy from a Bitbucket deployment step or a GitHub Actions deployment job; neither '
    'BITBUCKET_DEPLOYMENT_ENVIRONMENT nor GITHUB_ACTIONS is set', '')


def provider_of(environ):
    """GitHub Actions (GITHUB_ACTIONS=true), Bitbucket Pipelines (any BITBUCKET_ variable), else none."""
    if environ.get('GITHUB_ACTIONS') == 'true':
        return GITHUB
    if any(key.startswith('BITBUCKET_') for key in environ):
        return BITBUCKET
    return NO_PROVIDER


def expand_json(environ):
    """Adds the names of DEPLOY_VARIABLES_JSON and DEPLOY_SECRETS_JSON that environ lacks; returns the
    names that are both a secret and a variable (the secret is used).

    A name the job's own environment sets (even empty) wins, so the workflow's inputs (REF, IMAGES, ...)
    and the runner's PATH or HOME cannot be replaced by a secret; GITHUB_*, RUNNER_* and ACTIONS_* names
    (GITHUB_TOKEN among the secrets) are never set. No value or part of the JSON is ever printed.
    """
    present, found, both = set(environ), {}, []
    for key, kind in ((VARIABLES_JSON, 'variable'), (SECRETS_JSON, 'secret')):
        text = environ.get(key, '')
        if not text.strip():
            continue
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            source = 'secrets' if kind == 'secret' else 'vars'
            raise Refusal(f'{key} is not a JSON object: pass ${{{{ toJSON({source}) }}}} in the workflow')
        for name, value in data.items():
            if (not isinstance(value, str) or not ENVIRONMENT_NAME.fullmatch(name) or name in present
                    or name.upper().startswith(RESERVED_PREFIXES)):
                continue
            if name in found and found[name][0] != kind:
                both.append(name)
            found[name] = (kind, value)
    environ.update({name: value for name, (_, value) in found.items()})
    return both


def deployment_variables(directory, environment):
    """{name: {'required', 'secured', 'source'}}: the Deployment variables one environment needs.

    setup's own: DATABASE_PASSWORD, DATABASE_ROOT_PASSWORD and the PrestaShop keys of PS_VERSION
    (required); DATABASE_MONITOR_PASSWORD (metrics or dbmonitor), METRICS_TOKEN (metrics) and
    DBMONITOR_TOKEN (dbmonitor) (optional: the IP allowlist guards both endpoints), <TOOL>_HTPASSWD of the active
    pma, mailpit, cron and backup (optional Basic-auth on top of their allowlist). The project's:
    DEPLOY_REQUIRED_VARIABLES, DEPLOY_OPTIONAL_VARIABLES and every APP_ENVIRONMENT name the env files
    leave to the process.
    A name the env files set is not a Deployment variable (the file wins); one they leave blank hides
    the Deployment variable, which only setup's own secrets survive (project.PROCESS_SECRETS).
    """
    from project import PROCESS_SECRETS, TOOL_PASSWORDS
    from project_prestashop import required_keys
    values, mentioned = file_settings(directory, environment)
    services = str(values.get('SERVICES') or '').split()
    setup_required = ['DATABASE_PASSWORD', 'DATABASE_ROOT_PASSWORD']
    version = str(values.get('PS_VERSION') or '')
    if values.get('PROFILE') in ('ps', 'prestashop') and re.match(r'\d+\.\d', version):
        setup_required += required_keys(version)
    setup_optional = (['DATABASE_MONITOR_PASSWORD'] if {'metrics', 'dbmonitor'} & set(services) else []) + \
        (['METRICS_TOKEN'] if 'metrics' in services else []) + (['DBMONITOR_TOKEN'] if 'dbmonitor' in services else []) + \
        [password for tool, password in TOOL_PASSWORDS.items() if tool in services]
    groups = [('setup', setup_required, True),
              ('DEPLOY_REQUIRED_VARIABLES', names_setting(values, 'DEPLOY_REQUIRED_VARIABLES'), True),
              ('setup', setup_optional, False),
              ('DEPLOY_OPTIONAL_VARIABLES', names_setting(values, 'DEPLOY_OPTIONAL_VARIABLES'), False),
              ('APP_ENVIRONMENT', [name for name in names_setting(values, 'APP_ENVIRONMENT') if name not in mentioned], False)]
    plain = names_setting(values, 'DEPLOY_PLAIN_VARIABLES')
    listed, hidden = {}, []
    for source, names, required in groups:
        for name in names:
            if name in listed:
                continue
            if name in mentioned and mentioned[name] != '':
                continue
            if name in mentioned and name not in PROCESS_SECRETS:
                hidden.append(name)
                continue
            listed[name] = {'required': required, 'secured': name not in plain, 'source': source}
    if hidden:
        raise ValueError(f"env/common.env or env/{environment}.env.example names {', '.join(hidden)} with an empty "
                         "value, which hides the Deployment variable from the containers: remove the line (only "
                         "setup's own secrets, such as DATABASE_PASSWORD, may stay blank)")
    candidates = {name for _, names, _ in groups for name in names}
    unknown = [name for name in plain if name not in candidates]
    if unknown:
        raise ValueError(f"DEPLOY_PLAIN_VARIABLES names {', '.join(unknown)}, which no list names "
                         "(DEPLOY_REQUIRED_VARIABLES, DEPLOY_OPTIONAL_VARIABLES, APP_ENVIRONMENT)")
    return listed


def history_commit(path, which):
    """Commit of a deploy-history entry: 'last' (failed or not) or 'rollback' (make deploy rollback=1's)."""
    from project_deploy import rollback_entry
    entries = json.loads(path.read_text()) if path.is_file() else []
    if not entries:
        return ''
    entry = entries[-1] if which == 'last' else rollback_entry(entries)
    return entry['commit'] if entry else ''


class Step:
    """One run of the deploy step against one build root."""

    def __init__(self, directory, root, environment, environ, out, err):
        self.directory, self.environment, self.out, self.err = directory, environment, out, err
        self.code = root / 'app/public'
        self.separate = (self.code / '.git').exists()
        self.child = {key: value for key, value in environ.items() if key not in DROPPED_VARIABLES}
        # The steps run with this setup, whichever the build root's Makefile would find.
        self.child['SETUP_DIRECTORY'] = str(SETUP_ROOT)
        self.history = directory / '.generated/release' / environment / 'deployments.json'

    def say(self, message):
        print(message, file=self.err, flush=True)

    def git(self, *args, directory=None):
        return subprocess.run(['git', '-C', str(directory or self.directory), *args], check=True, env=self.child,
                              capture_output=True, text=True).stdout.strip()

    def resolve(self, ref, directory=None):
        result = subprocess.run(['git', '-C', str(directory or self.directory), 'rev-parse', '--verify', '--quiet',
                                 '--end-of-options', ref + '^{commit}'], env=self.child, capture_output=True, text=True)
        if result.returncode or not ref:
            where = 'the code repository app/public' if directory else 'the environment repository'
            raise Refusal(f'Unknown ref {ref} in {where}')
        return result.stdout.strip()

    def write_env(self):
        """env/<env>.env: the checked-out example plus the server's deploy target (target.env)."""
        example = self.directory / f'env/{self.environment}.env.example'
        if not example.is_file():
            raise Refusal(f'env/{self.environment}.env.example is missing in commit {self.git("rev-parse", "HEAD")[:12]}')
        text = example.read_text()
        if text and not text.endswith('\n'):
            text += '\n'
        target = target_settings(self.directory, self.environment)
        values, _ = file_settings(self.directory, self.environment)
        self.same_host = values.get('DEPLOY_CONTEXT') == 'default' and not values.get('DEPLOY_SSH')
        if target:
            named = [key for key in TARGET_KEYS if key in read_dotenv(example)]
            if named:
                raise Refusal(f"env/{self.environment}.env.example sets {' '.join(named)}, but the deploy target is "
                              f"the server's ({target_file(self.directory, self.environment)}): remove them from the example")
            text += "# Added by make pipeline-deploy from setup-server's target.env: where this server deploys.\n"
            text += ''.join(f'{key}={target[key]}\n' for key in TARGET_KEYS if key in target)
        elif not values.get('DEPLOY_PATH'):
            raise Refusal(f'No deploy target: {target_file(self.directory, self.environment)} is missing (setup-server registers it: '
                          f'sudo bin/add-project) and env/{self.environment}.env.example sets no DEPLOY_PATH')
        elif self.same_host and not values.get('APP_UID'):
            text += f'# Added by make pipeline-deploy: the runner account owns the build root and the target.\n' \
                    f'APP_UID={os.getuid()}\nAPP_GID={os.getgid()}\n'
        path = self.directory / f'env/{self.environment}.env'
        temporary = path.with_name(f'.{path.name}.pipeline')
        temporary.write_text(text)
        temporary.chmod(0o600)
        os.replace(temporary, path)

    def checkout(self, commit):
        self.git('checkout', '--quiet', '--detach', commit)
        self.write_env()

    def make(self, target, *arguments):
        command = ['make', target, f'ENV={self.environment}', *arguments]
        print(f'==> {" ".join(command)}', file=self.out, flush=True)
        status = subprocess.run(command, cwd=self.directory, env=self.child).returncode
        if status:
            raise StepFailed(f'make {target} failed (exit {status})', status)

    def require(self, variables, environ, provider, name):
        missing = [key for key, variable in variables.items() if variable['required'] and not environ.get(key)]
        if missing:
            hint = ''
            if provider is GITHUB and not environ.get(SECRETS_JSON):
                hint = (f'\nThe job passes no {SECRETS_JSON}: give it env {SECRETS_JSON}: ${{{{ toJSON(secrets) }}}} '
                        f'and {VARIABLES_JSON}: ${{{{ toJSON(vars) }}}}, or map each name in its env.')
            raise MissingVariables(f"{provider.settings.format(name=name)}: {' '.join(missing)}{hint}")


def environment_of(environment, name, provider=BITBUCKET):
    """The setup ENV of this step: ENV=stage|prod, or the one the deployment environment names."""
    derived = DEPLOYMENT_ENVIRONMENTS.get(name.lower())
    if not name:
        raise Refusal(provider.outside)
    if environment in ('stage', 'prod'):
        if derived and derived != environment:
            raise Refusal(f'ENV={environment} does not match the {provider.label} deployment environment {name} '
                          f'(ENV={derived})')
        return environment
    if environment == 'local' and derived:
        return derived
    raise Refusal(f'Run this from a Staging or Production deployment step (this one is {name}), '
                  'or pass ENV=stage|prod for a deployment environment with another name')


def pipeline_deploy(directory, environment='local', root=None, environ=None, out=None, err=None):
    """The deploy step; returns the exit status (64: refused before any change)."""
    from project import project_root
    environ = dict(os.environ if environ is None else environ)
    out, err = out or sys.stdout, err or sys.stderr
    directory = Path(directory).resolve()
    root = Path(root).resolve() if root else project_root(directory)

    def refuse(message):
        print(message, file=err)
        print('Nothing was changed.', file=err)
        return USAGE

    provider = provider_of(environ)
    deployment = environ.get(provider.environment, '') if provider.environment else ''
    try:
        environment = environment_of(environment, deployment, provider)
        for duplicate in expand_json(environ):
            print(f'Warning: {duplicate} is both a secret and a configuration variable; the secret is used.',
                  file=err)
        switches = {}
        for name, default in SWITCHES:
            switches[name] = environ.get(name) or default
            if switches[name] not in ('yes', 'no'):
                raise Refusal(f'{name} must be yes or no, not {switches[name]!r}')
        given = {name: environ[name] for name in REF_VARIABLES if environ.get(name)}
        if len(set(given.values())) > 1:
            raise Refusal(f"{', '.join(given)} name different refs: set only REF")
        ref = next(iter(given.values()), '')
        step = Step(directory, root, environment, environ, out, err)
        rollback = switches['ROLLBACK'] == 'yes'
        if step.separate and not rollback and not ref:
            raise Refusal('Name the code tag or commit to deploy with REF (SHOP_REF): app/public is its own '
                          'repository, and the pipeline commit belongs to this environment repository')
        if not step.separate and not rollback and not (ref or environ.get(provider.commit)):
            raise Refusal(f'Neither REF nor {provider.commit} names the commit to deploy')
        if step.separate and not environ.get(provider.commit):
            raise Refusal(f'{provider.commit} is not set: the environment checkout follows the pipeline commit')
        try:
            variables = deployment_variables(directory, environment)
        except ValueError as error:
            raise Refusal(str(error)) from None
        step.require(variables, environ, provider, deployment)
    except MissingVariables as refusal:
        print(refusal, file=err)
        print(f'Nothing was changed. make deploy-variables ENV={environment} lists every name and checks '
              f'{provider.label}.', file=err)
        return USAGE
    except Refusal as refusal:
        return refuse(str(refusal))
    return run_step(step, environ, provider, deployment, ref, switches)


def run_step(step, environ, provider, deployment, ref, switches):
    rollback, images = switches['ROLLBACK'] == 'yes', switches['IMAGES'] == 'yes'
    try:
        live = step.git('rev-parse', '--verify', 'HEAD')
    except subprocess.CalledProcessError:
        print(f'{step.directory} is not a git checkout with a commit; clone the environment repository there first',
              file=step.err)
        return USAGE
    state = {'restore': False, 'images': False, 'deploy': False}
    target = ''
    try:
        step.git('fetch', '--quiet', '--tags', 'origin')
        if step.separate:
            step.git('fetch', '--quiet', '--tags', 'origin', directory=step.code)
        if rollback and not history_commit(step.history, 'rollback'):
            raise Refusal(f'No earlier completed deploy in {step.history.relative_to(step.directory)} to roll back to')
        if step.separate:
            target = step.resolve(environ[provider.commit])
            if not rollback:
                step.resolve(ref, directory=step.code)
        elif rollback:
            target = history_commit(step.history, 'rollback')
        else:
            ref = ref or environ[provider.commit]
            target = step.resolve(ref)
        state['restore'] = True
        step.checkout(target)
        print(f'==> {step.directory} at {target[:12]}, env/{step.environment}.env written from its example',
              file=step.out, flush=True)
        # The deployed commit's own lists: a name it adds is checked before anything runs.
        try:
            variables = deployment_variables(step.directory, step.environment)
            wanted = needed_setup_version(step.directory)
        except ValueError as error:
            raise Refusal(str(error)) from None
        if wanted:
            raise SetupTooOld(wanted, (SETUP_ROOT / 'VERSION').read_text().strip())
        step.require(variables, environ, provider, deployment)
        empty = [name for name, variable in variables.items() if not variable['required'] and not environ.get(name)]
        if empty:
            step.say(f"Warning: empty Deployment variables, their integrations stay off: {' '.join(empty)}")
        if images:
            step.make('release')
            state['images'] = True
            # On this same host the images are built in the target's Docker already: no save | load copy.
            step.make('deploy-images', *(['transfer=0'] if step.same_host else []))
        state['deploy'] = True
        if rollback:
            step.make('deploy', 'rollback=1')
        else:
            step.make('deploy', f'ref={ref}', f'backup={1 if switches["BACKUP"] == "yes" else 0}')
        state['restore'] = False
        if not step.separate:
            # Setup decides what it deployed; the checkout follows it if the two ever disagree.
            deployed = history_commit(step.history, 'last')
            if deployed and deployed != target:
                step.say(f'Warning: setup deployed {deployed}, not {target}; the build root follows it.')
                step.checkout(deployed)
        return 0
    except (Refusal, subprocess.CalledProcessError, OSError, ValueError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            step.say(f'git {" ".join(error.cmd[3:])} failed: {(error.stderr or "").strip()}')
            status = error.returncode or 1
        elif not isinstance(error, Refusal):  # OSError, or a ValueError of an env or history file
            step.say(f'Error: {error}')
            status = 1
        else:
            if not isinstance(error, StepFailed):
                step.say(str(error))
            status = error.status
        if state['restore']:
            restore(step, live, target, state, provider)
        elif status == USAGE:
            step.say('Nothing was changed.')
        if isinstance(error, SetupTooOld):
            return update_setup_and_restart(step, error.wanted, environ, status)
        return status


def update_setup_and_restart(step, wanted, environ, status):
    """On a setup-server worker (SETUP_SERVER_ROOT, the runner's own checkout): the newer setup, then the step again."""
    if not environ.get('SETUP_SERVER_ROOT'):
        step.say(f'Update setup to v{wanted} or newer first (git -C {SETUP_ROOT} pull or check out a release tag).')
        return status
    if environ.get('SETUP_SELF_UPDATED'):
        step.say(f'setup was updated to {environ["SETUP_SELF_UPDATED"]} for this deploy already and is still too old.')
        return status
    from setup_self_update import update_setup
    try:
        previous, tag = update_setup(wanted)
    except (ValueError, subprocess.CalledProcessError, OSError) as error:
        detail = (getattr(error, 'stderr', '') or str(error)).strip()
        step.say(f'setup could not be updated to v{wanted} or newer: {detail}')
        return status
    step.say(f'==> setup {previous} -> {tag} ({SETUP_ROOT}): the project needs {wanted}; the step starts again on it.')
    sys.stdout.flush(), sys.stderr.flush()
    os.execve(sys.executable, [sys.executable, *sys.argv], {**os.environ, 'SETUP_SELF_UPDATED': tag})


def restore(step, live, target, state, provider=BITBUCKET):
    say = step.say
    if state['images']:
        say(f'The deploy failed after the containers were restarted on the images of {target[:12]}; the build root '
            'stays at that commit (the running containers mount its config).')
        if step.separate:
            say(f'Fix and deploy again with IMAGES=yes, or {provider.rerun.format(commit=live)} with IMAGES=yes and '
                'the code that was live (REF=..., or ROLLBACK=yes).')
        else:
            say(f'Fix and deploy again with IMAGES=yes, or return with REF={live} IMAGES=yes (ROLLBACK=yes when the '
                f'history already records a deploy of {target}).')
        return
    try:
        step.checkout(live)
    except (subprocess.CalledProcessError, OSError, ValueError, Refusal):
        say(f'The deploy failed and the build root could not return to {live}: check it out by hand '
            f'(git -C {step.directory} checkout --detach {live}) and rewrite env/{step.environment}.env.')
        return
    if not state['deploy']:
        say(f'Nothing was deployed: the build root is back at the previously live commit {live}.')
        return
    say(f'The deploy failed: the build root is back at the previously live commit {live}.')
    say('If it failed after the rsync (setup records it as failed), the target holds the new code with that config: '
        'fix and deploy again, or run ROLLBACK=yes.')
