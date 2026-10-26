"""setup-server's tool access: who may open a server environment's browser tools, and from which addresses.

setup-server keeps it in the build root, beside target.env: tool-access/<env>/users.htpasswd (a line per developer
granted the environment, name:crypt hash) and tool-access/<env>/allowed-ips (its addresses, one per line). Every
deploy and make tool-access copy them to data/<env>/tool-access/ on the target, which nginx-proxy mounts (access.sh):
the users guard every password tool without its own <TOOL>_HTPASSWD, the addresses join every tool's IP list (never
the site's). make tool-access also renders the rules again and reloads nginx-proxy, so a grant or a revocation holds
at once, without a deploy. A build root without tool-access/<env>/ leaves the target as it is.
"""

from pathlib import Path
import re
import subprocess

from project_target import deploy_target

SOURCE = 'tool-access'
USERS = 'users.htpasswd'
ADDRESSES = 'allowed-ips'
# Literal IPs/CIDRs only, as access.sh accepts them.
ADDRESS = re.compile(r'[0-9a-fA-F:.]+(?:/[0-9]{1,3})?')
USER = re.compile(r'[^:\s]+:\S+')
RELOAD = '/usr/local/bin/access.sh && nginx -s reload'


def source_directory(project):
    """tool-access/<env>/ of the build root (the directory holding target.env)."""
    return Path(project.root) / SOURCE / project.environment


def lines(path):
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.lstrip().startswith('#')] \
        if path.is_file() else []


def server_addresses(project):
    """The addresses setup-server lets reach this environment's tools; [] when it keeps none."""
    return lines(source_directory(project) / ADDRESSES)


def check_source(directory):
    """Refuse what nginx-proxy would refuse, before it reaches the target. A user line may hold a secret: an error
    names its position only."""
    for number, line in enumerate(lines(directory / USERS), 1):
        if not USER.fullmatch(line):
            raise ValueError(f'{directory / USERS}: line {number} is not name:hash')
    for address in lines(directory / ADDRESSES):
        if not ADDRESS.fullmatch(address) or ('.' not in address and ':' not in address):
            raise ValueError(f'{directory / ADDRESSES}: {address[:60]!r} is not an IP or CIDR')


def target_directory(project, deploy_path):
    data = Path(project.data_directory).resolve().relative_to(Path(project.root).resolve())
    return f'{deploy_path.rstrip("/")}/{data.as_posix()}/{SOURCE}'


def push_tool_access(project, deploy_path):
    """Copy tool-access/<env>/ to data/<env>/tool-access/ on the target; False when the build root has none.

    rsync replaces each file by a rename, so nginx never reads half a user file; readable by nginx's workers."""
    source = source_directory(project)
    if not source.is_dir():
        return False
    check_source(source)
    target = deploy_target(project)
    remote = target_directory(project, deploy_path)
    target.mkdir([remote])
    target.push(f'{source}/', f'{remote}/', '-rtp', '--delete', '--checksum', '--chmod=D755,F644')
    print(f'==> tool access to {target.label}{remote}: {len(lines(source / USERS))} users, '
          f'{len(lines(source / ADDRESSES))} addresses', flush=True)
    return True


def reload_proxy(project):
    """Render the access rules again in the running nginx-proxy and reload it (a refused rule keeps the old ones)."""
    try:
        project.run(['exec', '-T', 'nginx-proxy', 'sh', '-c', RELOAD])
    except subprocess.CalledProcessError as error:
        raise ValueError(f'nginx-proxy did not take the tool access (exit {error.returncode}): is it running? '
                         f'The files are on the target; the next deploy starts it with them') from None
    print('nginx-proxy reloaded: the tool access holds now.')


def apply_tool_access(project):
    """make tool-access: what setup-server runs after a change of who may use this environment's tools."""
    from project_release import release_settings
    _, deploy_path = release_settings(project, remote=False)
    if not push_tool_access(project, deploy_path):
        print(f'No {SOURCE}/{project.environment}/ beside target.env: setup-server keeps no tool access for '
              f'{project.environment}; nothing changed.')
        return False
    reload_proxy(project)
    return True
