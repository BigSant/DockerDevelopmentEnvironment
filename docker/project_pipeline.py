"""Bitbucket Pipelines deploy step: make pipeline-deploy [ENV=stage|prod] in the build root.

The step runs on a self-hosted shell runner of the build server; the work happens in the persistent
build root (its environment checkout, the directory this runs in), not in the runner's own clone,
which only identifies the commit. Secrets stay in the step's process environment (Deployment
variables). Two layouts:

  one repository (asana.local): the code is part of the environment repository (app/public has no
      .git of its own). The checkout is the commit being deployed: REF (APP_REF), else the pipeline
      commit; ROLLBACK=yes checks out the commit setup's rollback returns to. When the build root is
      also DEPLOY_PATH the running containers mount its config, so it must always match the code.
  separate code repository (Forsena): app/public is the shop's own repository. The environment
      checkout follows the pipeline commit; REF (SHOP_REF) names the code commit to deploy and is
      required unless ROLLBACK=yes.

Before anything changes: the required Deployment variables (setup's and DEPLOY_REQUIRED_VARIABLES)
must be set. Then env/<env>.env is written from the deployed commit's env/<env>.env.example (plus
the runner account's APP_UID/APP_GID when DEPLOY_CONTEXT=default deploys to this same host),
IMAGES=yes runs make release + make deploy-images, then make deploy (BACKUP=no: backup=0) or
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

from project_env_files import file_settings, names_setting, require_setup_version

SETUP_ROOT = Path(__file__).resolve().parent.parent
BITBUCKET_ENVIRONMENTS = {'staging': 'stage', 'production': 'prod'}
BITBUCKET_NAMES = {setup: bitbucket for bitbucket, setup in BITBUCKET_ENVIRONMENTS.items()}
# One ref variable; the per-layout names of the former project scripts keep working.
REF_VARIABLES = ('REF', 'APP_REF', 'SHOP_REF')
SWITCHES = (('IMAGES', 'no'), ('ROLLBACK', 'no'), ('BACKUP', 'yes'))
USAGE = 64
# The step's make variables would leak into the make steps it starts; Bitbucket's DOCKER_HOST points
# at its cloud Docker service and would override DEPLOY_CONTEXT (Docker reads it first).
DROPPED_VARIABLES = ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES', 'DOCKER_HOST')


class Refusal(Exception):
    """A check failed; status 64 (usage) unless a step's own status is passed on."""

    def __init__(self, message, status=USAGE):
        super().__init__(message)
        self.status = status


class MissingVariables(Refusal):
    """Required Deployment variables are empty."""


class StepFailed(Refusal):
    """A make step failed; it has printed its own error."""


def deployment_variables(directory, environment):
    """{name: {'required', 'secured', 'source'}}: the Deployment variables one environment needs.

    setup's own: DATABASE_PASSWORD, DATABASE_ROOT_PASSWORD, METRICS_TOKEN (SERVICES has metrics) and
    the PrestaShop keys of PS_VERSION (required); DATABASE_MONITOR_PASSWORD (metrics or dbmonitor)
    and DBMONITOR_TOKEN (dbmonitor) (optional). The project's: DEPLOY_REQUIRED_VARIABLES,
    DEPLOY_OPTIONAL_VARIABLES and every APP_ENVIRONMENT name the env files leave to the process.
    A name the env files set is not a Deployment variable (the file wins); one they leave blank hides
    the Deployment variable, which only setup's own secrets survive (project.PROCESS_SECRETS).
    """
    from project import PROCESS_SECRETS
    from project_prestashop import required_keys
    values, mentioned = file_settings(directory, environment)
    services = str(values.get('SERVICES') or '').split()
    setup_required = ['DATABASE_PASSWORD', 'DATABASE_ROOT_PASSWORD'] + (['METRICS_TOKEN'] if 'metrics' in services else [])
    version = str(values.get('PS_VERSION') or '')
    if values.get('PROFILE') in ('ps', 'prestashop') and re.match(r'\d+\.\d', version):
        setup_required += required_keys(version)
    setup_optional = (['DATABASE_MONITOR_PASSWORD'] if {'metrics', 'dbmonitor'} & set(services) else []) + \
        (['DBMONITOR_TOKEN'] if 'dbmonitor' in services else [])
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
        """env/<env>.env: the checked-out example, plus this account's ids when it deploys to this host."""
        example = self.directory / f'env/{self.environment}.env.example'
        if not example.is_file():
            raise Refusal(f'env/{self.environment}.env.example is missing in commit {self.git("rev-parse", "HEAD")[:12]}')
        text = example.read_text()
        if text and not text.endswith('\n'):
            text += '\n'
        values, _ = file_settings(self.directory, self.environment)
        self.same_host = values.get('DEPLOY_CONTEXT') == 'default' and not values.get('DEPLOY_SSH')
        if self.same_host and not values.get('APP_UID'):
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

    def require(self, variables, environ, bitbucket):
        missing = [name for name, variable in variables.items() if variable['required'] and not environ.get(name)]
        if missing:
            raise MissingVariables("Deployment variables missing or empty (Repository settings -> Deployments -> "
                                   f"{bitbucket}): {' '.join(missing)}")


def environment_of(environment, bitbucket):
    """The setup ENV of this step: ENV=stage|prod, or the one the Bitbucket deployment environment names."""
    derived = BITBUCKET_ENVIRONMENTS.get(bitbucket.lower())
    if not bitbucket:
        raise Refusal('Run make pipeline-deploy from a Bitbucket deployment step (deployment: Staging or Production); '
                      'BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set')
    if environment in ('stage', 'prod'):
        if derived and derived != environment:
            raise Refusal(f'ENV={environment} does not match the Bitbucket deployment environment {bitbucket} '
                          f'(ENV={derived})')
        return environment
    if environment == 'local' and derived:
        return derived
    raise Refusal(f'Run this from a Staging or Production deployment step (this one is {bitbucket}), '
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

    bitbucket = environ.get('BITBUCKET_DEPLOYMENT_ENVIRONMENT', '')
    try:
        environment = environment_of(environment, bitbucket)
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
        if not step.separate and not rollback and not (ref or environ.get('BITBUCKET_COMMIT')):
            raise Refusal('Neither REF nor BITBUCKET_COMMIT names the commit to deploy')
        if step.separate and not environ.get('BITBUCKET_COMMIT'):
            raise Refusal('BITBUCKET_COMMIT is not set: the environment checkout follows the pipeline commit')
        try:
            variables = deployment_variables(directory, environment)
        except ValueError as error:
            raise Refusal(str(error)) from None
        step.require(variables, environ, bitbucket)
    except MissingVariables as refusal:
        print(refusal, file=err)
        print(f'Nothing was changed. make deploy-variables ENV={environment} lists every name and checks Bitbucket.',
              file=err)
        return USAGE
    except Refusal as refusal:
        return refuse(str(refusal))
    return run_step(step, environ, bitbucket, ref, switches)


def run_step(step, environ, bitbucket, ref, switches):
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
            target = step.resolve(environ['BITBUCKET_COMMIT'])
            if not rollback:
                step.resolve(ref, directory=step.code)
        elif rollback:
            target = history_commit(step.history, 'rollback')
        else:
            ref = ref or environ['BITBUCKET_COMMIT']
            target = step.resolve(ref)
        state['restore'] = True
        step.checkout(target)
        print(f'==> {step.directory} at {target[:12]}, env/{step.environment}.env written from its example',
              file=step.out, flush=True)
        # The deployed commit's own lists: a name it adds is checked before anything runs.
        try:
            variables = deployment_variables(step.directory, step.environment)
            require_setup_version(step.directory)
        except ValueError as error:
            raise Refusal(str(error)) from None
        step.require(variables, environ, bitbucket)
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
            step.make('deploy', f'ref={ref}', f'backup={0 if switches["BACKUP"] == "no" else 1}')
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
            restore(step, live, target, state)
        elif status == USAGE:
            step.say('Nothing was changed.')
        return status


def restore(step, live, target, state):
    say = step.say
    if state['images']:
        say(f'The deploy failed after the containers were restarted on the images of {target[:12]}; the build root '
            'stays at that commit (the running containers mount its config).')
        if step.separate:
            say(f'Fix and deploy again with IMAGES=yes, or run the pipeline on commit {live} with IMAGES=yes and the '
                'code that was live (REF=..., or ROLLBACK=yes).')
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
