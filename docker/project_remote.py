"""The checkout's origin remote: its URL, and the owner/repository of a CI provider's hosting."""
import re
import subprocess

REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+')


def origin_url(directory):
    """The origin remote's URL, or '' when the directory has none (or is not a checkout)."""
    result = subprocess.run(['git', '-C', str(directory), 'remote', 'get-url', 'origin'], capture_output=True, text=True)
    return '' if result.returncode else result.stdout.strip()


def repository(directory, values, setting, host, provider, owner, failure):
    """owner/repository: the setting when given, else the origin remote when it is on host; failure is raised."""
    configured = str(values.get(setting) or '').strip()
    if configured:
        if not REPOSITORY.fullmatch(configured):
            raise failure(f'{setting} must be {owner}/repository, not {configured!r}')
        return configured
    remote = re.compile(re.escape(host) + r'[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/*$')
    match = remote.search(origin_url(directory))
    if not match:
        raise failure(f'Cannot tell the {provider} repository: the origin remote is not on {host}; set '
                      f'{setting}={owner}/repository in env/common.env')
    return f'{match[1]}/{match[2]}'
