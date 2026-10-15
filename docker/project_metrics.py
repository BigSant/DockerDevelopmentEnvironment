"""Settings of the target /metrics service (SERVICES=metrics), validated before Compose sees them."""

import ipaddress
import re
from urllib.parse import urlsplit

TOKEN_MINIMUM = 24
# Container-side defaults; the collector itself lives in runtime/metrics/.
DEFAULTS = {'METRICS_SUPPORT_LEVEL': '0', 'METRICS_DISK_WRITE_MB': '100', 'METRICS_HTTP_WINDOW_MINUTES': '5',
            'METRICS_HTTP_LOG_MB': '32', 'METRICS_DOCKER_INTERVAL': '15', 'LOGS_RETENTION_DAYS': '14', 'LOGS_MAX_MB': '2048'}


def resolve_metrics(settings):
    """Values the metrics service receives; empty METRICS_URLS probes the project's own domain."""
    values = {key: str(settings.get(key) or default) for key, default in DEFAULTS.items()}
    urls = str(settings.get('METRICS_URLS') or '').split()
    if not urls and settings.get('DOMAIN'):
        # Every hostname DOMAIN lists (project_domains), the primary one first.
        hosts = str(settings.get('SETUP_DOMAIN_HOSTS') or '').split() or [settings['DOMAIN']]
        urls = [f"https://{host}/" for host in hosts]
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https') or not parts.hostname or '"' in url or '\\' in url:
            raise ValueError(f'METRICS_URLS lists http(s) URLs separated by spaces; {url!r} is not one')
    values['METRICS_URLS'] = ' '.join(urls)
    address = str(settings.get('METRICS_PROBE_ADDRESS') or '')
    if address:
        try:
            ipaddress.ip_address(address)
        except ValueError:
            raise ValueError('METRICS_PROBE_ADDRESS must be an IPv4 or IPv6 address (where the site is served)') from None
    values['METRICS_PROBE_ADDRESS'] = address
    interface = str(settings.get('METRICS_NETWORK_INTERFACE') or '')
    if interface and not re.fullmatch(r'[A-Za-z0-9_.:@-]{1,15}', interface):
        raise ValueError('METRICS_NETWORK_INTERFACE must be a network interface name such as eth0')
    values['METRICS_NETWORK_INTERFACE'] = interface
    if not re.fullmatch(r'\d', values['METRICS_SUPPORT_LEVEL']):
        raise ValueError('METRICS_SUPPORT_LEVEL must be a support level from 0 to 9')
    if not re.fullmatch(r'\d+', values['METRICS_DISK_WRITE_MB']) or int(values['METRICS_DISK_WRITE_MB']) > 1024:
        raise ValueError('METRICS_DISK_WRITE_MB must be 0 (off) to 1024')
    # The HTTP figures cover the last full minutes (the old script: 5, its cron and scrape interval).
    if not re.fullmatch(r'\d+', values['METRICS_HTTP_WINDOW_MINUTES']) or not 1 <= int(values['METRICS_HTTP_WINDOW_MINUTES']) <= 60:
        raise ValueError('METRICS_HTTP_WINDOW_MINUTES must be 1 to 60 (the Prometheus scrape interval)')
    # Each of the two files a log keeps in the HTTP log volume (current and rotated).
    if not re.fullmatch(r'\d+', values['METRICS_HTTP_LOG_MB']) or not 1 <= int(values['METRICS_HTTP_LOG_MB']) <= 1024:
        raise ValueError('METRICS_HTTP_LOG_MB must be 1 to 1024')
    # How often the Docker collector reads the containers; a snapshot older than three intervals is stale.
    if not re.fullmatch(r'\d+', values['METRICS_DOCKER_INTERVAL']) or not 5 <= int(values['METRICS_DOCKER_INTERVAL']) <= 300:
        raise ValueError('METRICS_DOCKER_INTERVAL must be 5 to 300 seconds')
    # The log collector (metrics-logs): days kept and the size of all kept files; today's files always stay.
    if not re.fullmatch(r'\d+', values['LOGS_RETENTION_DAYS']) or not 1 <= int(values['LOGS_RETENTION_DAYS']) <= 366:
        raise ValueError('LOGS_RETENTION_DAYS must be 1 to 366 days')
    if not re.fullmatch(r'\d+', values['LOGS_MAX_MB']) or not 64 <= int(values['LOGS_MAX_MB']) <= 1048576:
        raise ValueError('LOGS_MAX_MB must be 64 to 1048576')
    return values


def check_token(settings, environment):
    """The token is optional (the IP allowlist guards the endpoint); one that is set must be usable,
    since the endpoint answers 503 with a short one."""
    token = str(settings.get('METRICS_TOKEN') or '')
    if environment in ('stage', 'prod') and token and len(token) < TOKEN_MINIMUM:
        raise ValueError(f'METRICS_TOKEN on {environment} must have {TOKEN_MINIMUM}+ characters (or stay empty)')
