"""New-project onboarding: isolation, retries and the real generated Compose model."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from create_project import scaffold, start, main, normalize_name
from project import Project
from project_environment import initialize_directories, initialize_env
from project_bootstrap import bootstrap


class CreateProjectTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / '.test-work'
        scratch.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)

    def create(self, name='new-shop'):
        with contextlib.redirect_stdout(io.StringIO()):
            return scaffold(name, self.parent)

    def test_phpstorm_name_is_ready_before_first_open_without_docker(self):
        with patch('create_project.subprocess.run', side_effect=AssertionError('Must not invoke Docker or bootstrap')), \
                patch('project_bootstrap.available_ports', side_effect=AssertionError('Ports belong to bootstrap')):
            app = self.create('melga')
        self.assertEqual((app / '.idea/.name').read_text(), 'melga\n')
        self.assertEqual({p.name for p in (app / '.idea').iterdir()}, {'.name'})

    def test_new_project_has_working_core_config_and_private_credentials(self):
        app = self.create()
        self.assertEqual({p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_file()}, {
            'Makefile', '.gitignore', 'compose/base.yaml', 'env/common.env',
            'env/local.env', 'public/index.php', '.generated/create-project.json', '.idea/.name',
        })
        self.assertEqual({p.name for p in app.iterdir() if p.is_dir()}, {'compose', 'env', 'public', '.generated', '.idea'})
        project = Project(app)
        active = json.loads(project.capture(['config', '--format', 'json']))['services']
        self.assertEqual(set(active), {'nginx-proxy', 'webserver', 'php-fpm', 'database'})
        self.assertEqual(set(project.model()['services']), set(active) | {'php-fpm-base'})
        self.assertEqual(Path(active['php-fpm']['build']['dockerfile']), ROOT / 'docker/Dockerfile')
        self.assertEqual(project.settings['EXTRA_COMPOSE_FILES'], '')
        self.assertEqual((app / 'env/common.env').read_text(), 'PROJECT_NAME=new-shop\n')
        self.assertEqual({line.split('=', 1)[0] for line in (app / 'env/local.env').read_text().splitlines() if line},
                         {'DOMAIN', 'DATABASE_USER', 'DATABASE_NAME', 'DATABASE_PASSWORD'})
        self.assertEqual(project.settings['LOCALHOST_PORT'], '0')
        self.assertEqual(project.profiles, '')
        self.assertRegex(active['database']['image'], r'-s[0-9a-f]{12}$')
        self.assertNotIn('REDIS_VERSION', (app / 'env/common.env').read_text())
        self.assertEqual(project.settings['PROFILE'], '')
        self.assertEqual(project.settings['CACHE_MODE'], 'off')
        self.assertEqual(project.settings['MAIL_MODE'], 'off')
        self.assertEqual(project.web_directory, app / 'public')
        self.assertEqual(project.data_directory, app.parent / 'data')
        self.assertEqual(project.settings['DOMAIN'], 'new-shop.local')
        self.assertIn('Project new-shop is running.', (app / 'public/index.php').read_text())
        self.assertRegex(active['database']['environment']['MYSQL_PASSWORD'], r'^[0-9a-f]{48}$')
        self.assertEqual((app / 'env/local.env').stat().st_mode & 0o777, 0o600)
        result = subprocess.run(['make', '-s', '-C', str(app), f'SETUP_DIRECTORY={ROOT}', 'check'],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_directories(project)
        self.assertEqual({p.name for p in (app / 'config').iterdir()}, {'php', 'mysql', 'apache', 'nginx-proxy'})

    def test_private_env_can_be_restored_from_a_user_supplied_template(self):
        app = self.create()
        (app / 'env/local.env').unlink()
        (app / 'env/local.env.example').write_text(
            'DOMAIN=new-shop.local\nDATABASE_USER=example\nDATABASE_NAME=example\nDATABASE_PASSWORD=example\n')
        with contextlib.redirect_stdout(io.StringIO()):
            initialize_env(app, 'local')
        project = Project(app)
        self.assertEqual(project.settings['DOMAIN'], 'new-shop.local')
        self.assertEqual((app / 'env/local.env').stat().st_mode & 0o777, 0o600)

    def test_project_yaml_adds_service_without_profile_env(self):
        app = self.create()
        (app / 'compose/local.yaml').write_text('services:\n  worker:\n    image: alpine:3.20\n    command: ["sleep", "infinity"]\n')
        project = Project(app)
        active = json.loads(project.capture(['config', '--format', 'json']))['services']
        self.assertIn('worker', active)
        self.assertEqual(project.profiles, '')
        for environment in ('stage', 'prod', 'test'):
            (app / f'env/{environment}.env').write_text(
                f'DOMAIN=new-shop.{environment}.local\nDATABASE_NAME=example\nDATABASE_USER=example\nDATABASE_PASSWORD=example\n')
            other = Project(app, environment)
            self.assertEqual(other.settings['PROFILE'], '')
            self.assertEqual(other.profiles, '')
            active = json.loads(other.capture(['config', '--format', 'json']))['services']
            self.assertEqual(set(active), {'nginx-proxy', 'webserver', 'php-fpm', 'database'})

    def test_bootstrap_assigns_distinct_ports_and_preserves_credentials(self):
        first, second = Project(self.create('one')), Project(self.create('two'))
        one_password = first.model()['services']['database']['environment']['MYSQL_PASSWORD']
        two_password = second.model()['services']['database']['environment']['MYSQL_PASSWORD']
        self.assertNotEqual(one_password, two_password)
        with patch('project_bootstrap.prepare_host') as host, patch('project_bootstrap.initialize_ide'), \
                contextlib.redirect_stdout(io.StringIO()):
            first = bootstrap(first)
            second = bootstrap(second)
            self.assertEqual(host.call_count, 2)
            before = first.env_files[-1].read_bytes()
            bootstrap(first)
            self.assertEqual(first.env_files[-1].read_bytes(), before)
        one = {first.settings[k] for k in ('LOCALHOST_PORT', 'LOCALHOST_PORT_SSL')}
        two = {second.settings[k] for k in ('LOCALHOST_PORT', 'LOCALHOST_PORT_SSL')}
        self.assertFalse(one & two)

    def test_retry_preserves_every_existing_file(self):
        app = self.create()
        (app / '.idea/.name').write_text('My custom display name\n')
        (app / 'public/index.php').write_text('<?php echo "My application";')
        with (app / 'env/local.env').open('a') as handle:
            handle.write('CACHE_MODE=on\n')
        # Explicit extensions belong to the user; a retry must not remove them.
        (app / 'compose/redis.yaml').write_text('services: {}\n')
        before = {p.relative_to(app): p.read_bytes() for p in app.rglob('*') if p.is_file()}
        self.assertEqual(self.create(), app)
        after = {p.relative_to(app): p.read_bytes() for p in app.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_retry_repairs_missing_name_without_rewriting_ide_workspace(self):
        app = self.create('melga')
        (app / '.idea/.name').unlink()
        workspace = app / '.idea/workspace.xml'
        workspace.write_bytes(b'<project version="4"><component name="Personal" /></project>')
        before = workspace.read_bytes()
        self.create('melga')
        self.assertEqual((app / '.idea/.name').read_text(), 'melga\n')
        self.assertEqual(workspace.read_bytes(), before)

    def test_project_name_symlink_is_rejected_without_changing_target(self):
        app = self.create()
        path = app / '.idea/.name'
        path.unlink()
        outside = self.parent / 'name'
        outside.write_text('keep')
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.create()
        self.assertEqual(outside.read_text(), 'keep')

    def test_existing_directory_and_symlink_are_preserved(self):
        root = self.parent / 'new-shop'
        root.mkdir()
        sentinel = root / 'user-file'
        sentinel.write_text('keep')
        with self.assertRaisesRegex(ValueError, 'Directory already exists'):
            self.create()
        self.assertEqual(list(root.iterdir()), [sentinel])
        (self.parent / 'linked').symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.create('linked')
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_invalid_names_do_not_create_files(self):
        for name in ('../escape', 'with space', 'with__gap', '-leading', 'trailing-', 'a' * 33, 'two--dashes'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.create(name)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_original_display_name_and_normalized_identity_survive_ide_init(self):
        from project_ide import ide_plan
        for entered, normalized in [('Melga', 'melga'), ('MelgaMCP', 'melga-mcp'),
                                    ('GameroomAkeneo', 'gameroom-akeneo')]:
            with self.subTest(entered=entered):
                app = self.create(entered)
                self.assertEqual(app.parent.name, normalized)
                project = Project(app)
                self.assertEqual(project.settings['PROJECT_NAME'], normalized)
                self.assertEqual((app / 'env/common.env').read_text(), f'PROJECT_NAME={normalized}\n')
                self.assertEqual(project.settings['DATABASE_NAME'], normalized.replace('-', '_'))
                self.assertEqual(project.settings['DOMAIN'], normalized.replace('_', '-') + '.local')
                self.assertEqual((app / '.idea/.name').read_text(), entered + '\n')
                model = project.model()
                self.assertEqual(model['name'], normalized + '-local')
                self.assertEqual(model['services']['database']['environment']['MYSQL_USER'], normalized.replace('-', '_'))
                self.assertTrue(all(service.get('container_name', normalized).startswith(normalized)
                                    for key, service in model['services'].items() if key != 'php-fpm-base'))
                _, outputs, _ = ide_plan(project, 'Docker')
                self.assertEqual(outputs['.idea/.name'], (entered + '\n').encode())
                # Both spellings address the same project, preserving the original display name.
                before = (app / 'env/local.env').read_bytes()
                (app / '.idea/.name').unlink()
                self.assertEqual(self.create(normalized), app)
                self.assertEqual(self.create(normalized.replace('-', '_')), app)
                self.assertEqual((app / '.idea/.name').read_text(), entered + '\n')
                self.assertEqual((app / 'env/local.env').read_bytes(), before)
                with contextlib.redirect_stdout(io.StringIO()): initialize_env(app, 'local')
                self.assertEqual(Project(app).settings['DOMAIN'], normalized.replace('_', '-') + '.local')
                self.assertEqual((app / 'env/local.env').read_bytes(), before)

    def test_ide_name_is_optional_and_custom_name_is_preserved(self):
        from project_ide import ide_plan
        app = self.create('MelgaMCP')
        (app / '.idea/.name').write_text('Custom IDE name\n')
        _, outputs, _ = ide_plan(Project(app), 'Docker')
        self.assertEqual(outputs['.idea/.name'], b'Custom IDE name\n')
        (app / '.idea/.name').unlink()
        _, outputs, _ = ide_plan(Project(app), 'Docker')
        self.assertEqual(outputs['.idea/.name'], b'melga-mcp\n')
        with (app / 'env/common.env').open('a') as env:
            env.write('PROJECT_DISPLAY_NAME=Explicit name\n')
        _, outputs, _ = ide_plan(Project(app), 'Docker')
        self.assertEqual(outputs['.idea/.name'], b'Explicit name\n')

    def test_acronyms_and_explicit_separators(self):
        for value, expected in [('XMLParser', 'xml-parser'), ('MCP', 'mcp'), ('Shop2API', 'shop2-api'),
                                ('melga_mcp', 'melga-mcp'), ('old-shop', 'old-shop')]:
            with self.subTest(value=value): self.assertEqual(normalize_name(value), expected)

    def test_domain_alias_collision_is_rejected_before_creating_files(self):
        # Projects created with the previous underscore naming retain their files.
        app = self.parent / 'melga_mcp/app'
        (app / 'env').mkdir(parents=True)
        (app / 'env/local.env').write_text('DOMAIN=melga-mcp.local\n')
        private = (app / 'env/local.env').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Domain .* is already used'):
            self.create('MelgaMCP')
        self.assertFalse((self.parent / 'melga-mcp').exists())
        self.assertEqual((app / 'env/local.env').read_bytes(), private)

    def test_failed_start_preserves_scaffold_and_retry_is_possible(self):
        app = self.create()
        private = (app / 'env/local.env').read_bytes()
        with patch('create_project.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['make'])), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            start(app)
        self.assertEqual(self.create(), app)
        self.assertEqual((app / 'env/local.env').read_bytes(), private)

    def test_public_command_help_and_invalid_name(self):
        help_result = subprocess.run([str(ROOT / 'create-project'), '--help'], cwd=self.parent,
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('name', help_result.stdout)
        result = subprocess.run([str(ROOT / 'create-project'), '../invalid', '--no-start'],
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Name', result.stderr)

    def test_default_creation_does_not_require_docker_or_start_services(self):
        app = self.parent / 'demo/app'
        with patch('create_project.scaffold', return_value=app), \
                patch('create_project.prerequisites') as prerequisites, \
                patch('create_project.start') as start_project, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['demo']), 0)
            prerequisites.assert_not_called()
            start_project.assert_not_called()
            self.assertEqual(main(['demo', '--start']), 0)
            prerequisites.assert_called_once_with()
            start_project.assert_called_once_with(app)
