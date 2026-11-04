"""Deploy attempts: the id a caller gives one deploy (DEPLOY_ATTEMPT), its stage events and the status document.

A caller such as AI OS names every deploy with an attempt id. make pipeline-deploy refuses an id the environment
already knows before anything changes, records it as `attempt` in each deploy-history entry the attempt causes, and
keeps one file per attempt: .generated/release/<env>/attempts/<id>.json. The file holds the outcome so far and the
events of each stage. Events are appended and never rewritten, and every write is a temporary file renamed into
place, so a reader never sees half a file and a killed run leaves the stages it finished. make deploy-status prints
the dde-deploy-status/v1 document from these files and the history; it changes nothing.

A deploy without DEPLOY_ATTEMPT writes none of this: no attempt file, no `attempt` field. Every writer of the
environment's history (make pipeline-deploy, make deploy, make deploy-health) holds its lock, deploy.lock.
"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess

FORMAT = 'dde-deploy-status/v1'
ATTEMPT = re.compile(r'[A-Za-z0-9-]{8,64}')
COMMIT = re.compile(r'[0-9a-f]{40}')
# The outcomes of the status document; `unknown_attempt` is the status query's only.
OUTCOMES = ('in_progress', 'live', 'refused', 'not_deployed', 'failed_after_code', 'rolled_back', 'rollback_failed',
            'unknown_attempt')
HISTORY_IN_STATUS = 20
STATUS_LIMIT = 64 * 1024
# Set for the make steps of a pipeline run that holds the environment's lock, so they do not wait for it.
LOCK_VARIABLE = 'SETUP_DEPLOY_LOCK'
FIELDS = ('live', 'code_reached', 'outcome')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def check_attempt(value, name='DEPLOY_ATTEMPT'):
    """An attempt id: 8-64 letters, digits and dashes (a UUID, or manual-<name>); it names a file."""
    if not ATTEMPT.fullmatch(value or ''):
        raise ValueError(f'{name} must be 8-64 letters, digits or dashes (an attempt UUID or manual-<name>), not {value!r}')
    return value


def release_root(directory, environment):
    return Path(directory) / '.generated/release' / environment


def history_path(directory, environment):
    return release_root(directory, environment) / 'deployments.json'


def read_history(directory, environment):
    path = history_path(directory, environment)
    return json.loads(path.read_text()) if path.is_file() else []


def write_json(path, data):
    """Atomically: a reader sees the old file or the new one, never part of one."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    os.replace(temporary, path)


def write_history(directory, environment, entries):
    write_json(history_path(directory, environment), entries)


def live_entry(entries):
    """The environment's live release: its latest completed deploy (rollbacks included), or None."""
    completed = [entry for entry in entries if not entry.get('failed')]
    return completed[-1] if completed else None


def live_commit(directory, environment):
    entry = live_entry(read_history(directory, environment))
    return entry['commit'] if entry else ''


def attempt_path(directory, environment, attempt):
    return release_root(directory, environment) / 'attempts' / f'{check_attempt(attempt)}.json'


def read_attempt(directory, environment, attempt):
    path = attempt_path(directory, environment, attempt)
    return json.loads(path.read_text()) if path.is_file() else None


def attempt_entries(entries, attempt):
    return [entry for entry in entries if entry.get('attempt') == attempt]


def is_known(directory, environment, attempt):
    """Whether the environment has seen this attempt: an attempt file or a history entry carries it."""
    return attempt_path(directory, environment, attempt).is_file() or \
        bool(attempt_entries(read_history(directory, environment), attempt))


class Lock:
    """A held environment lock: its file, and the descriptor a run passes to its make steps (None: a parent's)."""

    def __init__(self, path, fd):
        self.path, self.fd = path, fd


@contextmanager
def environment_lock(directory, environment, environ=None):
    """The environment's deploy lock, a stable file never removed: every writer of its history and attempt files waits
    for it. A pipeline run holds it for the whole run and passes its descriptor to its make steps (Step.run_make), so
    the lock lasts until the last of them exits even if the run itself is killed; LOCK_VARIABLE tells them they hold
    it already. The lock is released by closing, never by an unlock that would free it for the children too."""
    import fcntl
    path = release_root(directory, environment) / 'deploy.lock'
    held = (environ or os.environ).get(LOCK_VARIABLE)
    # Compared as real paths: the step's make children name the same directory in their own way (a link, ..).
    if held and os.path.realpath(held) == os.path.realpath(path):
        yield Lock(path, None)
        return
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(path, 'a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield Lock(path, handle.fileno())


class Attempt:
    """One attempt's file. Without an id every method does nothing, so callers need no checks of their own."""

    def __init__(self, directory, environment, attempt):
        self.directory, self.environment, self.id = Path(directory), environment, attempt or None
        self.path = attempt_path(directory, environment, attempt) if attempt else None

    def __bool__(self):
        return self.id is not None

    def read(self):
        return json.loads(self.path.read_text()) if self and self.path.is_file() else None

    def create(self, **fields):
        if self:
            write_json(self.path, {'id': self.id, 'environment': self.environment, 'sha': None, 'expected_live': None,
                                   'outcome': 'in_progress', 'reason': None, **fields, 'events': []})

    def update(self, **fields):
        """Top-level fields (outcome, reason, sha); the events are never rewritten."""
        if not self:
            return
        data = self.read()
        if data is None:
            self.create()
            data = self.read()
        data.update({key: value for key, value in fields.items() if key != 'events'})
        write_json(self.path, data)

    def event(self, stage, state, **fields):
        if not self:
            return
        data = self.read()
        if data is None:
            self.create()
            data = self.read()
        data['events'].append({'stage': stage, 'state': state, **fields, 'at': now()})
        write_json(self.path, data)

    def outcome(self):
        data = self.read()
        return data['outcome'] if data else None


def code_reached(directory, environment, attempt):
    """Whether this attempt's code reached the target: make deploy records it once the rsync is done."""
    return bool(attempt_entries(read_history(directory, environment), attempt))


def public_entry(entry):
    """A history entry for the status document: the fields a reader needs, backup as a file name only."""
    keys = ('at', 'ref', 'commit', 'attempt', 'rollback', 'failed', 'health', 'cache', 'migrations')
    public = {key: entry[key] for key in keys if key in entry}
    public['backup'] = Path(entry['backup']).name if entry.get('backup') else None
    return public


def derived_outcome(entries):
    """An attempt the history knows but no file describes (make deploy attempt=… by hand)."""
    last = entries[-1]
    if not last.get('failed'):
        return 'rolled_back' if len(entries) > 1 and last.get('rollback') else 'live'
    return 'rollback_failed' if len(entries) > 1 else 'failed_after_code'


def settings_names(directory, environment, empty=None):
    from project_env_files import deployment_variables
    try:
        variables = deployment_variables(directory, environment)
    except (ValueError, OSError):
        return None
    return {'required': [name for name, v in variables.items() if v['required']],
            'optional': [name for name, v in variables.items() if not v['required']],
            'plain': [name for name, v in variables.items() if not v['secured']],
            'empty_at_last_deploy': list(empty or [])}


def latest_nightly(directory, environment):
    """setup-server's newest nightly (or backup-now) copy of this environment: worker-<env>-<UTC>Z.sql.gz."""
    backups = sorted((Path(directory) / '.generated/backups').glob(f'worker-{environment}-*Z.sql.gz'))
    if not backups:
        return None
    stamp = re.search(r'-(\d{8}T\d{6})', backups[-1].name)
    at = datetime.strptime(stamp[1], '%Y%m%dT%H%M%S').replace(tzinfo=timezone.utc).isoformat() if stamp else None
    return {'file': backups[-1].name, 'at': at}


def build_root_state(directory, fetch=False):
    directory = Path(directory)
    git = lambda *args: subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True)
    top = git('rev-parse', '--show-toplevel')
    if (directory / 'public/.git').exists():
        layout = 'separate'
    elif top.returncode == 0 and Path(top.stdout.strip()).resolve() != directory.resolve():
        layout = 'monorepo'
    else:
        layout = 'one-repository'
    state = {'layout': layout, 'fetch': None}
    if fetch:
        state['fetch'] = 'ok' if git('fetch', '--quiet', '--tags', 'origin').returncode == 0 else 'failed'
    return state


def setup_version():
    from project_pipeline import SETUP_ROOT
    result = subprocess.run(['git', '-C', str(SETUP_ROOT), 'describe', '--tags', '--always'], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else 'v' + (SETUP_ROOT / 'VERSION').read_text().strip()


def status_document(directory, environment, attempt=None, run=None, fetch=False):
    """The dde-deploy-status/v1 document of one environment and, given its id, one attempt. Names and file names
    only: no secret value, env file content or response body."""
    from project_env_files import file_settings
    directory = Path(directory)
    entries = read_history(directory, environment)
    try:
        values, _ = file_settings(directory, environment)
    except (ValueError, OSError):
        values = {}  # a broken env file must not hide what the history says
    document = {'format': FORMAT, 'project': values.get('PROJECT_NAME') or None, 'environment': environment,
                'observed_at': now(), 'setup': setup_version()}
    if run:
        document['run'] = run
    data, empty = None, None
    if attempt:
        data = read_attempt(directory, environment, attempt)
        own = attempt_entries(entries, attempt)
        if data is None and own:
            data = {'id': attempt, 'sha': own[0]['commit'], 'expected_live': None,
                    'outcome': derived_outcome(own), 'reason': None, 'events': []}
        if data is None:
            document['attempt'] = {'id': attempt, 'known': False, 'sha': None, 'expected_live': None,
                                   'outcome': 'unknown_attempt', 'reason': None, 'events': []}
        else:
            document['attempt'] = {'id': attempt, 'known': True, 'sha': data.get('sha'),
                                   'expected_live': data.get('expected_live'), 'outcome': data['outcome'],
                                   'reason': data.get('reason'), 'events': data['events']}
    else:
        document['attempt'] = None
    live = live_entry(entries)
    document['live'] = {'commit': live['commit'], 'attempt': live.get('attempt'), 'deployed_at': live.get('at'),
                        'rollback': bool(live.get('rollback'))} if live else None
    for source in (data, read_attempt(directory, environment, live['attempt']) if live and live.get('attempt') else None):
        listed = [event for event in (source or {}).get('events', []) if event['stage'] == 'settings']
        if listed and empty is None:
            empty = listed[-1].get('empty_settings')
    document['history'] = [public_entry(entry) for entry in entries[-HISTORY_IN_STATUS:]]
    document['settings'] = settings_names(directory, environment, empty)
    document['backups'] = {'latest_nightly': latest_nightly(directory, environment)}
    repaired = any(event.get('checkout_repaired') for event in (data or {}).get('events', []))
    document['build_root'] = {**build_root_state(directory, fetch), 'checkout_repaired': repaired}
    return within_limit(document)


def render(document):
    """The bytes make deploy-status prints."""
    return (json.dumps(document, indent=2) + '\n').encode()


def within_limit(document):
    """At most 64 KiB as printed, which AI OS accepts: the oldest history entries go first, then the attempt's
    older events (its first, accepted, and the newest stay; events_omitted counts the rest, which its file keeps)."""
    while len(render(document)) > STATUS_LIMIT and document['history']:
        document['history'] = document['history'][1:]
    attempt = document.get('attempt')
    while attempt and len(render(document)) > STATUS_LIMIT and len(attempt['events']) > 2:
        excess = len(render(document)) - STATUS_LIMIT
        average = max(1, len(json.dumps(attempt['events'], indent=2)) // len(attempt['events']))
        drop = min(len(attempt['events']) - 2, max(1, excess // average + 1))
        attempt['events'] = attempt['events'][:1] + attempt['events'][1 + drop:]
        attempt['events_omitted'] = attempt.get('events_omitted', 0) + drop
    return document


def status_field(directory, environment, attempt, field):
    """One value for a workflow step: live (the live commit or none), code_reached or outcome (true/false, a word)."""
    if field not in FIELDS:
        raise ValueError(f"field must be {', '.join(FIELDS)}, not {field!r}")
    if field == 'live':
        return live_commit(directory, environment) or 'none'
    if not attempt:
        raise ValueError(f'field={field} needs attempt=ID')
    if field == 'code_reached':
        return 'true' if code_reached(directory, environment, attempt) else 'false'
    document = status_document(directory, environment, attempt)
    return document['attempt']['outcome']


def deploy_status(directory, environment, attempt=None, field=None, fetch=False, run=None, out=None):
    """make deploy-status: prints the document (or one field) and returns the exit status (64: bad arguments)."""
    import sys
    out = out or sys.stdout
    try:
        if attempt:
            check_attempt(attempt, 'attempt')
        if field:
            print(status_field(directory, environment, attempt, field), file=out)
            return 0
    except ValueError as error:
        print(error, file=sys.stderr)
        return 64
    out.write(render(status_document(directory, environment, attempt, run, fetch)).decode())
    return 0
