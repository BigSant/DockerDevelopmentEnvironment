#!/usr/bin/env python3
"""Metric names the existing Grafana dashboards and alerts query, and the group setup serves each from.

The dashboards and alerts live in the monitoring repository (server_stats_viewer), not here. Their names
are recorded in fixtures/grafana-metric-names.json; regenerate it after they change:

    python3 tests/grafana_names.py <server_stats_viewer checkout> > tests/fixtures/grafana-metric-names.json

Only names are recorded, never queries, project names or data source ids.
"""

import json
from pathlib import Path
import re
import sys

FIXTURE = Path(__file__).resolve().parent / 'fixtures/grafana-metric-names.json'

# Target /metrics groups. A ticket that implements a group adds it to IMPLEMENTED (test_metrics.py).
GROUPS = {
    'system': {
        'cpu_usage_usr', 'cpu_usage_nice', 'cpu_usage_sys', 'cpu_usage_iowait', 'cpu_usage_irq', 'cpu_usage_soft',
        'cpu_usage_steal', 'cpu_usage_guest', 'cpu_usage_gnice', 'cpu_usage_idle',
        'ram_total', 'ram_used', 'ram_free', 'ram_shared', 'ram_buff_cache', 'ram_available',
        'disk_size', 'disk_used', 'disk_available', 'disk_available_percentage',
        'disk_inodes_total', 'disk_inodes_used', 'disk_inodes_available', 'disk_inodes_available_percentage',
        'iousage_usr', 'ip_usage_nice', 'io_usage_sys', 'io_usage_iowait', 'io_usage_steal', 'io_usage_idle',
        'network_rxpck', 'network_txpck', 'network_rxkb', 'network_txkb',
        'uptime', 'disk_write_speed',
    },
    'website': {'website_response_time', 'website_response_status', 'support_level'},
    # 07: HTTP logs, and the security check (most connections per client).
    'http': {'http_response_codes', 'http_bytes_transferred_total', 'http_request_ips', 'http_error_type'},
    'security': {'ddos_most_connections_ip_count'},
    # 08: database statistics through the monitor account.
    'database': {'database_slow_query_total', 'database_slow_query_time_total', 'database_slow_query_time_average',
                 'sql_buffer_pool_size', 'sql_buffer_used', 'sql_data_cached', 'sql_dirty_pages', 'sql_ram_free'},
    # 09: connections and processes (with containers and cron, which have no dashboard yet).
    'host': {'active_connections', 'processes_consuming_cpu',
             'processes_consuming_cpu_total', 'processes_consuming_memory', 'processes_consuming_memory_total'},
}
# Not the target's: shop business data (PrestaShop module), PageSpeed (monitoring app), Prometheus itself.
ELSEWHERE = re.compile(r'^(monitoring_|pagespeed_insight_)|^up$')

KEYWORDS = {'bool', 'offset', 'and', 'or', 'unless', 'by', 'without', 'on', 'ignoring', 'group_left',
            'group_right', 'inf', 'nan', 'atan2'}


def names_in(expression):
    """Metric names in one PromQL expression (functions, labels, ranges and Grafana variables removed)."""
    text = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', '""', expression)
    text = re.sub(r'\{[^{}]*\}', ' ', text)
    text = re.sub(r'\[[^\[\]]*\]', ' ', text)
    text = re.sub(r'\b(by|without|on|ignoring|group_left|group_right)\s*\([^()]*\)', ' ', text)
    found = set()
    for match in re.finditer(r'(?<![\w$.])([A-Za-z_:][A-Za-z0-9_:]*)\s*(\()?', text):
        if not match[2] and match[1] not in KEYWORDS:
            found.add(match[1])
    return found


def expressions(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'expr' and isinstance(value, str):
                yield value
            else:
                yield from expressions(value)
    elif isinstance(node, list):
        for value in node:
            yield from expressions(value)


def extract(viewer):
    """All metric names the dashboards (script/grafana/*.json) and alerts (script/grafana/alerting/*.yaml) use."""
    import yaml
    grafana = Path(viewer) / 'script/grafana'
    documents = [json.loads(path.read_text()) for path in sorted(grafana.glob('*.json'))]
    documents += [yaml.safe_load(path.read_text()) for path in sorted(grafana.glob('alerting/*.yaml'))]
    if not documents:
        raise ValueError(f'No dashboards or alerts under {grafana}')
    return sorted(set().union(*(names_in(e) for document in documents for e in expressions(document))))


def recorded():
    return set(json.loads(FIXTURE.read_text())['names'])


def group_of(name):
    if ELSEWHERE.search(name):
        return 'elsewhere'
    return next((group for group, names in GROUPS.items() if name in names), None)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(json.dumps({'source': 'server_stats_viewer script/grafana/*.json and script/grafana/alerting/*.yaml',
                      'names': extract(sys.argv[1])}, indent=1))
