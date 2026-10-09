"""Client IP rules for every web entry point and the runner's stage/prod guard."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
REMOTE_LIMITS = ('PHP_MEMORY=1g\nPHP_CPUS=1\nDATABASE_MEMORY=2g\nDATABASE_CPUS=1\n'
                 'WEBSERVER_MEMORY=256m\nWEBSERVER_CPUS=1\nPROXY_MEMORY=128m\nPROXY_CPUS=0.5\n')
sys.path[:0] = [str(ROOT), str(ROOT / 'docker')]
from create_project import scaffold
from project import Project

ACCESS = ROOT / 'docker/docker/nginx-proxy/conf/access.sh'
PROXY_CONF = ROOT / 'docker/docker/nginx-proxy/conf'
# /proc/net/route of a container on a Docker bridge: default route via 172.29.0.1.
ROUTES = ('Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n'
          'eth0\t00000000\t01001DAC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n'
          'eth0\t00001DAC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n')


class AccessRulesTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.output = Path(temporary.name)

    def rules(self, environment, routes=ROUTES, **settings):
        (self.output / 'route').write_text(routes)
        env = {'PATH': os.environ['PATH'], 'SETUP_ENVIRONMENT': environment,
               'SETUP_ACCESS_DIR': str(self.output / 'access'), 'SETUP_REAL_IP_FILE': str(self.output / 'real-ip.conf'),
               'SETUP_ROUTE_FILE': str(self.output / 'route'), **settings}
        result = subprocess.run(['bash', str(ACCESS)], env=env, capture_output=True, text=True, timeout=5)
        read = lambda name: (self.output / 'access' / f'{name}.conf').read_text() if result.returncode == 0 else None
        return result, read

    def test_empty_lists_keep_local_and_test_open(self):
        for environment in ('local', 'test'):
            result, read = self.rules(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('site', 'pma', 'mailpit', 'cron', 'dbmonitor', 'metrics'):
                self.assertEqual(read(name), '', name)

    def test_empty_lists_deny_tools_but_keep_the_site_open_on_stage_and_prod(self):
        for environment in ('stage', 'prod'):
            result, read = self.rules(environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(read('site'), '')
            for name in ('pma', 'mailpit', 'cron', 'dbmonitor', 'metrics'):
                self.assertEqual(read(name), 'deny all;\n', name)

    def test_common_list_applies_everywhere_and_service_list_replaces_it(self):
        result, read = self.rules('local', ALLOWED_IPS='203.0.113.10,198.51.100.0/24',
                                  PMA_ALLOWED_IPS='2001:db8::10')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(read('site'), 'allow 203.0.113.10;\nallow 198.51.100.0/24;\ndeny all;\n')
        self.assertEqual(read('mailpit'), read('site'))
        self.assertEqual(read('pma'), 'allow 2001:db8::10;\ndeny all;\n')

    def real_ip(self):
        return (self.output / 'real-ip.conf').read_text()

    def test_behind_the_host_proxy_only_the_docker_gateway_and_loopback_are_trusted(self):
        # Ports on 127.0.0.1: DirectAdmin / the host Nginx arrive through Docker's gateway, nothing else may forward.
        result, _ = self.rules('prod')
        self.assertEqual(result.returncode, 0, result.stderr)
        conf = self.real_ip()
        trusted = [line.split()[1].rstrip(';') for line in conf.splitlines() if line.startswith('set_real_ip_from')]
        self.assertEqual(trusted, ['127.0.0.1', '::1', '172.29.0.1'])
        self.assertIn('real_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n', conf)
        self.assertIn('    172.29.0.1 1;\n', conf)
        for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'):
            self.assertNotIn(network, conf)

    def test_without_a_default_route_only_loopback_is_trusted(self):
        result, _ = self.rules('prod', routes=ROUTES.splitlines(keepends=True)[0])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([line for line in self.real_ip().splitlines() if line.startswith('set_real_ip_from')],
                         ['set_real_ip_from 127.0.0.1;', 'set_real_ip_from ::1;'])

    def test_direct_clients_trust_no_forwarding_headers(self):
        # Ports on a VPN/LAN address: clients connect directly, so their X-Forwarded-* are never trusted.
        result, _ = self.rules('prod', SETUP_DIRECT_CLIENTS='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        conf = self.real_ip()
        self.assertNotIn('set_real_ip_from', conf)
        self.assertNotIn('real_ip_header', conf)
        self.assertIn('geo $realip_remote_addr $setup_trusted_proxy {\n    default 0;\n}\n', conf)

    def test_explicit_trusted_proxies_win_in_both_modes(self):
        for direct in ('0', '1'):
            result, _ = self.rules('local', TRUSTED_PROXIES='10.20.0.5', SETUP_DIRECT_CLIENTS=direct)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.real_ip(),
                             'set_real_ip_from 10.20.0.5;\nreal_ip_header X-Forwarded-For;\nreal_ip_recursive on;\n'
                             'geo $realip_remote_addr $setup_trusted_proxy {\n    default 0;\n    10.20.0.5 1;\n}\n')

    def test_upstreams_get_only_the_resolved_client_and_the_scheme_from_trusted_proxies(self):
        # Appending to the client's X-Forwarded-For would hand a spoofed chain to Apache/PHP.
        for path in PROXY_CONF.iterdir():
            self.assertNotIn('$proxy_add_x_forwarded_for', path.read_text(), path.name)
        for path in (PROXY_CONF / 'proxy_params', PROXY_CONF / 'sites.conf'):
            self.assertIn('X-Forwarded-For $remote_addr;', ' '.join(path.read_text().split()), path.name)
        nginx = (PROXY_CONF / 'nginx.conf').read_text()
        self.assertIn('map "$setup_trusted_proxy:$http_x_forwarded_proto" $setup_forwarded_proto {', nginx)
        self.assertIn('"~^1:(?<proto>https?)$" $proto;', nginx)

    def test_directive_injection_and_hostnames_are_rejected(self):
        for value in ('all', 'example.com', '203.0.113.1; allow all;', '${IP}', '1.2.3.4}'):
            for key in ('ALLOWED_IPS', 'SITE_ALLOWED_IPS', 'TRUSTED_PROXIES'):
                with self.subTest(value=value, key=key):
                    result, _ = self.rules('prod', **{key: value})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Invalid IP/CIDR', result.stderr)


class RunnerAccessTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.app = scaffold('access-demo', temporary.name)
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SERVICES=pma mailpit\n')
        (self.app / 'env/stage.env').write_text(
            REMOTE_LIMITS + 'DOMAIN=demo.example.com\nDATABASE_USER=demo\nDATABASE_NAME=demo\nDATABASE_ROOT_PASSWORD=root-test-only\nDATABASE_PASSWORD=test\n')

    def test_stage_tools_require_an_allowlist(self):
        with self.assertRaisesRegex(ValueError, 'pma on stage requires PMA_ALLOWED_IPS or ALLOWED_IPS'):
            Project(self.app, 'stage')
        with (self.app / 'env/stage.env').open('a') as env:
            env.write('ALLOWED_IPS=203.0.113.10\nSITE_ALLOWED_IPS=198.51.100.0/24\n')
        services = json.loads(Project(self.app, 'stage').capture(['config', '--format', 'json']))['services']
        proxy = services['nginx-proxy']['environment']
        self.assertEqual(proxy['ALLOWED_IPS'], '203.0.113.10')
        self.assertEqual(proxy['SITE_ALLOWED_IPS'], '198.51.100.0/24')

    def test_cron_ui_is_guarded_and_edits_its_crontab_only_locally(self):
        with (self.app / 'env/common.env').open('a') as env:
            env.write('SERVICES=cron\n')
        with self.assertRaisesRegex(ValueError, 'cron on stage requires CRON_ALLOWED_IPS or ALLOWED_IPS'):
            Project(self.app, 'stage')
        with (self.app / 'env/stage.env').open('a') as env:
            env.write('CRON_ALLOWED_IPS=203.0.113.10\n')
        stage = Project(self.app, 'stage')
        self.assertEqual((stage.process_env['SETUP_ENABLE_CRON'], stage.process_env['SETUP_CRON_CONFIG_ACCESS']), ('1', 'ro'))
        local = Project(self.app)
        self.assertEqual((local.process_env['SETUP_ENABLE_CRON'], local.process_env['SETUP_CRON_CONFIG_ACCESS']), ('1', 'rw'))

    def test_local_tools_start_without_a_list_and_ports_bind_to_localhost(self):
        services = json.loads(Project(self.app).capture(['config', '--format', 'json']))['services']
        self.assertEqual(services['nginx-proxy']['environment']['SETUP_ENABLE_MAILPIT'], '1')
        for port in services['nginx-proxy']['ports']:
            self.assertEqual(port['host_ip'], '127.0.0.1')

        self.assertEqual(services['nginx-proxy']['environment']['SETUP_DIRECT_CLIENTS'], '0')

    def ports(self, address, environment='stage'):
        with (self.app / f'env/{environment}.env').open('a') as env:
            env.write(f'ALLOWED_IPS=203.0.113.10\nPUBLISH_ADDRESS={address}\n')
        project = Project(self.app, environment)
        proxy = json.loads(project.capture(['config', '--format', 'json']))['services']['nginx-proxy']
        return project, proxy

    def test_ports_publish_on_the_configured_address_for_direct_clients(self):
        project, proxy = self.ports('10.8.0.14')
        self.assertEqual(project.settings['PUBLISH_ADDRESS'], '10.8.0.14')
        self.assertEqual(sorted((p['host_ip'], p['target']) for p in proxy['ports']), [('10.8.0.14', 80), ('10.8.0.14', 443)])
        self.assertEqual(proxy['environment']['SETUP_DIRECT_CLIENTS'], '1')

    def test_loopback_addresses_keep_the_host_proxy_mode(self):
        for address in ('127.0.0.1', '::1'):
            with self.subTest(address=address):
                _, proxy = self.ports(address, 'local')
                self.assertEqual({p['host_ip'] for p in proxy['ports']}, {address})
                self.assertEqual(proxy['environment']['SETUP_DIRECT_CLIENTS'], '0')

    def test_invalid_publish_address_is_a_clear_error(self):
        for address in ('vpn0', '10.8.0', '10.8.0.0/24', '10.8.0.14:80', 'fe80::1%eth0', '0.0.0.0', '::'):
            with self.subTest(address=address):
                (self.app / 'env/local.env').write_text(
                    (self.app / 'env/local.env').read_text().split('PUBLISH_ADDRESS=')[0] + f'PUBLISH_ADDRESS={address}\n')
                with self.assertRaisesRegex(ValueError, 'PUBLISH_ADDRESS must be one IPv4 or IPv6 address'):
                    Project(self.app)


if __name__ == '__main__':
    unittest.main()
