"""The environments of a project: local and test on a developer machine, stage and prod on a server, and the
extra stage-class server environments env/common.env lists in STAGE_ENVIRONMENTS (e.g. a temporary `mdv` for a
large piece of work, with its own domain, database and containers).

An extra environment behaves exactly like stage: every guard, permission and policy of stage applies to it
(limits, tool allowlists, APP_UID, the secret store, mail, non-prod after-import SQL, releases from an untagged
setup). Only prod is prod. Its deployment environment (Bitbucket) or GitHub environment carries its own name;
stage and prod keep staging and production.
"""

from pathlib import Path
import re

SETTING = 'STAGE_ENVIRONMENTS'
LOCAL = ('local', 'test')
RESERVED = ('local', 'test', 'stage', 'prod')
NAME = re.compile(r'[a-z][a-z0-9]{1,15}')
# The deployment environment names of both providers: staging -> ENV=stage, production -> ENV=prod.
DEPLOYMENT_ENVIRONMENTS = {'staging': 'stage', 'production': 'prod'}
DEPLOYMENT_NAMES = {setup: name for name, setup in DEPLOYMENT_ENVIRONMENTS.items()}


def parse(value):
    """The names of a STAGE_ENVIRONMENTS value (space or comma separated), in their order; ValueError for a
    name that is not lowercase letters and digits (2-16, a letter first) or that setup already has."""
    names = list(dict.fromkeys(str(value or '').replace(',', ' ').split()))
    invalid = [name for name in names if not NAME.fullmatch(name)]
    if invalid:
        raise ValueError(f"{SETTING} names lowercase letters and digits, 2-16, a letter first: {', '.join(invalid)}")
    taken = [name for name in names if name in RESERVED]
    if taken:
        raise ValueError(f"{SETTING} lists extra environments only; {', '.join(taken)} already exist")
    return tuple(names)


def stage_environments(source):
    """The extra environments: of a settings mapping, or of the env/common.env of an environment repository."""
    if isinstance(source, (str, Path)):
        from project_env_files import read_dotenv
        common = Path(source) / 'env/common.env'
        source = read_dotenv(common) if common.is_file() else {}
    return parse(source.get(SETTING))


def environments(source):
    """Every environment the project has: local, test, stage, prod and its extra ones."""
    return (*RESERVED, *stage_environments(source))


def server_environments(source):
    """The environments that run on a server: stage, prod and the extra ones."""
    return ('stage', 'prod', *stage_environments(source))


def is_server(environment):
    """A server environment (stage, prod or an extra one): anything but local and test."""
    return environment not in LOCAL


def is_stage_class(environment):
    """stage or an extra environment: a server environment that is not prod."""
    return is_server(environment) and environment != 'prod'


def require(source, environment):
    """ENV, refused unless the project has it."""
    if environment in RESERVED:
        return environment
    known = stage_environments(source)
    if environment not in known:
        listed = f' (it lists {" ".join(known)})' if known else ''
        raise ValueError(f'ENV={environment} is not an environment of this project: local, test, stage, prod, or one '
                         f'that {SETTING} in env/common.env lists{listed}')
    return environment


def deployment_environment(name, extra=()):
    """The setup ENV a deployment (Bitbucket) or GitHub environment name stands for, or None."""
    lowered = name.lower()
    return DEPLOYMENT_ENVIRONMENTS.get(lowered) or (lowered if lowered in extra else None)


def deployment_name(environment):
    """The deployment (Bitbucket) or GitHub environment of ENV: staging, production, or the extra one's own name."""
    return DEPLOYMENT_NAMES.get(environment, environment)
