"""The CI deploy step: make pipeline-deploy [ENV=stage|prod|<extra>] in the build root.

Two CI providers run it the same way. Bitbucket Pipelines: a deployment step (deployment: Staging or
Production, or an extra environment's own name) whose Deployment variables are the step's process environment.
GitHub Actions: a job with environment: staging, production or an extra environment's own name (STAGE_ENVIRONMENTS
in env/common.env) that passes the environment's name in DEPLOY_ENVIRONMENT (GitHub
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
make deploy rollback=1. IMAGES=auto decides it (images_needed()): yes when the build root has no release
images yet, when setup moved since they were built, or when the commits from the live one to the deployed
one touch what the images or the containers are made of; a rollback stays code-only. CRON (default auto:
when those commits touch config/cron/) runs make cron-reload after the deploy, because the running cron
container reads its crontab only at start and nothing above recreates it for a changed mounted file.
CACHE (default auto) goes to make deploy, which clears the application cache when the deploy changes what it
holds (cache_needed); yes always, no never.
A failure returns the checkout and env file to the previously live commit, unless this run already
restarted the containers on the new images: then nothing is undone and the step prints the way back.

The live commit the auto switches compare with is the build root's checkout and, when it differs (a build root that
deploys stage and prod, or one a killed run left behind), also the environment's last completed deploy in its history:
a switch says yes when either comparison needs it. Every run holds the environment's lock
(.generated/release/<env>/deploy.lock, project_attempts.environment_lock) from its checks to its end; a second run
waits. It keeps .generated/release/<env>/pipeline-run.json (replaced atomically, removed only by the run that wrote it)
while it works; finding one, the next run first does what the killed run's own end would have done (the checkout back
at the commit before it, or at the code it already deployed), and an unreadable one is refused and left for a person.

A monorepo build root (setup-server's --monorepo: the build root is the clone and holds app/Makefile) keeps the
server's target.env / target.<env>.env beside app/, untracked (.git/info/exclude); a commit that tracks either is
refused (64, target_tracked) before its checkout could overwrite them.

Opt-in, for a caller that tracks its deploys (AI OS; project_attempts):
  DEPLOY_ATTEMPT=<id>: an attempt id. Under the environment's lock it is refused (64, attempt_exists) when the
      environment already knows it, unless ROLLBACK=yes follows that attempt's own failed deploy; it is recorded as
      `attempt` in the history entries it causes, and .generated/release/<env>/attempts/<id>.json gets the outcome and
      an event per stage. A refusal of a new attempt writes that file too (outcome refused and the reason).
  DEPLOY_EXPECTED_LIVE=<commit>|none: refused (64, live_changed) unless the environment's last completed deploy is
      that commit (none: no deploy yet).
  HEALTH=auto|yes|no (auto): after the deploy, make deploy-health when the deployed commit configures it
      (DEPLOY_HEALTH_PATHS or DEPLOY_HEALTH_RELEASE_PATH) or the caller passes DEPLOY_HEALTH_EXTRA; no leaves it to the
      caller's own step. On prod, a failed check (or any failure after the code was copied: the hook, migrations,
      cache, reload) rolls back automatically (make deploy rollback=1) and checks again; the attempt is rolled_back
      only when that check passes. A project that configures none of it deploys as before.
GitHub's DEPLOY_VARIABLES_JSON / DEPLOY_SECRETS_JSON are expanded first, so each of these may come from them too.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

from project_attempts import (LOCK_VARIABLE, Attempt, COMMIT, check_attempt, environment_lock, is_known, live_commit,
                              now, read_history, release_root, write_json)
from project_env_files import (TARGET_KEYS, deployment_variables, file_settings,
                               needed_setup_version, project_root, read_dotenv, target_file, target_settings)
from project_environments import DEPLOYMENT_ENVIRONMENTS, NAME, RESERVED, is_server, server_environments

SETUP_ROOT = Path(__file__).resolve().parent.parent
# GitHub Actions: the job's secrets and configuration variables, as toJSON(secrets) / toJSON(vars).
SECRETS_JSON, VARIABLES_JSON = 'DEPLOY_SECRETS_JSON', 'DEPLOY_VARIABLES_JSON'
# Names the expansion never sets: GitHub's own (GITHUB_TOKEN is in the secrets context) and the runner's.
RESERVED_PREFIXES = ('GITHUB_', 'RUNNER_', 'ACTIONS_')
ENVIRONMENT_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
# One ref variable; the per-layout names of the former project scripts keep working.
REF_VARIABLES = ('REF', 'APP_REF', 'SHOP_REF')
SWITCHES = (('IMAGES', 'no'), ('CRON', 'auto'), ('CACHE', 'auto'), ('ROLLBACK', 'no'), ('BACKUP', 'no'), ('HEALTH', 'auto'))
# Switches that take auto: the step decides IMAGES and CRON from the commits it deploys, make deploy decides CACHE,
# and HEALTH runs make deploy-health when the deployed commit configures a check.
AUTO_SWITCHES = ('IMAGES', 'CRON', 'CACHE', 'HEALTH')
ATTEMPT_VARIABLE, EXPECTED_VARIABLE = 'DEPLOY_ATTEMPT', 'DEPLOY_EXPECTED_LIVE'
RUN_MARKER = 'pipeline-run.json'
# setup-server's deploy target files at a monorepo build root, which the repository must not track.
TRACKED_TARGET = re.compile(r'target(\.[A-Za-z0-9_-]+)?\.env')
# What the images and containers are made of, in the environment repository: IMAGES=auto rebuilds when the
# deployed commits touch it (env/<env>.env.example is added per environment). config/ is mounted, but Apache,
# nginx and the PHP startup read it at container start; config/cron/ is left to CRON (make cron-reload), and
# config/metrics/ is read by every traffic analysis (make deploy copies it).
IMAGE_PATHS = ('Dockerfile', '.dockerignore', 'docker', 'compose', 'config', ':(exclude)config/cron', ':(exclude)config/metrics',
               'env/common.env')
CRON_PATHS = ('config/cron',)
USAGE = 64
# The step's make variables would leak into the make steps it starts; Bitbucket's DOCKER_HOST points
# at its cloud Docker service and would override DEPLOY_CONTEXT (Docker reads it first). The JSON of
# every GitHub secret stays in this process: the steps get the expanded names only.
DROPPED_VARIABLES = ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'MAKEOVERRIDES', 'DOCKER_HOST', SECRETS_JSON, VARIABLES_JSON)


class Refusal(Exception):
    """A check failed; status 64 (usage) unless a step's own status is passed on. reason: the attempt's code."""
    reason = 'bad_input'

    def __init__(self, message, status=USAGE, reason=None):
        super().__init__(message)
        self.status = status
        self.reason = reason or self.reason


class SetupTooOld(Refusal):
    """The deployed commit needs a newer setup (SETUP_MIN_VERSION)."""
    reason = 'setup_too_old'

    def __init__(self, wanted, current):
        super().__init__(f'This project needs setup {wanted} or newer (SETUP_MIN_VERSION in env/common.env); '
                         f'the setup at {SETUP_ROOT} is {current}')
        self.wanted = wanted


class MissingVariables(Refusal):
    """Required Deployment variables are empty."""
    reason = 'missing_settings'


class AttemptExists(Refusal):
    """DEPLOY_ATTEMPT names an attempt the environment already knows."""
    reason = 'attempt_exists'


class StepFailed(Refusal):
    """A make step failed; it has printed its own error."""

    def __init__(self, message, status, target):
        super().__init__(message, status, {'release': 'images', 'deploy-images': 'images'}.get(target, target))


class Provider:
    """What differs between the CI providers: where the environment name, the commit and the secrets come from."""

    def __init__(self, label, environment, commit, settings, outside, rerun):
        self.label, self.environment, self.commit = label, environment, commit
        self.settings, self.outside, self.rerun = settings, outside, rerun


BITBUCKET = Provider(
    'Bitbucket', 'BITBUCKET_DEPLOYMENT_ENVIRONMENT', 'BITBUCKET_COMMIT',
    'Deployment variables missing or empty (Repository settings -> Deployments -> {name})',
    'Run make pipeline-deploy from a Bitbucket deployment step (deployment: Staging, Production or an extra '
    "environment's own name); BITBUCKET_DEPLOYMENT_ENVIRONMENT is not set",
    'run the pipeline on commit {commit}')
GITHUB = Provider(
    'GitHub', 'DEPLOY_ENVIRONMENT', 'GITHUB_SHA',
    'Environment secrets or variables missing or empty (Settings -> Environments -> {name})',
    'Run make pipeline-deploy from a GitHub Actions job with environment: staging, production or an extra '
    "environment's own name that passes the same name in DEPLOY_ENVIRONMENT "
    '(templates/pipelines/github-deploy.yml); DEPLOY_ENVIRONMENT is not set',
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
        self.root = root
        self.code = root / 'app/public'
        self.separate = (self.code / '.git').exists()
        self.child = {key: value for key, value in environ.items() if key not in DROPPED_VARIABLES}
        # The steps run with this setup, whichever the build root's Makefile would find.
        self.child['SETUP_DIRECTORY'] = str(SETUP_ROOT)
        self.history = directory / '.generated/release' / environment / 'deployments.json'
        self.marker = release_root(directory, environment) / RUN_MARKER
        self.marker_data = None  # this run's marker, once it wrote one

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
        if self.environment not in server_environments(self.directory):
            raise Refusal(f'The deployment environment {self.environment} is not an environment of commit '
                          f'{self.git("rev-parse", "HEAD")[:12]}: list it in STAGE_ENVIRONMENTS of env/common.env')
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

    def monorepo(self):
        """Whether the build root is itself the clone (setup-server's --monorepo: the repository holds app/)."""
        if not (self.root / 'app/Makefile').is_file():
            return False
        top = subprocess.run(['git', '-C', str(self.directory), 'rev-parse', '--show-toplevel'], env=self.child,
                             capture_output=True, text=True)
        return top.returncode == 0 and Path(top.stdout.strip()).resolve() == self.root.resolve()

    def refuse_tracked_target(self, commit):
        """A monorepo commit must not track the server's target.env / target.<env>.env at the build root: its checkout
        would overwrite the deploy target setup-server keeps there (untracked, in .git/info/exclude)."""
        if self.separate or not self.monorepo():
            return
        names = self.git('ls-tree', '--name-only', commit, directory=self.root).splitlines()
        tracked = [name for name in names if TRACKED_TARGET.fullmatch(name)]
        if tracked:
            raise Refusal(f"Commit {commit[:12]} tracks {' '.join(tracked)} at the build root, where setup-server keeps "
                          "this server's deploy target: checking it out would overwrite it. Remove the file from the "
                          "repository (git rm --cached) and deploy a commit without it", reason='target_tracked')

    def checkout(self, commit):
        self.refuse_tracked_target(commit)
        self.git('checkout', '--quiet', '--detach', commit)
        self.write_env()

    def run_make(self, target, *arguments):
        command = ['make', target, f'ENV={self.environment}', *arguments]
        print(f'==> {" ".join(command)}', file=self.out, flush=True)
        return subprocess.run(command, cwd=self.directory, env=self.child).returncode

    def make(self, target, *arguments):
        status = self.run_make(target, *arguments)
        if status:
            raise StepFailed(f'make {target} failed (exit {status})', status, target)

    def mark(self, **fields):
        """The run's marker while it works, replaced atomically: the run holds the environment's lock, so a marker the
        next run finds is a killed run's."""
        self.marker_data = {**(self.marker_data or {}), **fields}
        write_json(self.marker, self.marker_data)

    def unmark(self):
        """Removes the marker only if this run wrote it."""
        if self.marker_data is not None:
            self.marker.unlink(missing_ok=True)
            self.marker_data = None

    def repair(self, head, record):
        """A killed run's leftovers: what its own end would have done. Returns the build root's commit afterwards.
        An unreadable marker is refused and left for a person: it is the only record of where that run stopped."""
        if not self.marker.is_file():
            return head
        try:
            left = json.loads(self.marker.read_text())
            entries = read_history(self.directory, self.environment)
            grown = len(entries) > left['entries']
        except (ValueError, KeyError, TypeError):
            raise Refusal(f'{self.marker} is not a pipeline run marker: check the build root, then remove it') from None
        return self.repaired(head, record, left, entries, grown)

    def repaired(self, head, record, left, entries, grown):
        """The killed run's end: its code deployed (follow it), its images restarted (stay), else back to its start."""
        deployed = grown and not entries[-1].get('failed')
        if deployed and not self.separate:
            commit = entries[-1]['commit']
            self.say(f'==> A killed run deployed {commit[:12]} to ENV={self.environment} but did not finish; '
                     'the build root follows it.')
        elif deployed or left.get('images'):
            commit = head
            self.say(f'==> A killed run left the build root at {head[:12]} after it ' +
                     ('deployed the code' if deployed else 'restarted the containers on its images') + '; it stays there.')
        else:
            commit = left['from']
            self.say(f'==> A killed run left the build root at {head[:12]}; it is back at {commit[:12]}, '
                     f'the commit before that run.')
        if commit != head:
            self.checkout(commit)
        self.marker.unlink()  # the killed run's, repaired: this run holds the environment's lock
        interrupted = Attempt(self.directory, self.environment, left.get('attempt'))
        if interrupted and interrupted.outcome() == 'in_progress':
            interrupted.event('interrupted', 'failed', code_reached=grown)
            interrupted.update(outcome='failed_after_code' if grown else 'not_deployed', reason='interrupted')
        record.event('repair', 'ok', checkout_repaired=commit != head, interrupted_attempt=left.get('attempt'),
                     commit=commit)
        return commit

    def require(self, variables, environ, provider, name):
        missing = [key for key, variable in variables.items() if variable['required'] and not environ.get(key)]
        if missing:
            hint = ''
            if provider is GITHUB and not environ.get(SECRETS_JSON):
                hint = (f'\nThe job passes no {SECRETS_JSON}: give it env {SECRETS_JSON}: ${{{{ toJSON(secrets) }}}} '
                        f'and {VARIABLES_JSON}: ${{{{ toJSON(vars) }}}}, or map each name in its env.')
            raise MissingVariables(f"{provider.settings.format(name=name)}: {' '.join(missing)}{hint}")


def environment_of(environment, name, provider=BITBUCKET):
    """The setup ENV of this step: ENV=stage|prod|<extra>, or the one the deployment environment names: staging,
    production, or an extra environment's own name. Whether the project lists that one is checked against the
    deployed commit's env/common.env (Step.write_env): a commit may add it."""
    derived = DEPLOYMENT_ENVIRONMENTS.get(name.lower())
    if not derived and NAME.fullmatch(name.lower()) and name.lower() not in RESERVED:
        derived = name.lower()
    if not name:
        raise Refusal(provider.outside)
    if is_server(environment):
        if derived and derived != environment:
            raise Refusal(f'ENV={environment} does not match the {provider.label} deployment environment {name} '
                          f'(ENV={derived})')
        return environment
    if environment == 'local' and derived:
        return derived
    raise Refusal(f'Run this from a Staging or Production deployment step, or one named after an extra environment '
                  f'(this one is {name}), or pass ENV=stage|prod|<extra> for a deployment environment with another name')


def pipeline_deploy(directory, environment='local', root=None, environ=None, out=None, err=None):
    """The deploy step; returns the exit status (64: refused before any change)."""
    environ = dict(os.environ if environ is None else environ)
    out, err = out or sys.stdout, err or sys.stderr
    directory = Path(directory).resolve()
    root = Path(root).resolve() if root else project_root(directory)
    provider = provider_of(environ)
    deployment = environ.get(provider.environment, '') if provider.environment else ''
    try:
        environment = environment_of(environment, deployment, provider)
        # GitHub's variables and secrets first: DEPLOY_ATTEMPT and the switches may come from them.
        for duplicate in expand_json(environ):
            print(f'Warning: {duplicate} is both a secret and a configuration variable; the secret is used.',
                  file=err)
        attempt = environ.get(ATTEMPT_VARIABLE, '')
        if attempt:
            check_attempt(attempt)
    except (Refusal, ValueError) as refusal:
        print(refusal, file=err)
        print('Nothing was changed.', file=err)
        return USAGE
    # One run at a time per environment, with or without an attempt: its checks, its acceptance and its changes.
    with environment_lock(directory, environment, environ) as lock:
        environ[LOCK_VARIABLE] = str(lock)
        return checked_step(directory, root, environment, environ, provider, deployment, out, err)


def checked_step(directory, root, environment, environ, provider, deployment, out, err):
    record = Attempt(directory, environment, environ.get(ATTEMPT_VARIABLE))
    # new: an attempt not seen yet; follow: ROLLBACK=yes after its own failed deploy; resume: its run restarted on a
    # newer setup (SetupTooOld); known: any other repeat, refused.
    mode = attempt_mode(directory, environment, record, environ)

    def refuse(refusal):
        print(refusal, file=err)
        if isinstance(refusal, MissingVariables):
            print(f'Nothing was changed. make deploy-variables ENV={environment} lists every name and checks '
                  f'{provider.label}.', file=err)
        else:
            print('Nothing was changed.', file=err)
        message = str(refusal).splitlines()[0]
        if mode == 'new':
            record.create(outcome='refused', reason=refusal.reason, expected_live=environ.get(EXPECTED_VARIABLE) or None)
            record.event('refused', 'refused', reason=refusal.reason, message=message)
        elif mode == 'follow':
            record.event('rollback', 'refused', reason=refusal.reason, message=message)
        elif mode == 'resume':
            record.event('refused', 'refused', reason=refusal.reason, message=message)
            record.update(outcome='refused', reason=refusal.reason)
        return USAGE

    try:
        switches = {}
        for name, default in SWITCHES:
            switches[name] = environ.get(name) or default
            allowed = ('yes', 'no', 'auto') if name in AUTO_SWITCHES else ('yes', 'no')
            if switches[name] not in allowed:
                raise Refusal(f"{name} must be {', '.join(allowed[:-1])} or {allowed[-1]}, not {switches[name]!r}")
        rollback = switches['ROLLBACK'] == 'yes'
        if mode == 'follow' and not rollback:
            mode = 'known'
        if mode == 'known':
            return refuse_known(record, environment, err)
        given = {name: environ[name] for name in REF_VARIABLES if environ.get(name)}
        if len(set(given.values())) > 1:
            raise Refusal(f"{', '.join(given)} name different refs: set only REF")
        ref = next(iter(given.values()), '')
        step = Step(directory, root, environment, environ, out, err)
        if step.separate and not rollback and not ref:
            raise Refusal('Name the code tag or commit to deploy with REF (SHOP_REF): app/public is its own '
                          'repository, and the pipeline commit belongs to this environment repository')
        if not step.separate and not rollback and not (ref or environ.get(provider.commit)):
            raise Refusal(f'Neither REF nor {provider.commit} names the commit to deploy')
        if step.separate and not environ.get(provider.commit):
            raise Refusal(f'{provider.commit} is not set: the environment checkout follows the pipeline commit')
        check_expected_live(directory, environment, environ.get(EXPECTED_VARIABLE, ''))
        try:
            variables = deployment_variables(directory, environment)
        except ValueError as error:
            raise Refusal(str(error)) from None
        step.require(variables, environ, provider, deployment)
    except Refusal as refusal:
        return refuse(refusal)
    if mode == 'new':
        record.create(expected_live=environ.get(EXPECTED_VARIABLE) or None, ref=ref or None, rollback=rollback)
        record.event('accepted', 'ok', rollback=rollback, run_id=environ.get('GITHUB_RUN_ID') or None)
    return run_step(step, environ, provider, deployment, ref, switches, record, mode == 'follow')


def attempt_mode(directory, environment, record, environ):
    if not record:
        return None
    if not is_known(directory, environment, record.id):
        return 'new'
    data = record.read()
    # The lock is free while the step restarts, so a repeat refused meanwhile may follow the setup event.
    own = [event['stage'] for event in (data or {}).get('events', []) if event['stage'] != 'repeated']
    if environ.get('SETUP_SELF_UPDATED') and data and data['outcome'] == 'in_progress' and own and own[-1] == 'setup':
        return 'resume'
    entries = read_history(directory, environment)
    if entries and entries[-1].get('attempt') == record.id and entries[-1].get('failed'):
        return 'follow'  # only with ROLLBACK=yes, which checked_step decides once the switches are read
    return 'known'


def refuse_known(record, environment, err):
    data = record.read()
    print(f'Attempt {record.id} is known in ENV={environment} already' +
          (f" (outcome: {data['outcome']})" if data else ' (its deploy is in the history)') +
          '; a repeated attempt changes nothing. Deploy again with a new attempt id.', file=err)
    print('Nothing was changed.', file=err)
    if data:
        record.event('repeated', 'refused', reason='attempt_exists')
    return USAGE


def check_expected_live(directory, environment, expected):
    """DEPLOY_EXPECTED_LIVE: the commit the caller believes is live (none: nothing yet); anything else is refused."""
    if not expected:
        return
    if expected != 'none' and not COMMIT.fullmatch(expected):
        raise Refusal(f'{EXPECTED_VARIABLE} must be a full lower-case commit or none, not {expected!r}')
    live = live_commit(directory, environment) or 'none'
    if live != expected:
        raise Refusal(f'ENV={environment} changed outside this deploy: its live commit is {live}, not {expected} '
                      f'({EXPECTED_VARIABLE})', reason='live_changed')


def images_needed(step, live, target):
    """IMAGES=auto: (yes or no, the reason). setup's make deploy copies code only and does not notice outdated images."""
    from project_release import LOCK_NAME
    lock = step.directory / '.generated/release' / step.environment / LOCK_NAME
    if not lock.is_file():
        return True, f'no release images yet ({lock.relative_to(step.directory)})'
    try:
        built = json.loads(lock.read_text())['setup']
    except (ValueError, KeyError):
        return True, f'{lock.relative_to(step.directory)} does not name the setup the images were built with'
    try:
        current = step.git('describe', '--tags', '--always', '--dirty', directory=SETUP_ROOT)
    except subprocess.CalledProcessError:
        return True, f'the setup version is unknown ({SETUP_ROOT} is no git checkout)'
    if built != current:
        return True, f'the images were built with setup {built}, setup is now {current}'
    changed = step.git('diff', '--name-only', live, target, '--', *IMAGE_PATHS, f'env/{step.environment}.env.example')
    if changed:
        return True, f"changed since {live[:12]}: {' '.join(changed.split())}"
    return False, f'nothing image-related changed since {live[:12]}'


def cron_needed(step, live, target):
    """CRON=auto: (yes or no, the reason) — whether the deployed commits change the crontab."""
    changed = step.git('diff', '--name-only', live, target, '--', *CRON_PATHS)
    if changed:
        return True, f"changed since {live[:12]}: {' '.join(changed.split())}"
    return False, f'the crontab did not change since {live[:12]}'


def live_bases(step, head):
    """The commits the auto switches compare with: the build root's checkout and, when it differs, the environment's
    last completed deploy (one repository: the history records environment commits)."""
    if step.separate:
        return [head]
    recorded = live_commit(step.directory, step.environment)
    known = recorded and subprocess.run(['git', '-C', str(step.directory), 'cat-file', '-e', recorded + '^{commit}'],
                                        env=step.child, capture_output=True).returncode == 0
    return [head, recorded] if known and recorded != head else [head]


def needed_since(needed, step, bases, target):
    """needed() against every base: yes when any comparison says yes (never fewer than the checkout alone)."""
    answers = [needed(step, base, target) for base in bases]
    return next((answer for answer in answers if answer[0]), answers[0])


def decide(step, name, value, needed):
    """A switch's (yes/no, the reason); auto asks needed() and prints the answer with its reason in the step log."""
    if value != 'auto':
        return value == 'yes', f'{name}={value}'
    answer, reason = needed()
    print(f"==> {name}=auto: {'yes' if answer else 'no'} ({reason})", file=step.out, flush=True)
    return answer, reason


def health_wanted(step, value, environ):
    """(run make deploy-health in this step, the deployed commit configures a check)."""
    from project_deploy_health import configured
    values, _ = file_settings(step.directory, step.environment)
    wanted = configured(values, environ)
    if value == 'yes' and not wanted:
        raise Refusal('HEALTH=yes, but the deployed commit configures no health check: set DEPLOY_HEALTH_PATHS or '
                      'DEPLOY_HEALTH_RELEASE_PATH in env/common.env, or pass DEPLOY_HEALTH_EXTRA')
    return (wanted if value == 'auto' else value == 'yes'), wanted


def settle(record, follow, outcome, reason):
    """The attempt's outcome after a failure; a follow-up rollback that fails leaves production in doubt."""
    if follow:
        record.event('rollback', 'failed', reason=reason)
        record.update(outcome='rollback_failed')
    else:
        record.update(outcome=outcome, reason=reason)


def auto_rollback(step, record, switches):
    """prod, after a failed health check or hook: the previous completed release again, then its health check.
    Returns the commit rolled back to, or '' when there was none or the rollback failed."""
    attempt = [f'attempt={record.id}'] if record else []
    to = history_commit(step.history, 'rollback')
    if not to:
        step.say(f'No earlier completed deploy of ENV={step.environment} to roll back to: the failed release stays.')
        record.event('rollback', 'failed', reason='no earlier completed deploy')
        record.update(outcome='rollback_failed')
        return ''
    step.say(f'==> The deploy failed on ENV={step.environment}: rolling back to {to[:12]} (HEALTH)')
    try:
        if not step.separate:
            step.checkout(to)
        step.make('deploy', 'rollback=1', f'cache={switches["CACHE"]}', *attempt)
    except (Refusal, subprocess.CalledProcessError, OSError, ValueError) as error:
        step.say(f'The rollback failed ({error}); ENV={step.environment} may not be serving. Check it and run '
                 f'make deploy ENV={step.environment} rollback=1 or deploy a known release.')
        record.event('rollback', 'failed', reason=str(error).splitlines()[0] if str(error) else 'error')
        record.update(outcome='rollback_failed')
        return ''
    record.event('rollback', 'ok', to=to)
    # rolled_back only once the rolled-back release passes its check; a refused or killed check is no pass.
    if step.run_make('deploy-health', *attempt, 'rollback=1'):
        step.say(f'The rollback to {to[:12]} did not pass its health check: ENV={step.environment} may not be serving.')
        record.update(outcome='rollback_failed')
    else:
        record.update(outcome='rolled_back')
    return to


def run_step(step, environ, provider, deployment, ref, switches, record=None, follow=False):
    record = record if record is not None else Attempt(step.directory, step.environment, None)
    attempt = [f'attempt={record.id}'] if record else []
    rollback = switches['ROLLBACK'] == 'yes'
    try:
        live = step.git('rev-parse', '--verify', 'HEAD')
    except subprocess.CalledProcessError:
        print(f'{step.directory} is not a git checkout with a commit; clone the environment repository there first',
              file=step.err)
        settle(record, follow, 'refused', 'bad_input')
        return USAGE
    state = {'restore': False, 'images': False, 'deploy': False, 'deployed': False}
    target, health, entries = '', False, None
    try:
        live = step.repair(live, record)
        entries = len(read_history(step.directory, step.environment))
        step.git('fetch', '--quiet', '--tags', 'origin')
        if step.separate:
            step.git('fetch', '--quiet', '--tags', 'origin', directory=step.code)
        if rollback and not history_commit(step.history, 'rollback'):
            raise Refusal(f'No earlier completed deploy in {step.history.relative_to(step.directory)} to roll back to')
        if step.separate:
            target = step.resolve(environ[provider.commit])
            code = history_commit(step.history, 'rollback') if rollback else step.resolve(ref, directory=step.code)
        elif rollback:
            target = code = history_commit(step.history, 'rollback')
        else:
            ref = ref or environ[provider.commit]
            target = code = step.resolve(ref)
        if not follow:
            record.update(sha=code)
        # Decided before the checkout: the build root still holds the live commit.
        bases = live_bases(step, live)
        images, why = decide(step, 'IMAGES', 'no' if rollback and switches['IMAGES'] == 'auto' else switches['IMAGES'],
                             lambda: needed_since(images_needed, step, bases, target))
        cron, _ = decide(step, 'CRON', switches['CRON'], lambda: needed_since(cron_needed, step, bases, target))
        # Before the checkout changes anything: it would overwrite the server's target file.
        step.refuse_tracked_target(target)
        state['restore'] = True
        step.mark(**{'from': live, 'target': target, 'attempt': record.id, 'images': False, 'entries': entries,
                     'at': now()})
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
        health, checked = health_wanted(step, switches['HEALTH'], environ)
        record.event('settings', 'ok', empty_settings=empty)
        if images:
            step.make('release')
            state['images'] = True
            step.mark(images=True)
            # On this same host the images are built in the target's Docker already: no save | load copy.
            step.make('deploy-images', *(['transfer=0'] if step.same_host else []))
            record.event('images', 'ok')
        else:
            record.event('images', 'skipped', reason=why)
        state['deploy'] = True
        if rollback:
            step.make('deploy', 'rollback=1', f'cache={switches["CACHE"]}', *attempt)
        else:
            step.make('deploy', f'ref={ref}', f'backup={1 if switches["BACKUP"] == "yes" else 0}', f'cache={switches["CACHE"]}',
                      *attempt)
        state['restore'], state['deployed'] = False, True
        if rollback:
            record.event('rollback', 'ok', to=history_commit(step.history, 'last'))
        if not checked:
            # Nothing to check: the deploy (or the follow-up rollback) is its own result.
            record.update(outcome='rolled_back' if follow else 'live')
        if not step.separate:
            # Setup decides what it deployed; the checkout follows it if the two ever disagree.
            deployed = history_commit(step.history, 'last')
            if deployed and deployed != target:
                step.say(f'Warning: setup deployed {deployed}, not {target}; the build root follows it.')
                step.checkout(deployed)
        status = 0
        if health:
            # make deploy-health records the attempt's outcome (live, or the failure the rollback below moves on).
            status = step.run_make('deploy-health', *attempt, *(['rollback=1'] if follow else []))
            if status:
                step.say(f'The health check of ENV={step.environment} failed.')
            if follow:
                record.update(outcome='rollback_failed' if status else 'rolled_back')
            if status and step.environment == 'prod' and not rollback:
                to = auto_rollback(step, record, switches)
                if to:
                    cron, _ = decide(step, 'CRON', switches['CRON'], lambda: cron_needed(step, live, to))
        if cron:
            # The code is live: a crontab cron refuses fails the step but undoes nothing.
            step.make('cron-reload')
        step.unmark()
        return status
    except (Refusal, subprocess.CalledProcessError, OSError, ValueError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            step.say(f'git {" ".join(error.cmd[3:])} failed: {(error.stderr or "").strip()}')
            status, reason = error.returncode or 1, 'error'
        elif not isinstance(error, Refusal):  # OSError, or a ValueError of an env or history file
            step.say(f'Error: {error}')
            status, reason = 1, 'error'
        else:
            if not isinstance(error, StepFailed):
                step.say(str(error))
            status, reason = error.status, error.reason
        history = read_history(step.directory, step.environment) if entries is not None else []
        reached = entries is not None and len(history) > entries
        if state['deployed']:
            # After the deploy (cron-reload): the code is live and stays; the attempt keeps its outcome.
            record.event(reason, 'failed')
            step.unmark()
            return status
        if reached and history[-1].get('failed'):
            # make deploy records the step that failed after the copy (incomplete: it was killed).
            failed = history[-1]['failed']
            reason = {'after-rsync': 'hook', 'incomplete': 'interrupted'}.get(failed, failed)
        if reached and health and step.environment == 'prod' and not rollback:
            record.update(outcome='failed_after_code', reason=reason)
            auto_rollback(step, record, switches)
            step.unmark()
            return status
        if state['restore']:
            restore(step, live, target, state, provider)
        elif status == USAGE:
            step.say('Nothing was changed.')
        step.unmark()
        if isinstance(error, SetupTooOld):
            status = update_setup_and_restart(step, error.wanted, environ, status, record)
        if reached:
            settle(record, follow, 'failed_after_code', reason)
        elif isinstance(error, Refusal) and not isinstance(error, StepFailed):
            settle(record, follow, 'refused', reason)
        else:
            settle(record, follow, 'not_deployed', reason)
        return status


def update_setup_and_restart(step, wanted, environ, status, record=None):
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
    if record:
        # The restarted step goes on with this attempt (checked_step: resume) instead of refusing it as known.
        record.event('setup', 'updated', to=tag)
    sys.stdout.flush(), sys.stderr.flush()
    environment = {**os.environ, 'SETUP_SELF_UPDATED': tag}
    environment.pop(LOCK_VARIABLE, None)
    os.execve(sys.executable, [sys.executable, *sys.argv], environment)


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
