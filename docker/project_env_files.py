"""Project settings read straight from the env files, for commands that run before (or without) Compose.

The runner resolves settings through Compose (project-settings.yaml), which needs env/<env>.env and
Docker. A CI deploy step decides what to check before that file exists, and the Deployment
variables list is read on any machine, so both read the committed files themselves: setup's
docker/.env, env/common.env and env/<env>.env.example (env/<env>.env when there is no example).
The parser follows the dotenv rules setup documents (KEY=value, 'single quotes' literal, "double
quotes" with escapes, quoted values over several lines, # comments); it does not interpolate.
"""

from pathlib import Path
import re

DOCKER_ROOT = Path(__file__).resolve().parent
ENTRY = re.compile(r'(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*[=:][ \t]*')
NAME = re.compile(r'[A-Z][A-Z0-9_]*')
# Where a server deploys stage/prod is the server's knowledge, not the project's: setup-server registers it
# as target.env in the build root, beside the environment repository (bin/add-project).
TARGET_KEYS = ('DEPLOY_CONTEXT', 'DEPLOY_SSH', 'DEPLOY_PATH', 'APP_UID', 'APP_GID')
TARGET_FILE = 'target.env'
TARGET_VALUE = re.compile(r'[A-Za-z0-9@._:/+-]*')
ESCAPES = {'n': '\n', 't': '\t', 'r': '\r'}


def _closing(text, quote):
    """Index of the closing quote in text (after the opening one), or -1."""
    if quote == "'":
        return text.find("'")
    index = 0
    while index < len(text):
        if text[index] == '\\':
            index += 2
            continue
        if text[index] == '"':
            return index
        index += 1
    return -1


def parse_dotenv(text, source='env file', strict=False):
    """{key: value} of a dotenv text; never repeats a line in an error, it may hold a secret.

    strict refuses $ outside single quotes: Compose would expand it, so a value stored elsewhere
    unexpanded would differ from what the env file means.
    """
    values = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        number, line = index + 1, lines[index].lstrip()
        index += 1
        if not line.strip() or line.startswith('#'):
            continue
        match = ENTRY.match(line)
        if not match:
            raise ValueError(f'{source}:{number}: not a KEY=value line')
        key, raw = match[1], line[match.end():]
        quote = raw[:1] if raw[:1] in ("'", '"') else ''
        if quote:
            body = raw[1:]
            end = _closing(body, quote)
            while end < 0:
                if index >= len(lines):
                    kind = 'single' if quote == "'" else 'double'
                    raise ValueError(f'{source}:{number}: {key} has no closing {kind} quote')
                body += '\n' + lines[index]
                index += 1
                end = _closing(body, quote)
            value = body[:end]
            if quote == '"':
                value = re.sub(r'\\(.)', lambda escape: ESCAPES.get(escape[1], escape[1]), value, flags=re.S)
        else:
            value = '' if raw.startswith('#') else re.split(r'\s#', raw, maxsplit=1)[0].strip()
        if strict and quote != "'" and '$' in value:
            raise ValueError(f"{source}:{number}: {key} holds $ outside single quotes: put the value in 'single quotes'")
        values[key] = value
    return values


def read_dotenv(path, strict=False):
    return parse_dotenv(Path(path).read_text(encoding='utf-8'), str(path), strict)


def environment_file(directory, environment):
    """The committed settings of one environment: env/<env>.env.example, else env/<env>.env (or None)."""
    for name in (f'env/{environment}.env.example', f'env/{environment}.env'):
        if (Path(directory) / name).is_file():
            return Path(directory) / name
    return None


def target_file(directory, environment=None):
    """The build root's target.<env>.env, else its target.env: beside app/ (or app/ of the app/docker layout).

    One build root deploys both stage and prod; target.<env>.env sends one of them elsewhere.
    """
    directory = Path(directory)
    root = directory.parent.parent if directory.parent.name == 'app' else directory.parent
    specific = root / f'target.{environment}.env' if environment else None
    return specific if specific and specific.is_file() else root / TARGET_FILE


def target_settings(directory, environment=None):
    """The deploy target setup-server registered for this build root and ENV; {} when there is none."""
    path = target_file(directory, environment)
    if not path.is_file():
        return {}
    values = read_dotenv(path)
    unknown = sorted(set(values) - set(TARGET_KEYS))
    if unknown:
        raise ValueError(f"{path} sets {', '.join(unknown)}; it holds only {' '.join(TARGET_KEYS)}")
    odd = sorted(key for key, value in values.items() if not TARGET_VALUE.fullmatch(value))
    if odd:
        raise ValueError(f"{path}: unexpected characters in {', '.join(odd)}")
    return values


def file_settings(directory, environment):
    """(values, mentioned): the merged settings, and every key the project's files name -> its value.

    A key the project files name hides the process environment (Deployment variables, GitHub secrets),
    except setup's own secrets left blank (project.PROCESS_SECRETS).
    """
    directory = Path(directory)
    values, mentioned = {}, {}
    if (DOCKER_ROOT / '.env').is_file():
        values.update(read_dotenv(DOCKER_ROOT / '.env'))
    for path in (directory / 'env/common.env', environment_file(directory, environment)):
        if path and path.is_file():
            entries = read_dotenv(path)
            values.update(entries)
            mentioned.update(entries)
    if environment in ('stage', 'prod'):
        values.update(target_settings(directory, environment))
    return values, mentioned


def names_setting(values, key):
    """The variable names of a space-separated setting, validated, in their first-listed order."""
    names = list(dict.fromkeys(str(values.get(key) or '').split()))
    invalid = [name for name in names if not NAME.fullmatch(name)]
    if invalid:
        raise ValueError(f"{key} lists variable names only (A-Z, 0-9, _; starting with a letter): {', '.join(invalid)}")
    return names


def version_tuple(text):
    if not re.fullmatch(r'\d+(\.\d+){0,2}', text):
        return None
    return tuple(int(part) for part in text.split('.')) + (0,) * (2 - text.count('.'))


def needed_setup_version(directory):
    """SETUP_MIN_VERSION of env/common.env when this setup is older than it, else None."""
    common = Path(directory) / 'env/common.env'
    if not common.is_file():
        return None
    wanted = str(read_dotenv(common).get('SETUP_MIN_VERSION') or '').strip().removeprefix('v')
    if not wanted:
        return None
    if version_tuple(wanted) is None:
        raise ValueError(f'SETUP_MIN_VERSION in {common} must be a version such as 2.3.0, not {wanted!r}')
    version = (DOCKER_ROOT.parent / 'VERSION').read_text().strip()
    if version_tuple(version) is None or version_tuple(version) < version_tuple(wanted):
        return wanted
    return None


def require_setup_version(directory):
    """SETUP_MIN_VERSION in env/common.env: the oldest setup the project runs with; nothing when unset."""
    wanted = needed_setup_version(directory)
    if wanted:
        version = (DOCKER_ROOT.parent / 'VERSION').read_text().strip()
        raise ValueError(f'This project needs setup {wanted} or newer (SETUP_MIN_VERSION in env/common.env); '
                         f'the setup at {DOCKER_ROOT.parent} is {version}. Update it first (git -C '
                         f'{DOCKER_ROOT.parent} pull; on a setup-server: the web UI or bin/update --setup-ref '
                         f'v{wanted} - a deploy does it itself within the same MAJOR version)')
